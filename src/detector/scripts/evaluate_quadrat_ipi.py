from __future__ import annotations

import argparse
import io
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import numpy as np

from prodog_sast.email_text import fields_to_model_text
from prodog_sast.model import load_artifact, predict

BASE_URL = (
    "https://huggingface.co/datasets/mihailgribov/quadrat-ipi/resolve/"
    "v1.0.2/data"
)
VERDICTS = ("safe", "suspicious", "danger")
SIGNALS = {
    "authority_policy": re.compile(
        r"\b(?:authori[sz]ed|approved|compliance|policy|procedure|required|security)\b",
        re.I,
    ),
    "action_request": re.compile(
        r"\b(?:please|must|need|send|reply|forward|copy|upload|download|execute)\b",
        re.I,
    ),
    "prompt_language": re.compile(
        r"\b(?:assistant|agent|system|instruction|prompt|ignore|previous)\b",
        re.I,
    ),
    "sensitive_data": re.compile(
        r"\b(?:secret|password|token|api[ _-]?key|confidential|credential|private)\b",
        re.I,
    ),
}


def _rows(filename: str) -> Iterator[dict[str, Any]]:
    request = Request(f"{BASE_URL}/{filename}", headers={"User-Agent": "ProDog-eval/1"})
    with urlopen(request, timeout=120) as response:  # noqa: S310 - fixed HTTPS source
        with io.TextIOWrapper(response, encoding="utf-8") as handle:
            for line in handle:
                value = json.loads(line)
                if value.get("host_type") == "email" and value.get("host_source") == "clinton":
                    yield value


def _signals(text: str) -> list[str]:
    return [name for name, pattern in SIGNALS.items() if pattern.search(text)]


def _signal_terms(text: str) -> dict[str, list[str]]:
    return {
        name: sorted({match.group(0).lower() for match in pattern.finditer(text)})
        for name, pattern in SIGNALS.items()
        if pattern.search(text)
    }


def _percentiles(values: list[float]) -> dict[str, float]:
    return {
        f"p{point}": round(float(np.percentile(values, point)), 6)
        for point in (1, 5, 25, 50, 75, 95, 99)
    }


def _metrics(rows: list[dict[str, Any]], positive_verdicts: set[str]) -> dict[str, Any]:
    tp = sum(row["label"] == 1 and row["verdict"] in positive_verdicts for row in rows)
    fp = sum(row["label"] == 0 and row["verdict"] in positive_verdicts for row in rows)
    tn = sum(row["label"] == 0 and row["verdict"] not in positive_verdicts for row in rows)
    fn = sum(row["label"] == 1 and row["verdict"] not in positive_verdicts for row in rows)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "recall": tp / max(1, tp + fn),
        "precision": tp / max(1, tp + fp),
        "false_positive_rate": fp / max(1, fp + tn),
        "specificity": tn / max(1, tn + fp),
        "accuracy": (tp + tn) / max(1, len(rows)),
    }


def _distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(row["verdict"] for row in rows)
    return {verdict: counts[verdict] for verdict in VERDICTS}


def _by(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "none")].append(row)
    return {
        name: {
            "samples": len(group),
            "verdicts": _distribution(group),
            "safe_rate": sum(row["verdict"] == "safe" for row in group) / len(group),
        }
        for name, group in sorted(groups.items())
    }


