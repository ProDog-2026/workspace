import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_app import BASE, FakeGoogle

from prodog_gmail.app import COOKIE, create_app
from prodog_gmail.config import Settings

A = "00000000-0000-4000-8000-000000000001"
B = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def owned_client(tmp_path):
    google = FakeGoogle()
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/auth/v1/user":
            token = request.headers.get("authorization")
            users = {"Bearer user-a": A, "Bearer user-b": B}
            if token not in users:
                return httpx.Response(401, json={"message": "untrusted provider error"})
            return httpx.Response(200, json={"id": users[token], "email": "same@example.invalid"})
        return google(request)

    settings = Settings(
        _env_file=None,
        app_mode="google",
        google_client_id="synthetic-client",
        google_client_secret="synthetic-secret",
        supabase_url="https://test.supabase.co",
        supabase_anon_key="sb_publishable_synthetic",
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "owned.db",
    )
    with TestClient(create_app(settings, httpx.MockTransport(handle)), base_url=BASE) as client:
        yield client, requests


def post(client, path):
    payload = client.get("/api/session").json()
    return client.post(path, headers={"Origin": BASE, "X-CSRF-Token": payload["csrf_token"]})


def connect(client, code="mail-a"):
    start = post(client, "/api/oauth/google/start")
    assert start.status_code == 200, start.text
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
    authorization = client.headers.pop("authorization", None)
    try:
        response = client.get(
            "/oauth/google/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
    finally:
        if authorization:
            client.headers["authorization"] = authorization
    assert response.headers["location"] == "/?connected=1"


def test_oauth_requires_verified_supabase_identity(owned_client):
    client, requests = owned_client
    assert post(client, "/api/oauth/google/start").status_code == 401
    client.headers["Authorization"] = "Bearer forged-token"
    response = client.get("/api/session")
    assert response.status_code == 401
    assert "untrusted provider error" not in response.text
    assert all(r.url.path == "/auth/v1/user" for r in requests)


@pytest.mark.parametrize(
    "authorization",
    [
        b"",
        b"Bearer ",
        b"Bearer user a",
        b"Bearer \tuser-a",
        b"Bearer user-a\r\nX: injected",
        b"Bearer \x00",
        b"Bearer \xff",
        b"Basic user-a",
        b"Bearer " + b"a" * 16384,
    ],
)
def test_malformed_bearer_is_safe_401_without_provider_request(owned_client, authorization):
    client, requests = owned_client
    client.raise_server_exceptions = False
    response = client.get("/api/session", headers=[(b"Authorization", authorization)])
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "app_auth_required"
    assert not requests


def test_oauth_owner_survives_callback_refresh_and_cookie_expiry(owned_client):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    connect(client)
    store = client.app.state.store
    account = store.session(client.cookies.get(COOKIE))["account_id"]
    assert store.account_owner(account) == A
    assert client.app.state.pipeline.account_ready(account) is True
    assert client.get("/api/session").json()["user_id"] == A
    assert client.get("/api/mails?limit=1").status_code == 200
    client.cookies.clear()
    restored = client.get("/api/session").json()
    assert restored["authenticated"] is True and restored["user_id"] == A
    assert restored["email"] == "mail-a@example.invalid"
    assert store.session(client.cookies.get(COOKIE))["account_id"] == account


def test_account_switch_and_anonymous_request_cannot_read_other_owner(owned_client):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    connect(client)
    old_cookie = client.cookies.get(COOKIE)
    client.headers["Authorization"] = "Bearer user-b"
    assert client.get("/api/mails").status_code == 401
    assert client.get("/api/session").json()["authenticated"] is False
    client.cookies.clear()
    client.cookies.set(COOKIE, old_cookie)
    client.headers.pop("authorization")
    assert client.get("/api/mails").status_code == 401


def test_existing_unowned_grant_is_not_claimed_by_matching_email(owned_client):
    client, _ = owned_client
    store = client.app.state.store
    account = store.account("same@example.invalid", "google")
    store.save_tokens(account, {"access_token": "legacy", "expires_at": time.time() + 3600})
    client.cookies.set(COOKIE, store.new_session(account))
    client.headers["Authorization"] = "Bearer user-a"
    assert client.get("/api/session").json()["authenticated"] is False
    assert store.account_owner(account) is None
    assert client.app.state.pipeline.account_ready(account) is False


def test_switching_identity_invalidates_pending_oauth_callback(owned_client):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    response = post(client, "/api/oauth/google/start")
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
    client.headers["Authorization"] = "Bearer user-b"
    client.get("/api/session")
    client.headers.pop("authorization")
    response = client.get(
        "/oauth/google/callback",
        params={"state": state, "code": "late"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/?error=invalid_state"
