from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from prodog_sast.email_text import fields_to_model_text
from prodog_sast.model import load_artifact, predict
from prodog_sast.transformer import (
    DEFAULT_MODEL_PATH,
    combine_predictions,
    load_transformer,
    predict_transformer,
)

FILES = ("test_cases_dh_base.json", "test_cases_ds_base.json")
VERDICTS = ("safe", "suspicious", "danger")


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _distribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(record["verdict"] for record in records)
    return {
        "samples": len(records),
        "verdicts": {verdict: counts[verdict] for verdict in VERDICTS},
        "attack_safe_escape_rate": counts["safe"] / len(records),
        "review_recall": (counts["suspicious"] + counts["danger"]) / len(records),
        "danger_recall": counts["danger"] / len(records),
    }


def _evaluation(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_attack_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_tool[record["user_tool"]].append(record)
        by_attack_type[str(record["attack_type"])].append(record)
    return {
        "overall": _distribution(records),
        "by_user_tool": {
            name: _distribution(group) for name, group in sorted(by_tool.items())
        },
        "by_attack_type": {
            name: _distribution(group)
            for name, group in sorted(by_attack_type.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the detector on held-out InjecAgent Gmail cases"
    )
    parser.add_argument("data_root")
    parser.add_argument("--model", default="models/prompt_injection.joblib")
    parser.add_argument("--transformer-model", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--transformer-batch-size", type=int, default=16)
    parser.add_argument("--output", default="reports/injecagent-gmail-metrics.json")
    args = parser.parse_args()

    samples: list[dict[str, Any]] = []
    for filename in FILES:
        values = json.loads((Path(args.data_root) / filename).read_text(encoding="utf-8"))
        for index, value in enumerate(values):
            user_tool = str(value.get("User Tool", ""))
            if not user_tool.startswith("Gmail"):
                continue
            samples.append(
                {
                    "file": filename,
                    "index": index,
                    "user_tool": user_tool,
                    "attack_type": value.get("Attack Type"),
                    "text": fields_to_model_text(
                        body_text=str(value.get("Tool Response", ""))
                    ),
                }
            )
    if not samples:
        raise ValueError("no InjecAgent Gmail cases found")

    texts = [sample["text"] for sample in samples]
    artifact = load_artifact(args.model)
    transformer = load_transformer(args.transformer_model)
    tfidf_results = predict(artifact, texts)
    transformer_results = predict_transformer(
        transformer, texts, batch_size=args.transformer_batch_size
    )
    ensemble_results = combine_predictions(tfidf_results, transformer_results)

    def attach(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**sample, **result}
            for sample, result in zip(samples, results, strict=True)
        ]

    tfidf_records = attach(tfidf_results)
    transformer_records = attach(transformer_results)
    ensemble_records = attach(ensemble_results)

    report = {
        "dataset": "InjecAgent Gmail base",
        "source_sha256": {
            filename: sha256(Path(args.data_root) / filename) for filename in FILES
        },
        "training_status": "held out; never used for training or threshold selection",
        "models": {
            "tfidf": {
                "path": args.model,
                "sha256": sha256(args.model),
                "suspicious_threshold": artifact["suspicious_threshold"],
                "danger_threshold": artifact["danger_threshold"],
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
            "tfidf_only": _evaluation(tfidf_records),
            "transformer_only": _evaluation(transformer_records),
            "tfidf_transformer_ensemble": _evaluation(ensemble_records),
        },
        "comparison": {
            "ensemble_changed_verdicts": sum(
                tfidf["verdict"] != ensemble["verdict"]
                for tfidf, ensemble in zip(
                    tfidf_results, ensemble_results, strict=True
                )
            )
        },
        "safe_examples": [
            {
                key: sample[key]
                for key in (
                    "file",
                    "index",
                    "user_tool",
                    "attack_type",
                    "prompt_injection_probability",
                    "verdict",
                )
            }
            for sample in ensemble_records
            if sample["verdict"] == "safe"
        ][:30],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
