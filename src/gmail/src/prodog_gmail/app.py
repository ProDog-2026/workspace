import asyncio
import hmac
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .demo import demo_messages
from .errors import ConnectorError
from .google import GoogleClient
from .mail import mail_metadata, parse_gmail_message
from .scan import load_detector, scan_mail
from .store import Store
from .supabase import Supabase

COOKIE = "prodog_session"
STATIC = Path(__file__).parent / "static"
MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        config = settings or get_settings()
        application.state.settings = config
        application.state.store = Store(config)
        application.state.rates = defaultdict(deque)
        application.state.detector = load_detector(config.detector_model_path)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=10),
            follow_redirects=False,
            transport=transport,
        ) as client:
            application.state.google = GoogleClient(config, application.state.store, client)
            application.state.supabase = (
                Supabase(config, client) if config.supabase_configured else None
            )
            yield

    application = FastAPI(
        title="ProDog Gmail connector",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def session(request: Request, *, required=False):
        secret = request.cookies.get(COOKIE)
        current = application.state.store.session(secret)
        if required and (
            not current
            or not current["account_id"]
            or current["mode"] != application.state.settings.app_mode
        ):
            raise ConnectorError("authentication_required", "먼저 메일 계정을 연결해 주세요.", 401)
        return secret, current

    def csrf(request: Request):
        secret, current = session(request)
        origin = request.headers.get("origin", "")
        supplied = request.headers.get("x-csrf-token", "")
        if (
            origin != application.state.settings.app_base_url
            or not current
            or not supplied
            or len(supplied) != 64
            or not supplied.isascii()
            or not hmac.compare_digest(supplied, application.state.store.csrf(secret))
        ):
            raise ConnectorError(
                "csrf_failed", "요청을 확인하지 못했습니다. 페이지를 새로고침하세요.", 403
            )
        return secret, current

    def set_cookie(response, secret: str):
        response.set_cookie(
            COOKIE,
            secret,
            max_age=application.state.settings.session_ttl_seconds,
            httponly=True,
            secure=False,
            samesite="lax",
            path="/",
        )

    def session_payload(secret: str):
        current = application.state.store.session(secret)
        config = application.state.settings
        authenticated = bool(
            current
            and current["account_id"]
            and current["mode"] == config.app_mode
            and (
                current["mode"] == "demo"
                or application.state.store.has_tokens(current["account_id"])
            )
        )
        bundle = (
            application.state.store.tokens(current["account_id"])
            if authenticated and config.app_mode == "google"
            else None
        )
        return {
            "authenticated": authenticated,
            "needs_reconnect": bool(bundle and bundle.get("needs_reconnect")),
            "email": current["email"] if authenticated else None,
            "csrf_token": application.state.store.csrf(secret),
            "mode": config.app_mode,
            "configured": config.google_configured if config.app_mode == "google" else True,
        }

    def limit_requests(secret: str, action: str, maximum: int):
        now = time.monotonic()
        rates = application.state.rates
        # Local process only. Expire inactive keys to avoid accumulating stale sessions.
        for key in list(rates):
            if not rates[key] or rates[key][-1] <= now - 60:
                del rates[key]
        bucket = rates[(secret, action)]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= maximum:
            raise ConnectorError("rate_limited", "요청이 많습니다. 잠시 후 다시 시도하세요.", 429)
        bucket.append(now)

    @application.middleware("http")
    async def protect_local_app(request: Request, call_next):
        expected = urlsplit(application.state.settings.app_base_url).netloc
        if request.headers.get("host", "").lower() != expected.lower():
            return JSONResponse(
                {"error": {"code": "invalid_host", "message": "설정된 로컬 주소로 접속하세요."}},
                status_code=400,
            )
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self'; base-uri 'none'; "
                    "form-action 'none'; frame-ancestors 'none'"
                ),
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        return response

    @application.exception_handler(ConnectorError)
    async def connector_error(request, exc):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default detail can echo attacker-supplied input and OAuth query values.
        return JSONResponse(
            {"error": {"code": "invalid_request", "message": "요청 형식을 확인해 주세요."}},
            status_code=422,
        )

    @application.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @application.get("/health")
    async def health():
        return {"status": "ok", "mode": application.state.settings.app_mode}

    @application.get("/api/session")
    async def get_session(request: Request):
        secret, current = session(request)
        if not current:
            secret = application.state.store.new_session()
        response = JSONResponse(session_payload(secret))
        if not current:
            set_cookie(response, secret)
        return response

    @application.post("/api/demo/connect")
    async def connect_demo(request: Request):
        secret, _ = csrf(request)
        if application.state.settings.app_mode != "demo":
            raise ConnectorError(
                "demo_disabled", "실제 Google 연결 모드에서는 데모를 사용할 수 없습니다.", 404
            )
        account_id = application.state.store.account("demo@prodog.invalid", "demo")
        new = application.state.store.new_session(account_id, old=secret)
        response = JSONResponse(session_payload(new))
        set_cookie(response, new)
        return response

    @application.post("/api/oauth/google/start")
    async def start_google(request: Request):
        secret, _ = csrf(request)
        config = application.state.settings
        if config.app_mode != "google" or not config.google_configured:
            raise ConnectorError(
                "google_not_configured", "Google OAuth 설정 후 서버를 다시 시작하세요.", 503
            )
        limit_requests(secret, "oauth", 10)
        state, verifier = application.state.store.start_flow(secret)
        return {"authorization_url": application.state.google.authorization_url(state, verifier)}

    @application.get("/oauth/google/callback")
    async def google_callback(request: Request):
        config, store = application.state.settings, application.state.store
        try:
            if config.app_mode != "google" or not config.google_configured:
                raise ConnectorError("google_not_configured", "Google 설정이 필요합니다.", 503)
            secret, current = session(request)
            state = request.query_params.get("state", "")
            if not current or not state or len(state) > 128:
                raise ConnectorError("invalid_state", "연결 요청을 확인하지 못했습니다.")
            verifier = store.consume_flow(secret, state)
            if request.query_params.get("error"):
                raise ConnectorError("oauth_denied", "Google 연결이 취소되었습니다.")
            code = request.query_params.get("code", "")
            if not code or len(code) > 8192:
                raise ConnectorError("invalid_callback", "연결 응답이 올바르지 않습니다.")
            oauth_response = await application.state.google.exchange_code(code, verifier)
            access = oauth_response.get("access_token")
            if not isinstance(access, str) or not access or len(access) > 16384:
                raise ConnectorError(
                    "invalid_google_response", "연결 응답이 올바르지 않습니다.", 502
                )
            profile = await application.state.google.profile(access)
            new = store.complete_oauth(
                secret,
                profile["emailAddress"],
                oauth_response,
                application.state.google.token_bundle,
            )
            response = RedirectResponse(config.dashboard_url + "/?connected=1", status_code=303)
            set_cookie(response, new)
            return response
        except ConnectorError as exc:
            # Fixed codes only; authorization code, state, and upstream errors never enter URLs.
            return RedirectResponse(config.dashboard_url + "/?error=" + exc.code, status_code=303)

    async def get_mail(current: dict, message_id: str):
        if not MESSAGE_ID.fullmatch(message_id):
            raise ConnectorError("invalid_message_id", "올바른 메일 ID가 아닙니다.")
        if current["mode"] == "demo":
            raw = next((m for m in demo_messages() if m["id"] == message_id), None)
            if raw is None:
                raise ConnectorError("mail_not_found", "메일을 찾을 수 없습니다.", 404)
        else:
            raw = await application.state.google.message(current["account_id"], message_id)
        parsed = parse_gmail_message(raw)
        application.state.store.save_metadata(current["account_id"], mail_metadata(parsed))
        return parsed

    async def load_page(current: dict, limit: int, page_token: str | None):
        if current["mode"] == "demo":
            try:
                start = int(page_token or "0")
                if start < 0 or start > len(demo_messages()):
                    raise ValueError("offset")
            except ValueError as exc:
                raise ConnectorError("invalid_page_token", "페이지 정보를 확인하세요.") from exc
            all_messages = demo_messages()
            ids = [m["id"] for m in all_messages[start : start + limit]]
            next_token = str(start + limit) if start + limit < len(all_messages) else None
            estimate = len(all_messages)
        else:
            result = await application.state.google.list_messages(
                current["account_id"], limit, page_token
            )
            ids = [m["id"] for m in result.get("messages", [])][:limit]
            next_token, estimate = result.get("nextPageToken"), result.get("resultSizeEstimate", 0)
        semaphore = asyncio.Semaphore(4)

        async def fetch(message_id):
            async with semaphore:
                try:
                    parsed = await get_mail(current, message_id)
                    return {
                        **mail_metadata(parsed),
                        "detection": scan_mail(application.state.detector, parsed),
                    }
                except ConnectorError as exc:
                    # One throttled message should not hide the rest of the inbox.
                    if exc.code not in {"mail_not_found", "message_too_large", "rate_limited"}:
                        raise
                    return {
                        "id": message_id,
                        "thread_id": "",
                        "subject": "가져오지 못한 메일",
                        "sender": "",
                        "received_at": None,
                        "labels": [],
                        "body_hash": None,
                        "trust": "UNTRUSTED",
                        "warnings": [exc.code],
                        "truncated": True,
                        "attachments": [],
                    }

        messages = await asyncio.gather(*(fetch(mid) for mid in ids))
        return {
            "messages": messages,
            "next_page_token": next_token,
            "result_size_estimate": estimate,
        }

    @application.get("/api/mails")
    async def list_mails(
        request: Request,
        limit: int = Query(20, ge=1, le=50),
        page_token: str | None = Query(None, max_length=2048),
    ):
        secret, current = session(request, required=True)
        limit_requests(secret, "list", 30)
        return await load_page(current, limit, page_token)

    @application.post("/api/sync")
    async def sync(request: Request, limit: int = Query(20, ge=1, le=50)):
        """Push the latest mails and their verdicts to Supabase. Explicit, so a GET never writes."""
        secret, current = csrf(request)
        limit_requests(secret, "sync", 10)
        sink = application.state.supabase
        if sink is None:
            raise ConnectorError("supabase_not_configured", "Supabase 설정이 없습니다.", 503)
        page = await load_page(current, limit, None)
        saved = await sink.save_page(current["email"], page["messages"])
        return {"saved": saved}

    @application.get("/api/mails/{message_id}")
    async def mail_detail(request: Request, message_id: str):
        secret, current = session(request, required=True)
        limit_requests(secret, "detail", 120)
        return await get_mail(current, message_id)

    @application.post("/api/disconnect")
    async def disconnect(request: Request):
        secret, _ = csrf(request)
        _, current = session(request, required=True)
        if current["mode"] == "google":
            await application.state.google.revoke(current["account_id"])
        if application.state.supabase:
            # Before the local wipe: if this fails the user can retry with the session intact.
            await application.state.supabase.delete_connection(current["email"])
        application.state.store.forget_account(current["account_id"])
        response = JSONResponse({"disconnected": True})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax")
        return response

    @application.post("/api/logout")
    async def logout(request: Request):
        secret, _ = csrf(request)
        application.state.store.delete_session(secret)
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax")
        return response

    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    return application


app = create_app()
