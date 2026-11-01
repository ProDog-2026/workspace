"""Complete owner-authenticated API path with synthetic mail and mocked providers."""

import time

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_pipeline import BASE, post, wait_results
from test_supabase import USER, FakeSupabase

from prodog_gmail.app import create_app
from prodog_gmail.config import Settings


def test_owned_collection_static_dynamic_and_durable_cloud_delivery(tmp_path, monkeypatch):
    class Detector:
        def __init__(self, _):
            pass

        def status(self):
            return {"ready": True, "model_id": "synthetic", "message": "ready"}

        async def scan(self, mail):
            danger = mail["id"] == "demo-2"
            return {
                "status": "completed",
                "model_id": "synthetic",
                "error": None,
                "verdict": "danger" if danger else "safe",
                "score": 0.9 if danger else 0.01,
                "suspicious_threshold": 0.1,
                "danger_threshold": 0.8,
            }

    async def model_revision(*_):
        return "synthetic-revision"

    async def execute(self, record):
        record.update(
            status="completed",
            finished_at="synthetic",
            observations={
                "canary_leaked": False,
                "summary_file_present": True,
                "complete": True,
            },
        )
        self.save(record)

    monkeypatch.setattr("prodog_gmail.pipeline.StaticDetector", Detector)
    monkeypatch.setattr("prodog_gmail.pipeline.MailPipeline.validate_model", model_revision)
    monkeypatch.setattr("prodog_gmail.experiments.Experiments.execute", execute)
    fake = FakeSupabase()
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "owned-pipeline.db",
        token_encryption_key=Fernet.generate_key().decode(),
        supabase_url="https://test.supabase.co",
        supabase_anon_key="sb_publishable_synthetic",
        supabase_service_key="sb_secret_synthetic",
    )
    with TestClient(create_app(settings, httpx.MockTransport(fake)), base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer synthetic-user"
        assert post(client, "/api/demo/connect").status_code == 200
        assert (
            post(
                client,
                "/api/analysis/settings",
                {
                    "enabled": True,
                    "model": "synthetic-model",
                },
            ).status_code
            == 200
        )
        assert post(client, "/api/mails?limit=3").status_code == 200
        results = wait_results(client)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if client.get("/api/analysis").json()["cloud"]["pending"] == 0:
                break
            time.sleep(0.01)
        assert client.get("/api/analysis").json()["cloud"]["pending"] == 0
        saved = {row["mail_id"]: row for row in fake.rows("analysis_results")}
        assert len(saved) == len(results) == 3
        assert all(row["user_id"] == USER for row in saved.values())
        attack = saved["mail-demo-2"]
        assert attack["verdict"] == "danger" and attack["status"] == "succeeded"
        assert attack["analysis"]["dynamic"]["observations"]["summary_file_present"] is True
        assert all("authorization" not in call[3].headers for call in fake.calls)
        assert all("analysis_text" not in call[3].content.decode() for call in fake.calls)
