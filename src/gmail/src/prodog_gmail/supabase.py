"""Server-side writes to Supabase (PostgREST). Metadata and verdicts only, never mail bodies."""

import httpx

from .errors import ConnectorError

MERGE = "resolution=merge-duplicates,return=representation"


def fetch_status(meta: dict) -> str:
    """Rules from arch/SUPABASE_TABLE.md. Complete does not mean analyzed or safe."""
    if meta.get("body_hash") is None:
        return "failed"
    if meta.get("truncated") or meta.get("warnings") or meta.get("attachments"):
        return "incomplete"
    return "complete"


class Supabase:
    def __init__(self, config, client: httpx.AsyncClient):
        key = config.supabase_service_key.get_secret_value()
        self.base = config.supabase_url.rstrip("/") + "/rest/v1"
        self.user_id = config.supabase_user_id
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

    async def save_page(self, email: str, messages: list[dict]) -> int:
        connection = await self._request(
            "POST",
            "gmail_connections",
            params={"on_conflict": "user_id,email"},
            rows=[{"user_id": self.user_id, "email": email, "status": "active"}],
            prefer=MERGE,
        )
        connection_id = connection[0]["id"]
        # Explicit allowlist: a body field added to the parser later cannot leak through here.
        mail_rows = [
            {
                "user_id": self.user_id,
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
            self._analysis_row(mail_ids[m["id"]], m["detection"])
            for m in messages
            if m.get("detection") and m["id"] in mail_ids
        ]
        if results:
            await self._request("POST", "analysis_results", rows=results)
        return len(saved)

    def _analysis_row(self, mail_id: str, detection: dict) -> dict:
        ok = detection["status"] == "ok"
        return {
            "user_id": self.user_id,
            "mail_id": mail_id,
            "status": "succeeded" if ok else "failed",
            "verdict": detection["verdict"],
            "static_score": detection["static_score"],
            "final_score": detection["static_score"],
            "types": [],
            "evidence": [],
            "status_detail": None if ok else "탐지 중 오류가 났어요.",
        }

    async def delete_connection(self, email: str) -> None:
        # mail_messages and analysis_results go with it (ON DELETE CASCADE in SUPABASE_TABLE.md).
        await self._request(
            "DELETE",
            "gmail_connections",
            params={"user_id": f"eq.{self.user_id}", "email": f"eq.{email}"},
        )
