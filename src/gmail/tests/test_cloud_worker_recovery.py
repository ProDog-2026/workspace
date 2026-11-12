import asyncio
import contextlib
import json
import logging
import sqlite3
import time
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from prodog_gmail.cloud_sync import CloudSync
from prodog_gmail.config import Settings
from prodog_gmail.errors import ConnectorError
from prodog_gmail.pipeline import MailPipeline
from prodog_gmail.store import Store

PRIVATE = "synthetic SQL/mail/credential must never appear in logs or status"
USER = "00000000-0000-4000-8000-000000000001"
JOB = "00000000-0000-4000-8000-000000000011"


class Sink:
    def __init__(self):
        self.delivered = []

    async def save_page(self, email, messages, **kwargs):
        self.delivered.extend(kwargs["analyses"].values())


@pytest.fixture
def sync(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "worker.db",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    store = Store(settings)
    MailPipeline(settings, store, SimpleNamespace(settings=settings, records={}))
    account = store.account("test@example.invalid", "demo", user_id=USER)
    store.save_metadata(account, {"id": "test-mail", "body_hash": "a" * 64})
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO analysis_jobs VALUES (?,?,?,?,?,?,?,?)",
            (
                JOB,
                account,
                "test-mail",
                "input",
                "config",
                json.dumps({"id": JOB}),
                None,
                "2026-10-30T01:00:00Z",
            ),
        )
    cloud = CloudSync(store, Sink())
    cloud.mark(account, "test-mail")
    return cloud, account


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=3)


@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, RuntimeError])
@pytest.mark.parametrize(
    "phase",
    ["select", "read", "sink_before", "sink_after", "delivery", "delete", "backoff"],
)
def test_worker_retries_failures_without_losing_outbox(
    sync, monkeypatch, caplog, phase, error_type
):
    cloud, account = sync
    caplog.set_level(logging.ERROR, logger="prodog_gmail.cloud_sync")

    async def check():
        failed = asyncio.Event()
        failing = True
        connection = cloud.store.connection
        save_page = cloud.sink.save_page
        sql_prefix = {
            "select": "SELECT * FROM cloud_outbox",
            "read": "SELECT a.email",
            "delivery": "INSERT INTO cloud_deliveries",
            "delete": "DELETE FROM cloud_outbox",
            "backoff": "UPDATE cloud_outbox SET attempts",
        }.get(phase)

        def fail():
            failed.set()
            raise error_type(PRIVATE)

        class Connection:
            def __init__(self, conn):
                self.conn = conn

            def execute(self, sql, *args):
                if failing and sql_prefix and sql.startswith(sql_prefix):
                    fail()
                return self.conn.execute(sql, *args)

        @contextlib.contextmanager
        def faulty_connection():
            with connection() as conn:
                yield Connection(conn)

        async def faulty_sink(*args, **kwargs):
            if failing and phase == "sink_before":
                fail()
            if failing and phase == "backoff":
                raise ConnectorError("supabase_unavailable", PRIVATE, 502)
            await save_page(*args, **kwargs)
            if failing and phase == "sink_after":
                fail()

        monkeypatch.setattr(cloud.store, "connection", faulty_connection)
        monkeypatch.setattr(cloud.sink, "save_page", faulty_sink)
        await cloud.open()
        worker = cloud.worker
        try:
            await asyncio.wait_for(failed.wait(), timeout=1)
            assert not worker.done(), "an unexpected failure must not terminate the worker"
            status = cloud.status(account)
            assert status["pending"] == 1
            assert status["error"] and PRIVATE not in status["error"]
            records = [r for r in caplog.records if r.name == "prodog_gmail.cloud_sync"]
            assert records
            assert all(not r.exc_info and not r.args for r in records)
            assert PRIVATE not in caplog.text
            with connection() as conn:
                row = conn.execute("SELECT * FROM cloud_outbox").fetchone()
                assert row["revision"] == 1 and row["attempts"] == 0
                assert conn.execute("SELECT COUNT(*) FROM cloud_deliveries").fetchone()[0] == (
                    1 if phase == "delete" else 0
                )
            failing = False
            cloud.changed.set()
            await wait_until(lambda: cloud.status(account)["pending"] == 0)
            assert cloud.worker is worker and not worker.done()
            assert cloud.status(account)["error"] is None
            assert {data["id"] for data in cloud.sink.delivered} == {JOB}
            with connection() as conn:
                assert conn.execute("SELECT COUNT(*) FROM cloud_deliveries").fetchone()[0] == 1
        finally:
            # Also retrieve the terminated task's exception on the pre-fix implementation.
            with contextlib.suppress(Exception):
                await cloud.close()

    asyncio.run(check())