def _signal_rates(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {
        name: sum(name in row["signals"] for row in rows) / max(1, len(rows))
        for name in SIGNALS
    }


def _lengths(rows: list[dict[str, Any]]) -> dict[str, float]:
    def median(field: str) -> float:
        values = [row[field] for row in rows if row.get(field) is not None]
        return round(float(np.median(values)), 4) if values else 0.0

    return {
        "median_text_chars": median("text_chars"),
        "median_injection_chars": median("injection_chars"),
        "median_injection_share": median("injection_share"),
    }


def _threshold_sweep(
    rows: list[dict[str, Any]], thresholds: list[float], danger_threshold: float
) -> dict[str, Any]:
    result = {}
    for threshold in thresholds:
        tp = sum(row["label"] == 1 and row["score"] >= threshold for row in rows)
        fp = sum(row["label"] == 0 and row["score"] >= threshold for row in rows)
        tn = sum(row["label"] == 0 and row["score"] < threshold for row in rows)
        fn = sum(row["label"] == 1 and row["score"] < threshold for row in rows)

        def triage(label: int, gate: float = threshold) -> dict[str, int]:
            selected = [row for row in rows if row["label"] == label]
            return {
                "safe": sum(row["score"] < gate for row in selected),
                "suspicious": sum(
                    gate <= row["score"] < danger_threshold for row in selected
                ),
                "danger": sum(row["score"] >= danger_threshold for row in selected),
            }

        result[f"{threshold:g}"] = {
            "triage": {"clean": triage(0), "injected": triage(1)},
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
            "recall": tp / max(1, tp + fn),
            "precision": tp / max(1, tp + fp),
            "false_positive_rate": fp / max(1, fp + tn),
            "review_rate": (tp + fp) / max(1, len(rows)),
        }
    return result


def _example(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "id",
            "score",
            "verdict",
            "family",
            "action",
            "obfuscation",
            "spliced_at",
            "injection",
            "signal_terms",
        )
        if row.get(key) is not None
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the frozen detector on Quadrat-IPI Clinton emails"
    )
    parser.add_argument("--model", default="models/prompt_injection.joblib")
    parser.add_argument("--output", default="reports/quadrat-ipi-clinton-metrics.json")
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--suspicious-threshold", type=float)
    parser.add_argument("--threshold-sweep", nargs="+", type=float)
    args = parser.parse_args()

    artifact = load_artifact(args.model)
    original_suspicious_threshold = float(
        artifact.get("trained_suspicious_threshold", artifact["suspicious_threshold"])
    )
    if args.suspicious_threshold is not None:
        danger_threshold = float(artifact["danger_threshold"])
        if not 0 <= args.suspicious_threshold < danger_threshold:
            raise ValueError(
                "suspicious threshold must satisfy 0 <= suspicious < danger"
            )
        artifact["suspicious_threshold"] = args.suspicious_threshold
    if args.threshold_sweep and any(
        not 0 <= threshold < float(artifact["danger_threshold"])
        for threshold in args.threshold_sweep
    ):
        raise ValueError("sweep thresholds must satisfy 0 <= suspicious < danger")
    evaluated: list[dict[str, Any]] = []
    for filename, label in (("negatives.jsonl", 0), ("positives.jsonl", 1)):
        batch: list[dict[str, Any]] = []
        for row in _rows(filename):
            batch.append(row)
            if len(batch) < args.batch_size:
                continue
            _score_batch(artifact, batch, label, evaluated)
            batch = []
        if batch:
            _score_batch(artifact, batch, label, evaluated)

    if not evaluated or {row["label"] for row in evaluated} != {0, 1}:
        raise ValueError("filtered dataset must contain clean and injected emails")

    clean = [row for row in evaluated if row["label"] == 0]
    injected = [row for row in evaluated if row["label"] == 1]
    false_negative = [row for row in injected if row["verdict"] == "safe"]
    true_positive = [row for row in injected if row["verdict"] != "safe"]
    false_positive = [row for row in clean if row["verdict"] != "safe"]
    true_negative = [row for row in clean if row["verdict"] == "safe"]

    report = {
        "dataset": "Quadrat-IPI v1.0.2",
        "source": "https://huggingface.co/datasets/mihailgribov/quadrat-ipi",
        "filter": {"host_type": "email", "host_source": "clinton"},
        "training_status": (
            "classifier weights are external; dataset is used for threshold development"
            if args.suspicious_threshold is not None or args.threshold_sweep
            else "external; never used for training or threshold selection"
        ),
        "input_adapter": "the released text field is passed as plain email body",
        "thresholds": {
            "suspicious": artifact["suspicious_threshold"],
            "danger": artifact["danger_threshold"],
            "original_suspicious": original_suspicious_threshold,
        },
        "samples": {
            "total": len(evaluated),
            "clean": len(clean),
            "injected": len(injected),
        },
        "triage": {"clean": _distribution(clean), "injected": _distribution(injected)},
        "review_gate": _metrics(evaluated, {"suspicious", "danger"}),
        "danger_gate": _metrics(evaluated, {"danger"}),
        "threshold_sweep": _threshold_sweep(
            evaluated,
            args.threshold_sweep or [],
            float(artifact["danger_threshold"]),
        ),
        "score_percentiles": {
            "clean": _percentiles([row["score"] for row in clean]),
            "injected": _percentiles([row["score"] for row in injected]),
        },
        "injected_breakdown": {
            field: _by(injected, field)
            for field in ("family", "action", "obfuscation", "spliced_at", "mail_type")
        },
        "error_analysis": {
            "counts": {
                "true_positive": len(true_positive),
                "false_positive": len(false_positive),
                "true_negative": len(true_negative),
                "false_negative": len(false_negative),
            },
            "signal_rates": {
                "true_positive": _signal_rates(true_positive),
                "false_positive": _signal_rates(false_positive),
                "true_negative": _signal_rates(true_negative),
                "false_negative": _signal_rates(false_negative),
            },
            "lengths": {
                "true_positive": _lengths(true_positive),
                "false_positive": _lengths(false_positive),
                "true_negative": _lengths(true_negative),
                "false_negative": _lengths(false_negative),
            },
            "note": (
                "signal rates are correlations from fixed lexical groups, "
                "not exact hashed-feature attribution"
            ),
        },
        "examples": {
            "false_negative": [
                _example(row)
                for row in sorted(false_negative, key=lambda item: item["score"], reverse=True)[:10]
            ],
            "suspicious_true_positive": [
                _example(row)
                for row in sorted(
                    (row for row in injected if row["verdict"] == "suspicious"),
                    key=lambda item: item["score"],
                )[:10]
            ],
            "danger_true_positive": [
                _example(row)
                for row in sorted(
                    (row for row in injected if row["verdict"] == "danger"),
                    key=lambda item: item["score"],
                    reverse=True,
                )[:5]
            ],
            "false_positive_metadata": [
                {
                    "id": row["id"],
                    "score": row["score"],
                    "verdict": row["verdict"],
                    "mail_type": row.get("mail_type"),
                    "signal_terms": row["signal_terms"],
                }
                for row in sorted(false_positive, key=lambda item: item["score"], reverse=True)[:30]
            ],
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _score_batch(
    artifact: dict[str, Any],
    batch: list[dict[str, Any]],
    label: int,
    evaluated: list[dict[str, Any]],
) -> None:
    texts = [fields_to_model_text(body_text=str(row["text"])) for row in batch]
    for row, result, model_text in zip(batch, predict(artifact, texts), texts, strict=True):
        evaluated.append(
            {
                "id": row["id"],
                "label": label,
                "score": result["prompt_injection_probability"],
                "verdict": result["verdict"],
                "family": row.get("family"),
                "action": row.get("action"),
                "obfuscation": row.get("obfuscation", {}).get("kind")
                if isinstance(row.get("obfuscation"), dict)
                else row.get("obfuscation"),
                "spliced_at": row.get("spliced_at"),
                "mail_type": row.get("mail_type"),
                "injection": row.get("injection"),
                "signals": _signals(model_text),
                "signal_terms": _signal_terms(model_text),
                "text_chars": len(str(row["text"])),
                "injection_chars": len(str(row.get("injection") or "")) or None,
                "injection_share": (
                    len(str(row.get("injection") or "")) / max(1, len(str(row["text"])))
                    if row.get("injection")
                    else None
                ),
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
