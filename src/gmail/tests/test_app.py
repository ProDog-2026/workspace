import base64
import hashlib
import json
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from prodog_gmail.app import COOKIE, create_app
from prodog_gmail.config import GMAIL_SCOPE, Settings
from prodog_gmail.demo import demo_messages
from prodog_gmail.store import digest

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        app_mode="demo",
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "test.db",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url=BASE) as client:
        yield client


def post(client, path):
    csrf = client.get("/api/session").json()["csrf_token"]
    return client.post(path, headers={"Origin": BASE, "X-CSRF-Token": csrf})


def connect_demo(client):
    response = post(client, "/api/demo/connect")
    assert response.status_code == 200
    return response.json()


def oauth_start(client):
    response = post(client, "/api/oauth/google/start")
    assert response.status_code == 200
    return parse_qs(urlsplit(response.json()["authorization_url"]).query)


def callback(client, state, code="good-code"):
    return client.get(
        "/oauth/google/callback", params={"state": state, "code": code}, follow_redirects=False
    )


class FakeGoogle:
    def __init__(self):
        self.calls = []
        self.refresh_calls = 0
        self.revoke_fail = False
        self.scope = GMAIL_SCOPE
        self.omit_refresh = False

    def __call__(self, request):
        self.calls.append(request)
        body = parse_qs(request.content.decode())
        path = request.url.path
        if path == "/token":
            if body.get("grant_type") == ["refresh_token"]:
                self.refresh_calls += 1
                return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})
            data = {
                "access_token": "access-" + body["code"][0],
                "expires_in": 3600,
                "scope": self.scope,
            }
            if not self.omit_refresh:
                data["refresh_token"] = "refresh-" + body["code"][0]
            return httpx.Response(200, json=data)
        if path.endswith("/profile"):
            token = request.headers["authorization"].removeprefix("Bearer access-")
            return httpx.Response(200, json={"emailAddress": token + "@example.invalid"})
        if path.endswith("/messages"):
            messages = demo_messages()
            return httpx.Response(
                200, json={"messages": [{"id": m["id"]} for m in messages], "resultSizeEstimate": 3}
            )
        if "/messages/" in path:
            mail = dict(demo_messages()[0])
            mail["id"] = path.rsplit("/", 1)[1]
            token = request.headers["authorization"]
            mail["threadId"] = token  # Test evidence: which account's token handled the read.
            return httpx.Response(200, json=mail)
        if path == "/revoke":
            return httpx.Response(503 if self.revoke_fail else 200, json={})
        raise AssertionError(str(request.url))


@pytest.fixture
def google_client(settings):
    settings.app_mode = "google"
    settings.google_client_id = "fake-client"
    settings.google_client_secret = "fake-secret"
    fake = FakeGoogle()
    app = create_app(settings, httpx.MockTransport(fake))
    with TestClient(app, base_url=BASE) as client:
        # Settings assignment doesn't validate/coerce SecretStr by default.
        from pydantic import SecretStr

        settings.google_client_secret = SecretStr("fake-secret")
        yield client, fake


def test_anonymous_and_session_cookie(client):
    assert client.get("/api/mails").status_code == 401
    response = client.get("/api/session")
    assert response.json()["authenticated"] is False
    assert len(response.json()["csrf_token"]) == 64
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    "headers", [{}, {"Origin": "https://evil.invalid"}, {"Origin": BASE, "X-CSRF-Token": "bad"}]
)
def test_mutations_require_origin_and_csrf(client, headers):
    client.get("/api/session")
    response = client.post("/api/demo/connect", headers=headers)
    assert response.status_code == 403


def test_dns_rebinding_host_rejected(client):
    assert client.get("/api/session", headers={"Host": "evil.invalid"}).status_code == 400


def test_non_ascii_csrf_is_rejected_without_server_error(client):
    client.get("/api/session")
    response = client.post(
        "/api/demo/connect",
        headers=[(b"Origin", BASE.encode()), (b"X-CSRF-Token", b"\xff" * 64)],
    )
    assert response.status_code == 403


