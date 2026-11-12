from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from prodog_sast.data import load_samples
from prodog_sast.model import DEFAULT_SUSPICIOUS_THRESHOLD, load_artifact, predict
from prodog_sast.transformer import (
    DEFAULT_MODEL_PATH,
    combine_predictions,
    load_transformer,
    predict_transformer,
)


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


def evaluation(labels: list[int], results: list[dict]) -> dict:
    verdicts = [item["verdict"] for item in results]
    return {
        "verdict_counts": {
            verdict: verdicts.count(verdict)
            for verdict in ("safe", "suspicious", "danger")
        },
        "confusion_by_label": {
            name: {
                verdict: sum(
                    label == expected and predicted == verdict
                    for label, predicted in zip(labels, verdicts, strict=True)
                )
                for verdict in ("safe", "suspicious", "danger")
            }
            for name, expected in (("benign", 0), ("injection", 1))
        },
        "review_metrics": metrics(labels, verdicts, {"suspicious", "danger"}),
        "danger_metrics": metrics(labels, verdicts, {"danger"}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a model on labeled email data")
    parser.add_argument("data")
    parser.add_argument("--model", default="models/prompt_injection.joblib")
    parser.add_argument("--transformer-model", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--transformer-batch-size", type=int, default=16)
    parser.add_argument("--output", default="reports/external-metrics.json")
    args = parser.parse_args()

    samples, _ = load_samples(args.data)
    artifact = load_artifact(args.model)
    tfidf_results: list[dict] = []
    for start in range(0, len(samples), 128):
        batch = samples[start : start + 128]
        tfidf_results.extend(predict(artifact, [sample.text for sample in batch]))

    labels = [sample.label for sample in samples]
    transformer = load_transformer(args.transformer_model)
    transformer_results = predict_transformer(
        transformer,
        [sample.text for sample in samples],
        batch_size=args.transformer_batch_size,
    )
    ensemble_results = combine_predictions(tfidf_results, transformer_results)
    suspicious_threshold = artifact.get(
        "suspicious_threshold", DEFAULT_SUSPICIOUS_THRESHOLD
    )
    danger_threshold = artifact.get("danger_threshold", artifact["threshold"])
    report = {
        "source": str(args.data),
        "source_sha256": sha256(args.data),
        "samples": len(samples),
        "labels": {"benign": labels.count(0), "injection": labels.count(1)},
        "models": {
            "tfidf": {
                "path": args.model,
                "sha256": sha256(args.model),
                "suspicious_threshold": suspicious_threshold,
                "danger_threshold": danger_threshold,
            },
            "transformer": {
                "path": args.transformer_model,
                "weights_sha256": sha256(
                    Path(args.transformer_model) / "model.safetensors"
                ),
                "suspicious_threshold": transformer["config"]["suspicious_threshold"],
                "danger_threshold": transformer["config"]["danger_threshold"],
            },
        },
        "evaluations": {
            "tfidf_only": evaluation(labels, tfidf_results),
            "transformer_only": evaluation(labels, transformer_results),
            "tfidf_transformer_ensemble": evaluation(labels, ensemble_results),
        },
        "comparison": {
            "ensemble_changed_verdicts": sum(
                tfidf["verdict"] != ensemble["verdict"]
                for tfidf, ensemble in zip(
                    tfidf_results, ensemble_results, strict=True
                )
            )
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
