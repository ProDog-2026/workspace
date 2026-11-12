"""Verify browser access tokens with the configured Supabase Auth server."""

import base64
import hashlib
import json
import re
import time
from collections import OrderedDict
from uuid import UUID

import httpx

from .errors import ConnectorError


class SupabaseAuth:
    def __init__(self, settings, client):
        self.url = settings.supabase_url.rstrip("/") + "/auth/v1/user"
        self.key = settings.supabase_anon_key
        self.client = client
        self.cache = OrderedDict()

    async def verify(self, authorization):
        if authorization is None:
            return None
        if len(authorization) > 16384 or not re.fullmatch(
            r"Bearer [A-Za-z0-9._~+/-]+=*", authorization
        ):
            raise ConnectorError("app_auth_required", "앱에 다시 로그인하세요.", 401)
        token_hash = hashlib.sha256(authorization.encode()).hexdigest()
        cached = self.cache.get(token_hash)
        if (
            cached
            and cached[0] > time.monotonic()
            and (cached[2] is None or cached[2] > time.time())
        ):
            return cached[1]
        try:
            response = await self.client.get(
                self.url, headers={"apikey": self.key, "Authorization": authorization}, timeout=10
            )
            if response.status_code in {401, 403}:
                raise ConnectorError("app_auth_required", "앱 로그인이 만료됐어요.", 401)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data["id"], str):
                raise ValueError
            user_id = str(UUID(data["id"]))
            if data.get("is_anonymous"):
                raise ValueError
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ConnectorError(
                "app_auth_unavailable", "앱 로그인 정보를 검증하지 못했어요. 다시 시도하세요.", 503
            ) from exc
        expires_at = None
        # Unverified exp may only shorten caching; identity always comes from the provider.
        try:
            _, payload, _ = authorization[7:].split(".")
            exp = json.loads(base64.urlsafe_b64decode(payload + "==="))["exp"]
            if type(exp) in (int, float):
                expires_at = exp
        except (ValueError, KeyError, TypeError, RecursionError):
            pass
        self.cache[token_hash] = (time.monotonic() + 10, user_id, expires_at)
        self.cache.move_to_end(token_hash)
        while len(self.cache) > 256:
            self.cache.popitem(last=False)
        return user_id
