import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import urlencode

import httpx

from .config import GMAIL_SCOPE, Settings
from .errors import ConnectorError
from .identity import JWKS_URL, nonce_for, verify_identity
from .store import Store

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_URL = "https://gmail.googleapis.com/gmail/v1/users/me"


class GoogleClient:
    def __init__(self, settings: Settings, store: Store, client: httpx.AsyncClient):
        self.settings, self.store, self.client = settings, store, client
        self._locks: dict[str, asyncio.Lock] = {}

    def authorization_url(self, state: str, verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        return (
            AUTHORIZE_URL
            + "?"
            + urlencode(
                {
                    "client_id": self.settings.google_client_id,
                    "redirect_uri": self.settings.redirect_uri,
                    "response_type": "code",
                    "scope": "openid email " + GMAIL_SCOPE,
                    "nonce": nonce_for(verifier),
                    "access_type": "offline",
                    "prompt": "consent select_account",
                    "state": state,
                    "code_challenge": challenge.rstrip(b"=").decode(),
                    "code_challenge_method": "S256",
                }
            )
        )

    async def _json(self, method: str, url: str, *, max_bytes=4_000_000, **kwargs) -> dict:
        for attempt in range(3):
            try:
                async with self.client.stream(method, url, **kwargs) as response:
                    status = response.status_code
                    # Only idempotent Gmail GETs retry, with bounded backoff.
                    if method == "GET" and (status == 429 or status >= 500) and attempt < 2:
                        await asyncio.sleep(0.2 * (2**attempt))
                        continue
                    if status >= 500 or status == 429:
                        code = "rate_limited" if status == 429 else "provider_unavailable"
                        raise ConnectorError(
                            code, "Google 요청이 지연됩니다. 잠시 후 재시도하세요.", 503
                        )
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ConnectorError(
                                "message_too_large", "응답 크기가 처리 한도를 넘습니다.", 413
                            )
                        chunks.append(chunk)
                    try:
                        data = json.loads(b"".join(chunks))
                    except (ValueError, UnicodeError):
                        data = {}
                    if not isinstance(data, dict):
                        data = {}
                    if status == 401 or data.get("error") == "invalid_grant":
                        raise ConnectorError(
                            "reconnect_required",
                            "Google 연결이 만료되었습니다. 다시 연결하세요.",
                            401,
                        )
                    if status == 403:
                        details = data.get("error")
                        reasons = (
                            {
                                e.get("reason")
                                for e in details.get("errors", [])
                                if isinstance(e, dict)
                            }
                            if isinstance(details, dict)
                            else set()
                        )
                        if reasons & {
                            "rateLimitExceeded",
                            "userRateLimitExceeded",
                            "quotaExceeded",
                            "dailyLimitExceeded",
                        }:
                            if method == "GET" and attempt < 2:
                                await asyncio.sleep(0.2 * (2**attempt))
                                continue
                            raise ConnectorError(
                                "rate_limited",
                                "Gmail 사용량 한도에 도달했습니다. 잠시 후 재시도하세요.",
                                503,
                            )
                        raise ConnectorError(
                            "permission_denied",
                            "Gmail API 사용 설정과 메일 읽기 권한을 확인하세요.",
                            403,
                        )
                    if status == 404:
                        raise ConnectorError("mail_not_found", "메일이 없거나 삭제되었습니다.", 404)
                    if status >= 400:
                        raise ConnectorError(
                            "google_request_failed", "Google 요청을 완료하지 못했습니다.", 502
                        )
                    if not data:
                        raise ConnectorError(
                            "invalid_google_response", "Google 응답을 읽지 못했습니다.", 502
                        )
                    return data
            except (httpx.HTTPError, OSError) as exc:
                raise ConnectorError(
                    "provider_unavailable", "Google에 연결할 수 없습니다. 다시 시도하세요.", 503
                ) from exc
        raise AssertionError("unreachable")

    @staticmethod
    def token_bundle(response: dict, previous: dict | None = None) -> dict:
        previous = previous or {}
        scope = response.get("scope", previous.get("scope", ""))
        if not isinstance(scope, str) or GMAIL_SCOPE not in scope.split():
            raise ConnectorError(
                "missing_scope", "Gmail 읽기 권한에 동의해야 연결할 수 있습니다.", 403
            )
        access = response.get("access_token")
        refresh = response.get("refresh_token") or (
            previous.get("refresh_token") if not previous.get("needs_reconnect") else None
        )
        if not isinstance(access, str) or not access or len(access) > 16384:
            raise ConnectorError(
                "invalid_google_response", "Google 연결 응답이 올바르지 않습니다.", 502
            )
        if not isinstance(refresh, str) or not refresh or len(refresh) > 16384:
            raise ConnectorError(
                "refresh_token_missing", "지속 연결 권한을 받지 못했습니다. 다시 연결하세요.", 400
            )
        try:
            expires_in = min(max(int(response.get("expires_in", 3600)), 1), 86400)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ConnectorError(
                "invalid_google_response", "Google 연결 응답이 올바르지 않습니다.", 502
            ) from exc
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": time.time() + expires_in,
            "scope": scope,
        }

    async def exchange_code(self, code: str, verifier: str) -> dict:
        return await self._json(
            "POST",
            TOKEN_URL,
            max_bytes=65536,
            data={
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret.get_secret_value(),
                "redirect_uri": self.settings.redirect_uri,
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
            },
        )

    async def profile(self, access_token: str) -> dict:
        result = await self._json(
            "GET",
            GMAIL_URL + "/profile",
            max_bytes=65536,
            headers={"Authorization": "Bearer " + access_token},
        )
        email = result.get("emailAddress")
        if not isinstance(email, str) or "@" not in email or len(email) > 320:
            raise ConnectorError(
                "invalid_google_response", "메일 계정 정보를 확인하지 못했습니다.", 502
            )
        return result

    async def identity(self, response: dict, verifier: str, email: str) -> str:
        jwks = await self._json("GET", JWKS_URL, max_bytes=65536)
        return verify_identity(
            response.get("id_token"), jwks,
            client_id=self.settings.google_client_id, nonce=nonce_for(verifier),
            access_token=response["access_token"], email=email,
        )

    async def access_token(self, account_id: str, rejected: str | None = None) -> str:
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            bundle = self.store.tokens(account_id)
            if not bundle:
                raise ConnectorError("reconnect_required", "Google 계정을 연결해 주세요.", 401)
            if bundle.get("needs_reconnect"):
                raise ConnectorError("reconnect_required", "Google 계정을 다시 연결해 주세요.", 401)
            # If another coroutine refreshed the rejected token, reuse its new result.
            if bundle["expires_at"] > time.time() + 60 and bundle["access_token"] != rejected:
                return bundle["access_token"]
            try:
                response = await self._json(
                    "POST",
                    TOKEN_URL,
                    max_bytes=65536,
                    data={
                        "client_id": self.settings.google_client_id,
                        "client_secret": self.settings.google_client_secret.get_secret_value(),
                        "grant_type": "refresh_token",
                        "refresh_token": bundle["refresh_token"],
                    },
                )
            except ConnectorError as exc:
                if exc.code == "reconnect_required":
                    self.store.invalidate_tokens(account_id)
                raise
            updated = self.token_bundle(response, bundle)
            self.store.save_tokens(account_id, updated)
            return updated["access_token"]

    async def gmail(self, account_id: str, path: str, params: dict | None = None) -> dict:
        token = await self.access_token(account_id)
        for attempt in range(2):
            try:
                return await self._json(
                    "GET",
                    GMAIL_URL + path,
                    params=params,
                    headers={"Authorization": "Bearer " + token},
                )
            except ConnectorError as exc:
                if exc.code == "reconnect_required" and attempt:
                    self.store.invalidate_tokens(account_id)
                if exc.code != "reconnect_required" or attempt:
                    raise
                token = await self.access_token(account_id, rejected=token)
        raise AssertionError("unreachable")

    async def list_messages(self, account_id: str, limit: int, page_token: str | None):
        params = {"labelIds": "INBOX", "maxResults": limit}
        if page_token:
            params["pageToken"] = page_token
        return await self.gmail(account_id, "/messages", params)

    async def message(self, account_id: str, message_id: str):
        return await self.gmail(account_id, "/messages/" + message_id, {"format": "raw"})

    async def revoke(self, account_id: str):
        bundle = self.store.tokens(account_id)
        if not bundle:
            return
        try:
            # POST body rather than URL: do not put a token into query/access logs.
            async with self.client.stream(
                "POST", REVOKE_URL, data={"token": bundle["refresh_token"]}
            ) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 65536:
                        raise ConnectorError(
                            "revoke_failed", "Google 연결 해제 응답을 확인하지 못했습니다.", 503
                        )
                status = response.status_code
        except httpx.HTTPError as exc:
            raise ConnectorError(
                "revoke_failed", "Google 연결 해제에 실패했습니다. 다시 시도하세요.", 503
            ) from exc
        if status != 200:
            # Invalid token means there is no live grant left to revoke.
            try:
                already_invalid = (
                    status == 400 and json.loads(content).get("error") == "invalid_token"
                )
            except (ValueError, AttributeError):
                already_invalid = False
            if not already_invalid:
                raise ConnectorError(
                    "revoke_failed", "Google 연결 해제에 실패했습니다. 다시 시도하세요.", 503
                )
        self._locks.pop(account_id, None)