def test_demo_lifecycle_rotation_pagination_body_not_stored(client, settings):
    client.get("/api/session")
    before = client.cookies.get(COOKIE)
    result = connect_demo(client)
    assert result["authenticated"] and result["email"] == "demo@prodog.invalid"
    assert client.cookies.get(COOKIE) != before
    assert client.app.state.store.session(before) is None
    response = client.get("/api/mails?limit=2").json()
    assert len(response["messages"]) == 2
    assert response["next_page_token"] == "2"
    assert "text_plain" not in response["messages"][0]
    assert len(client.get("/api/mails?limit=2&page_token=2").json()["messages"]) == 1
    mail_id = response["messages"][0]["id"]
    mail = client.get("/api/mails/" + mail_id).json()
    assert mail["trust"] == "UNTRUSTED"
    assert mail["analysis_text"]
    client.get("/api/mails?limit=2")  # Idempotent metadata upsert.
    with sqlite3.connect(settings.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM mails").fetchone()[0] == 3
        metadata = json.loads(conn.execute("SELECT metadata_json FROM mails LIMIT 1").fetchone()[0])
        assert not ({"text_plain", "text_html", "analysis_text", "raw"} & metadata.keys())
    assert post(client, "/api/disconnect").json() == {"disconnected": True}
    assert client.get("/api/mails").status_code == 401
    with sqlite3.connect(settings.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM mails").fetchone()[0] == 0


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "page_token=-1", "page_token=oops"])
def test_invalid_pagination(client, query):
    connect_demo(client)
    assert client.get("/api/mails?" + query).status_code in {400, 422}


def test_local_logout_invalidates_session(client):
    connect_demo(client)
    old = client.cookies.get(COOKIE)
    assert post(client, "/api/logout").status_code == 200
    assert client.app.state.store.session(old) is None
    assert client.get("/api/mails").status_code == 401


def test_oauth_disabled_in_demo(client):
    assert post(client, "/api/oauth/google/start").status_code == 503


def test_google_flow_pkce_encrypts_tokens_and_rotates_session(google_client, settings):
    client, fake = google_client
    params = oauth_start(client)
    old = client.cookies.get(COOKIE)
    assert params["scope"] == [GMAIL_SCOPE]
    assert params["access_type"] == ["offline"]
    assert params["redirect_uri"] == [BASE + "/oauth/google/callback"]
    assert params["code_challenge_method"] == ["S256"]
    response = callback(client, params["state"][0])
    assert response.status_code == 303 and response.headers["location"] == "/?connected=1"
    assert client.cookies.get(COOKIE) != old
    token_request = fake.calls[0]
    form = parse_qs(token_request.content.decode())
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == params["code_challenge"][0]
    assert client.get("/api/session").json()["email"] == "good-code@example.invalid"
    db_bytes = settings.database_path.read_bytes()
    assert b"refresh-good-code" not in db_bytes
    assert b"access-good-code" not in db_bytes
    assert b"fake-secret" not in db_bytes


def test_callback_wrong_browser_state_replay_and_expiry(google_client):
    client, fake = google_client
    params = oauth_start(client)
    secret = client.cookies.get(COOKIE)
    client.cookies.clear()
    wrong = callback(client, params["state"][0])
    assert wrong.headers["location"] == "/?error=invalid_state"
    assert not fake.calls
    client.cookies.set(COOKIE, secret)
    assert callback(client, params["state"][0]).headers["location"] == "/?connected=1"
    assert callback(client, params["state"][0]).headers["location"] == "/?error=invalid_state"
    params = oauth_start(client)
    with client.app.state.store.connection() as conn:
        conn.execute("UPDATE oauth_flows SET expires_at=?", (time.time() - 1,))
    assert callback(client, params["state"][0]).headers["location"] == "/?error=invalid_state"


