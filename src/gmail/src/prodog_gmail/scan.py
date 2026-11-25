"""Bridge to the static detector (src/detector). Mail text stays in memory; nothing is stored."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from prodog_sast.email_text import collect_api_to_model_text
from prodog_sast.model import load_artifact, predict

_INPUT_VERSION = "pdf-v1"
_VERDICT_RANK = {"safe": 0, "suspicious": 1, "danger": 2}


def load_detector(path: Path | None):
    # Fails loudly on a bad path or file so a broken model never looks like "not scanned".
    return load_artifact(path) if path else None


def scan_mail(artifact, mail: Mapping[str, Any]) -> dict[str, Any] | None:
    """None = detector not configured. A failure never becomes `safe`."""
    if artifact is None:
        return None
    try:
        # Score each PDF independently so a long mail body cannot mask an attachment.
        inputs = [collect_api_to_model_text(mail), *mail.get("pdf_texts", [])]
        index, result = max(
            enumerate(predict(artifact, inputs)),
            key=lambda entry: (
                _VERDICT_RANK[entry[1]["verdict"]],
                entry[1]["prompt_injection_probability"],
            ),
        )
    except Exception:  # any model error must surface as "failed", not crash the list
        return {"status": "failed", "verdict": None, "probability": None, "static_score": None}
    probability = result["prompt_injection_probability"]
    return {
        "status": "ok",
        "verdict": result["verdict"],
        "probability": probability,
        # Display only; verdict is already fixed by the detector's own thresholds.
        "static_score": round(probability * 100),
        **({"source": "pdf"} if index else {}),
        # Shown next to the score so the user can see why 8% is "suspicious".
        "thresholds": {
            "suspicious": round(result["suspicious_threshold"] * 100, 1),
            "danger": round(result["danger_threshold"] * 100, 1),
        },
    }


def model_key(artifact) -> str:
    """Reuse saved verdicts only with the same input extraction, detector and thresholds."""
    if artifact is None:
        return _INPUT_VERSION + "|none"
    artifact_key = "|".join(
        str(artifact.get(key)) for key in ("created_at", "suspicious_threshold", "danger_threshold")
    )
    return _INPUT_VERSION + "|" + artifact_key
