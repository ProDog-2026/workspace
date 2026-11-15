"""Bridge to the static detector (src/detector). Mail text stays in memory; nothing is stored."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from prodog_sast.email_text import collect_api_to_model_text
from prodog_sast.model import load_artifact, predict


def load_detector(path: Path | None):
    # Fails loudly on a bad path or file so a broken model never looks like "not scanned".
    return load_artifact(path) if path else None


def scan_mail(artifact, mail: Mapping[str, Any]) -> dict[str, Any] | None:
    """None = detector not configured. A failure never becomes `safe`."""
    if artifact is None:
        return None
    try:
        result = predict(artifact, [collect_api_to_model_text(mail)])[0]
    except Exception:  # any model error must surface as "failed", not crash the list
        return {"status": "failed", "verdict": None, "probability": None, "static_score": None}
    probability = result["prompt_injection_probability"]
    return {
        "status": "ok",
        "verdict": result["verdict"],
        "probability": probability,
        # Display only; verdict is already fixed by the detector's own thresholds.
        "static_score": round(probability * 100),
        # Shown next to the score so the user can see why 8% is "suspicious".
        "thresholds": {
            "suspicious": round(result["suspicious_threshold"] * 100, 1),
            "danger": round(result["danger_threshold"] * 100, 1),
        },
    }
