import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from prodog_gmail.app import COOKIE, create_app
from prodog_gmail.config import Settings
from prodog_gmail.errors import ConnectorError
from prodog_gmail.pipeline import MailPipeline

BASE = "http://127.0.0.1:8765"


def test_post_collection_does_not_run_legacy_detector_twice(configured, monkeypatch):
    client, calls = configured

    def legacy_scan(*_):
        pytest.fail("POST collection must use only the queued static detector")

    monkeypatch.setattr("prodog_gmail.app.scan_mail", legacy_scan)
    assert post(client, "/api/mails?limit=3").status_code == 200
    wait_results(client)
    assert len(calls["static"]) == 3 and len(calls["dynamic"]) == 1


def test_new_vm_target_creates_new_analysis_for_same_mail(configured):
    client, _calls = configured
    first = post(client, "/api/mails?limit=1").json()["messages"][0]["analysis"]["id"]
    client.app.state.settings.sandbox_docker_context = "prodog-vm"
    second = post(client, "/api/mails?limit=1").json()["messages"][0]["analysis"]["id"]
    assert first != second


def test_pending_model_refresh_cannot_reenable_automatic_analysis(configured, monkeypatch):
    client, calls = configured
    entered, release = threading.Event(), threading.Event()

    async def delayed_validation(*_):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.001)
        return "new-revision"

    monkeypatch.setattr(MailPipeline, "validate_model", delayed_validation)
    responses = []
    thread = threading.Thread(target=lambda: responses.append(post(client, "/api/mails?limit=3")))
    thread.start()
    try:
        assert entered.wait(3)
        assert (
            post(
                client, "/api/analysis/settings", {"enabled": False, "model": "test-model"}
            ).status_code
            == 200
        )
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive() and responses[0].status_code == 200
    with client.app.state.store.connection() as conn:
        account_id = conn.execute("SELECT account_id FROM analysis_settings").fetchone()[0]
    assert client.app.state.pipeline.settings(account_id)["enabled"] is False
    results = wait_results(client)
    assert calls["dynamic"] == []
    assert all(r["status"] == "skipped" for r in results)


