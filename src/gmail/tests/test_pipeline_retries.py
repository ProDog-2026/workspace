import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_pipeline import configured as configured
from test_pipeline import post, wait_results

from prodog_gmail.app import COOKIE
from prodog_gmail.errors import ConnectorError


def test_post_collection_retries_static_failure_after_recovery(configured, monkeypatch):
    client, _ = configured
    pipeline = client.app.state.pipeline
    scan = pipeline.detector.scan

    async def fail(_):
        return {
            "status": "error",
            "verdict": None,
            "model_id": "test-artifact",
            "error": {"code": "static_not_ready", "message": "synthetic failure"},
        }

    monkeypatch.setattr(pipeline.detector, "scan", fail)
    assert post(client, "/api/mails?limit=3").status_code == 200
    failed = wait_results(client)
    assert all(result["status"] == "static_error" for result in failed)
    old_ids = {result["id"] for result in failed}
    # Reading results must not schedule a retry.
    assert {result["id"] for result in wait_results(client)} == old_ids
    monkeypatch.setattr(pipeline.detector, "scan", scan)
    response = post(client, "/api/mails?limit=3")
    assert response.status_code == 200
    assert all(mail["analysis"]["id"] not in old_ids for mail in response.json()["messages"])
    recovered = wait_results(client)
    assert all(result["status"] in {"skipped", "completed"} for result in recovered)
    assert next(r for r in recovered if r["gmail_message_id"] == "demo-2")["status"] == "completed"
    assert {r["id"] for r in wait_results(client)}.isdisjoint(old_ids)
    assert post(client, "/api/mails?limit=3").status_code == 200
    assert {r["id"] for r in wait_results(client)} == {r["id"] for r in recovered}


@pytest.mark.parametrize("status", ["error", "failed", "timeout", "step_limit"])
@pytest.mark.parametrize("persist_finished", [True, False])
def test_post_collection_retries_dynamic_failure_with_new_source(
    configured, monkeypatch, status, persist_finished
):
    client, _ = configured
    pipeline, runner = client.app.state.pipeline, client.app.state.experiments
    execute = type(runner).execute
    finished = runner.on_finished

    async def fail(self, record):
        record.update(
            status=status,
            finished_at="test",
            error={"code": "synthetic_failure", "message": "synthetic failure"},
        )
        self.save(record)

    monkeypatch.setattr(type(runner), "execute", fail)
    if not persist_finished:
        monkeypatch.setattr(runner, "on_finished", lambda _: None)
    assert post(client, "/api/mails?limit=3").status_code == 200
    client.portal.call(pipeline.queue.join)
    old = next(iter(runner.records.values()))

    # Await the fake execution through the runner's real task, including its callback.
    async def settle():
        if runner.active:
            await runner.active

    client.portal.call(settle)
    previous = pipeline.latest(old["owner"].removeprefix("account:"), "demo-2")
    assert previous["status"] == ("error" if status in {"timeout", "step_limit"} else status)
    if not persist_finished:
        assert json.loads(pipeline.job(previous["id"])["data_json"])["status"] == "dynamic_queued"
    monkeypatch.setattr(type(runner), "execute", execute)
    monkeypatch.setattr(runner, "on_finished", finished)
    response = post(client, "/api/mails?limit=3")
    assert response.status_code == 200
    new = next(m["analysis"] for m in response.json()["messages"] if m["id"] == "demo-2")
    assert new["id"] != previous["id"]
    recovered = next(r for r in wait_results(client) if r["gmail_message_id"] == "demo-2")
    assert recovered["id"] == new["id"] and recovered["status"] == "completed"
    assert recovered["dynamic"]["id"] != old["id"]
    assert runner.records[recovered["dynamic"]["id"]]["source"]["job_id"] == new["id"]
    assert post(client, "/api/mails?limit=3").status_code == 200
    assert (
        next(r for r in wait_results(client) if r["gmail_message_id"] == "demo-2")["id"]
        == new["id"]
    )
    assert len(runner.records) == 2


