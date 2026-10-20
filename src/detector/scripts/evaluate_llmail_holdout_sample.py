from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from prodog_sast.data import iter_samples
from prodog_sast.model import _plan, load_artifact, predict


def _metrics(labels: list[int], verdicts: list[str]) -> dict[str, Any]:
    pairs = list(zip(labels, verdicts, strict=True))
    tp = sum(label == 1 and verdict != "safe" for label, verdict in pairs)
    fp = sum(label == 0 and verdict != "safe" for label, verdict in pairs)
    tn = sum(label == 0 and verdict == "safe" for label, verdict in pairs)
    fn = sum(label == 1 and verdict == "safe" for label, verdict in pairs)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "recall": tp / max(1, tp + fn),
        "precision": tp / max(1, tp + fp),
        "false_positive_rate": fp / max(1, fp + tn),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a deterministic LLMail same-source holdout sample"
    )
    parser.add_argument("data", nargs="?", default="data/processed/training-corpus.jsonl")
    parser.add_argument("--model", default="models/prompt_injection.joblib")
    parser.add_argument("--output", default="reports/llmail-holdout-sample-metrics.json")
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--suspicious-threshold", type=float)
    args = parser.parse_args()
    if args.samples < 1:
        raise ValueError("samples must be at least 1")

    def stream():
        return iter_samples(args.data)

    plan = _plan(stream, args.seed)
    test_rows = [
        sample
        for sample in stream()
        if sample.source == "llmail-inject"
        and plan.split_of[(sample.source, sample.group)] == "test"
    ]
    benign = [sample for sample in test_rows if sample.label == 0]
    attack = [sample for sample in test_rows if sample.label == 1]
    rng = random.Random(args.seed)
    selected_attack = rng.sample(attack, min(len(attack), max(0, args.samples - len(benign))))
    selected = benign + selected_attack
    rng.shuffle(selected)

    artifact = load_artifact(args.model)
    if args.suspicious_threshold is not None:
        danger_threshold = float(artifact["danger_threshold"])
        if not 0 <= args.suspicious_threshold < danger_threshold:
            raise ValueError(
                "suspicious threshold must satisfy 0 <= suspicious < danger"
            )
        artifact["suspicious_threshold"] = args.suspicious_threshold
    predictions = predict(artifact, [sample.text for sample in selected])
    labels = [sample.label for sample in selected]
    verdicts = [item["verdict"] for item in predictions]
    scores = [float(item["prompt_injection_probability"]) for item in predictions]

    def triage(label: int) -> dict[str, int]:
        return {
            verdict: sum(
                expected == label and predicted == verdict
                for expected, predicted in zip(labels, verdicts, strict=True)
            )
            for verdict in ("safe", "suspicious", "danger")
        }

    report = {
        "dataset": "Microsoft LLMail-Inject Challenge",
        "source": "https://huggingface.co/datasets/microsoft/llmail-inject-challenge",
        "evaluation_status": "same-source internal test; not external",
        "selection": {
            "split": "test",
            "seed": args.seed,
            "requested_samples": args.samples,
            "selected_samples": len(selected),
            "all_available_benign": len(benign),
            "sampled_injection": len(selected_attack),
            "holdout_pool": dict(Counter(sample.label for sample in test_rows)),
        },
        "thresholds": {
            "suspicious": artifact["suspicious_threshold"],
            "danger": artifact["danger_threshold"],
            "trained_suspicious": artifact["trained_suspicious_threshold"],
        },
        "triage": {"benign": triage(0), "injection": triage(1)},
        "review_metrics": _metrics(labels, verdicts),
        "score_percentiles": {
            name: {
                f"p{point}": round(float(np.percentile(values, point)), 6)
                for point in (5, 25, 50, 75, 95)
            }
            for name, values in (
                (
                    "benign",
                    [
                        score
                        for score, label in zip(scores, labels, strict=True)
                        if label == 0
                    ],
                ),
                (
                    "injection",
                    [
                        score
                        for score, label in zip(scores, labels, strict=True)
                        if label == 1
                    ],
                ),
            )
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
