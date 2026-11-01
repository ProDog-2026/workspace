"""Opt-in real cloud test: synthetic metadata only; remove only its new connection."""

import asyncio
import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from prodog_gmail.config import Settings
from prodog_gmail.supabase import Supabase

pytestmark = pytest.mark.skipif(
    os.getenv("PRODOG_SUPABASE_LIVE_TESTS") != "1", reason="real cloud test is opt-in"
)


def test_real_pending_result_upsert_read_and_cleanup():
    async def check():
        settings = Settings()
        owner = str(uuid.UUID(os.environ["PRODOG_SUPABASE_TEST_USER_ID"]))
        tag = "prodog-verification-" + uuid.uuid4().hex
        email = tag + "@example.invalid"
        metadata = {
            "id": tag,
            "subject": "[SYNTHETIC] ProDog integration verification",
            "sender": email,
            "received_at": datetime.now(UTC).isoformat(),
            "body_hash": "a" * 64,
            "warnings": [],
            "attachments": [],
            "truncated": False,
        }
        data = {
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "static_queued",
            "static": None,
            "dynamic": None,
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            sink = Supabase(settings, client)
            try:
                await sink.save_page(email, [metadata], user_id=owner, analyses={tag: data})
                params = {
                    "select": "id,verdict,static_score,analysis",
                    "gmail_message_id": "eq." + tag,
                }
                response = await client.get(
                    sink.base + "/scans", params=params, headers=sink.headers
                )
                assert response.status_code == 200
                assert len(response.json()) == 1 and response.json()[0]["verdict"] is None
                data.update(
                    status="completed",
                    static={
                        "status": "completed",
                        "verdict": "danger",
                        "score": 0.93,
                        "model_id": "synthetic-verification-model",
                    },
                    dynamic={"status": "completed", "observations": {"canary_leaked": False}},
                )
                for _ in range(2):
                    await sink.save_page(email, [metadata], user_id=owner, analyses={tag: data})
                response = await client.get(
                    sink.base + "/scans", params=params, headers=sink.headers
                )
                rows = response.json()
                assert response.status_code == 200 and len(rows) == 1
                assert rows[0]["verdict"] == "danger" and rows[0]["static_score"] == 93
                assert rows[0]["analysis"]["dynamic"]["observations"]["canary_leaked"] is False
                # Simulate a pre-fix legacy result newer than the authoritative job.
                metadata["detection"] = {"status": "ok", "verdict": "safe", "static_score": 1}
                await sink.save_page(email, [metadata], user_id=owner)
                response = await client.get(
                    sink.base + "/scans", params=params, headers=sink.headers
                )
                assert response.status_code == 200 and response.json()[0]["verdict"] == "danger"
                failed_tag = tag + "-failed"
                await sink.save_page(
                    email,
                    [
                        {
                            **metadata,
                            "id": failed_tag,
                            "body_hash": None,
                            "received_at": None,
                            "warnings": ["message_too_large"],
                            "truncated": True,
                        }
                    ],
                    user_id=owner,
                )
                failed = await client.get(
                    sink.base + "/scans",
                    headers=sink.headers,
                    params={"gmail_message_id": "eq." + failed_tag, "select": "status,verdict"},
                )
                assert failed.status_code == 200
                assert failed.json() == [{"status": "failed", "verdict": None}]
                anonymous = await client.get(
                    sink.base + "/scans",
                    params=params,
                    headers={"apikey": settings.supabase_anon_key},
                )
                assert anonymous.status_code in {401, 403}
            finally:
                await sink.delete_connection(email, user_id=owner)
            response = await client.get(sink.base + "/scans", params=params, headers=sink.headers)
            assert response.status_code == 200 and response.json() == []

    asyncio.run(check())
