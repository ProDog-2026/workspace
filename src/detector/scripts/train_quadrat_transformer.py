from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

from prodog_sast.email_text import fields_to_model_text
from prodog_sast.transformer import CONFIG_NAME, predict_transformer

REVISION = "v1.0.2"
BASE_URL = f"https://huggingface.co/datasets/mihailgribov/quadrat-ipi/resolve/{REVISION}/data"
FILES = ("negatives.jsonl", "positives.jsonl")


def _download(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for filename in FILES:
        output = destination / filename
        if output.is_file():
            continue
        partial = output.with_suffix(output.suffix + ".part")
        request = Request(f"{BASE_URL}/{filename}", headers={"User-Agent": "ProDog-train/1"})
        with urlopen(request, timeout=120) as response:  # noqa: S310 - fixed HTTPS source
            with partial.open("wb") as handle:
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
        partial.replace(output)


def _split(row: dict[str, Any], seed: int) -> str:
    group = f"{row.get('host_source', '')}:{row.get('host_id', row['id'])}"
    value = int.from_bytes(hashlib.sha256(f"{seed}:{group}".encode()).digest()[:8])
    return "validation" if value % 5 == 0 else "train"


def _rank(row: dict[str, Any], seed: int) -> bytes:
    return hashlib.sha256(f"{seed}:{row['id']}".encode()).digest()


def _email_rows(path: Path, label: int, seed: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("host_type") != "email":
                continue
            row["numeric_label"] = label
            row["model_split"] = _split(row, seed)
            rows.append(row)
    return rows


def _take(rows: list[dict[str, Any]], limit: int, seed: int) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: _rank(row, seed))
    return ordered if limit <= 0 else ordered[:limit]


def _centered_window(text: str, span: list[int], size: int) -> str:
    start, end = int(span[0]), int(span[1])
    center = (start + end) // 2
    left = max(0, min(len(text) - size, center - size // 2))
    return text[left : left + size]


def _clean_window(text: str, row_id: str, size: int) -> str:
    if len(text) <= size:
        return text
    possible = len(text) - size + 1
    offset = int.from_bytes(hashlib.sha256(row_id.encode()).digest()[:8]) % possible
    return text[offset : offset + size]


def _training_text(row: dict[str, Any], window_chars: int) -> str:
    text = str(row["text"])
    if row["numeric_label"] == 1 and row.get("inj_span"):
        body = _centered_window(text, row["inj_span"], window_chars)
    else:
        body = _clean_window(text, str(row["id"]), window_chars)
    return fields_to_model_text(subject=str(row.get("subject") or ""), body_text=body)


def _threshold(labels: np.ndarray, scores: np.ndarray, max_fpr: float) -> float:
    candidates = np.unique(np.concatenate(([0.0, 0.5, 1.0], scores)))
    best: tuple[float, float, float] | None = None
    for threshold in candidates:
        predicted = scores >= threshold
        negatives = labels == 0
        positives = labels == 1
        fpr = float(np.mean(predicted[negatives]))
        recall = float(np.mean(predicted[positives]))
        candidate = (recall, -fpr, -float(threshold))
        if fpr <= max_fpr and (best is None or candidate > best):
            best = candidate
    return -best[2] if best else 1.0


def _metrics(labels: np.ndarray, scores: np.ndarray, threshold: float) -> dict[str, Any]:
    predicted = (scores >= threshold).astype(int)
    negatives = labels == 0
    positives = labels == 1
    return {
        "samples": int(len(labels)),
        "positives": int(np.sum(positives)),
        "true_positive": int(np.sum((predicted == 1) & positives)),
        "false_positive": int(np.sum((predicted == 1) & negatives)),
        "true_negative": int(np.sum((predicted == 0) & negatives)),
        "false_negative": int(np.sum((predicted == 0) & positives)),
        "precision": float(precision_score(labels, predicted, zero_division=0)),
        "recall": float(recall_score(labels, predicted, zero_division=0)),
        "f1": float(f1_score(labels, predicted, zero_division=0)),
        "false_positive_rate": float(np.mean(predicted[negatives])),
        "roc_auc": float(roc_auc_score(labels, scores)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fine-tune a small Transformer on Quadrat email rows"
    )
    parser.add_argument("--data", default=f"data/vendor/quadrat-ipi-{REVISION}")
    parser.add_argument("--output", default="models/quadrat-transformer")
    parser.add_argument("--report", default="reports/quadrat-transformer-metrics.json")
    parser.add_argument("--base-model", default="prajjwal1/bert-tiny")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=3e-5)
    # 모델 상한(512)까지 쓴다. 256이면 공격 학습 샘플 62%가 잘린다(#86).
    parser.add_argument("--max-length", type=int, default=512)
    # 학습 샘플 하나를 만들 때 원문에서 떼올 글자 수. 추론 조각 나누기와 무관하다.
    parser.add_argument("--training-window-chars", type=int, default=1000)
    parser.add_argument("--max-train-per-label", type=int, default=0)
    parser.add_argument("--max-validation-per-label", type=int, default=1000)
    parser.add_argument("--max-suspicious-fpr", type=float, default=0.05)
    parser.add_argument("--max-danger-fpr", type=float, default=0.002)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    data_dir = Path(args.data)
    _download(data_dir)
    rows = _email_rows(data_dir / "negatives.jsonl", 0, args.seed)
    rows += _email_rows(data_dir / "positives.jsonl", 1, args.seed)

    by_split_label = {
        (split, label): [
            row
            for row in rows
            if row["model_split"] == split and row["numeric_label"] == label
        ]
        for split in ("train", "validation")
        for label in (0, 1)
    }
    train_limit = min(
        len(by_split_label[("train", 0)]),
        len(by_split_label[("train", 1)]),
    )
    if args.max_train_per_label > 0:
        train_limit = min(train_limit, args.max_train_per_label)
    train_rows = _take(by_split_label[("train", 0)], train_limit, args.seed)
    train_rows += _take(by_split_label[("train", 1)], train_limit, args.seed)
    validation_rows = _take(
        by_split_label[("validation", 0)], args.max_validation_per_label, args.seed
    )
    validation_rows += _take(
        by_split_label[("validation", 1)], args.max_validation_per_label, args.seed
    )
    if not train_rows or {row["numeric_label"] for row in validation_rows} != {0, 1}:
        raise ValueError("Quadrat email rows must contain both labels in both splits")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    model = AutoModelForSequenceClassification.from_pretrained(args.base_model, num_labels=2)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    examples = [
        (_training_text(row, args.training_window_chars), row["numeric_label"])
        for row in train_rows
    ]
    losses: list[float] = []
    model.train()
    for _ in range(args.epochs):
        random.shuffle(examples)
        for start in range(0, len(examples), args.batch_size):
            batch = examples[start : start + args.batch_size]
            encoded = tokenizer(
                [text for text, _ in batch],
                padding=True,
                truncation=True,
                max_length=args.max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            labels = torch.tensor([label for _, label in batch], device=device)
            optimizer.zero_grad()
            output = model(**encoded, labels=labels)
            output.loss.backward()
            optimizer.step()
            losses.append(float(output.loss.detach().cpu()))

    model.eval()
    # 임계값 재측정용 임시값. 아래 _threshold 가 실제 운영값을 고른다.
    scoring_config = {"suspicious_threshold": 0.5, "danger_threshold": 0.9}
    validation_texts = [
        fields_to_model_text(
            subject=str(row.get("subject") or ""), body_text=str(row["text"])
        )
        for row in validation_rows
    ]
    scored = predict_transformer(
        {"model": model, "tokenizer": tokenizer, "device": device, "config": scoring_config},
        validation_texts,
        batch_size=args.batch_size,
    )
    labels = np.array([row["numeric_label"] for row in validation_rows], dtype=int)
    scores = np.array([result["prompt_injection_probability"] for result in scored])
    suspicious = _threshold(labels, scores, args.max_suspicious_fpr)
    danger = _threshold(labels, scores, args.max_danger_fpr)
    if danger <= suspicious:
        danger = float(np.nextafter(suspicious, 1.0))
    if not 0 <= suspicious < danger <= 1:
        raise ValueError("calibration did not produce valid three-level thresholds")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(output)
    tokenizer.save_pretrained(output)
    config = {
        "format_version": 1,
        "dataset": f"Quadrat-IPI {REVISION}",
        "dataset_url": "https://huggingface.co/datasets/mihailgribov/quadrat-ipi",
        "filter": {"host_type": "email"},
        "base_model": args.base_model,
        # 조각 상한은 추론 때 모델에서 읽는다. 여기 적으면 둘이 어긋난다(#86).
        "train_max_length": args.max_length,
        "training_window_chars": args.training_window_chars,
        "suspicious_threshold": suspicious,
        "danger_threshold": danger,
    }
    (output / CONFIG_NAME).write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = {
        **config,
        "device": str(device),
        "seed": args.seed,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "training": {
            "samples": len(train_rows),
            "per_label": train_limit,
            "mean_loss": float(np.mean(losses)),
        },
        "validation_population": {
            "samples": len(validation_rows),
            "clean": int(np.sum(labels == 0)),
            "injected": int(np.sum(labels == 1)),
        },
        "validation_review_gate": _metrics(labels, scores, suspicious),
        "validation_danger_gate": _metrics(labels, scores, danger),
        "note": (
            "Quadrat populations were group-split by host_source and host_id; "
            "this is not an independent external test."
        ),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
