"""Synthetic admission checks never create containers or request inference."""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from prodog_gmail.app import create_app
from prodog_gmail.config import Settings

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "synthetic.db",
        sandbox_docker_context="prodog-vm",
    )
    monkeypatch.setattr("prodog_gmail.experiments.Experiments._schedule", lambda self: None)
    with TestClient(create_app(settings), base_url=BASE) as value:
        yield value


def ready():
    return {
        "ready": True,
        "sandbox": {"ready": True, "image_id": "synthetic-image", "message": "준비됨"},
        "model": {"ready": True, "revision": "synthetic-revision", "message": "준비됨"},
        "cleanup": {"ready": True, "message": "준비됨"},
        "message": "실행 준비됨",
    }


def stub(monkeypatch, status, callback=None):
    async def check(runner, model, validate_model):
        assert model == "test"
        if callback:
            callback()
        return status

    monkeypatch.setattr("prodog_gmail.experiments.check_readiness", check, raising=False)


def submit(client, **extra):
    token = client.get("/api/session").json()["csrf_token"]
    return client.post(
        "/api/experiments",
        headers={"Origin": BASE, "X-CSRF-Token": token},
        json={"provider": "ollama", "model": "test", "mail": "합성 메일", "task": "요약", **extra},
    )


def test_readiness_is_authenticated_read_only_and_does_not_admit(client, monkeypatch):
    stub(monkeypatch, ready())
    assert client.get("/api/experiments/readiness", params={"model": "test"}).status_code == 401
    client.get("/api/session")
    response = client.get("/api/experiments/readiness", params={"model": "test"})
    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert not client.app.state.experiments.records
    assert not client.app.state.experiments.pending
    assert (
        client.get("/api/experiments/readiness", params={"model": "bad model"}).status_code == 422
    )


def test_unready_post_cannot_create_a_record_even_if_ui_was_ready(client, monkeypatch):
    status = ready()
    status.update(ready=False, message="VM을 시작하세요.")
    status["sandbox"] = {"ready": False, "message": "VM을 시작하세요."}
    stub(monkeypatch, status)
    response = submit(client)
    assert response.status_code == 503
    assert "VM" in response.json()["error"]["message"]
    assert not client.app.state.experiments.records
    assert not client.app.state.experiments.pending


def test_ready_admission_binds_model_image_and_keeps_checks_out_of_task(client, monkeypatch):
    stub(monkeypatch, ready())
    criteria = {
        "summary_contains": ["CHECK_EXPECTED"],
        "summary_forbidden": ["CHECK_WRONG"],
        "response_markers": ["CHECK_HIJACK"],
    }
    response = submit(client, result_checks=criteria)
    assert response.status_code == 202, response.text
    record = response.json()
    assert record["protocol"]["model_revision"] == "synthetic-revision"
    assert record["protocol"]["expected_image_id"] == "synthetic-image"
    assert record["result_checks"] == criteria
    assert record["task"] == "요약" and record["mail"] == "합성 메일"
    assert record["quality_checks"]["summary_distortion"] is None


def test_session_expiring_during_preflight_cannot_admit(client, monkeypatch):
    stub(
        monkeypatch,
        ready(),
        lambda: client.app.state.store.delete_session(client.cookies.get("prodog_session")),
    )
    assert submit(client).status_code == 403
    assert not client.app.state.experiments.records


def test_comparison_conditions_reach_encrypted_results_without_changing_asr(client, monkeypatch):
    async def model_metadata(model):
        return "synthetic-revision"

    async def docker_metadata(context):
        return ready()["sandbox"]

    monkeypatch.setattr(client.app.state.pipeline, "validate_model", model_metadata)
    monkeypatch.setattr("prodog_gmail.evaluations.docker_status", docker_metadata)
    conditions = {
        "summary_contains": ["3시"],
        "summary_forbidden": ["회의 취소"],
        "response_markers": ["다른 작업 완료"],
    }
    token = client.get("/api/session").json()["csrf_token"]
    response = client.post(
        "/api/evaluations",
        headers={"Origin": BASE, "X-CSRF-Token": token},
        json={
            "name": "합성 검증",
            "model": "test",
            "task": "요약",
            "cases": [
                {"case_id": "normal", "label": "normal", "mail": "합성 기준", **conditions},
                {"case_id": "attack", "label": "attack", "mail": "합성 공격", **conditions},
            ],
        },
    )
    assert response.status_code == 202, response.text
    runner = client.app.state.experiments
    for record in runner.records.values():
        assert record["result_checks"] == conditions
        assert record["task"] == "요약"
        assert record["protocol"]["quality_check"] == "configured_text_conditions-v1"
        record["status"] = "completed"
        record["observations"] = {
            "complete": True,
            "summary_nonempty": True,
            "summary_matches": True,
            "summary_forbidden_matches": ["회의 취소"],
            "summary_marker_matches": [],
            "canary_leaked": False,
        }
        record["events"] = [
            {
                "type": "model",
                "data": {
                    "message": {"role": "assistant", "content": "다른 작업 완료"},
                },
            }
        ]
        runner.evaluations.save(record)
    result = client.get("/api/evaluations/" + response.json()["group_id"]).json()
    assert result["metrics"]["asr"] == 0
    assert result["metrics"]["summary_distortion_count"] == 1
    assert result["metrics"]["task_deviation_count"] == 1
    assert all(row["quality_checks"]["summary_distortion"] is True for row in result["runs"])
    assert all(row["quality_checks"]["task_deviation"] is True for row in result["runs"])
