"""The public Gmail API gates and owns dynamic executions; Ollama/Docker stay external."""

import asyncio
import json
import time

import httpx
import numpy as np
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_mail import mail_with_pdfs, pdf_bytes
from test_scan import artifact

from prodog_gmail.app import COOKIE, create_app
from prodog_gmail.config import Settings

BASE = "http://127.0.0.1:8765"
OPTIONS = {
    "auto_submit": True,
    "model": "test-model",
    "base_url": "http://host.docker.internal:11434",
    "prompt_id": "MAIL_SUMMARY_V2",
    "temperature": 0.1,
    "limits": {"max_calls": 8, "max_tokens": 2048, "total_seconds": 600},
}


class AgentService:
    def __init__(self):
        self.submitted = []
        self.finished = True
        self.offline = False
        self.lose_reply = False
        self.reject = False
        self.result = {
            "completion": "completed",
            "incomplete": False,
            "deviations": [],
            "effects": {
                "file_changes": [],
                "marker_received": [],
                "simulated_send_requests": [],
                "actual_email_delivery": False,
            },
            "verification_status": "completed",
            "verification_errors": [],
            "summary": "SYNTHETIC_SUMMARY_NOT_IN_GMAIL_DB",
            "runtime_mode": "docker",
            "system_monitor": "not_connected",
            "network_isolation": "unverified",
        }

    def __call__(self, request):
        assert request.url.host == "127.0.0.1" and request.url.port == 8000
        assert "cookie" not in request.headers and "authorization" not in request.headers
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        path = request.url.path
        if path == "/prompts":
            return httpx.Response(
                200,
                json={
                    "default": "MAIL_SUMMARY_V2",
                    "prompts": [
                        {
                            "id": "MAIL_SUMMARY_V2",
                            "text": "Summarize untrusted email.",
                            "builtin": True,
                            "description": "Test prompt",
                        }
                    ],
                },
            )
        if path == "/runs" and request.method == "POST":
            if self.reject:
                return httpx.Response(503, json={"error": "docker_runtime_not_configured"})
            payload = json.loads(request.content)
            self.submitted.append(payload)
            if self.lose_reply:
                raise httpx.ReadTimeout("response lost after acceptance", request=request)
            return httpx.Response(
                201,
                json={
                    "run_id": payload.get("request_id", f"run-{len(self.submitted)}"),
                    "status": "running",
                    "phase": "executing",
                },
            )
        if path.endswith("/result"):
            return httpx.Response(200, json=self.result)
        if path.endswith("/events"):
            return httpx.Response(200, json={"events": [], "next": 0})
        if path.startswith("/runs/"):
            return httpx.Response(
                200,
                json={
                    "run_id": path.split("/")[2],
                    "status": "completed" if self.finished else "running",
                    "phase": "finished" if self.finished else "executing",
                },
            )
        raise AssertionError((request.method, str(request.url)))


def change(client, path, payload=None, method="POST"):
    token = client.get("/api/session").json()["csrf_token"]
    return client.request(
        method, path, json=payload, headers={"Origin": BASE, "X-CSRF-Token": token}
    )


