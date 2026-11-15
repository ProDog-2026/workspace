"""Provider contract and failure tests; every HTTP call uses MockTransport."""

import asyncio
import base64
import hashlib
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet

from prodog_gmail.config import GMAIL_SCOPE, Settings
from prodog_gmail.errors import ConnectorError
from prodog_gmail.google import AUTHORIZE_URL, GMAIL_URL, REVOKE_URL, TOKEN_URL, GoogleClient
from prodog_gmail.store import Store


@pytest.fixture
def google_state(tmp_path):
    settings = Settings(
        _env_file=None,
        app_mode="google",
        app_base_url="http://127.0.0.1:8765",
        google_client_id="synthetic-client.apps.googleusercontent.com",
        google_client_secret="synthetic-client-secret",
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "test.db",
    )
    store = Store(settings)
    account = store.account("test@prodog.invalid", "google")
    store.save_tokens(account, {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "expires_at": time.time() + 3600,
        "scope": GMAIL_SCOPE,
    })
    return settings, store, account


@asynccontextmanager
async def mock_google(state, handler):
    settings, store, _ = state
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        yield GoogleClient(settings, store, http)


def expire_token(state):
    _, store, account = state
    bundle = store.tokens(account)
    bundle["expires_at"] = time.time() - 1
    store.save_tokens(account, bundle)


def fail_if_called(request):
    pytest.fail(f"Unexpected HTTP request: {request.method} {request.url.path}")


def test_authorization_url_requests_only_readonly_offline_scope_and_pkce(google_state):
    async def scenario():
        async with mock_google(google_state, fail_if_called) as google:
            verifier = "synthetic-verifier-" * 4
            result = google.authorization_url("synthetic-state", verifier)
            parts = urlsplit(result)
            query = parse_qs(parts.query)
            assert result.startswith(AUTHORIZE_URL + "?")
            assert set(query["scope"][0].split()) == {"openid", "email", GMAIL_SCOPE}
            assert query["nonce"]
            assert query["access_type"] == ["offline"]
            assert query["response_type"] == ["code"]
            assert query["state"] == ["synthetic-state"]
            assert query["redirect_uri"] == [google_state[0].redirect_uri]
            expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            assert query["code_challenge"] == [expected.decode().rstrip("=")]
            assert query["code_challenge_method"] == ["S256"]
            assert "client_secret" not in query
            assert verifier not in result

    asyncio.run(scenario())


def test_code_exchange_sends_verifier_and_secret_in_post_body(google_state):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == TOKEN_URL
        body = parse_qs(request.content.decode())
        assert body["code"] == ["synthetic-code"]
        assert body["code_verifier"] == ["synthetic-verifier"]
        assert body["grant_type"] == ["authorization_code"]
        assert body["redirect_uri"] == [google_state[0].redirect_uri]
        assert body["client_secret"] == ["synthetic-client-secret"]
        return httpx.Response(200, json={"access_token": "new-access"})

    async def scenario():
        async with mock_google(google_state, handler) as google:
            assert await google.exchange_code("synthetic-code", "synthetic-verifier") == {
                "access_token": "new-access"
            }

    asyncio.run(scenario())
    assert len(calls) == 1


def test_refresh_preserves_existing_refresh_token_and_scope(google_state):
    expire_token(google_state)
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == TOKEN_URL
        body = parse_qs(request.content.decode())
        assert body["refresh_token"] == ["old-refresh"]
        assert body["grant_type"] == ["refresh_token"]
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    async def scenario():
        async with mock_google(google_state, handler) as google:
            assert await google.access_token(google_state[2]) == "new-access"
            assert await google.access_token(google_state[2]) == "new-access"

    asyncio.run(scenario())
    saved = google_state[1].tokens(google_state[2])
    assert saved["refresh_token"] == "old-refresh"
    assert saved["scope"] == GMAIL_SCOPE
    assert saved["expires_at"] > time.time() + 3500
    assert len(calls) == 1


@pytest.mark.parametrize("rejected", [None, "old-access"])
def test_concurrent_refresh_performs_one_token_request(google_state, rejected):
    if rejected is None:
        expire_token(google_state)
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    async def scenario():
        async with mock_google(google_state, handler) as google:
            results = await asyncio.gather(*[
                google.access_token(google_state[2], rejected=rejected) for _ in range(12)
            ])
            assert results == ["new-access"] * 12

    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("second_status", [200, 401])
