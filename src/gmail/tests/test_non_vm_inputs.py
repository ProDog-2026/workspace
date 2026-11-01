"""Synthetic input/adapter regressions; never load an artifact or contact Gmail."""

import asyncio
import base64
import io
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from prodog_sast import model
from prodog_sast.email_text import collect_api_to_model_text

from prodog_gmail.mail import parse_gmail_message
from prodog_gmail.scan import scan_mail
from prodog_gmail.static_detector import StaticDetector


def parse_raw(source):
    return parse_gmail_message({"raw": base64.urlsafe_b64encode(source).decode("ascii")})


def multipart(parts):
    return (
        b'Content-Type: multipart/mixed; boundary="synthetic"\r\n\r\n'
        + b"".join(
            b"--synthetic\r\nContent-Type: " + kind.encode()
            + b"; charset=utf-8\r\n\r\n" + text.encode() + b"\r\n"
            for kind, text in parts
        )
        + b"--synthetic--\r\n"
    )


@pytest.mark.parametrize("field", ["text_plain", "text_html"])
def test_ipc_drops_duplicate_analysis_for_either_body(field, monkeypatch):
    body = "😀" * 270_000
    mail = parse_raw(f"Content-Type: {'text/html' if field == 'text_html' else 'text/plain'}; "
                     f"charset=utf-8\r\n\r\n{body}".encode())
    assert mail[field] == body and mail["analysis_text"] == body
    assert not mail["truncated"] and mail["warnings"] == []
    detector = StaticDetector(SimpleNamespace())
    monkeypatch.setattr(detector, "status", lambda: {"ready": True, "model_id": "synthetic"})
    captured = []

    async def run(payload):
        captured.append(json.loads(payload))
        return json.dumps({
            "score": 0.1, "verdict": "suspicious",
            "suspicious_threshold": 0.05, "danger_threshold": 0.8,
        }).encode(), 0

    monkeypatch.setattr(detector, "_run", run)
    result = asyncio.run(detector.scan(mail))
    assert result["status"] == "completed"
    assert captured == [{"subject": "", "sender": "", "text_plain": mail["text_plain"],
                         "text_html": mail["text_html"]}]


def test_ipc_preserves_fallback_when_both_bodies_absent(monkeypatch):
    detector = StaticDetector(SimpleNamespace())
    monkeypatch.setattr(detector, "status", lambda: {"ready": True, "model_id": "synthetic"})
    captured = []

    async def run(payload):
        captured.append(json.loads(payload))
        return b'{"score":0,"verdict":"safe","suspicious_threshold":0.05,"danger_threshold":0.8}', 0

    monkeypatch.setattr(detector, "_run", run)
    assert asyncio.run(detector.scan({"analysis_text": "fallback"}))["status"] == "completed"
    assert captured == [{"analysis_text": "fallback"}]


@pytest.mark.parametrize("kind,field", [("text/plain", "text_plain"), ("text/html", "text_html")])
def test_mime_text_parts_remain_separated(kind, field):
    mail = parse_raw(multipart([(kind, "first"), (kind, "second")]))
    assert mail[field] == "first\nsecond"
    assert mail["analysis_text"] == "first\nsecond"
    assert mail["warnings"] == [] and mail["truncated"] is False


def test_interleaved_mime_parts_keep_analysis_order_and_html_source():
    html = "<!-- hidden --><p>middle</p>"
    mail = parse_raw(multipart([
        ("text/plain", "first"), ("text/html", html), ("text/plain", "last"),
    ]))
    assert mail["text_plain"] == "first\nlast"
    assert mail["text_html"] == html
    assert mail["analysis_text"] == "first\n" + html + "\nlast"
    text = collect_api_to_model_text(mail)
    assert "[PLAIN] first\nlast" in text
    assert "[HTML_RAW] " + html in text
    assert "[HTML_TEXT] middle" in text


@pytest.mark.parametrize("second_kind", ["text/plain", "text/html"])
@pytest.mark.parametrize("tail", ["b", "bc"])
def test_mime_separators_count_toward_shared_budget(second_kind, tail):
    mail = parse_raw(multipart([("text/plain", "a" * 299_998), (second_kind, tail)]))
    assert mail["analysis_text"] == "a" * 299_998 + "\nb"
    assert len(mail["text_plain"]) + len(mail["text_html"]) <= 300_000
    assert mail["truncated"] is (tail == "bc")
    assert mail["warnings"] == (["text_size_limit_exceeded"] if tail == "bc" else [])


def test_full_budget_does_not_grow_for_later_parts_and_keeps_attachment_metadata():
    source = multipart([
        ("text/plain", "a" * 300_000), ("text/html", "overflow"),
        ("text/plain", "more"), ("application/octet-stream", "synthetic attachment"),
    ])
    mail = parse_raw(source)
    assert mail["analysis_text"] == mail["text_plain"] == "a" * 300_000
    assert mail["text_html"] == ""
    assert mail["truncated"] is True
    assert mail["warnings"].count("text_size_limit_exceeded") == 1
    assert len(mail["attachments"]) == 1


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -float("inf"), -0.01, 1.01])
def test_legacy_adapter_returns_failed_for_invalid_probabilities(score):
    class NumericPipeline:
        def predict_proba(self, texts):
            return np.array([[1 - score, score]] * len(texts))

    result = scan_mail(
        {"pipeline": NumericPipeline(), "threshold": 0.8}, {"text_plain": "synthetic"}
    )
    assert result == {
        "status": "failed", "verdict": None, "probability": None, "static_score": None,
    }


def test_worker_rejects_nan_using_common_predict(monkeypatch, capsys, tmp_path):
    class InvalidPipeline:
        def predict_proba(self, texts):
            return np.array([[float("nan"), float("nan")]] * len(texts))

    monkeypatch.setattr(
        model, "load_artifact", lambda path: {"pipeline": InvalidPipeline(), "threshold": 0.8}
    )
    script = Path(__file__).resolve().parents[1] / "scripts/scan_collected_mail.py"
    monkeypatch.setattr(sys, "argv", [str(script), str(tmp_path / "unused.joblib")])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"text_plain":"synthetic"}')))
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(script), run_name="__main__")
    output = capsys.readouterr()
    assert stopped.value.code == 1
    assert output.err == ""
    assert json.loads(output.out)["error"]["code"] == "static_inference_failed"
