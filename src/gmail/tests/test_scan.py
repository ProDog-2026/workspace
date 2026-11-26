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


def test_pdf_verdict_is_separate_from_body_verdict():
    class ContentScore:
        def predict_proba(self, texts):
            return np.array([
                [0.8, 0.2] if "Synthetic PDF note" in text else [0.99, 0.01]
                for text in texts
            ])

    detector = artifact() | {"pipeline": ContentScore()}
    mail = MAIL | {
        "attachments": [
            {"filename": "image.png"},
            {"filename": "note.pdf", "pdf_status": "parsed"},
        ],
        "pdf_texts": ["Synthetic PDF note"],
    }
    result = scan_mail(detector, mail)
    assert result["verdict"] == "safe"
    assert result["static_score"] == 1
    assert result["pdf_results"][0]["attachment_index"] == 1
    assert result["pdf_results"][0]["verdict"] == "suspicious"
    assert result["pdf_results"][0]["static_score"] == 20


def test_pdf_scan_failure_does_not_hide_body_verdict():
    class FailingPdf:
        def predict_proba(self, texts):
            if any("Synthetic PDF note" in text for text in texts):
                raise RuntimeError("failed PDF")
            return np.array([[0.99, 0.01]] * len(texts))

    detector = artifact() | {"pipeline": FailingPdf()}
    mail = MAIL | {
        "attachments": [{"filename": "note.pdf", "pdf_status": "parsed"}],
        "pdf_texts": ["Synthetic PDF note"],
    }
    result = scan_mail(detector, mail)
    assert result["status"] == "ok"
    assert result["verdict"] == "safe"
    assert result["pdf_results"][0]["status"] == "failed"
    assert result["pdf_results"][0]["verdict"] is None


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


def test_detection_carries_thresholds_for_the_verdict_reason():
    result = scan_mail(artifact(probability=0.08), MAIL)
    assert result["verdict"] == "suspicious"
    assert result["thresholds"] == {"suspicious": 5.0, "danger": 40.0}


def test_pdf_scan_failure_is_retried_on_next_mail_list(client, monkeypatch):
    scans = []

    def scan(_detector, _mail):
        scans.append(1)
        pdf_status = "failed" if len(scans) == 1 else "ok"
        return {
            "status": "ok", "verdict": "safe", "probability": 0.01, "static_score": 1,
            "pdf_results": [{"attachment_index": 0, "status": pdf_status}],
        }

    monkeypatch.setattr("prodog_gmail.app.scan_mail", scan)
    first = client.get("/api/mails?limit=1").json()["messages"][0]
    second = client.get("/api/mails?limit=1").json()["messages"][0]
    assert first["detection"]["pdf_results"][0]["status"] == "failed"
    assert second["detection"]["pdf_results"][0]["status"] == "ok"
    assert len(scans) == 2


def test_malformed_model_result_is_reported_as_scan_failure(monkeypatch):
    monkeypatch.setattr("prodog_gmail.scan.predict", lambda _artifact, _texts: [{}])
    result = scan_mail(artifact(probability=0.1), MAIL)
    assert result["status"] == "failed"
    assert result["verdict"] is None