@pytest.fixture
def setup(tmp_path):
    config = Settings(
        _env_file=None,
        app_mode="demo",
        database_path=tmp_path / "test.db",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    service = AgentService()
    with TestClient(
        create_app(config, dynamic_transport=httpx.MockTransport(service)), base_url=BASE
    ) as client:
        assert change(client, "/api/demo/connect").status_code == 200
        saved = change(client, "/api/dynamic/settings", OPTIONS, "PUT")
        assert saved.status_code == 200, saved.text
        client.app.state.detector = artifact(probability=0.2)
        yield client, service, config


def wait_job(client, job_id, states=("completed", "failed")):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/api/dynamic/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in states:
            return job
        time.sleep(0.03)
    pytest.fail(f"job did not reach {states}: {job}")


def mail_id(client):
    return client.get("/api/mails").json()["messages"][0]["id"]


def test_get_never_starts_container(setup):
    client, service, _ = setup
    mail_id(client)
    assert client.get("/api/dynamic/jobs").json()["jobs"] == []
    assert service.submitted == []


def test_manual_gmail_run_bypasses_static_classifier_and_keeps_automatic_gate(setup):
    client, service, _ = setup
    client.app.state.detector = None
    mid = mail_id(client)
    assert change(client, f"/api/dynamic/mail/{mid}").json()["reason"] == "static_not_ready"
    assert client.post(f"/api/dynamic/manual-mail/{mid}").status_code == 403
    response = change(client, f"/api/dynamic/manual-mail/{mid}")
    assert response.status_code == 202
    job = wait_job(client, response.json()["job"]["id"])
    assert job["source"] == "gmail_manual"
    assert job["static_detection"] is None
    assert job["mail_id"] == mid
    assert len(service.submitted) == 1
    assert "UNTRUSTED EMAIL DATA" in service.submitted[0]["email"]["body"]
    assert change(client, f"/api/dynamic/manual-mail/{mid}").json()["job"]["id"] != job["id"]


def test_manual_gmail_requires_connected_account(setup):
    client, service, _ = setup
    mid = mail_id(client)
    client.cookies.set(COOKIE, client.app.state.store.new_session(), domain="127.0.0.1", path="/")
    assert change(client, f"/api/dynamic/manual-mail/{mid}").status_code == 401
    assert service.submitted == []


def test_deployment_endpoint_requires_csrf_and_returns_progress(setup):
    client, _, _ = setup

    class FakeDeployment:
        async def status(self):
            return {"state": "running", "stage": "building"}

        async def start(self):
            return {"state": "running", "stage": "building"}

    client.app.state.deployment = FakeDeployment()
    assert client.post("/api/dynamic/deployment").status_code == 403
    assert client.get("/api/dynamic/deployment").json()["state"] == "running"
    assert change(client, "/api/dynamic/deployment").json()["stage"] == "building"


@pytest.mark.parametrize(("probability", "verdict"), [(0.2, "suspicious"), (0.9, "danger")])
def test_flagged_mail_is_submitted_once_and_preserves_static_result(setup, probability, verdict):
    client, service, config = setup
    client.app.state.detector = artifact(probability=probability)
    mail, other = client.get("/api/mails?limit=2").json()["messages"]
    mid = mail["id"]
    client.app.state.detector["pipeline"].error = RuntimeError("second inference unavailable")
    first = change(client, f"/api/dynamic/mail/{mid}")
    assert first.status_code == 202, first.text
    jid = first.json()["job"]["id"]
    second = change(client, f"/api/dynamic/mail/{mid}")
    assert second.json()["job"]["id"] == jid
    job = wait_job(client, jid)
    assert job["status"] == "completed"
    assert job["observation"] == "not_observed"
    assert job["mail_id"] == mid
    assert job["body_hash"] == mail["body_hash"]
    assert job["static_detection"]["verdict"] == verdict
    assert job["static_detection"] == mail["detection"]
    assert len(service.submitted) == 1
    assert service.submitted[0]["config"]["model"] == "test-model"
    assert "UNTRUSTED" in service.submitted[0]["email"]["body"]
    assert "SYNTHETIC_SUMMARY_NOT_IN_GMAIL_DB" not in config.database_path.read_bytes().decode(
        errors="ignore"
    )
    row, other_row = client.get("/api/mails?limit=2").json()["messages"]
    assert row["detection"] == mail["detection"]
    assert row["dynamic"]["id"] == jid
    assert other_row["id"] == other["id"]
    assert other_row["dynamic"] is None


@pytest.mark.parametrize(("probability", "verdict"), [(0.2, "suspicious"), (0.9, "danger")])
def test_explicit_start_works_without_automatic_handoff_and_reuses_job(setup, probability, verdict):
    client, service, _ = setup
    client.app.state.detector = artifact(probability=probability)
    assert change(
        client, "/api/dynamic/settings", OPTIONS | {"auto_submit": False}, "PUT"
    ).status_code == 200
    path = f"/api/dynamic/mail/{mail_id(client)}"
    assert change(client, path).json()["reason"] == "automatic_disabled"
    assert client.post(path + "?start=true").status_code == 403

    response = change(client, path + "?start=true")
    assert response.status_code == 202
    job = wait_job(client, response.json()["job"]["id"])
    assert job["static_detection"]["verdict"] == verdict
    assert change(client, path + "?start=true").json()["job"]["id"] == job["id"]
    assert len(service.submitted) == 1


def test_pdf_text_is_classified_before_the_same_mail_enters_dynamic_queue(setup, monkeypatch):
    client, service, config = setup
    pdf_note = "Synthetic project agenda from PDF"
    raw = mail_with_pdfs(pdf_bytes(pdf_note))
    monkeypatch.setattr("prodog_gmail.app.demo_messages", lambda: [raw])

    class AgendaScore:
        def predict_proba(self, texts):
            return np.array([[0.1, 0.9] if pdf_note in text else [0.8, 0.2] for text in texts])

    client.app.state.detector = artifact() | {"pipeline": AgendaScore()}
    with monkeypatch.context() as transient:
        transient.setattr("prodog_gmail.mail.extract_pdf_text", lambda *_: ("limit", ""))
        limited = client.get("/api/mails").json()["messages"][0]
        assert limited["attachments"][0]["pdf_status"] == "limit"
        assert limited["detection"]["static_score"] == 20
    refresh = change(client, f"/api/dynamic/mail/{limited['id']}?start=true")
    assert refresh.status_code == 202, refresh.text

    response = client.get("/api/mails")
    mail = response.json()["messages"][0]
    assert mail["attachments"][0]["pdf_status"] == "parsed"
    assert mail["detection"]["verdict"] == "suspicious"
    assert mail["detection"]["pdf_results"][0]["verdict"] == "danger"
    assert pdf_note not in response.text
    assert pdf_note not in client.get(f"/api/mails/{mail['id']}").text
    job = wait_job(client, refresh.json()["job"]["id"])
    assert job["status"] == "completed"
    assert job["static_detection"] == mail["detection"]
    assert job["pdf_attachments_count"] == 1
    assert pdf_note in service.submitted[0]["email"]["body"]
    assert pdf_note.encode() not in config.database_path.read_bytes()
    result = client.get("/api/mails").json()["messages"][0]
    assert result["dynamic"]["id"] == job["id"]
    assert result["detection"] == mail["detection"]

    # A transient extraction failure cannot pair the cached verdict with different input.
    monkeypatch.setattr("prodog_gmail.mail.extract_pdf_text", lambda *_: ("limit", ""))
    retry = change(client, f"/api/dynamic/mail/{mail['id']}?retry=true")
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "pdf_extraction_changed"
    cached = client.get("/api/mails").json()["messages"][0]
    assert cached["attachments"][0]["pdf_status"] == "parsed"
    assert cached["detection"] == mail["detection"]
    assert cached["dynamic"]["id"] == job["id"]




def test_pdf_scan_failure_retries_after_dynamic_start(setup, monkeypatch):
    client, _, _ = setup
    pdf_note = "Synthetic attachment to retry"
    raw = mail_with_pdfs(pdf_bytes(pdf_note))
    monkeypatch.setattr("prodog_gmail.app.demo_messages", lambda: [raw])

    class IntermittentPdfScore:
        fail_pdf = True

        def predict_proba(self, texts):
            if self.fail_pdf and any(pdf_note in text for text in texts):
                raise RuntimeError("PDF scan failed")
            return np.array([[0.8, 0.2] for _ in texts])

    pipeline = IntermittentPdfScore()
    client.app.state.detector = artifact() | {"pipeline": pipeline}
    with monkeypatch.context() as transient:
        transient.setattr("prodog_gmail.mail.extract_pdf_text", lambda *_: ("limit", ""))
        mail = client.get("/api/mails").json()["messages"][0]

    response = change(client, f"/api/dynamic/mail/{mail['id']}?start=true")
    assert response.status_code == 202, response.text
    assert response.json()["job"]["static_detection"]["pdf_results"][0]["status"] == "failed"
    pipeline.fail_pdf = False
    retry = change(client, f"/api/dynamic/mail/{mail['id']}?start=true")
    assert retry.status_code == 202, retry.text
    refreshed = client.get("/api/mails").json()["messages"][0]
    assert refreshed["detection"]["pdf_results"][0]["status"] == "ok"



def test_body_change_with_failed_pdf_scan_can_be_retried(setup, monkeypatch):
    client, _, _ = setup
    pdf_note = "Synthetic PDF scan retry"
    raw = mail_with_pdfs(pdf_bytes(pdf_note))
    monkeypatch.setattr("prodog_gmail.app.demo_messages", lambda: [raw])

    class ChangingScore:
        changed = False
        fail_pdf = True

        def predict_proba(self, texts):
            if self.fail_pdf and any(pdf_note in text for text in texts):
                raise RuntimeError("PDF scan failed")
            probability = 0.9 if self.changed else 0.2
            return np.array([[1 - probability, probability] for _ in texts])

    pipeline = ChangingScore()
    client.app.state.detector = artifact() | {"pipeline": pipeline}
    with monkeypatch.context() as transient:
        transient.setattr("prodog_gmail.mail.extract_pdf_text", lambda *_: ("limit", ""))
        mail = client.get("/api/mails").json()["messages"][0]
    assert mail["attachments"][0]["pdf_status"] == "limit"
    assert mail["detection"]["verdict"] == "suspicious"
    pipeline.changed = True
    failed = change(client, f"/api/dynamic/mail/{mail['id']}?start=true")
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "static_scan_failed"
    pipeline.fail_pdf = False
    changed = change(client, f"/api/dynamic/mail/{mail['id']}?start=true")
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "static_result_changed"
    refreshed = client.get("/api/mails").json()["messages"][0]
    assert refreshed["detection"]["verdict"] == "danger"


def test_changed_body_verdict_requires_refresh_before_dynamic_submission(setup, monkeypatch):
    client, service, _ = setup
    raw = mail_with_pdfs(pdf_bytes("Synthetic PDF"))
    monkeypatch.setattr("prodog_gmail.app.demo_messages", lambda: [raw])

    class ChangingScore:
        probability = 0.2

        def predict_proba(self, texts):
            return np.array([[1 - self.probability, self.probability] for _ in texts])

    pipeline = ChangingScore()
    client.app.state.detector = artifact() | {"pipeline": pipeline}
    with monkeypatch.context() as transient:
        transient.setattr("prodog_gmail.mail.extract_pdf_text", lambda *_: ("limit", ""))
        mail = client.get("/api/mails").json()["messages"][0]
    assert mail["detection"]["verdict"] == "suspicious"

    pipeline.probability = 0.9
    response = change(client, f"/api/dynamic/mail/{mail['id']}?start=true")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "static_result_changed"
    refreshed = client.get("/api/mails").json()["messages"][0]
    assert refreshed["detection"]["verdict"] == "danger"
    assert service.submitted == []


def test_linked_result_remains_visible_after_a_standalone_experiment(setup):
    client, _, _ = setup
    mid = mail_id(client)
    linked = change(client, f"/api/dynamic/mail/{mid}").json()["job"]
    wait_job(client, linked["id"])
    manual = change(client, f"/api/dynamic/manual-mail/{mid}").json()["job"]
    wait_job(client, manual["id"])

    row = client.get("/api/mails").json()["messages"][0]
    assert row["dynamic"]["id"] == linked["id"]
    assert row["dynamic"]["static_detection"] == row["detection"]


@pytest.mark.parametrize("probability", [None, 0.01])
def test_safe_or_unconfigured_mail_stays_out_of_dynamic_queue(setup, probability):
    client, service, _ = setup
    client.app.state.detector = None if probability is None else artifact(probability=probability)
    path = f"/api/dynamic/mail/{mail_id(client)}"
    for suffix in ("", "?start=true", "?retry=true"):
        response = change(client, path + suffix)
        assert response.status_code == 200
        assert response.json()["reason"] == ("static_not_ready" if probability is None
                                             else "not_flagged")
    assert service.submitted == []


def test_foreign_account_cannot_read_or_stop_a_job(setup):
    client, _, _ = setup
    jid = change(client, f"/api/dynamic/mail/{mail_id(client)}").json()["job"]["id"]
    store = client.app.state.store
    other = store.account("other@example.invalid", "demo")
    client.cookies.set(COOKIE, store.new_session(other), domain="127.0.0.1", path="/")
    for suffix in ("", "/result", "/events", "/flow"):
        assert client.get(f"/api/dynamic/jobs/{jid}{suffix}").status_code == 404
    assert change(client, f"/api/dynamic/jobs/{jid}/stop").status_code == 404
    assert client.get("/api/dynamic/jobs").json()["jobs"] == []


def test_timeout_is_incomplete_and_never_normal(setup):
    client, service, _ = setup
    service.result.update(completion="timeout", incomplete=True)
    jid = change(client, f"/api/dynamic/mail/{mail_id(client)}").json()["job"]["id"]
    job = wait_job(client, jid, ("incomplete", "failed"))
    assert job["status"] == "incomplete"
    assert job["observation"] == "unknown"
    assert job["static_detection"]["verdict"] == "suspicious"
    row = client.get("/api/mails").json()["messages"][0]
    assert row["detection"] == job["static_detection"]
    assert row["dynamic"]["status"] == "incomplete"


def test_new_static_model_failure_keeps_completed_dynamic_result(setup):
    client, service, _ = setup
    mid = mail_id(client)
    path = f"/api/dynamic/mail/{mid}"
    job = wait_job(client, change(client, path).json()["job"]["id"])
    client.app.state.detector = artifact(error=RuntimeError("model unavailable")) | {
        "created_at": "replacement-model",
    }

    assert change(client, path + "?start=true").json()["reason"] == "static_not_ready"
    row = client.get("/api/mails").json()["messages"][0]
    assert row["detection"]["status"] == "failed"
    assert row["dynamic"]["id"] == job["id"]
    assert row["dynamic"]["status"] == "completed"
    assert len(service.submitted) == 1


def test_csrf_and_local_endpoint_validation(setup):
    client, _, _ = setup
    assert client.put("/api/dynamic/settings", json=OPTIONS).status_code == 403
    assert (
        change(
            client,
            "/api/dynamic/settings",
            OPTIONS | {"base_url": "https://external.example"},
            "PUT",
        ).status_code
        == 422
    )


def test_service_unavailable_returns_actionable_error_without_job(setup):
    client, service, _ = setup
    mid = mail_id(client)
    service.offline = True
    response = change(client, f"/api/dynamic/mail/{mid}")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dynamic_unavailable"
    assert service.submitted == []
    row = client.get("/api/mails").json()["messages"][0]
    assert row["detection"]["verdict"] == "suspicious"
    assert row["dynamic"] is None


def test_only_one_job_runs_until_previous_execution_finishes(setup):
    client, service, _ = setup
    service.finished = False
    messages = client.get("/api/mails").json()["messages"]
    first = change(client, f"/api/dynamic/mail/{messages[0]['id']}").json()["job"]
    second = change(client, f"/api/dynamic/mail/{messages[1]['id']}").json()["job"]
    wait_job(client, first["id"], ("running",))
    assert client.get(f"/api/dynamic/jobs/{second['id']}").json()["status"] == "queued"
    assert len(service.submitted) == 1
    service.finished = True
    wait_job(client, second["id"])
    assert len(service.submitted) == 2


def test_repeat_retry_creates_new_attempt_but_refresh_reuses_latest(setup):
    client, service, _ = setup
    path = f"/api/dynamic/mail/{mail_id(client)}"
    ids = []
    for index in range(3):
        job = change(client, path + ("?retry=true" if index else "")).json()["job"]
        ids.append(job["id"])
        wait_job(client, job["id"])
    assert len(set(ids)) == 3
    assert change(client, path).json()["job"]["id"] == ids[-1]
    assert len(service.submitted) == 3


def test_running_job_recovers_after_backend_restart_without_resubmission(tmp_path):
    config = Settings(
        _env_file=None,
        app_mode="demo",
        database_path=tmp_path / "restart.db",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    service = AgentService()
    service.finished = False
    with TestClient(
        create_app(config, dynamic_transport=httpx.MockTransport(service)), base_url=BASE
    ) as c:
        change(c, "/api/demo/connect")
        change(c, "/api/dynamic/settings", OPTIONS, "PUT")
        c.app.state.detector = artifact(probability=0.2)
        job = change(c, f"/api/dynamic/mail/{mail_id(c)}").json()["job"]
        wait_job(c, job["id"], ("running",))
        cookies = dict(c.cookies)
    service.finished = True
    with TestClient(
        create_app(config, dynamic_transport=httpx.MockTransport(service)), base_url=BASE
    ) as c:
        c.cookies.update(cookies)
        recovered = wait_job(c, job["id"])
        assert recovered["status"] == "completed"
        assert recovered["run_id"] == job["id"]
    assert len(service.submitted) == 1


def test_failed_evidence_verification_is_not_a_clean_completion(setup):
    client, service, _ = setup
    service.result.update(verification_status="failed", verification_errors=["missing_archive"])
    job = change(client, f"/api/dynamic/mail/{mail_id(client)}").json()["job"]
    assert wait_job(client, job["id"], ("incomplete",))["observation"] == "unknown"


def test_html_and_comments_are_preserved_for_dynamic_analysis(setup):
    client, service, _ = setup
    messages = client.get("/api/mails").json()["messages"]
    mid = messages[1]["id"]
    original = client.get(f"/api/mails/{mid}").json()
    job = change(client, f"/api/dynamic/mail/{mid}").json()["job"]
    wait_job(client, job["id"])
    assert original["analysis_text"] in service.submitted[0]["email"]["body"]


def test_lost_submission_reply_reserves_slot_and_recovers_same_run(setup):
    client, service, _ = setup
    service.lose_reply = True
    service.finished = False
    messages = client.get("/api/mails").json()["messages"]
    path = f"/api/dynamic/mail/{messages[0]['id']}"
    first = change(client, path).json()["job"]
    second = change(client, f"/api/dynamic/mail/{messages[1]['id']}").json()["job"]
    running = wait_job(client, first["id"], ("running", "unreachable"))
    assert running["run_id"] == first["id"]
    assert change(client, path + "?retry=true").json()["job"]["id"] == first["id"]
    assert client.get(f"/api/dynamic/jobs/{second['id']}").json()["status"] == "queued"
    assert len(service.submitted) == 1
    service.lose_reply = False
    service.finished = True
    assert wait_job(client, first["id"])["status"] == "completed"
    wait_job(client, second["id"])


def test_explicit_runtime_rejection_remains_retryable(setup):
    client, service, _ = setup
    service.reject = True
    path = f"/api/dynamic/mail/{mail_id(client)}"
    job = change(client, path).json()["job"]
    assert wait_job(client, job["id"])["error_code"] == "docker_runtime_not_configured"
    service.reject = False
    retry = change(client, path + "?retry=true").json()["job"]
    assert retry["id"] != job["id"]
    assert wait_job(client, retry["id"])["status"] == "completed"


def test_disconnect_invalidates_enqueue_waiting_for_options(setup):
    client, _, _ = setup
    service = client.app.state.dynamic
    owner = client.app.state.store.session(client.cookies.get(COOKIE))["account_id"]

    async def race():
        started, resume = asyncio.Event(), asyncio.Event()
        original = service.options

        async def delayed(account):
            options = await original(account)
            started.set()
            await resume.wait()
            return options

        service.options = delayed
        pending = asyncio.create_task(
            service.enqueue(owner, email={"subject": "test", "body": "test"})
        )
        await started.wait()
        await service.revoke(owner)
        resume.set()
        from prodog_gmail.errors import ConnectorError

        with pytest.raises(ConnectorError, match="authentication_required"):
            await pending
        assert service.db.listing(owner) == []

    client.portal.call(race)