def test_denied_oauth_consumes_state_without_provider_call(google_client):
    client, fake = google_client
    state = oauth_start(client)["state"][0]
    response = client.get(
        "/oauth/google/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/?error=oauth_denied"
    assert not fake.calls
    assert callback(client, state).headers["location"] == "/?error=invalid_state"


def test_new_oauth_request_invalidates_previous_flow(google_client):
    client, fake = google_client
    old = oauth_start(client)["state"][0]
    new = oauth_start(client)["state"][0]
    assert callback(client, old).headers["location"] == "/?error=invalid_state"
    assert callback(client, new).headers["location"] == "/?connected=1"


def test_scope_denial_never_authenticates(google_client):
    client, fake = google_client
    fake.scope = "openid email"
    state = oauth_start(client)["state"][0]
    assert callback(client, state).headers["location"] == "/?error=missing_scope"
    assert client.get("/api/session").json()["authenticated"] is False


def test_refresh_preserved_on_reconnect_and_mail_use(google_client):
    client, fake = google_client
    assert callback(client, oauth_start(client)["state"][0]).status_code == 303
    fake.omit_refresh = True
    assert callback(client, oauth_start(client)["state"][0]).headers["location"] == "/?connected=1"
    account_id = client.app.state.store.session(client.cookies.get(COOKIE))["account_id"]
    bundle = client.app.state.store.tokens(account_id)
    assert bundle["refresh_token"] == "refresh-good-code"
    bundle["expires_at"] = 0
    client.app.state.store.save_tokens(account_id, bundle)
    response = client.get("/api/mails")
    assert response.status_code == 200
    assert fake.refresh_calls == 1
    assert client.app.state.store.tokens(account_id)["refresh_token"] == "refresh-good-code"


def test_reconnect_cannot_reuse_known_invalid_refresh_token(google_client):
    client, fake = google_client
    callback(client, oauth_start(client)["state"][0])
    account = client.app.state.store.session(client.cookies.get(COOKIE))["account_id"]
    client.app.state.store.invalidate_tokens(account)
    info = client.get("/api/session").json()
    assert info["authenticated"] and info["needs_reconnect"]
    fake.omit_refresh = True
    response = callback(client, oauth_start(client)["state"][0])
    assert response.headers["location"] == "/?error=refresh_token_missing"
    assert client.app.state.store.tokens(account)["needs_reconnect"] is True
    fake.omit_refresh = False
    assert callback(client, oauth_start(client)["state"][0]).headers["location"] == "/?connected=1"
    assert client.get("/api/session").json()["needs_reconnect"] is False


def test_account_isolation_same_message_id_and_invalidated_session(google_client):
    client, fake = google_client
    callback(client, oauth_start(client)["state"][0], "alice")
    alice_secret = client.cookies.get(COOKIE)
    mail_id = demo_messages()[0]["id"]
    alice = client.get("/api/mails/" + mail_id).json()
    assert alice["thread_id"] == "Bearer access-alice"
    # New browser session, then connect Bob.
    client.cookies.clear()
    callback(client, oauth_start(client)["state"][0], "bob")
    bob = client.get("/api/mails/" + mail_id).json()
    assert bob["thread_id"] == "Bearer access-bob"
    with client.app.state.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM mails WHERE gmail_message_id=?", (mail_id,)
            ).fetchone()[0]
            == 2
        )
    post(client, "/api/disconnect")
    client.cookies.set(COOKIE, alice_secret)
    assert client.get("/api/mails/" + mail_id).json()["thread_id"] == "Bearer access-alice"


def test_revoke_failure_keeps_tokens_for_retry(google_client):
    client, fake = google_client
    callback(client, oauth_start(client)["state"][0])
    account = client.app.state.store.session(client.cookies.get(COOKIE))["account_id"]
    fake.revoke_fail = True
    assert post(client, "/api/disconnect").status_code == 503
    assert client.app.state.store.tokens(account)
    fake.revoke_fail = False
    assert post(client, "/api/disconnect").status_code == 200
    assert client.app.state.store.tokens(account) is None
    revoke = next(req for req in reversed(fake.calls) if req.url.path == "/revoke")
    assert not revoke.url.query
    assert parse_qs(revoke.content.decode())["token"] == ["refresh-good-code"]


def test_expired_session_rejects_reads(google_client):
    client, fake = google_client
    callback(client, oauth_start(client)["state"][0])
    with client.app.state.store.connection() as conn:
        conn.execute(
            "UPDATE sessions SET expires_at=0 WHERE id_hash=?",
            (digest(client.cookies.get(COOKIE)),),
        )
    assert client.get("/api/mails").status_code == 401


def test_token_ciphertext_bound_to_account(client):
    store = client.app.state.store
    alice, bob = (
        store.account("alice@example.invalid", "google"),
        store.account("bob@example.invalid", "google"),
    )
    store.save_tokens(alice, {"refresh_token": "secret-refresh"})
    with store.connection() as conn:
        encrypted = conn.execute(
            "SELECT encrypted FROM tokens WHERE account_id=?", (alice,)
        ).fetchone()[0]
        conn.execute("INSERT INTO tokens VALUES (?, ?)", (bob, encrypted))
    from prodog_gmail.errors import ConnectorError

    with pytest.raises(ConnectorError, match="token_store_error"):
        store.tokens(bob)


def test_invalid_config_does_not_print_supplied_secrets(tmp_path):
    with pytest.raises(ValueError) as caught:
        Settings(
            _env_file=None,
            token_encryption_key="bad-private-key",
            google_client_secret="do-not-print-this-secret",
            database_path=tmp_path / "db",
        )
    assert "do-not-print-this-secret" not in str(caught.value)
    assert "bad-private-key" not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://evil.invalid:8765",
        "http://user@127.0.0.1:8765",
        "http://127.0.0.1:8765/path",
    ],
)
def test_config_refuses_public_or_ambiguous_origin(tmp_path, url):
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            app_base_url=url,
            token_encryption_key=Fernet.generate_key().decode(),
            database_path=tmp_path / "db",
        )
