import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from prodog_gmail.config import Settings
from prodog_gmail.errors import ConnectorError
from prodog_gmail.pipeline import MailPipeline
from prodog_gmail.store import Store
from prodog_gmail.supabase import Supabase

USER = "00000000-0000-4000-8000-000000000001"
JOB = "00000000-0000-4000-8000-000000000011"


def test_cloud_recovery_delivers_each_changed_job_without_losing_older_results(tmp_path):
    from prodog_gmail.cloud_sync import CloudSync

    async def check():
        settings = Settings(
            _env_file=None,
            database_path=tmp_path / "history.db",
            token_encryption_key=Fernet.generate_key().decode(),
        )
        store = Store(settings)
        pipeline = MailPipeline(settings, store, SimpleNamespace(settings=settings, records={}))
        account = store.account("test@example.invalid", "demo", user_id=USER)
        store.save_metadata(account, {"id": "test-mail", "body_hash": "a" * 64})

        class Sink:
            delivered = []

            async def save_page(self, email, messages, **kwargs):
                self.delivered.extend(kwargs["analyses"].values())

        sink = Sink()
        cloud = CloudSync(store, sink)
        pipeline.cloud = cloud
        first = {"id": JOB, "status": "static_queued"}
        second = {"id": "00000000-0000-4000-8000-000000000012", "status": "completed"}
        with store.connection() as conn:
            conn.execute(
                "INSERT INTO analysis_jobs VALUES (?,?,?,?,?,?,?,?)",
                (
                    JOB,
                    account,
                    "test-mail",
                    "input",
                    "config-a",
                    json.dumps(first),
                    None,
                    "2026-10-30T01:00:00Z",
                ),
            )
        cloud.mark(account, "test-mail")
        await cloud.flush_once()
        assert sink.delivered[-1]["status"] == "static_queued"
        first["status"] = "completed"
        pipeline.write(JOB, first)
        with store.connection() as conn:
            conn.execute(
                "INSERT INTO analysis_jobs VALUES (?,?,?,?,?,?,?,?)",
                (
                    second["id"],
                    account,
                    "test-mail",
                    "input",
                    "config-b",
                    json.dumps(second),
                    None,
                    "2026-10-30T01:01:00Z",
                ),
            )
        cloud.mark(account, "test-mail")
        cloud = CloudSync(store, sink)
        await cloud.flush_once()
        completed = {data["id"] for data in sink.delivered if data["status"] == "completed"}
        assert completed == {first["id"], second["id"]}
        assert cloud.status(account)["pending"] == 0
        before = len(sink.delivered)
        cloud.mark(account, "test-mail")
        await cloud.flush_once()
        assert len(sink.delivered) == before

    asyncio.run(check())


@pytest.mark.parametrize(
    "missing_table", ["gmail_connections", "mail_messages", "analysis_results"]
)
def test_incomplete_cloud_acknowledgment_is_retryable(missing_table):
    import json

    def handle(request):
        table = request.url.path.rsplit("/", 1)[-1]
        rows = json.loads(request.content)
        if table == missing_table:
            return httpx.Response(200, json=[])
        if table == "gmail_connections":
            return httpx.Response(200, json=[{"id": "connection"}])
        if table == "mail_messages":
            return httpx.Response(200, json=[{"id": "mail", "gmail_id": "test-mail"}])
        return httpx.Response(200, json=rows)

    async def check():
        settings = SimpleNamespace(
            supabase_url="https://example.supabase.co",
            supabase_service_key=SecretStr("sb_secret_synthetic"),
        )
        metadata = dict(
            id="test-mail",
            subject="synthetic",
            sender="test@example.invalid",
            received_at=None,
            body_hash="a" * 64,
            attachments=[],
            warnings=[],
            truncated=False,
        )
        job = {"id": JOB, "status": "static_queued", "created_at": "2026-10-30T01:00:00Z"}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(ConnectorError, match="supabase_error"):
                await Supabase(settings, client).save_page(
                    "test@example.invalid", [metadata], user_id=USER, analyses={"test-mail": job}
                )

    asyncio.run(check())


