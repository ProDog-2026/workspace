"""Server-side writes to Supabase (PostgREST). Metadata and verdicts only, never mail bodies."""

import uuid

import httpx

from .errors import ConnectorError

MERGE = "resolution=merge-duplicates,return=representation"


def fetch_status(meta: dict) -> str:
    """Rules from arch/SUPABASE_TABLE.md. Complete does not mean analyzed or safe."""
    if meta.get("body_hash") is None:
        return "failed"
    unread = any(a.get("pdf_status") != "parsed" for a in (meta.get("attachments") or []))
    if meta.get("truncated") or meta.get("warnings") or unread:
        return "incomplete"
    return "complete"


class Supabase:
    def __init__(self, config, client: httpx.AsyncClient):
        key = config.supabase_service_key.get_secret_value()
        self.base = config.supabase_url.rstrip("/") + "/rest/v1"
        self.client = client
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    async def _request(self, method, table, *, params=None, rows=None, prefer=None) -> list[dict]:
        headers = {**self.headers, **({"Prefer": prefer} if prefer else {})}
        try:
            response = await self.client.request(
                method, f"{self.base}/{table}", params=params, json=rows, headers=headers
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(
                "supabase_unavailable", "Supabase에 연결하지 못했습니다.", 502
            ) from exc
        if response.status_code >= 400:
            # Fixed message: the upstream body can echo the rows we sent.
            raise ConnectorError("supabase_error", "Supabase 저장에 실패했습니다.", 502)
        return response.json() if response.content else []

    async def save_page(self, user_id: str, email: str, messages: list[dict]) -> int:
        user_id = str(uuid.UUID(user_id))
        connection = await self._request(
            "POST",
            "gmail_connections",
            params={"on_conflict": "user_id,email"},
            rows=[{"user_id": user_id, "email": email, "status": "active"}],
            prefer=MERGE,
        )
        connection_id = connection[0]["id"]
        # Explicit allowlist: a body field added to the parser later cannot leak through here.
        mail_rows = [
            {
                "user_id": user_id,
                "connection_id": connection_id,
                "gmail_id": m["id"],
                "subject": m["subject"],
                "sender": m["sender"],
                "received_at": m["received_at"],
                "body_hash": m["body_hash"],
                "attachments": m["attachments"],
                "warnings": m["warnings"],
                "truncated": m["truncated"],
                "fetch_status": fetch_status(m),
            }
            for m in messages
        ]
        saved = await self._request(
            "POST",
            "mail_messages",
            params={"on_conflict": "connection_id,gmail_id"},
            rows=mail_rows,
            prefer=MERGE,
        )
        mail_ids = {row["gmail_id"]: row["id"] for row in saved}
        results = [
            self._analysis_row(user_id, mail_ids[m["id"]], m["detection"])
            for m in messages
            if m.get("detection") and m["id"] in mail_ids
        ]
        if results:
            await self._request("POST", "analysis_results", rows=results)
        return len(saved)

    def _analysis_row(self, user_id: str, mail_id: str, detection: dict) -> dict:
        ok = detection["status"] == "ok"
        return {
            "user_id": user_id,
            "mail_id": mail_id,
            "status": "succeeded" if ok else "failed",
            "verdict": detection["verdict"],
            "static_score": detection["static_score"],
            "final_score": detection["static_score"],
            "types": [],
            "evidence": [],
            "status_detail": None if ok else "탐지 중 오류가 났어요.",
        }

    async def delete_connection(self, user_id: str, email: str) -> None:
        user_id = str(uuid.UUID(user_id))
        # mail_messages and analysis_results go with it (ON DELETE CASCADE in SUPABASE_TABLE.md).
        await self._request(
            "DELETE",
            "gmail_connections",
            params={"user_id": f"eq.{user_id}", "email": f"eq.{email}"},
        )

    async def authenticate(self, id_token: str, access_token: str, verifier: str,
                           google_sub: str) -> str:
        try:
            response = await self.client.post(
                self.base.removesuffix("/rest/v1") + "/auth/v1/token",
                params={"grant_type": "id_token"}, headers=self.headers,
                json={"provider": "google", "id_token": id_token,
                      "access_token": access_token, "nonce": "oidc:" + verifier},
            )
            if response.status_code != 200:
                raise ValueError("auth rejected")
            user = response.json()["user"]
            matching = [identity for identity in user["identities"]
                        if identity.get("provider") == "google"
                        and identity.get("identity_data", {}).get("sub") == google_sub]
            if len(matching) != 1:
                raise ValueError("provider binding")
            return str(uuid.UUID(user["id"]))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ConnectorError(
                "identity_unavailable", "사용자 로그인을 확인하지 못했습니다. 다시 시도하세요.", 502
            ) from exc
