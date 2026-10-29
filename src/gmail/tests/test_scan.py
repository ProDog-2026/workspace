import numpy as np
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from prodog_gmail.app import create_app
from prodog_gmail.config import Settings
from prodog_gmail.scan import scan_mail

BASE = "http://127.0.0.1:8765"
MAIL = {"subject": "hi", "sender": "a@example.com", "text_plain": "hello", "text_html": ""}


class Stub:
    def __init__(self, probability=None, error=None):
        self.probability, self.error = probability, error

    def predict_proba(self, texts):
        if self.error:
            raise self.error
        return np.array([[1 - self.probability, self.probability]] * len(texts))


def artifact(**kwargs):
    return {"pipeline": Stub(**kwargs), "threshold": 0.4, "suspicious_threshold": 0.05,
            "danger_threshold": 0.4}


@pytest.mark.parametrize(
    ("probability", "verdict", "score"),
    [(0.01, "safe", 1), (0.2, "suspicious", 20), (0.9, "danger", 90)],
)
def test_verdict_and_score(probability, verdict, score):
    result = scan_mail(artifact(probability=probability), MAIL)
    assert (result["verdict"], result["static_score"], result["status"]) == (verdict, score, "ok")


def test_no_detector_means_not_scanned():
    assert scan_mail(None, MAIL) is None


def test_model_error_is_never_safe():
    result = scan_mail(artifact(error=RuntimeError("boom")), MAIL)
    assert result["status"] == "failed"
    assert result["verdict"] is None


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None,
        app_mode="demo",
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "test.db",
    )
    with TestClient(create_app(settings), base_url=BASE) as client:
        csrf = client.get("/api/session").json()["csrf_token"]
        client.post("/api/demo/connect", headers={"Origin": BASE, "X-CSRF-Token": csrf})
        yield client


def test_mail_list_carries_detection(client):
    client.app.state.detector = artifact(probability=0.9)
    messages = client.get("/api/mails").json()["messages"]
    assert messages
    assert {m["detection"]["verdict"] for m in messages} == {"danger"}


def test_mail_list_without_detector_has_null_detection(client):
    messages = client.get("/api/mails").json()["messages"]
    assert {m["detection"] for m in messages} == {None}