def test_secret_key_and_verified_owner_write_pending_then_static_dynamic_result():
    calls = []

    def handle(request):
        import json

        rows = json.loads(request.content)
        calls.append((request, rows))
        table = request.url.path.rsplit("/", 1)[-1]
        if table == "gmail_connections":
            return httpx.Response(200, json=[{"id": "connection-id"}])
        if table == "mail_messages":
            return httpx.Response(200, json=[{"id": "mail-id", "gmail_id": "test-mail"}])
        return httpx.Response(200, json=rows)

    async def check():
        settings = SimpleNamespace(
            supabase_url="https://example.supabase.co",
            supabase_service_key=SecretStr("sb_secret_synthetic"),
        )
        metadata = {
            "id": "test-mail",
            "subject": "합성",
            "sender": "test@example.invalid",
            "received_at": None,
            "body_hash": "a" * 64,
            "warnings": [],
            "attachments": [],
            "truncated": False,
            "raw": "NEVER_SEND_MAIL_BODY",
        }
        data = {"id": JOB, "created_at": "2026-10-30T01:00:00Z", "status": "static_queued"}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            sink = Supabase(settings, client)
            await sink.save_page(
                "test@example.invalid", [metadata], user_id=USER, analyses={"test-mail": data}
            )
            pending = calls[-1][1][0]
            assert pending["id"] == JOB and pending["status"] == "pending"
            assert pending["verdict"] is None
            data.update(
                status="error",
                static={
                    "status": "completed",
                    "verdict": "danger",
                    "score": 0.93,
                    "model_id": "synthetic-static-model",
                },
                dynamic={"status": "error", "observations": {"canary_leaked": None}},
            )
            await sink.save_page(
                "test@example.invalid", [metadata], user_id=USER, analyses={"test-mail": data}
            )
            completed = calls[-1][1][0]
            assert completed["id"] == JOB
            assert completed["status"] == "succeeded" and completed["verdict"] == "danger"
            assert completed["static_score"] == 93 and completed["llm_score"] is None
            assert completed["analysis"]["dynamic"]["status"] == "error"
            assert all("authorization" not in request.headers for request, _ in calls)
            assert all(row["user_id"] == USER for _, rows in calls for row in rows)
            assert all(
                "NEVER_SEND_MAIL_BODY" not in request.content.decode() for request, _ in calls
            )

    asyncio.run(check())


def test_durable_outbox_retries_and_preserves_new_updates_during_save(tmp_path):
    from prodog_gmail.cloud_sync import CloudSync

    async def check():
        settings = Settings(
            _env_file=None,
            token_encryption_key=Fernet.generate_key().decode(),
            database_path=tmp_path / "outbox.db",
        )
        store = Store(settings)
        MailPipeline(settings, store, SimpleNamespace(settings=settings, records={}))
        account = store.account("test@example.invalid", "demo", user_id=USER)
        store.save_metadata(
            account,
            {
                "id": "test-mail",
                "subject": "합성",
                "sender": "test@example.invalid",
                "received_at": None,
                "body_hash": "a" * 64,
                "warnings": [],
                "attachments": [],
                "truncated": False,
            },
        )

        class Sink:
            fail = True
            advance = False
            calls = []

            async def save_page(self, email, messages, **kwargs):
                if self.fail:
                    from prodog_gmail.errors import ConnectorError

                    raise ConnectorError("supabase_unavailable", "서버 연결 실패", 502)
                self.calls.append((email, messages, kwargs))
                if self.advance:
                    cloud.mark(account, "test-mail")

        sink = Sink()
        cloud = CloudSync(store, sink)
        cloud.mark(account, "test-mail")
        await cloud.flush_once()
        assert cloud.status(account)["pending"] == 1
        assert cloud.status(account)["error"]
        cloud = CloudSync(store, sink)
        sink.fail = False
        sink.advance = True
        cloud.mark(account, "test-mail")
        await cloud.flush_once()
        assert cloud.status(account)["pending"] == 1
        assert sink.calls[-1][2]["user_id"] == USER
        sink.advance = False
        await cloud.flush_once()
        assert cloud.status(account)["pending"] == 0
        store.forget_account(account)
        cloud.mark(account, "test-mail")
        assert cloud.status(account)["pending"] == 0

    asyncio.run(check())
