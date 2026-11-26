"""Bridge to the static detector (src/detector). Mail text stays in memory; nothing is stored."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from prodog_sast.email_text import collect_api_to_model_text
from prodog_sast.model import load_artifact, predict

_INPUT_VERSION = "pdf-separate-v2"


def load_detector(path: Path | None):
    # Fails loudly on a bad path or file so a broken model never looks like "not scanned".
    return load_artifact(path) if path else None


def _scan_text(artifact, text: str) -> dict[str, Any]:
    try:
        result = predict(artifact, [text])[0]
        probability = result["prompt_injection_probability"]
        return {
            "status": "ok",
            "verdict": result["verdict"],
            "probability": probability,
            "static_score": round(probability * 100),
            "thresholds": {
                "suspicious": round(result["suspicious_threshold"] * 100, 1),
                "danger": round(result["danger_threshold"] * 100, 1),
            },
        }
    except Exception:  # A model error is reported as a failure, never as safe.
        return {"status": "failed", "verdict": None, "probability": None, "static_score": None}


def scan_mail(artifact, mail: Mapping[str, Any]) -> dict[str, Any] | None:
    """Scan the mail body and parsed PDF attachments independently."""
    if artifact is None:
        return None
    try:
        body_text = collect_api_to_model_text(mail)
    except Exception:
        detection = {"status": "failed", "verdict": None, "probability": None, "static_score": None}
    else:
        detection = _scan_text(artifact, body_text)
    parsed_indexes = [
        index for index, attachment in enumerate(mail.get("attachments", []))
        if attachment.get("pdf_status") == "parsed"
    ]
    pdf_texts = mail.get("pdf_texts", [])
    if len(parsed_indexes) != len(pdf_texts):
        detection["pdf_results"] = [
            {"attachment_index": index, "status": "failed", "verdict": None,
             "probability": None, "static_score": None}
            for index in parsed_indexes
        ]
    else:
        detection["pdf_results"] = [
            {"attachment_index": index, **_scan_text(artifact, pdf_text)}
            for index, pdf_text in zip(parsed_indexes, pdf_texts, strict=True)
        ]
    return detection


def cacheable_detection(detection: dict[str, Any] | None) -> bool:
    """Retry failed body or attachment scans on the next request."""
    return detection is None or (
        detection.get("status") == "ok"
        and all(result.get("status") == "ok" for result in detection.get("pdf_results", []))
    )


def model_key(artifact) -> str:
    """Reuse saved verdicts only with the same input extraction, detector and thresholds."""
    if artifact is None:
        return _INPUT_VERSION + "|none"
    artifact_key = "|".join(
        str(artifact.get(key)) for key in ("created_at", "suspicious_threshold", "danger_threshold")
    )
    return _INPUT_VERSION + "|" + artifact_key