def test_unauthorized_gmail_refreshes_once_and_retries_once(google_state, second_status):
    calls = []
    get_count = 0

    def handler(request):
        nonlocal get_count
        calls.append(request)
        if request.method == "POST":
            assert str(request.url) == TOKEN_URL
            return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})
        assert str(request.url).startswith(GMAIL_URL + "/messages/message-id")
        assert request.url.params["format"] == "raw"
        get_count += 1
        assert request.headers["Authorization"] == (
            "Bearer old-access" if get_count == 1 else "Bearer new-access"
        )
        if get_count == 1 or second_status == 401:
            return httpx.Response(401, json={"error": {"message": "do not expose this"}})
        return httpx.Response(200, json={"id": "message-id", "raw": "Ym9keQ"})

    async def scenario():
        async with mock_google(google_state, handler) as google:
            if second_status == 200:
                result = await google.message(google_state[2], "message-id")
                assert result["id"] == "message-id"
            else:
                with pytest.raises(ConnectorError) as caught:
                    await google.message(google_state[2], "message-id")
                assert caught.value.code == "reconnect_required"
                assert caught.value.status == 401

    asyncio.run(scenario())
    assert [request.method for request in calls] == ["GET", "POST", "GET"]


def test_missing_tokens_require_reconnect_without_http(google_state):
    async def scenario():
        async with mock_google(google_state, fail_if_called) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.access_token("nonexistent-account")
            assert caught.value.code == "reconnect_required"

    asyncio.run(scenario())


def test_invalid_grant_is_safe_and_requires_reconnect_without_repeated_refresh(google_state):
    expire_token(google_state)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={
            "error": "invalid_grant",
            "error_description": "sensitive-provider-diagnostic old-refresh old-access",
        })

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.access_token(google_state[2])
            assert caught.value.code == "reconnect_required"
            assert caught.value.status == 401
            for secret in ("sensitive-provider-diagnostic", "old-refresh", "old-access"):
                assert secret not in str(caught.value)
                assert secret not in caught.value.message
            assert google_state[1].tokens(google_state[2])["needs_reconnect"] is True
            with pytest.raises(ConnectorError) as second:
                await google.access_token(google_state[2])
            assert second.value.code == "reconnect_required"
            assert len(calls) == 1

        # Recreating the client must honor the persisted flag too.
        async with mock_google(google_state, fail_if_called) as google:
            with pytest.raises(ConnectorError) as later:
                await google.list_messages(google_state[2], 10, None)
            assert later.value.code == "reconnect_required"

    asyncio.run(scenario())
    assert len(calls) == 1
    assert google_state[1].tokens(google_state[2])["refresh_token"] == "old-refresh"


@pytest.mark.parametrize("status,code", [
    (429, "rate_limited"), (500, "provider_unavailable"), (503, "provider_unavailable")
])
def test_retryable_get_failures_stop_after_three_attempts(google_state, monkeypatch, status, code):
    calls, delays = [], []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("prodog_gmail.google.asyncio.sleep", fake_sleep)

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "synthetic failure"})

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.list_messages(google_state[2], 10, None)
            assert caught.value.code == code
            assert caught.value.status == 503

    asyncio.run(scenario())
    assert len(calls) == 3
    assert len(delays) == 2
    assert all(0 < delay <= 1 for delay in delays)


def test_retryable_get_can_recover_and_preserves_pagination(google_state, monkeypatch):
    calls = []

    async def no_sleep(delay):
        pass

    monkeypatch.setattr("prodog_gmail.google.asyncio.sleep", no_sleep)

    def handler(request):
        calls.append(request)
        assert request.url.params["maxResults"] == "7"
        assert request.url.params["labelIds"] == "INBOX"
        assert request.url.params["pageToken"] == "opaque+/page=token"
        if len(calls) < 3:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"messages": [{"id": "synthetic-id"}]})

    async def scenario():
        async with mock_google(google_state, handler) as google:
            result = await google.list_messages(google_state[2], 7, "opaque+/page=token")
            assert result["messages"] == [{"id": "synthetic-id"}]

    asyncio.run(scenario())
    assert len(calls) == 3


def test_token_post_is_not_automatically_retried(google_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"error": "temporary"})

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.exchange_code("single-use-code", "verifier")
            assert caught.value.code == "provider_unavailable"

    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"])
def test_gmail_quota_errors_are_distinct_from_permission_denial(google_state, monkeypatch, reason):
    calls = []

    async def no_sleep(delay):
        pass

    monkeypatch.setattr("prodog_gmail.google.asyncio.sleep", no_sleep)

    def handler(request):
        calls.append(request)
        return httpx.Response(403, json={"error": {
            "code": 403,
            "message": "Quota exceeded",
            "errors": [{"domain": "usageLimits", "reason": reason}],
        }})

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.list_messages(google_state[2], 10, None)
            assert caught.value.code == "rate_limited"
            assert caught.value.status == 503

    asyncio.run(scenario())
    assert 1 <= len(calls) <= 3


