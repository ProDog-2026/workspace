from __future__ import annotations

import argparse
import json
from pathlib import Path

from prodog_sast.data import load_samples
from prodog_sast.model import DEFAULT_SUSPICIOUS_THRESHOLD, load_artifact, predict


def metrics(labels: list[int], verdicts: list[str], detected: set[str]) -> dict:
    pairs = list(zip(labels, verdicts, strict=True))
    tp = sum(label == 1 and verdict in detected for label, verdict in pairs)
    fn = sum(label == 1 and verdict not in detected for label, verdict in pairs)
    fp = sum(label == 0 and verdict in detected for label, verdict in pairs)
    tn = sum(label == 0 and verdict not in detected for label, verdict in pairs)
    return {
        "true_positive": tp,
        "false_negative": fn,
        "false_positive": fp,
        "true_negative": tn,
        "recall": tp / max(1, tp + fn),
        "false_positive_rate": fp / max(1, fp + tn),
        "precision": tp / max(1, tp + fp),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a model on labeled email data")
    parser.add_argument("data")
    parser.add_argument("--model", default="models/prompt_injection.joblib")
    parser.add_argument("--output", default="reports/external-metrics.json")
    args = parser.parse_args()

    samples, _ = load_samples(args.data)
    artifact = load_artifact(args.model)
    verdicts: list[str] = []
    for start in range(0, len(samples), 128):
        batch = samples[start : start + 128]
        verdicts.extend(item["verdict"] for item in predict(artifact, [x.text for x in batch]))

    labels = [sample.label for sample in samples]
    suspicious_threshold = artifact.get(
        "suspicious_threshold", DEFAULT_SUSPICIOUS_THRESHOLD
    )
    danger_threshold = artifact.get("danger_threshold", artifact["threshold"])
    confusion = {
        name: {
            verdict: sum(
                label == expected and predicted == verdict
                for label, predicted in zip(labels, verdicts, strict=True)
            )
            for verdict in ("safe", "suspicious", "danger")
        }
        for name, expected in (("benign", 0), ("injection", 1))
    }
    report = {
        "source": str(args.data),
        "samples": len(samples),
        "thresholds": {
            "suspicious": suspicious_threshold,
            "danger": danger_threshold,
        },
        "verdict_counts": {
            verdict: verdicts.count(verdict)
            for verdict in ("safe", "suspicious", "danger")
        },
        "confusion_by_label": confusion,
        "review_metrics": metrics(labels, verdicts, {"suspicious", "danger"}),
        "danger_metrics": metrics(labels, verdicts, {"danger"}),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