def test_auto_configuration_explains_thinking_only_models():
    async def check():
        def response(request):
            return httpx.Response(
                200,
                json={
                    "capabilities": ["tools", "thinking"],
                    "thinking": {"values": [True], "default": True},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            pipeline = SimpleNamespace(runner=SimpleNamespace(client=client))
            with pytest.raises(ConnectorError, match="thinking_only_unsupported") as caught:
                await MailPipeline.validate_model(pipeline, "qwen3:4b")
            assert "qwen3:4b-instruct" in caught.value.message

    asyncio.run(check())


def post(client, path, payload=None):
    csrf = client.get("/api/session").json()["csrf_token"]
    return client.post(path, json=payload, headers={"Origin": BASE, "X-CSRF-Token": csrf})


@pytest.fixture
def configured(tmp_path, monkeypatch):
    calls = {"static": [], "dynamic": []}

    class Detector:
        def __init__(self, _):
            pass

        def status(self):
            return {"ready": True, "model_id": "test-artifact", "message": "ready"}

        async def scan(self, mail):
            calls["static"].append(mail["id"])
            danger = mail["id"] == "demo-2"
            return {
                "status": "completed",
                "score": 0.9 if danger else 0.01,
                "verdict": "danger" if danger else "safe",
                "model_id": "test-artifact",
                "suspicious_threshold": 0.1,
                "danger_threshold": 0.8,
                "error": None,
            }

    async def validate_model(*_):
        pass

    async def execute(self, record):
        calls["dynamic"].append(record)
        record.update(
            status="completed",
            finished_at="test",
            observations={
                "canary_leaked": True,
                "summary_file_present": True,
                "complete": True,
                "files": [],
                "changes": [],
            },
        )
        self.save(record)

    monkeypatch.setattr("prodog_gmail.pipeline.StaticDetector", Detector)
    monkeypatch.setattr("prodog_gmail.pipeline.MailPipeline.validate_model", validate_model)
    monkeypatch.setattr("prodog_gmail.experiments.Experiments.execute", execute)
    settings = Settings(
        _env_file=None,
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "test.db",
    )
    with TestClient(create_app(settings), base_url=BASE) as client:
        assert post(client, "/api/demo/connect").status_code == 200
        assert (
            post(
                client, "/api/analysis/settings", {"enabled": True, "model": "test-model"}
            ).status_code
            == 200
        )
        yield client, calls


def wait_results(client):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get("/api/analysis?message_ids=demo-1,demo-2,demo-3").json()["analyses"]
        if len(data) == 3 and all(
            r["status"] in {"skipped", "completed", "static_error", "error", "cancelled"}
            for r in data
        ):
            return data
        time.sleep(0.01)
    pytest.fail("pipeline did not finish")


def test_collection_to_static_gate_to_dynamic_and_dedupe(configured):
    client, calls = configured
    assert client.post("/api/mails").status_code == 403
    response = post(client, "/api/mails?limit=3")
    assert response.status_code == 200
    results = wait_results(client)
    assert len(calls["static"]) == 3
    assert len(calls["dynamic"]) == 1
    danger = next(r for r in results if r["gmail_message_id"] == "demo-2")
    assert danger["static"]["verdict"] == "danger"
    assert danger["dynamic"]["observations"]["canary_leaked"] is True
    record = calls["dynamic"][0]
    assert record["provider"] == "ollama" and record["model"] == "test-model"
    assert "score" not in record["task"] and "악성" not in record["task"]
    assert record["source"]["kind"] == "mail_pipeline"
    for _ in range(2):
        assert post(client, "/api/mails?limit=3").status_code == 200
        assert client.get("/api/mails?limit=3").status_code == 200
    assert len(calls["static"]) == 3 and len(calls["dynamic"]) == 1


def test_transformer_risk_alone_enqueues_dynamic_and_preserves_individual_results(configured):
    client, calls = configured
    pipeline = client.app.state.pipeline

    async def scan(mail):
        lexical = {
            "score": 0.04,
            "verdict": "safe",
            "suspicious_threshold": 0.05,
            "danger_threshold": 0.4,
        }
        semantic = {
            "score": 0.03,
            "verdict": "danger",
            "suspicious_threshold": 0.01,
            "danger_threshold": 0.02,
        }
        return {
            **semantic,
            "status": "completed",
            "model_id": "test-artifact",
            "error": None,
            "mode": "ensemble",
            "decided_by": ["transformer"],
            "detectors": {"tfidf": lexical, "transformer": semantic},
        }

    pipeline.detector.scan = scan
    assert post(client, "/api/mails?limit=3").status_code == 200
    results = wait_results(client)
    assert len(calls["dynamic"]) == 3
    assert all(r["status"] == "completed" for r in results)
    assert all(r["static"]["detectors"]["tfidf"]["verdict"] == "safe" for r in results)
    assert all(r["static"]["decided_by"] == ["transformer"] for r in results)


def test_collection_refreshes_model_revision_before_reusing_completed_results(
    configured, monkeypatch
):
    client, calls = configured
    post(client, "/api/mails?limit=3")
    wait_results(client)

    async def changed(*_):
        return "replacement-model-revision"

    monkeypatch.setattr(MailPipeline, "validate_model", changed)
    post(client, "/api/mails?limit=3")
    wait_results(client)
    assert len(calls["static"]) == 6 and len(calls["dynamic"]) == 2
    assert calls["dynamic"][-1]["source"]["model_revision"] == "replacement-model-revision"


def test_analysis_owner_is_account_scoped(configured):
    client, _ = configured
    post(client, "/api/mails?limit=3")
    results = wait_results(client)
    run_id = next(r["dynamic"]["id"] for r in results if r["dynamic"])
    store = client.app.state.store
    bob = store.account("bob@example.invalid", "demo")
    secret = store.new_session(bob)
    client.cookies.clear()
    client.cookies.set(COOKIE, secret)
    assert client.get("/api/analysis?message_ids=demo-2").json()["analyses"] == []
    assert client.get("/api/experiments/" + run_id).status_code == 404


def test_read_only_is_inert_and_disabling_dynamic_still_runs_static(configured):
    client, calls = configured
    assert client.get("/api/mails?limit=3").status_code == 200
    assert calls["static"] == []
    assert (
        post(
            client, "/api/analysis/settings", {"enabled": False, "model": "test-model"}
        ).status_code
        == 200
    )
    post(client, "/api/mails?limit=3")
    results = wait_results(client)
    assert len(calls["static"]) == 3 and calls["dynamic"] == []
    assert all(r["status"] == "skipped" for r in results)
    danger = next(r for r in results if r["gmail_message_id"] == "demo-2")
    assert danger["static"]["verdict"] == "danger"
    assert danger["skip_reason"] == "dynamic_disabled"


def test_real_mail_payload_not_written_in_plaintext(configured):
    client, _ = configured
    post(client, "/api/mails?limit=3")
    wait_results(client)
    for path in client.app.state.experiments.directory.glob("*.json"):
        data = json.loads(path.read_text())
        assert set(data) == {"encrypted"}
    with client.app.state.store.connection() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM analysis_jobs WHERE payload IS NOT NULL").fetchone()[
                0
            ]
            == 0
        )