def test_gmail_scope_denial_is_not_retried_or_mistaken_for_expired_token(google_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, json={"error": {
            "code": 403, "errors": [{"reason": "insufficientPermissions"}]
        }})

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.list_messages(google_state[2], 10, None)
            assert caught.value.code == "permission_denied"
            assert caught.value.status == 403

    asyncio.run(scenario())
    assert [request.method for request in calls] == ["GET"]


@pytest.mark.parametrize("scope", [None, "", "https://www.googleapis.com/auth/gmail.send"])
def test_token_bundle_rejects_missing_readonly_consent(scope):
    with pytest.raises(ConnectorError) as caught:
        GoogleClient.token_bundle({
            "scope": scope, "access_token": "access", "refresh_token": "refresh",
        })
    assert caught.value.code == "missing_scope"
    assert caught.value.status == 403


def test_refresh_response_cannot_silently_drop_granted_read_scope():
    with pytest.raises(ConnectorError) as caught:
        GoogleClient.token_bundle(
            {"scope": "openid", "access_token": "new"},
            {"scope": GMAIL_SCOPE, "refresh_token": "old"},
        )
    assert caught.value.code == "missing_scope"


@pytest.mark.parametrize("expires_in", [None, "not-a-number", float("inf")])
def test_invalid_token_expiry_is_a_safe_provider_error(expires_in):
    with pytest.raises(ConnectorError) as caught:
        GoogleClient.token_bundle({
            "scope": GMAIL_SCOPE, "access_token": "access", "refresh_token": "refresh",
            "expires_in": expires_in,
        })
    assert caught.value.code == "invalid_google_response"


@pytest.mark.parametrize("status,payload", [(200, {}), (400, {"error": "invalid_token"})])
def test_revoke_accepts_success_or_already_invalid_token(google_state, status, payload):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == REVOKE_URL
        assert not request.url.query
        assert parse_qs(request.content.decode()) == {"token": ["old-refresh"]}
        return httpx.Response(status, json=payload)

    async def scenario():
        async with mock_google(google_state, handler) as google:
            await google.revoke(google_state[2])

    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("status,payload", [
    (500, {"error": "temporary"}), (400, {"error": "unauthorized_client"}), (400, ["invalid"])
])
def test_revoke_failure_preserves_local_tokens(google_state, status, payload):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=payload)

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.revoke(google_state[2])
            assert caught.value.code == "revoke_failed"
            assert caught.value.status == 503

    asyncio.run(scenario())
    assert len(calls) == 1
    assert google_state[1].tokens(google_state[2])["refresh_token"] == "old-refresh"


def test_revoke_disconnected_account_does_not_call_provider(google_state):
    async def scenario():
        async with mock_google(google_state, fail_if_called) as google:
            await google.revoke("nonexistent-account")

    asyncio.run(scenario())


@pytest.mark.parametrize("operation,expected", [
    ("list", "provider_unavailable"), ("revoke", "revoke_failed")
])
def test_network_errors_are_safe(google_state, operation, expected):
    def handler(request):
        raise httpx.ConnectError("sensitive upstream token diagnostic", request=request)

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                if operation == "list":
                    await google.list_messages(google_state[2], 10, None)
                else:
                    await google.revoke(google_state[2])
            assert caught.value.code == expected
            assert "sensitive" not in caught.value.message
            assert "sensitive" not in str(caught.value)

    asyncio.run(scenario())


class OversizedBody(httpx.AsyncByteStream):
    def __init__(self):
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(100):
            self.consumed += 1
            yield b"x" * 8192

    async def aclose(self):
        self.closed = True


def test_token_response_size_is_bounded_and_stream_closed(google_state):
    stream = OversizedBody()

    def handler(request):
        return httpx.Response(200, stream=stream)

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.exchange_code("code", "verifier")
            assert caught.value.code == "message_too_large"
            assert caught.value.status == 413

    asyncio.run(scenario())
    assert stream.consumed <= 9
    assert stream.closed is True


def test_revoke_response_size_is_bounded_and_stream_closed(google_state):
    stream = OversizedBody()

    def handler(request):
        return httpx.Response(200, stream=stream)

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.revoke(google_state[2])
            assert caught.value.code in {"message_too_large", "revoke_failed"}

    asyncio.run(scenario())
    assert stream.consumed <= 9
    assert stream.closed is True


@pytest.mark.parametrize("body", [b"not json", b"[]", b"null", b"{}"])
def test_unusable_success_response_has_safe_error(google_state, body):
    def handler(request):
        return httpx.Response(200, content=body)

    async def scenario():
        async with mock_google(google_state, handler) as google:
            with pytest.raises(ConnectorError) as caught:
                await google.exchange_code("code", "verifier")
            assert caught.value.code == "invalid_google_response"

    asyncio.run(scenario())