@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, RuntimeError])
def test_worker_failure_waits_and_cancels_without_spinning(sync, monkeypatch, error_type):
    cloud, account = sync

    async def check():
        failed = asyncio.Event()
        attempts = 0

        async def broken_sink(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            failed.set()
            raise error_type(PRIVATE)

        monkeypatch.setattr(cloud.sink, "save_page", broken_sink)
        await cloud.open()
        worker = cloud.worker
        try:
            await asyncio.wait_for(failed.wait(), timeout=1)
            assert not worker.done()
            assert cloud.status(account)["error"]
            for _ in range(5):
                cloud.changed.set()
                await asyncio.sleep(0.01)
            assert attempts == 1, "wakeups must not bypass failure retry delay"
            await asyncio.wait_for(cloud.close(), timeout=0.2)
            assert worker.cancelled()
            await asyncio.sleep(0.03)
            assert attempts == 1
            assert cloud.status(account)["pending"] == 1
        finally:
            with contextlib.suppress(Exception):
                await cloud.close()

    asyncio.run(check())


def test_status_database_failure_reports_unknown_pending_then_recovers(sync, monkeypatch):
    cloud, account = sync
    connection = cloud.store.connection

    @contextlib.contextmanager
    def broken_connection():
        raise sqlite3.OperationalError(PRIVATE)
        yield

    with monkeypatch.context() as patch:
        patch.setattr(cloud.store, "connection", broken_connection)
        status = cloud.status(account)
    assert status["configured"] is True
    assert status["pending"] is None
    assert status["error"] and PRIVATE not in status["error"]
    assert cloud.status(account) == {"configured": True, "pending": 1, "error": None}
    with connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM cloud_outbox").fetchone()[0] == 1


def test_open_twice_keeps_one_worker_and_close_cancels_it(sync):
    cloud, _ = sync

    async def check():
        await cloud.open()
        first = cloud.worker
        try:
            await cloud.open()
            assert cloud.worker is first
            await cloud.close()
            assert first.cancelled()
        finally:
            await cloud.close()
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)

    asyncio.run(check())


def test_worker_preserves_connector_error_backoff_and_recovers_on_mark(sync, monkeypatch):
    cloud, account = sync

    async def check():
        failed = asyncio.Event()
        save_page = cloud.sink.save_page

        async def unavailable(*args, **kwargs):
            failed.set()
            raise ConnectorError("supabase_unavailable", PRIVATE, 502)

        monkeypatch.setattr(cloud.sink, "save_page", unavailable)
        started = time.time()
        await cloud.open()
        try:
            await asyncio.wait_for(failed.wait(), timeout=1)
            assert not cloud.worker.done()
            status = cloud.status(account)
            assert status["pending"] == 1 and status["error"]
            assert PRIVATE not in status["error"]
            with cloud.store.connection() as conn:
                row = conn.execute("SELECT * FROM cloud_outbox").fetchone()
                assert row["attempts"] == 1 and row["revision"] == 1
                assert started + 2 <= row["next_attempt"] <= time.time() + 2
            monkeypatch.setattr(cloud.sink, "save_page", save_page)
            cloud.mark(account, "test-mail")
            await wait_until(lambda: cloud.status(account)["pending"] == 0)
            assert cloud.status(account)["error"] is None
            assert cloud.sink.delivered == [{"id": JOB}]
        finally:
            await cloud.close()

    asyncio.run(check())
