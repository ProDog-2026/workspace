"""Unlink removes only the requesting owner's synthetic data after writers stop."""

import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_auth_binding import A, B, connect, post
from test_auth_binding import owned_client as owned_client

from prodog_gmail.app import COOKIE
from prodog_gmail.errors import ConnectorError
from prodog_gmail.experiments import ExperimentInput, Experiments
from prodog_gmail.store import digest


def seed(runner, owner, *, status="completed", evaluation=False):
    details = (
        {
            "group_id": uuid.uuid4().hex,
            "name": "합성 비교",
            "expected_runs": 1,
            "case_id": "normal",
            "label": "normal",
            "repeat": 1,
            "summary_contains": ["synthetic"],
        }
        if evaluation
        else None
    )
    record = runner.enqueue(
        ExperimentInput(
            provider="ollama", model="synthetic", task="요약", mail="SYNTHETIC_PRIVATE_BODY"
        ),
        owner,
        evaluation=details,
        prepare=True,
    )
    record["status"] = status
    runner.records[record["id"]] = record
    runner.save(record)
    if status == "queued":
        runner.pending.append(record["id"])
    return record


def test_disconnect_purges_account_user_and_legacy_sessions_but_preserves_other_user(
    owned_client,
    monkeypatch,
):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    connect(client)
    store, runner = client.app.state.store, client.app.state.experiments
    monkeypatch.setattr(runner, "_schedule", lambda: None)
    account = store.session(client.cookies.get(COOKIE))["account_id"]
    old_secret = store.new_session(account)
    own = [
        seed(runner, "account:" + account, status="queued"),
        seed(runner, "user:" + A, evaluation=True),
        seed(runner, digest(old_secret)),
    ]
    other = seed(runner, "user:" + B, evaluation=True)
    other_path = runner.directory / (other["id"] + ".json")
    other_bytes = other_path.read_bytes()
    temporary = runner.directory / (own[0]["id"] + ".tmp")
    temporary.write_text("SYNTHETIC_PRIVATE_BODY")
    assert post(client, "/api/disconnect").status_code == 200
    for record in own:
        assert record["id"] not in runner.records
        assert record["id"] not in runner.pending
        assert not (runner.directory / (record["id"] + ".json")).exists()
    assert not temporary.exists()
    assert runner.evaluations.groups("user:" + A) == []
    assert runner.evaluations.groups("user:" + B)
    assert other_path.read_bytes() == other_bytes
    with store.connection() as conn:
        assert conn.execute("SELECT 1 FROM accounts WHERE id=?", (account,)).fetchone() is None


@pytest.mark.parametrize("encrypted", [False, True])
def test_purge_also_removes_owned_orphans_and_temporary_records(owned_client, encrypted):
    client, _ = owned_client
    runner = client.app.state.experiments
    owner = "user:" + A
    paths = [runner.directory / (uuid.uuid4().hex + suffix) for suffix in (".json", ".tmp")]
    content = json.dumps({"owner": owner, "mail": "SYNTHETIC_PRIVATE_BODY"})
    if encrypted:
        content = json.dumps({"encrypted": runner.cipher.encrypt(content.encode()).decode()})
    for path in paths:
        path.write_text(content)
    client.portal.call(runner.forget_owners, {owner})
    assert all(not path.exists() for path in paths)


def test_cleanup_failure_keeps_record_and_account_for_retry(owned_client, monkeypatch):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    connect(client)
    runner = client.app.state.experiments
    monkeypatch.setattr(runner, "_schedule", lambda: None)
    record = seed(runner, "user:" + A, status="error")
    record["error"] = {"code": "cleanup_failed", "message": "synthetic"}
    runner.save(record)
    account = client.app.state.store.session(client.cookies.get(COOKIE))["account_id"]
    assert post(client, "/api/disconnect").status_code == 503
    assert (runner.directory / (record["id"] + ".json")).exists()
    assert client.app.state.store.account_owner(account) == A


