"""Refresh/OAuth races: synthetic grants, real SQLite, and no network."""

import asyncio
import time
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet

from prodog_gmail.config import GMAIL_SCOPE, Settings
from prodog_gmail.errors import ConnectorError
from prodog_gmail.google import GMAIL_URL, TOKEN_URL, GoogleClient
from prodog_gmail.store import Store

EMAIL = "refresh-race@example.invalid"
OWNER = "synthetic-owner"


@pytest.fixture
def state(tmp_path):
    settings = Settings(
        _env_file=None,
        app_mode="google",
        google_client_id="synthetic-client",
        google_client_secret="synthetic-secret",
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "refresh.db",
    )
    store = Store(settings)
    account = store.account(EMAIL, "google", user_id=OWNER)
    store.save_tokens(account, {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "scope": GMAIL_SCOPE,
        "expires_at": time.time() - 1,
    })
    return settings, store, account


def replace_grant(state, replacement, *, access="oauth-access"):
    settings, _, account = state
    # A distinct Store models the callback outside GoogleClient's refresh lock.
    store = Store(settings)
    if replacement == "deleted":
        store.forget_account(account)
    elif replacement != "unchanged":
        secret = store.new_session(account, user_id=OWNER)
        new = store.complete_oauth(secret, EMAIL, {
            "access_token": access,
            "refresh_token": "oauth-refresh",
            "scope": GMAIL_SCOPE,
            "expires_in": 3600,
        }, GoogleClient.token_bundle)
        assert store.session(new)["account_id"] == account
        if replacement == "invalid":
            store.invalidate_tokens(account)
        elif replacement == "expired":
            bundle = store.tokens(account)
            bundle["expires_at"] = time.time() - 1
            store.save_tokens(account, bundle)
    return store.tokens(account)


@pytest.mark.parametrize("outcome", ["success", "invalid_grant"])
@pytest.mark.parametrize("replacement", ["healthy", "invalid", "deleted", "unchanged", "expired"])
def test_refresh_completion_cannot_replace_or_invalidate_new_grant(state, outcome, replacement):
    async def scenario():
        settings, store, account = state
        started, release = asyncio.Event(), asyncio.Event()
        refreshes = []

        async def provider(request):
            assert request.method == "POST" and str(request.url) == TOKEN_URL
            body = parse_qs(request.content.decode())
            assert body["grant_type"] == ["refresh_token"]
            refresh = body["refresh_token"][0]
            refreshes.append(refresh)
            if refresh == "oauth-refresh":
                assert replacement == "expired"
                return httpx.Response(200, json={"access_token": "current-refreshed-access"})
            assert refresh == "old-refresh"
            started.set()
            await release.wait()
            if outcome == "invalid_grant":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={
                "access_token": "stale-access", "refresh_token": "stale-refresh",
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            google = GoogleClient(settings, store, http)
            pending = asyncio.create_task(google.access_token(account))
            try:
                await asyncio.wait_for(started.wait(), 5)
                current = replace_grant(state, replacement)
            finally:
                release.set()
            if replacement in {"invalid", "deleted"} or (
                replacement == "unchanged" and outcome == "invalid_grant"
            ):
                with pytest.raises(ConnectorError) as caught:
                    await asyncio.wait_for(pending, 5)
                assert caught.value.status == 401
                assert caught.value.code == (
                    "authentication_required" if replacement == "deleted" else "reconnect_required"
                )
                if replacement != "deleted":
                    assert store.tokens(account)["needs_reconnect"] is True
                    with pytest.raises(ConnectorError) as again:
                        await google.access_token(account)
                    assert again.value.code == "reconnect_required"
            else:
                expected = {
                    "healthy": "oauth-access",
                    "expired": "current-refreshed-access",
                    "unchanged": "stale-access",
                }[replacement]
                assert await asyncio.wait_for(pending, 5) == expected
                assert await google.access_token(account) == expected
            if replacement in {"healthy", "invalid", "deleted"}:
                assert store.tokens(account) == current
            elif replacement == "expired":
                assert store.tokens(account)["refresh_token"] == "oauth-refresh"
            elif outcome == "success":
                assert store.tokens(account)["refresh_token"] == "stale-refresh"
            assert refreshes == (
                ["old-refresh", "oauth-refresh"] if replacement == "expired" else ["old-refresh"]
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "replacement", ["healthy", "same_access", "invalid", "deleted", "unchanged"]
)
def test_second_gmail_401_only_invalidates_the_grant_used_for_that_request(state, replacement):
    async def scenario():
        settings, store, account = state
        started, release = asyncio.Event(), asyncio.Event()
        requests = []
        gets = 0

        async def provider(request):
            nonlocal gets
            requests.append(request.method)
            if request.method == "POST":
                assert str(request.url) == TOKEN_URL
                # Initial expiry refresh, followed by the refresh after the first 401.
                return httpx.Response(200, json={"access_token": "refreshed-old-access"})
            assert str(request.url) == GMAIL_URL + "/messages"
            assert request.headers["authorization"] == "Bearer refreshed-old-access"
            gets += 1
            if gets == 2:
                started.set()
                await release.wait()
            return httpx.Response(401, json={"error": {"message": "synthetic rejection"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            google = GoogleClient(settings, store, http)
            pending = asyncio.create_task(google.gmail(account, "/messages"))
            try:
                await asyncio.wait_for(started.wait(), 5)
                current = replace_grant(
                    state, replacement,
                    access=(
                        "refreshed-old-access" if replacement == "same_access" else "oauth-access"
                    ),
                )
            finally:
                release.set()
            with pytest.raises(ConnectorError) as caught:
                await asyncio.wait_for(pending, 5)
            assert caught.value.status == 401
            if replacement == "unchanged":
                assert store.tokens(account)["needs_reconnect"] is True
            else:
                assert store.tokens(account) == current
            if replacement in {"healthy", "same_access"}:
                assert await google.access_token(account) == current["access_token"]
            else:
                with pytest.raises(ConnectorError):
                    await google.access_token(account)
            assert requests == ["POST", "GET", "POST", "GET"]

    asyncio.run(scenario())
