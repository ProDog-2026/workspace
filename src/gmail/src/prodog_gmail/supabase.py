"""Server-side writes to Supabase (PostgREST). Metadata and verdicts only, never mail bodies."""

import math
import uuid

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
        self.client = client
        self.headers = {"apikey": key}
        if not key.startswith("sb_secret_"):
            self.headers["Authorization"] = f"Bearer {key}"

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
        try:
            rows = response.json() if response.content else []
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError
            return rows
        except ValueError as exc:
            raise ConnectorError(
                "supabase_error", "Supabase 응답을 확인하지 못했습니다.", 502
            ) from exc

    async def save_page(
        self, email: str, messages: list[dict], *, user_id: str, analyses: dict | None = None
    ) -> int:
        user_id = str(uuid.UUID(user_id))
        connection = await self._request(
            "POST",
            "gmail_connections",
            params={"on_conflict": "user_id,email"},
            rows=[{"user_id": user_id, "email": email, "status": "active"}],
            prefer=MERGE,
        )
        if len(connection) != 1 or not isinstance(connection[0].get("id"), str):
            raise ConnectorError("supabase_error", "Supabase 연결 저장을 확인하지 못했습니다.", 502)
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
        if (
            len(saved) != len(messages)
            or any(not isinstance(row.get(key), str) for row in saved for key in ("gmail_id", "id"))
            or {row["gmail_id"] for row in saved} != {m["id"] for m in messages}
        ):
            raise ConnectorError("supabase_error", "Supabase 메일 저장을 확인하지 못했습니다.", 502)
        mail_ids = {row["gmail_id"]: row["id"] for row in saved}
        results = []
        for mail in messages:
            data = (analyses or {}).get(mail["id"])
            if data is not None:
                results.append(self.pipeline_row(user_id, mail_ids[mail["id"]], mail, data))
            elif mail.get("detection"):
                results.append(self._analysis_row(user_id, mail_ids[mail["id"]], mail["detection"]))
        if results:
            saved_results = await self._request(
                "POST",
                "analysis_results",
                rows=results,
                params={"on_conflict": "id"},
                prefer=MERGE,
            )
            if (
                len(saved_results) != len(results)
                or any(not isinstance(row.get("mail_id"), str) for row in saved_results)
                or {row["mail_id"] for row in saved_results} != {row["mail_id"] for row in results}
            ):
                raise ConnectorError(
                    "supabase_error", "Supabase 분석 결과 저장을 확인하지 못했습니다.", 502
                )
        return len(saved)

    def _analysis_row(self, user_id: str, mail_id: str, detection: dict) -> dict:
        ok = detection["status"] == "ok"
        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"prodog:legacy:{user_id}:{mail_id}")),
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

    @staticmethod
    def pipeline_row(user_id, mail_id, metadata, data):
        static = data.get("static") or {}
        completed = static.get("status") == "completed" and static.get("verdict") in {
            "safe",
            "suspicious",
            "danger",
        }
        failed = not completed and data["status"] in {"static_error", "error", "cancelled"}
        score = static.get("score")
        score = (
            round(score * 100)
            if completed
            and type(score) in (int, float)
            and math.isfinite(score)
            and 0 <= score <= 1
            else None
        )
        detail = data.get("error") or static.get("error") or {}
        missing = list(metadata.get("warnings") or [])
        if metadata.get("attachments"):
            missing.append(f"첨부파일 {len(metadata['attachments'])}개 내용 검사 안 함")
        if metadata.get("truncated"):
            missing.append("메일 본문이 잘려 일부만 검사함")
        summary = {k: data.get(k) for k in ("id", "status", "static", "dynamic", "error")}
        return {
            "id": str(uuid.UUID(data["id"])),
            "user_id": user_id,
            "mail_id": mail_id,
            "status": "succeeded" if completed else "failed" if failed else "pending",
            "verdict": static.get("verdict") if completed else None,
            "static_score": score,
            "llm_score": None,
            "final_score": score,
            "types": [],
            "evidence": [],
            "missing_parts": missing,
            "status_detail": str(detail.get("message") or "분석이 완료되지 않았습니다.")[:200]
            if failed
            else None,
            "model": static.get("model_id"),
            "analysis": summary,
            "created_at": data["created_at"],
        }

    async def delete_connection(self, email: str, *, user_id: str) -> None:
        # mail_messages and analysis_results go with it (ON DELETE CASCADE in SUPABASE_TABLE.md).
        await self._request(
            "DELETE",
            "gmail_connections",
            params={"user_id": f"eq.{str(uuid.UUID(user_id))}", "email": f"eq.{email}"},
        )
