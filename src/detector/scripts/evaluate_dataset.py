from __future__ import annotations

import argparse
import json
from pathlib import Path

from prodog_sast.data import load_samples
from prodog_sast.model import DEFAULT_MALICIOUS_THRESHOLD, load_artifact, predict


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
    pairs = list(zip(labels, verdicts, strict=True))
    detected = {"suspicious", "malicious"}
    tp = sum(label == 1 and verdict in detected for label, verdict in pairs)
    fn = sum(label == 1 and verdict == "benign" for label, verdict in pairs)
    fp = sum(label == 0 and verdict in detected for label, verdict in pairs)
    tn = sum(label == 0 and verdict == "benign" for label, verdict in pairs)
    suspicious_threshold = artifact.get("suspicious_threshold", artifact["threshold"])
    malicious_threshold = artifact.get("malicious_threshold", DEFAULT_MALICIOUS_THRESHOLD)
    report = {
        "source": str(args.data),
        "samples": len(samples),
        "suspicious_threshold": suspicious_threshold,
        "malicious_threshold": malicious_threshold,
        "verdict_counts": {
            verdict: verdicts.count(verdict)
            for verdict in ("benign", "suspicious", "malicious")
        },
        "true_positive": tp,
        "false_negative": fn,
        "false_positive": fp,
        "true_negative": tn,
        "recall": tp / max(1, tp + fn),
        "false_positive_rate": fp / max(1, fp + tn),
        "precision": tp / max(1, tp + fp),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
