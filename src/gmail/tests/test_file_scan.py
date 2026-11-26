import base64
from email.message import EmailMessage

import numpy as np
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_mail import pdf_bytes

from prodog_gmail.app import create_app
from prodog_gmail.config import Settings

BASE = "http://127.0.0.1:8765"


def encoded(name, payload):
    return {"filename": name, "data": base64.b64encode(payload).decode()}


def eml_with_pdf(pdf):
    mail = EmailMessage()
    mail["Subject"] = "Synthetic subject"
    mail["From"] = "synthetic@example.test"
    mail.set_content("Synthetic normal text")
    mail.add_alternative("<html><!-- Synthetic hidden injection --></html>", subtype="html")
    mail.add_attachment(pdf, maintype="application", subtype="pdf", filename="embedded.pdf")
    return mail.as_bytes()


class ContentScore:
    def predict_proba(self, texts):
        return np.array([[0.1, 0.9] if "Synthetic hidden injection" in t or "Synthetic PDF" in t
                         else [0.99, 0.01] for t in texts])


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, app_mode="demo",
                        token_encryption_key=Fernet.generate_key().decode(),
                        database_path=tmp_path / "test.db")
    with TestClient(create_app(settings), base_url=BASE) as client:
        csrf = client.get("/api/session").json()["csrf_token"]
        client.headers.update({"Origin": BASE, "X-CSRF-Token": csrf})
        client.app.state.detector = {"pipeline": ContentScore(), "threshold": 0.4,
                                     "suspicious_threshold": 0.05, "danger_threshold": 0.4}
        yield client


def test_eml_html_and_matching_embedded_pdf_scanned_once(client):
    pdf = pdf_bytes("Synthetic PDF marker")
    response = client.post("/api/scan/file", json={"eml": encoded("one.eml", eml_with_pdf(pdf)),
                                                   "pdf": encoded("one.pdf", pdf)})
    assert response.status_code == 200, response.text
    mail = response.json()["mail"]
    assert mail["detection"]["verdict"] == "danger"
    assert len(mail["attachments"]) == 1
    assert mail["detection"]["pdf_results"][0]["verdict"] == "danger"
    assert "Synthetic hidden injection" not in response.text
    assert "Synthetic PDF marker" not in response.text


def test_distinct_external_pdf_is_scanned_in_addition(client):
    first = pdf_bytes("First file")
    second = pdf_bytes("Synthetic PDF second")
    response = client.post("/api/scan/file", json={"eml": encoded("one.eml", eml_with_pdf(first)),
                                                   "pdf": encoded("two.pdf", second)})
    assert response.status_code == 200, response.text
    mail = response.json()["mail"]
    assert len(mail["attachments"]) == 2
    assert [r["verdict"] for r in mail["detection"]["pdf_results"]] == ["safe", "danger"]


def test_embedded_pdf_is_scanned_without_external_file(client):
    response = client.post("/api/scan/file", json={
        "eml": encoded("one.eml", eml_with_pdf(pdf_bytes("Synthetic PDF marker")))
    })
    assert response.status_code == 200
    assert response.json()["mail"]["detection"]["pdf_results"][0]["verdict"] == "danger"


def test_invalid_external_pdf_is_reported_without_losing_mail_result(client):
    response = client.post("/api/scan/file", json={
        "eml": encoded("one.eml", eml_with_pdf(pdf_bytes("First file"))),
        "pdf": encoded("bad.pdf", b"not a PDF"),
    })
    assert response.status_code == 200
    mail = response.json()["mail"]
    assert mail["detection"]["verdict"] == "danger"
    assert mail["attachments"][-1]["pdf_status"] == "invalid"


def test_oversized_external_pdf_is_rejected(client):
    response = client.post("/api/scan/file", json={
        "eml": encoded("one.eml", eml_with_pdf(pdf_bytes())),
        "pdf": encoded("huge.pdf", b"x" * (1024 * 1024 + 1)),
    })
    assert response.status_code == 422


def test_missing_detector_is_explicit(client):
    client.app.state.detector = None
    response = client.post("/api/scan/file", json={
        "eml": encoded("one.eml", eml_with_pdf(pdf_bytes())),
    })
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "detector_not_configured"


@pytest.mark.parametrize("eml", [encoded("bad.eml", b"not a mail"),
                                 {"filename": "bad.eml", "data": "!?"},
                                 encoded("huge.eml", b"x" * (2 * 1024 * 1024 + 1))])
def test_bad_eml_is_rejected(client, eml):
    assert client.post("/api/scan/file", json={"eml": eml}).status_code == 422


def test_scan_requires_csrf(client):
    response = client.post("/api/scan/file", headers={"X-CSRF-Token": ""},
                           json={"eml": encoded("one.eml", eml_with_pdf(pdf_bytes()))})
    assert response.status_code == 403
