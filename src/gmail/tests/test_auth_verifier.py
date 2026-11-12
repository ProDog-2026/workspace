import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from prodog_gmail.auth import SupabaseAuth
from prodog_gmail.config import Settings
from prodog_gmail.errors import ConnectorError

USER = "00000000-0000-4000-8000-000000000001"
AUTH_SETTINGS = SimpleNamespace(
    supabase_url="https://test.supabase.co", supabase_anon_key="sb_publishable_synthetic"
)


def bearer(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"Bearer e30.{payload}.synthetic"


@pytest.mark.parametrize("authorization", ["Bearer 한글", "Bearer \ud800"])
def test_non_ascii_bearer_is_safe_401(authorization):
    async def check():
        def forbidden(_):
            pytest.fail("malformed credentials must not reach the provider")

        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            with pytest.raises(ConnectorError) as caught:
                await SupabaseAuth(AUTH_SETTINGS, client).verify(authorization)
            assert caught.value.status == 401
            assert caught.value.code == "app_auth_required"

    asyncio.run(check())


@pytest.mark.parametrize("user_id", [123, True, {}, [], None, "invalid-id"])
def test_malformed_provider_id_is_safe_503(user_id):
    async def check():
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"id": user_id}))
        async with httpx.AsyncClient(transport=transport) as client:
            auth = SupabaseAuth(AUTH_SETTINGS, client)
            with pytest.raises(ConnectorError) as caught:
                await auth.verify("Bearer synthetic-user")
            assert caught.value.status == 503
            assert caught.value.code == "app_auth_unavailable"
            assert "invalid-id" not in caught.value.message

    asyncio.run(check())


@pytest.mark.parametrize("expires_at", [1002, 1002.5, 999, -(10**400)])
@pytest.mark.parametrize("advance_monotonic", [True, False])
def test_cache_cannot_outlive_jwt_exp(monkeypatch, expires_at, advance_monotonic):
    clock = SimpleNamespace(wall=1000.0, monotonic=50.0)
    monkeypatch.setattr("prodog_gmail.auth.time.time", lambda: clock.wall)
    monkeypatch.setattr("prodog_gmail.auth.time.monotonic", lambda: clock.monotonic)

    async def check():
        calls = []

        def handle(request):
            calls.append(request)
            if len(calls) > 1:
                return httpx.Response(401, json={"message": "untrusted rejection"})
            return httpx.Response(200, json={"id": USER})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            auth = SupabaseAuth(AUTH_SETTINGS, client)
            authorization = bearer({"sub": "forged-local-identity", "exp": expires_at})
            assert await auth.verify(authorization) == USER
            if expires_at > clock.wall:
                clock.wall = expires_at - 0.1
                if advance_monotonic:
                    clock.monotonic = 50 + expires_at - 1000 - 0.1
                assert await auth.verify(authorization) == USER
                assert len(calls) == 1
                clock.wall = expires_at
                if advance_monotonic:
                    clock.monotonic = 50 + expires_at - 1000
            with pytest.raises(ConnectorError) as caught:
                await auth.verify(authorization)
            assert caught.value.status == 401
            assert len(calls) == 2

    asyncio.run(check())


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer opaque-synthetic-token",
        "Bearer e30.invalid.synthetic",
        bearer({}),
        bearer([]),
        bearer({"exp": "bad"}),
        bearer({"exp": True}),
        bearer({"exp": 9999999999}),
        bearer({"exp": 10**400}),
    ],
)
def test_verified_cache_remains_bounded_to_ten_seconds(monkeypatch, authorization):
    clock = SimpleNamespace(wall=1000.0, monotonic=50.0)
    monkeypatch.setattr("prodog_gmail.auth.time.time", lambda: clock.wall)
    monkeypatch.setattr("prodog_gmail.auth.time.monotonic", lambda: clock.monotonic)

    async def check():
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(200, json={"id": USER})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            auth = SupabaseAuth(AUTH_SETTINGS, client)
            assert await auth.verify(authorization) == USER
            clock.wall, clock.monotonic = 1009.9, 59.9
            assert await auth.verify(authorization) == USER
            assert len(calls) == 1
            clock.wall, clock.monotonic = 1010.0, 60.0
            assert await auth.verify(authorization) == USER
            assert len(calls) == 2

    asyncio.run(check())


def test_forged_claims_never_authorize_or_cache():
    async def check():
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(401, json={"message": "untrusted rejection"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            auth = SupabaseAuth(AUTH_SETTINGS, client)
            for _ in range(2):
                with pytest.raises(ConnectorError) as caught:
                    await auth.verify(bearer({"sub": USER, "exp": 9999999999}))
                assert caught.value.status == 401
                assert "untrusted rejection" not in caught.value.message
            assert len(calls) == 2

    asyncio.run(check())


@pytest.mark.parametrize(
    "fields",
    [
        {"supabase_url": "https://test.supabase.co"},
        {"supabase_anon_key": "sb_publishable_synthetic"},
        {"supabase_service_key": "sb_secret_synthetic"},
        {"supabase_url": "https://test.supabase.co", "supabase_service_key": "sb_secret_synthetic"},
        {
            "supabase_anon_key": "sb_publishable_synthetic",
            "supabase_service_key": "sb_secret_synthetic",
        },
    ],
)
def test_settings_reject_partial_supabase_configuration(fields):
    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None, token_encryption_key=Fernet.generate_key().decode(), **fields)
    assert "sb_secret_synthetic" not in str(caught.value)
    assert "sb_publishable_synthetic" not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://test.supabase.co",
        "http://127.0.0.1:54321",
        "https:///missing-host",
        "https://user:secret@test.supabase.co",
        "https://@test.supabase.co",
        "https://test.supabase.co/path",
        "https://test.supabase.co?query=1",
        "https://test.supabase.co?",
        "https://test.supabase.co#fragment",
        "https://test.supabase.co#",
        "https://test.supabase.co:bad",
        "https://test.supabase.co:65536",
        "https://test.supabase.co\n",
        "https://bad host",
        "https://test.supabase.co\\other-host",
    ],
)
def test_settings_require_supabase_https_origin(url):
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            token_encryption_key=Fernet.generate_key().decode(),
            supabase_url=url,
            supabase_anon_key="sb_publishable_synthetic",
        )


@pytest.mark.parametrize(
    ("url", "service_key"),
    [
        ("", ""),
        ("https://test.supabase.co/", ""),
        ("https://test.supabase.co:8443", ""),
        ("https://test.supabase.co/", "sb_secret_synthetic"),
    ],
)
def test_settings_preserve_disabled_auth_only_and_cloud_modes(url, service_key):
    settings = Settings(
        _env_file=None,
        token_encryption_key=Fernet.generate_key().decode(),
        supabase_url=url,
        supabase_anon_key="sb_publishable_synthetic" if url else "",
        supabase_service_key=service_key,
    )
    assert settings.supabase_auth_configured is bool(url)
    assert settings.supabase_configured is bool(url and service_key)
    assert settings.supabase_url == url
