"""Synthetic regressions for deletion, incomplete evidence and real SQLite contention."""

import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from test_auth_binding import connect, post
from test_auth_binding import owned_client as owned_client

from prodog_gmail.app import COOKIE
from prodog_gmail.canary import exposure_observed
from prodog_gmail.cloud_sync import CloudSync
from prodog_gmail.config import Settings
from prodog_gmail.errors import ConnectorError
from prodog_gmail.store import Store


def test_earlier_incomplete_files_keep_exposure_inconclusive():
    record = {
        "status": "completed",
        "canary_output_observed": False,
        "observations": {"complete": True, "files": []},
        "events": [
            {
                "type": "files",
                "data": {
                    "complete": False,
                    "files": [
                        {"path": "/workspace/large.bin", "contains_canary": None},
                    ],
                },
            }
        ],
    }
    assert exposure_observed(record) is None
    record["events"][0]["data"]["files"][0]["contains_canary"] = True
    assert exposure_observed(record) is True


def test_failed_disconnect_blocks_resync_across_restart_and_allows_retry(owned_client, monkeypatch):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    connect(client)
    app = client.app
    store, cloud = app.state.store, app.state.cloud
    account = store.session(client.cookies.get(COOKIE))["account_id"]
    store.save_metadata(account, {"id": "synthetic-mail"})
    cloud.mark(account, "synthetic-mail")
    client.portal.call(cloud.close)

    class Sink:
        exists = True

        async def delete_connection(self, *args, **kwargs):
            self.exists = False

        async def save_page(self, *args, **kwargs):
            self.exists = True

    sink = Sink()
    app.state.supabase = cloud.sink = sink

    async def fail(owners):
        raise ConnectorError("experiment_delete_failed", "synthetic delete failure", 503)

    with monkeypatch.context() as patch:
        patch.setattr(app.state.experiments, "forget_owners", fail)
        assert post(client, "/api/disconnect").status_code == 503
    assert sink.exists is False
    assert client.portal.call(cloud.flush_once) is False
    assert sink.exists is False
    restarted = CloudSync(Store(app.state.settings), sink)
    assert client.portal.call(restarted.flush_once) is False
    assert sink.exists is False
    assert cloud.status(account)["error"]
    assert client.get("/api/session").json()["disconnect_pending"] is True
    assert app.state.pipeline.account_ready(account) is False
    with pytest.raises(ConnectorError, match="account_deleting"):
        store.save_metadata(account, {"id": "late-mail"})
    assert post(client, "/api/disconnect").status_code == 200
    assert store.session(client.cookies.get(COOKIE)) is None


def test_database_contention_does_not_hold_the_event_loop(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "review.db",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    store = Store(settings)
    cloud = CloudSync(store, SimpleNamespace())
    locked, release = threading.Event(), threading.Event()

    def lock():
        with sqlite3.connect(store.path) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            locked.set()
            release.wait(0.5)

    writer = threading.Thread(target=lock)
    writer.start()
    assert locked.wait(1)

    async def check():
        tick = asyncio.create_task(asyncio.sleep(0.01))
        await asyncio.sleep(0)
        with pytest.raises(sqlite3.OperationalError):
            await cloud.flush_once()
        await tick
        assert not release.is_set()
        assert writer.is_alive(), "the query must fail while the writer is still locked"

    try:
        asyncio.run(check())
    finally:
        release.set()
        writer.join(timeout=1)


def test_pipeline_worker_survives_real_database_lock(tmp_path):
    from prodog_gmail.pipeline import MailPipeline

    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "pipeline.db",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    store = Store(settings)
    pipeline = MailPipeline(settings, store, SimpleNamespace(settings=settings, records={}))
    locked, release = threading.Event(), threading.Event()

    def lock():
        with sqlite3.connect(store.path) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            locked.set()
            release.wait(2)

    writer = threading.Thread(target=lock)
    writer.start()
    assert locked.wait(1)

    async def check():
        pipeline.queue.put_nowait("missing-job")
        worker = asyncio.create_task(pipeline.work())
        try:
            await asyncio.sleep(0.05)
            assert not worker.done()
            release.set()
            await asyncio.wait_for(pipeline.queue.join(), 1)
            assert not worker.done()
        finally:
            release.set()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    try:
        asyncio.run(check())
    finally:
        release.set()
        writer.join(timeout=1)


def test_locked_evaluation_summary_is_reconciled_from_saved_log(owned_client):
    from test_auth_binding import A
    from test_disconnect_experiments import seed

    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    assert client.get("/api/session").status_code == 200
    runner = client.app.state.experiments
    record = seed(runner, "user:" + A, evaluation=True)
    record["status"] = "error"
    record["error"] = {"code": "synthetic", "message": "synthetic"}
    with sqlite3.connect(runner.evaluations.path) as lock:
        lock.execute("BEGIN EXCLUSIVE")

        async def save():
            runner.save(record)

        client.portal.call(save)
        assert (runner.directory / (record["id"] + ".json")).exists()
    response = client.get("/api/evaluations/" + record["evaluation"]["group_id"])
    assert response.status_code == 200
    assert response.json()["runs"][0]["status"] == "error"


def test_api_database_lock_returns_retryable_error_then_recovers(owned_client):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    client.get("/api/session")
    with sqlite3.connect(client.app.state.store.path) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        response = client.get("/api/session")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_busy"
        assert response.headers["Retry-After"] == "1"
        assert client.get("/health").status_code == 200
    assert client.get("/api/session").status_code == 200
