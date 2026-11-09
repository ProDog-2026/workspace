"""The public Gmail API gates and owns dynamic executions; Ollama/Docker stay external."""

import asyncio
import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
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
        client.app.state.detector = artifact(probability=0.9)
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


@pytest.mark.parametrize("probability", [0.2, 0.9])
def test_flagged_mail_is_submitted_once_and_preserves_static_result(setup, probability):
    client, service, config = setup
    client.app.state.detector = artifact(probability=probability)
    mid = mail_id(client)
    first = change(client, f"/api/dynamic/mail/{mid}")
    assert first.status_code == 202, first.text
    jid = first.json()["job"]["id"]
    second = change(client, f"/api/dynamic/mail/{mid}")
    assert second.json()["job"]["id"] == jid
    job = wait_job(client, jid)
    assert job["status"] == "completed"
    assert job["observation"] == "not_observed"
    assert job["static_detection"]["verdict"] == ("danger" if probability == 0.9 else "suspicious")
    assert len(service.submitted) == 1
    assert service.submitted[0]["config"]["model"] == "test-model"
    assert "UNTRUSTED" in service.submitted[0]["email"]["body"]
    assert "SYNTHETIC_SUMMARY_NOT_IN_GMAIL_DB" not in config.database_path.read_bytes().decode(
        errors="ignore"
    )
    row = client.get("/api/mails").json()["messages"][0]
    assert row["dynamic"]["id"] == jid


@pytest.mark.parametrize("probability", [None, 0.01])
def test_safe_or_unconfigured_model_does_not_enter_dynamic_queue(setup, probability):
    client, service, _ = setup
    client.app.state.detector = None if probability is None else artifact(probability=probability)
    response = change(client, f"/api/dynamic/mail/{mail_id(client)}")
    assert response.status_code == 200
    assert response.json()["skipped"] is True
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
    assert job["static_detection"]["verdict"] == "danger"


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
        c.app.state.detector = artifact(probability=0.9)
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
