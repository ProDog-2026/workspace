from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from prodog_sast.email_text import fields_to_model_text
from prodog_sast.model import load_artifact, predict

FILES = ("test_cases_dh_base.json", "test_cases_ds_base.json")
VERDICTS = ("safe", "suspicious", "danger")


def _distribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(record["verdict"] for record in records)
    return {
        "samples": len(records),
        "verdicts": {verdict: counts[verdict] for verdict in VERDICTS},
        "attack_safe_escape_rate": counts["safe"] / len(records),
        "review_recall": (counts["suspicious"] + counts["danger"]) / len(records),
        "danger_recall": counts["danger"] / len(records),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the detector on held-out InjecAgent Gmail cases"
    )
    parser.add_argument("data_root")
    parser.add_argument("--model", default="models/prompt_injection.joblib")
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

    results = predict(load_artifact(args.model), [sample["text"] for sample in samples])
    for sample, result in zip(samples, results, strict=True):
        sample.update(result)

    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_attack_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        by_tool[sample["user_tool"]].append(sample)
        by_attack_type[str(sample["attack_type"])].append(sample)

    report = {
        "dataset": "InjecAgent Gmail base",
        "training_status": "held out; never used for training or threshold selection",
        "overall": _distribution(samples),
        "by_user_tool": {
            name: _distribution(records) for name, records in sorted(by_tool.items())
        },
        "by_attack_type": {
            name: _distribution(records)
            for name, records in sorted(by_attack_type.items())
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
            for sample in samples
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