@pytest.fixture
def paused_pipeline(configured):
    client, _ = configured
    pipeline = client.app.state.pipeline
    client.portal.call(pipeline.close)
    secret = client.cookies.get(COOKIE)
    account = pipeline.store.session(secret)["account_id"]
    mail = {"id": "synthetic", "analysis_text": "synthetic body", "body_hash": "synthetic"}
    return pipeline, account, secret, mail


@pytest.mark.parametrize(
    "status",
    [
        "static_queued",
        "static_running",
        "dynamic_queued",
        "dynamic_running",
        "completed",
        "skipped",
        "cancelled",
    ],
)
def test_submit_reuses_active_successful_and_same_session_cancelled_jobs(paused_pipeline, status):
    pipeline, account, secret, mail = paused_pipeline
    first = pipeline.submit(account, mail, secret)
    pipeline.write(first["id"], {**first, "status": status})
    assert pipeline.submit(account, mail, secret)["id"] == first["id"]
    assert pipeline.queue.qsize() == 1


def test_concurrent_submits_share_latest_retry_and_each_failure_allows_new_attempt(paused_pipeline):
    pipeline, account, secret, mail = paused_pipeline
    previous = pipeline.submit(account, mail, secret)
    ids = {previous["id"]}
    for status in ("static_error", "error", "failed"):
        pipeline.write(previous["id"], {**previous, "status": status}, clear=True)
        barrier = threading.Barrier(4)

        def submit(_, barrier=barrier):
            barrier.wait(timeout=3)
            return pipeline.submit(account, mail, secret)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(submit, range(4)))
        assert len({result["id"] for result in results}) == 1
        previous = results[0]
        assert previous["id"] not in ids
        ids.add(previous["id"])
        assert pipeline.submit(account, mail, secret)["id"] == previous["id"]
        assert pipeline.latest(account, mail["id"])["id"] == previous["id"]
    with pipeline.store.connection() as conn:
        rows = conn.execute("SELECT config_hash,data_json FROM analysis_jobs").fetchall()
    assert len(rows) == len(ids) == pipeline.queue.qsize() == 4
    assert len({row["config_hash"] for row in rows}) == 4
    assert len({json.loads(row["data_json"])["base_hash"] for row in rows}) == 1


def test_cancelled_retry_is_not_rerun_when_switching_between_valid_sessions(paused_pipeline):
    pipeline, account, first, mail = paused_pipeline
    failed = pipeline.submit(account, mail, first)
    pipeline.write(failed["id"], {**failed, "status": "error"}, clear=True)
    retry = pipeline.submit(account, mail, first)
    assert retry["id"] != failed["id"]
    pipeline.cancel_account(account)
    assert pipeline.submit(account, mail, first)["id"] == retry["id"]
    second = pipeline.store.new_session(account)
    other = pipeline.submit(account, mail, second)
    assert other["id"] != retry["id"]
    pipeline.cancel_account(account)
    for secret, expected in ((first, retry), (second, other)):
        result = pipeline.submit(account, mail, secret)
        assert result["id"] == expected["id"] and result["status"] == "cancelled"


def test_retry_obeys_capacity_and_pending_retry_reuses_at_capacity(paused_pipeline):
    pipeline, account, secret, mail = paused_pipeline
    failed = pipeline.submit(account, mail, secret)
    pipeline.write(failed["id"], {**failed, "status": "static_error"}, clear=True)
    for index in range(100):
        queued = pipeline.submit(account, {**mail, "id": f"capacity-{index}"}, secret)
    with pytest.raises(ConnectorError) as caught:
        pipeline.submit(account, mail, secret)
    assert caught.value.code == "analysis_queue_full"
    assert pipeline.latest(account, mail["id"])["id"] == failed["id"]
    pipeline.write(queued["id"], {**queued, "status": "completed"}, clear=True)
    retry = pipeline.submit(account, mail, secret)
    assert retry["id"] != failed["id"]
    assert pipeline.submit(account, mail, secret)["id"] == retry["id"]
    with pipeline.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 102
        assert (
            conn.execute("SELECT COUNT(*) FROM analysis_jobs WHERE payload IS NOT NULL").fetchone()[
                0
            ]
            == 100
        )