@pytest.mark.parametrize("path", ["/api/experiments", "/api/evaluations"])
def test_deletion_during_preflight_rejects_late_admission(owned_client, monkeypatch, path):
    from test_dynamic_admission import ready
    from test_evaluations import batch

    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    runner = client.app.state.experiments
    runner.settings.sandbox_docker_context = "synthetic-vm"
    entered, release = client.portal.call(asyncio.Event), client.portal.call(asyncio.Event)

    async def pause(*args):
        entered.set()
        await release.wait()
        return ready() if path.endswith("experiments") else "revision"

    async def docker_status(*args):
        return {"ready": True}

    monkeypatch.setattr("prodog_gmail.experiments.check_readiness", pause)
    monkeypatch.setattr(client.app.state.pipeline, "validate_model", pause)
    monkeypatch.setattr("prodog_gmail.evaluations.docker_status", docker_status)
    csrf = client.get("/api/session").json()["csrf_token"]
    body = (
        {"provider": "ollama", "model": "synthetic", "task": "요약", "mail": "합성"}
        if path.endswith("experiments")
        else batch()
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(
            client.post,
            path,
            headers={"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": csrf},
            json=body,
        )
        try:
            client.portal.call(asyncio.wait_for, entered.wait(), 2)
            client.portal.call(runner.forget_owners, {"user:" + A})
        finally:
            client.portal.call(release.set)
        response = request.result(timeout=3)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "experiment_history_changed"
    assert not runner.records and not list(runner.directory.glob("*.json"))


def test_disconnect_file_failure_is_reported_and_can_be_retried(owned_client, monkeypatch):
    client, _ = owned_client
    client.headers["Authorization"] = "Bearer user-a"
    connect(client)
    store, runner = client.app.state.store, client.app.state.experiments
    account = store.session(client.cookies.get(COOKIE))["account_id"]
    record = seed(runner, "account:" + account)
    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path.name == record["id"] + ".json":
            raise PermissionError("SYNTHETIC_PRIVATE_BODY must not appear in response")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", denied)
        response = post(client, "/api/disconnect")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "experiment_delete_failed"
    assert "SYNTHETIC_PRIVATE_BODY" not in response.text
    assert store.account_owner(account) == A
    assert post(client, "/api/disconnect").status_code == 200
    assert not (runner.directory / (record["id"] + ".json")).exists()


def test_purge_waits_for_active_writer_and_blocks_new_admission(owned_client, monkeypatch):
    client, _ = owned_client
    runner = client.app.state.experiments
    assert hasattr(runner, "forget_owners")
    monkeypatch.setattr(runner, "_schedule", lambda: None)

    async def check():
        owner = "user:" + A
        record = seed(runner, owner, status="running")
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def writer():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                record["status"] = "cancelled"
                runner.save(record)

        runner.running_id = record["id"]
        runner.active = asyncio.create_task(writer())
        await entered.wait()
        operation = asyncio.create_task(runner.forget_owners({owner}))
        try:
            await asyncio.wait_for(cleaning.wait(), 1)
            assert not operation.done()
            with pytest.raises(ConnectorError, match="experiment_deleting"):
                runner.enqueue(
                    ExperimentInput(provider="ollama", model="test", task="t", mail="m"), owner
                )
        finally:
            release.set()
            await operation
        await asyncio.sleep(0)
        assert not (runner.directory / (record["id"] + ".json")).exists()
        assert record["id"] not in runner.records
        replacement = Experiments(runner.settings)
        try:
            await replacement.open()
            assert record["id"] not in replacement.records
        finally:
            await replacement.close()

    client.portal.call(check)


def test_purge_does_not_stop_another_owners_active_work(owned_client, monkeypatch):
    client, _ = owned_client
    runner = client.app.state.experiments
    monkeypatch.setattr(runner, "_schedule", lambda: None)

    async def check():
        own = seed(runner, "user:" + A)
        other = seed(runner, "user:" + B, status="running")
        task = asyncio.create_task(asyncio.Event().wait())
        runner.active, runner.running_id = task, other["id"]
        try:
            await asyncio.wait_for(runner.forget_owners({"user:" + A}), 1)
            assert not task.done()
            assert other["id"] in runner.records
            assert own["id"] not in runner.records
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    client.portal.call(check)
