from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from prodog_sast.email_text import fields_to_model_text
from prodog_sast.model import DEFAULT_SUSPICIOUS_THRESHOLD, load_artifact, predict

POSITIONS = ("start", "middle", "end")
VERDICTS = ("safe", "suspicious", "danger")


def insert_attack(context: str, attack: str, position: str) -> str:
    if position == "start":
        return f"{attack}\n{context}"
    if position == "end":
        return f"{context}\n{attack}"
    if position == "middle":
        pivot = context.find(" ", len(context) // 2)
        if pivot < 0:
            pivot = len(context) // 2
        return f"{context[:pivot]}\n{attack}\n{context[pivot:]}"
    raise ValueError(f"unsupported position: {position}")


def load_contexts(path: Path) -> list[str]:
    contexts = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            context = record.get("context")
            if not isinstance(context, str) or not context.strip():
                raise ValueError(f"{path}:{line_number}: non-empty context required")
            contexts.append(context)
    if not contexts:
        raise ValueError(f"{path}: no contexts")
    return contexts


def load_attacks(path: Path) -> list[tuple[str, int, str]]:
    source = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise ValueError(f"{path}: object expected")
    attacks = []
    for category, prompts in source.items():
        if not isinstance(prompts, list):
            raise ValueError(f"{path}: attack list expected for {category}")
        for index, prompt in enumerate(prompts):
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"{path}: non-empty attack required for {category}[{index}]")
            attacks.append((category, index, prompt))
    if not attacks:
        raise ValueError(f"{path}: no attacks")
    return attacks


def distribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(record["verdict"] for record in records)
    scores = sorted(record["probability"] for record in records)

    def percentile(ratio: float) -> float:
        return scores[round((len(scores) - 1) * ratio)]

    return {
        "samples": len(records),
        "verdicts": {verdict: counts[verdict] for verdict in VERDICTS},
        "review_rate": (counts["suspicious"] + counts["danger"]) / len(records),
        "danger_rate": counts["danger"] / len(records),
        "score": {
            "min": scores[0],
            "p50": percentile(0.50),
            "p90": percentile(0.90),
            "p95": percentile(0.95),
            "max": scores[-1],
        },
    }