def test_static_failure_does_not_enter_dynamic_queue(configured, monkeypatch):
    client, calls = configured

    async def fail(_):
        return {
            "status": "error",
            "score": None,
            "verdict": None,
            "model_id": "test-artifact",
            "error": {"code": "static_not_ready", "message": "model missing"},
        }

    monkeypatch.setattr(client.app.state.pipeline.detector, "scan", fail)
    post(client, "/api/mails?limit=3")
    results = wait_results(client)
    assert all(r["status"] == "static_error" and r["static"]["verdict"] is None for r in results)
    assert calls["dynamic"] == []


def test_dynamic_error_keeps_static_danger(configured, monkeypatch):
    client, _ = configured

    async def fail(self, record):
        record.update(
            status="error",
            finished_at="test",
            error={"code": "model_timeout", "message": "timeout"},
        )
        self.save(record)

    monkeypatch.setattr("prodog_gmail.experiments.Experiments.execute", fail)
    post(client, "/api/mails?limit=3")
    danger = next(r for r in wait_results(client) if r["gmail_message_id"] == "demo-2")
    assert danger["status"] == "error" and danger["static"]["verdict"] == "danger"


def test_disable_during_static_analysis_prevents_dynamic_and_reenable_is_new_config(
    configured, monkeypatch
):
    client, calls = configured
    started, release = threading.Event(), threading.Event()
    original = client.app.state.pipeline.detector.scan

    async def slow(mail):
        import asyncio

        started.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        return await original(mail)

    monkeypatch.setattr(client.app.state.pipeline.detector, "scan", slow)
    post(client, "/api/mails?limit=3")
    try:
        assert started.wait(1)
        assert (
            post(
                client, "/api/analysis/settings", {"enabled": False, "model": "test-model"}
            ).status_code
            == 200
        )
        assert all(r["status"] == "cancelled" for r in wait_results(client))
        assert calls["dynamic"] == []
    finally:
        release.set()
    assert (
        post(client, "/api/analysis/settings", {"enabled": True, "model": "test-model"}).status_code
        == 200
    )
    post(client, "/api/mails?limit=3")
    result = wait_results(client)
    assert len(calls["dynamic"]) == 1
    assert any(r["status"] == "completed" for r in result)


def test_logout_then_login_retries_cancelled_mail_but_refresh_does_not(configured, monkeypatch):
    client, calls = configured
    started, release = threading.Event(), threading.Event()
    original = client.app.state.pipeline.detector.scan

    async def slow(mail):
        import asyncio

        started.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        return await original(mail)

    monkeypatch.setattr(client.app.state.pipeline.detector, "scan", slow)
    post(client, "/api/mails?limit=3")
    try:
        assert started.wait(1)
        old = client.get("/api/analysis?message_ids=demo-2").json()["analyses"][0]["id"]
        assert post(client, "/api/logout").status_code == 200
        assert post(client, "/api/demo/connect").status_code == 200
        post(client, "/api/mails?limit=3")
    finally:
        release.set()
    results = wait_results(client)
    new = next(r for r in results if r["gmail_message_id"] == "demo-2")
    assert new["id"] != old and new["status"] == "completed"
    count = len(calls["static"])
    post(client, "/api/mails?limit=3")
    assert len(calls["static"]) == count and len(calls["dynamic"]) == 1


def test_invalidated_google_grant_is_not_eligible_for_queued_work(configured):
    client, _ = configured
    store = client.app.state.store
    account = store.account("google@example.invalid", "google")
    store.save_tokens(account, {"refresh_token": "test", "needs_reconnect": False})
    assert client.app.state.pipeline.account_ready(account)
    store.invalidate_tokens(account)
    assert not client.app.state.pipeline.account_ready(account)


def test_cancel_retry_between_two_valid_sessions_reuses_prior_attempt(configured, monkeypatch):
    client, _ = configured
    release = threading.Event()
    original = client.app.state.pipeline.detector.scan

    async def slow(mail):
        import asyncio

        while not release.is_set():
            await asyncio.sleep(0.005)
        return await original(mail)

    monkeypatch.setattr(client.app.state.pipeline.detector, "scan", slow)
    first = client.cookies.get(COOKIE)
    account = client.app.state.store.session(first)["account_id"]
    second = client.app.state.store.new_session(account)
    attempts = []
    try:
        for secret in (first, second, first, second):
            client.cookies.clear()
            client.cookies.set(COOKIE, secret)
            response = post(client, "/api/mails?limit=3")
            assert response.status_code == 200
            attempts.append(
                next(
                    m["analysis"]["id"] for m in response.json()["messages"] if m["id"] == "demo-2"
                )
            )
            client.portal.call(client.app.state.pipeline.cancel_account, account)
        assert attempts[1] == attempts[3]
    finally:
        release.set()
