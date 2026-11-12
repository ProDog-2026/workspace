import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from test_auth_binding import connect
from test_auth_binding import owned_client as owned_client
from test_auth_binding import post as auth_post
from test_scan import artifact
from test_supabase import FakeSupabase
from test_supabase import make_client as make_client
from test_supabase import post as sync_post

from prodog_gmail.app import COOKIE
from prodog_gmail.config import Settings
from prodog_gmail.experiments import ExperimentInput
from prodog_gmail.pipeline import MailPipeline
from prodog_gmail.store import Store

JOB = "00000000000000000000000000000011"


def insert_job(store, account, message, data):
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO analysis_jobs VALUES (?,?,?,?,?,?,?,?)",
            (JOB, account, message, "input", "config", json.dumps(data), None, data["created_at"]),
        )


def test_missing_auth_session_read_preserves_grant_session_flow_and_jobs(owned_client):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    connect(client)
    store = client.app.state.store
    secret = client.cookies.get(COOKIE)
    account = store.session(secret)["account_id"]
    state, _ = store.start_flow(secret)
    cancelled = []
    client.app.state.pipeline.cancel_account = cancelled.append
    client.headers.pop("authorization")
    response = client.get("/api/session")
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert store.session(secret)["account_id"] == account
    assert store.consume_flow(secret, state)
    assert cancelled == []


@pytest.mark.parametrize("gmail_connected", [False, True])
def test_logout_cancels_current_user_evaluations_only(owned_client, monkeypatch, gmail_connected):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    if gmail_connected:
        connect(client)
    session = client.get("/api/session").json()
    runner = client.app.state.experiments
    monkeypatch.setattr(runner, "_schedule", lambda: None)
    spec = ExperimentInput(provider="ollama", model="synthetic", task="summarize", mail="test")
    own = runner.enqueue(spec, "user:" + session["user_id"])
    other = runner.enqueue(spec, "user:00000000-0000-4000-8000-000000000002")
    assert auth_post(client, "/api/logout").status_code == 200
    assert own["status"] == "cancelled"
    assert other["status"] == "queued"


def test_legacy_sync_reuses_existing_pipeline_verdict_without_scheduling(make_client):
    fake = FakeSupabase()
    client = make_client(fake)
    client.app.state.detector = artifact(probability=0.01)
    secret = client.cookies.get(COOKIE)
    account = client.app.state.store.session(secret)["account_id"]
    data = {
        "id": JOB,
        "status": "skipped",
        "created_at": "2026-10-31T00:00:00Z",
        "static": {"status": "completed", "verdict": "danger", "score": 0.95},
    }
    insert_job(client.app.state.store, account, "demo-1", data)
    assert sync_post(client, "/api/sync").status_code == 200
    rows = [row for row in fake.rows("analysis_results") if row["mail_id"] == "mail-demo-1"]
    assert len(rows) == 1 and rows[0]["verdict"] == "danger"
    assert rows[0]["id"] == "00000000-0000-0000-0000-000000000011"
    assert client.app.state.pipeline.queue.empty()


def test_repeated_legacy_sync_upserts_same_result_ids(make_client):
    fake = FakeSupabase()
    client = make_client(fake)
    client.app.state.detector = artifact(probability=0.01)
    assert sync_post(client, "/api/sync").status_code == 200
    first = {row["mail_id"]: row.get("id") for row in fake.rows("analysis_results")}
    assert sync_post(client, "/api/sync").status_code == 200
    for row in fake.rows("analysis_results"):
        assert row.get("id") and row["id"] == first[row["mail_id"]]
    assert len(set(first.values())) == len(first)


def test_cancelled_pipeline_records_terminal_run_summary_and_error(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "test.db",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    store = Store(settings)
    runner = SimpleNamespace(settings=settings, records={})
    pipeline = MailPipeline(settings, store, runner)
    account = store.account("synthetic@example.invalid", "demo")
    data = {
        "id": JOB,
        "status": "cancelled",
        "created_at": "2026-10-31T00:00:00Z",
        "static": {"status": "completed", "verdict": "danger", "score": 0.95},
        "dynamic": {"id": "run", "status": "running"},
    }
    insert_job(store, account, "synthetic-mail", data)
    run = {
        "id": "run",
        "owner": "account:" + account,
        "source": {"kind": "mail_pipeline", "job_id": JOB},
        "status": "error",
        "error": {"code": "cleanup_failed", "message": "synthetic"},
        "observations": None,
    }
    runner.records["run"] = run
    pipeline.finished(run)
    stored = json.loads(pipeline.job(JOB)["data_json"])
    assert stored["status"] == "cancelled"
    assert stored["dynamic"]["status"] == "error"
    assert stored["error"]["code"] == "cleanup_failed"
    assert stored["static"]["verdict"] == "danger"
    projected = pipeline.latest(account, "synthetic-mail")
    assert projected["status"] == "cancelled"
    assert projected["dynamic"]["status"] == "error"


@pytest.mark.parametrize("positive_source", ["flag", "event"])
def test_pipeline_summary_preserves_earlier_positive_separately_from_last_snapshot(positive_source):
    run = {
        "id": "run",
        "status": "completed",
        "error": None,
        "observations": {"canary_leaked": False, "complete": True},
        "events": [{"type": "files", "data": {"canary_leaked": True}}]
        if positive_source == "event"
        else [],
        "canary_target_observed": positive_source == "flag",
    }
    summary = MailPipeline.dynamic_summary(run)["observations"]
    assert summary["canary_leaked"] is True
    assert summary["canary_present_at_last_snapshot"] is False
    assert run["observations"]["canary_leaked"] is False
