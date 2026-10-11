"""Cancellation regressions with deterministic pauses at the provider boundary."""

import asyncio
import base64
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet

from prodog_gmail.app import COOKIE, create_app
from prodog_gmail.config import GMAIL_SCOPE, Settings

BASE = "http://127.0.0.1:8765"
BODY_MARKER = "PRIVATE_BODY_FROM_REQUEST_FINISHED_AFTER_DISCONNECT"


@asynccontextmanager
async def running_client(tmp_path, provider):
    settings = Settings(
        _env_file=None,
        app_mode="google",
        app_base_url=BASE,
        google_client_id="test-client",
        google_client_secret="test-secret",
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "race.db",
    )
    app = create_app(settings, httpx.MockTransport(provider))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE
        ) as client:
            yield app, client


def token_response():
    return httpx.Response(
        200,
        json={
            "access_token": "late-test-access",
            "refresh_token": "late-test-refresh",
            "scope": GMAIL_SCOPE,
            "expires_in": 3600,
        },
    )


async def disconnect_during_request(tmp_path, paused_request):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def provider(request):
        calls.append(request)
        path = request.url.path
        if path == "/revoke":
            return httpx.Response(200)
        if path == "/token":
            assert paused_request == "refresh"
            assert parse_qs(request.content.decode())["grant_type"] == ["refresh_token"]
            started.set()
            await release.wait()
            return token_response()
        if path.endswith("/messages/late-mail"):
            assert paused_request == "mail"
            started.set()
            await release.wait()
            source = (
                "Subject: Late message\r\n"
                "From: sender@example.invalid\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n\r\n" + BODY_MARKER
            ).encode()
            return httpx.Response(
                200,
                json={
                    "id": "late-mail",
                    "raw": base64.urlsafe_b64encode(source).decode(),
                },
            )
        raise AssertionError(f"Unexpected provider endpoint: {path}")

    async with running_client(tmp_path, provider) as (app, client):
        store = app.state.store
        account = store.account("race@example.invalid", "google")
        store.save_tokens(
            account,
            {
                "access_token": "earlier-test-access",
                "refresh_token": "earlier-test-refresh",
                "scope": GMAIL_SCOPE,
                "expires_at": time.time() + (-1 if paused_request == "refresh" else 3600),
            },
        )
        store.save_metadata(account, {"id": "earlier-mail", "subject": "Existing metadata"})
        secret = store.new_session(account)
        client.cookies.set(COOKIE, secret, domain="127.0.0.1", path="/")
        pending = asyncio.create_task(client.get("/api/mails/late-mail"))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            response = await client.post(
                "/api/disconnect",
                headers={"Origin": BASE, "X-CSRF-Token": store.csrf(secret)},
            )
            assert response.status_code == 200
            assert response.json() == {"disconnected": True}
        finally:
            release.set()
        late_response = await asyncio.wait_for(pending, timeout=5)

        # A late write must fail as a cancelled connection, not a SQLite FK 500.
        assert late_response.status_code == 401
        assert late_response.json()["error"]["code"] == "authentication_required"
        assert BODY_MARKER not in late_response.text
        assert store.session(secret) is None
        assert store.tokens(account) is None
        assert (await client.get("/api/session")).json()["authenticated"] is False
        assert (await client.get("/api/mails")).status_code == 401
        with store.connection() as conn:
            for table in ("accounts", "tokens", "mails", "oauth_flows"):
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE account_id IS NOT NULL"
                ).fetchone()[0]
                == 0
            )
        assert BODY_MARKER.encode() not in store.path.read_bytes()
        assert sum(request.url.path == "/revoke" for request in calls) == 1
        if paused_request == "refresh":
            assert not any("/messages/" in request.url.path for request in calls)


def test_disconnect_prevents_late_refresh_from_restoring_tokens(tmp_path):
    asyncio.run(disconnect_during_request(tmp_path, "refresh"))


def test_disconnect_prevents_late_mail_from_restoring_metadata(tmp_path):
    asyncio.run(disconnect_during_request(tmp_path, "mail"))


def test_logout_cancels_callback_already_exchanging_code(tmp_path):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def provider(request):
            if request.url.path == "/token":
                assert parse_qs(request.content.decode())["grant_type"] == ["authorization_code"]
                started.set()
                await release.wait()
                return token_response()
            if request.url.path.endswith("/profile"):
                return httpx.Response(200, json={"emailAddress": "race@example.invalid"})
            raise AssertionError(f"Unexpected provider endpoint: {request.url.path}")

        async with running_client(tmp_path, provider) as (app, client):
            session = (await client.get("/api/session")).json()
            old_cookie = client.cookies.get(COOKIE)
            headers = {"Origin": BASE, "X-CSRF-Token": session["csrf_token"]}
            start = await client.post("/api/oauth/google/start", headers=headers)
            assert start.status_code == 200
            state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
            pending = asyncio.create_task(
                client.get("/oauth/google/callback", params={"state": state, "code": "test-code"})
            )
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                logout = await client.post("/api/logout", headers=headers)
                assert logout.status_code == 200
                assert logout.json() == {"logged_out": True}
            finally:
                release.set()
            callback = await asyncio.wait_for(pending, timeout=5)

            assert callback.status_code == 303
            assert callback.headers["location"] == "/?error=invalid_state"
            assert "set-cookie" not in callback.headers
            assert app.state.store.session(old_cookie) is None
            assert (await client.get("/api/session")).json()["authenticated"] is False
            assert (await client.get("/api/mails")).status_code == 401
            with app.state.store.connection() as conn:
                for table in ("accounts", "tokens", "mails", "oauth_flows"):
                    assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
                assert (
                    conn.execute(
                        "SELECT COUNT(*) FROM sessions WHERE account_id IS NOT NULL"
                    ).fetchone()[0]
                    == 0
                )

    asyncio.run(scenario())