def value_summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def percentile(ratio: float) -> float:
        return ordered[round((len(ordered) - 1) * ratio)]

    return {
        "min": ordered[0],
        "p50": percentile(0.50),
        "p90": percentile(0.90),
        "p95": percentile(0.95),
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


def binary_metrics(
    clean: list[dict[str, Any]], injected: list[dict[str, Any]], detected: set[str]
) -> dict[str, float | int]:
    tp = sum(sample["verdict"] in detected for sample in injected)
    fn = len(injected) - tp
    fp = sum(sample["verdict"] in detected for sample in clean)
    tn = len(clean) - fp
    recall = tp / len(injected)
    false_positive_rate = fp / len(clean)
    return {
        "true_positive": tp,
        "false_negative": fn,
        "false_positive": fp,
        "true_negative": tn,
        "recall": recall,
        "false_positive_rate": false_positive_rate,
        "specificity": 1 - false_positive_rate,
        "balanced_accuracy": (recall + 1 - false_positive_rate) / 2,
        "precision": tp / (tp + fp),
    }


def unique_by_text(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {}
    for record in records:
        unique.setdefault(record["text"], record)
    return list(unique.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the detector on BIPIA EmailQA")
    parser.add_argument("context_data")
    parser.add_argument("attack_data")
    parser.add_argument("--model", default="models/prompt_injection.joblib")
    parser.add_argument("--output", default="reports/bipia-emailqa-metrics.json")
    args = parser.parse_args()

    contexts = load_contexts(Path(args.context_data))
    attacks = load_attacks(Path(args.attack_data))
    samples: list[dict[str, Any]] = []
    for context_index, context in enumerate(contexts):
        samples.append(
            {
                "label": 0,
                "context_index": context_index,
                "category": None,
                "attack_index": None,
                "attack": None,
                "position": "clean",
                "context": context,
                "text": fields_to_model_text(body_text=context),
            }
        )
        for category, attack_index, attack in attacks:
            for position in POSITIONS:
                samples.append(
                    {
                        "label": 1,
                        "context_index": context_index,
                        "category": category,
                        "attack_index": attack_index,
                        "attack": attack,
                        "position": position,
                        "context": context,
                        "text": fields_to_model_text(
                            body_text=insert_attack(context, attack, position)
                        ),
                    }
                )

    expected_attack_samples = len(contexts) * len(attacks) * len(POSITIONS)
    assert len(samples) == len(contexts) + expected_attack_samples

    artifact = load_artifact(args.model)
    results = []
    for start in range(0, len(samples), 128):
        batch = samples[start : start + 128]
        results.extend(predict(artifact, [sample["text"] for sample in batch]))

    for sample, result in zip(samples, results, strict=True):
        sample["verdict"] = result["verdict"]
        sample["probability"] = result["prompt_injection_probability"]

    clean = [sample for sample in samples if sample["label"] == 0]
    injected = [sample for sample in samples if sample["label"] == 1]
    unique_clean = unique_by_text(clean)
    unique_injected = unique_by_text(injected)
    by_position = defaultdict(list)
    by_category = defaultdict(list)
    by_attack = defaultdict(list)
    for sample in injected:
        by_position[sample["position"]].append(sample)
        by_category[sample["category"]].append(sample)
        by_attack[(sample["category"], sample["attack_index"], sample["attack"])].append(
            sample
        )

    clean_scores = {sample["context_index"]: sample["probability"] for sample in clean}
    deltas = [
        sample["probability"] - clean_scores[sample["context_index"]] for sample in injected
    ]

    false_positive_examples = sorted(
        (sample for sample in clean if sample["verdict"] != "safe"),
        key=lambda sample: sample["probability"],
        reverse=True,
    )[:20]
    lowest_scoring_attacks = sorted(injected, key=lambda sample: sample["probability"])[:20]
    attack_prompt_results = []
    for (category, attack_index, attack), records in by_attack.items():
        attack_prompt_results.append(
            {
                "category": category,
                "attack_index": attack_index,
                "attack": attack,
                **distribution(records),
            }
        )
    attack_prompt_results.sort(
        key=lambda result: (result["review_rate"], result["danger_rate"], result["attack"])
    )

    def example(sample: dict[str, Any]) -> dict[str, Any]:
        return {
            key: sample[key]
            for key in (
                "context_index",
                "category",
                "attack_index",
                "attack",
                "position",
                "probability",
                "verdict",
            )
        }

    report = {
        "dataset": {
            "name": "BIPIA EmailQA test",
            "context_data": args.context_data,
            "attack_data": args.attack_data,
            "clean_contexts": len(contexts),
            "unique_clean_contexts": len({sample["text"] for sample in clean}),
            "attack_prompts": len(attacks),
            "attack_categories": len({category for category, _, _ in attacks}),
            "positions": list(POSITIONS),
            "attack_samples": len(injected),
            "unique_attack_samples": len({sample["text"] for sample in injected}),
            "total_samples": len(samples),
            "middle_insertion": "first space at or after the character midpoint",
        },
        "thresholds": {
            "suspicious": artifact.get(
                "suspicious_threshold", DEFAULT_SUSPICIOUS_THRESHOLD
            ),
            "danger": artifact.get("danger_threshold", artifact["threshold"]),
        },
        "clean": distribution(clean),
        "injection": distribution(injected),
        "review_metrics": binary_metrics(clean, injected, {"suspicious", "danger"}),
        "danger_metrics": binary_metrics(clean, injected, {"danger"}),
        "deduplicated": {
            "total_samples": len(unique_clean) + len(unique_injected),
            "clean": distribution(unique_clean),
            "injection": distribution(unique_injected),
            "review_metrics": binary_metrics(
                unique_clean, unique_injected, {"suspicious", "danger"}
            ),
            "danger_metrics": binary_metrics(unique_clean, unique_injected, {"danger"}),
        },
        "paired_score_change": {
            "increased": sum(delta > 0 for delta in deltas),
            "unchanged": sum(delta == 0 for delta in deltas),
            "decreased": sum(delta < 0 for delta in deltas),
            "increase_rate": sum(delta > 0 for delta in deltas) / len(deltas),
            "delta": value_summary(deltas),
        },
        "injection_by_position": {
            position: distribution(by_position[position]) for position in POSITIONS
        },
        "injection_by_category": {
            category: distribution(records) for category, records in sorted(by_category.items())
        },
        "weakest_attack_prompts": attack_prompt_results[:15],
        "review_false_positive_examples": [example(sample) for sample in false_positive_examples],
        "lowest_scoring_attack_examples": [example(sample) for sample in lowest_scoring_attacks],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
