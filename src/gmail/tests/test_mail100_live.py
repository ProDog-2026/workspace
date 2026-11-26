"""Opt-in end-to-end check with one synthetic mail and a running local agent."""

import base64
import importlib
import json
import os
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from prodog_gmail.app import create_app
from prodog_gmail.config import Settings


@pytest.mark.skipif(
    not os.getenv("PRODOG_ISSUE89_DATA"), reason="requires local mail100 data and agent"
)
def test_synthetic_mail_keeps_static_and_real_dynamic_result_together(tmp_path, monkeypatch):
    data = Path(os.environ["PRODOG_ISSUE89_DATA"])
    model = Path(os.environ["PRODOG_ISSUE89_STATIC_MODEL"])
    raw = (data / "emails" / "001_email.eml").read_bytes()
    message = {
        "id": "issue89-001",
        "threadId": "issue89-thread-001",
        "labelIds": ["INBOX"],
        "raw": base64.urlsafe_b64encode(raw).decode(),
        "sizeEstimate": len(raw),
    }
    module = importlib.import_module("prodog_gmail.app")
    monkeypatch.setattr(module, "demo_messages", lambda: [message])
    settings = Settings(
        _env_file=None,
        app_mode="demo",
        database_path=tmp_path / "demo.db",
        token_encryption_key=Fernet.generate_key().decode(),
        detector_model_path=model,
    )
    options = {
        "auto_submit": True,
        "model": "qwen3:4b-instruct",
        "base_url": "http://host.docker.internal:11434",
        "prompt_id": "MAIL_SUMMARY_V2",
        "temperature": 0.1,
        "limits": {"max_calls": 8, "max_tokens": 2048, "total_seconds": 240},
    }
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:

        def change(method, path, payload=None):
            csrf = client.get("/api/session").json()["csrf_token"]
            return client.request(
                method,
                path,
                json=payload,
                headers={"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": csrf},
            )

        assert change("POST", "/api/demo/connect").status_code == 200
        assert change("PUT", "/api/dynamic/settings", options).status_code == 200
        before = client.get("/api/mails").json()["messages"][0]
        assert before["id"] == "issue89-001"
        assert before["detection"]["status"] == "ok"
        assert before["detection"]["verdict"] in {"suspicious", "danger"}
        response = change("POST", "/api/dynamic/mail/issue89-001")
        assert response.status_code == 202, response.text
        job_id = response.json()["job"]["id"]
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = client.get(f"/api/dynamic/jobs/{job_id}").json()
            if job["status"] in {"completed", "failed", "incomplete"}:
                break
            time.sleep(1)
        assert job["status"] == "completed", {
            "status": job["status"],
            "error": job.get("error_code"),
        }
        after = client.get("/api/mails").json()["messages"][0]
        assert after["detection"]["verdict"] == before["detection"]["verdict"]
        assert after["dynamic"]["id"] == job_id
        assert job["static_detection"]["verdict"] == before["detection"]["verdict"]
        if os.getenv("PRODOG_ISSUE89_EVIDENCE"):
            evidence = {
                "case_id": "001",
                "static_verdict": before["detection"]["verdict"],
                "dynamic_status": job["status"],
                "observation": job["observation"],
                "job_id": job_id,
                "run_id": job.get("run_id"),
            }
            Path(os.environ["PRODOG_ISSUE89_EVIDENCE"]).write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
