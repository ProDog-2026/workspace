"""Train the Korean mail injection classifiers (TF-IDF and KLUE RoBERTa) on one shared split.

Both models read the same rows and the same template-grouped split so the comparison
only reflects the model. Run from the repository root:

    python src/detector/scripts/train_korean_detector.py --model tfidf
    python src/detector/scripts/train_korean_detector.py --model transformer
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score

from prodog_sast.email_text import fields_to_model_text

ROOT = Path(__file__).resolve().parents[3]
CLEANED = ROOT / "docs/dataset/kopi-email-cleaned.jsonl"
CONFLICTS = ROOT / "docs/dataset/kopi-email-label-conflicts.jsonl"
SMOKE = ROOT / "docs/dataset/korean-smoke-mails.jsonl"
REPORTS = ROOT / "src/detector/reports"
MODELS = ROOT / "src/detector/models"
SPLITS = ("train", "validation", "test")
SHARES = {"train": 0.70, "validation": 0.15, "test": 0.15}


def load_rows(cleaned: Path, conflicts: Path | None) -> list[dict[str, str]]:
    rows = [json.loads(line) for line in cleaned.read_text(encoding="utf-8").splitlines() if line]
    if conflicts and conflicts.is_file():
        skip = {
            json.loads(line)["source_rows"]
            for line in conflicts.read_text(encoding="utf-8").splitlines()
            if line
        }
        rows = [row for row in rows if row["source_rows"] not in skip]
    for row in rows:
        row["label"] = 1 if row["original_label"] == "인젝션" else 0
        # CLI는 메일을 [SUBJECT]/[FROM]/[PLAIN] 표식이 붙은 형태로 모델에 넘긴다.
        # 학습도 같은 표현을 써야 추론에서 점수가 밀리지 않는다.
        row["model_text"] = fields_to_model_text(body_text=row["mail_text"])
    return rows


def template_key(row: dict[str, str]) -> str:
    """Group rows that came from the same source mail template.

    ponytail: 공격 구절을 빼고 숫자를 마스킹한 앞 30자만 쓴다. 거친 근사라 같은
    템플릿이 여러 그룹으로 갈릴 수 있다. 분할 누수 방향으로는 안전하지 않으니
    수작업 검수나 유사도 묶음이 필요하면 여기를 바꾼다.
    """
    text = row["mail_text"]
    phrase = row["suspected_attack_phrase"].strip()
    if phrase:
        text = text.replace(phrase, " ")
    text = re.sub(r"[0-9]+", "#", re.sub(r"\s+", "", text))
    return re.sub(r"[^가-힣#]", "", text)[:30]


def split_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Put whole template groups in one split, largest group first."""
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[template_key(row)].append(row)
    result: dict[str, list[dict[str, str]]] = {name: [] for name in SPLITS}
    for group in sorted(groups.values(), key=lambda item: (-len(item), template_key(item[0]))):
        name = min(SPLITS, key=lambda s: len(result[s]) / (SHARES[s] * len(rows)))
        result[name].extend(group)
    return result


def threshold_at_fpr(labels: np.ndarray, scores: np.ndarray, max_fpr: float) -> float:
    """Lowest score that keeps the benign false positive rate within the budget."""
    benign = np.sort(scores[labels == 0])[::-1]
    allowed = int(len(benign) * max_fpr)
    return float(np.nextafter(benign[allowed], 1.0)) if allowed < len(benign) else 0.0


def metrics(labels: np.ndarray, scores: np.ndarray, threshold: float) -> dict[str, Any]:
    predicted = scores >= threshold
    positives = labels == 1
    true_positive = int((predicted & positives).sum())
    false_positive = int((predicted & ~positives).sum())
    false_negative = int((~predicted & positives).sum())
    benign = int((~positives).sum())
    return {
        "samples": int(len(labels)),
        "positives": int(positives.sum()),
        "threshold": round(threshold, 6),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(true_positive / max(true_positive + false_positive, 1), 4),
        "recall": round(true_positive / max(int(positives.sum()), 1), 4),
        "false_positive_rate": round(false_positive / max(benign, 1), 4),
        "roc_auc": round(float(roc_auc_score(labels, scores)), 4),
    }


def train_tfidf(data: dict[str, list[dict[str, str]]], seed: int) -> tuple[Any, dict[str, Any]]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    pipeline = Pipeline(
        [
            (
                "features",
                FeatureUnion(
                    [
                        # 한국어는 조사가 붙어 단어 경계가 흐리다. 글자 n-gram을 같이 쓴다.
                        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
                        (
                            "char",
                            TfidfVectorizer(
                                analyzer="char_wb",
                                ngram_range=(2, 4),
                                min_df=2,
                                sublinear_tf=True,
                            ),
                        ),
                    ]
                ),
            ),
            ("classifier", LogisticRegression(max_iter=2000, C=4.0, random_state=seed)),
        ]
    )
    pipeline.fit(
        [row["model_text"] for row in data["train"]], [row["label"] for row in data["train"]]
    )

    def score(rows: list[dict[str, str]]) -> np.ndarray:
        return pipeline.predict_proba([row["model_text"] for row in rows])[:, 1]

    return pipeline, evaluate(data, score)


def train_transformer(
    data: dict[str, list[dict[str, str]]], seed: int, base_model: str, epochs: int, max_length: int
) -> tuple[Any, dict[str, Any]]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.manual_seed(seed)
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    # klue/roberta-small 설정에 torchscript=True가 있어 모델이 tuple을 반환한다.
    # 추론 코드가 .logits를 쓰므로 끈다.
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model, num_labels=2, torchscript=False
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5)
    rng = np.random.default_rng(seed)
    train = list(data["train"])
    batch_size = 16
    model.train()
    for epoch in range(epochs):
        rng.shuffle(train)
        total = 0.0
        for start in range(0, len(train), batch_size):
            batch = train[start : start + batch_size]
            encoded = tokenizer(
                [row["model_text"] for row in batch],
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            ).to(device)
            labels = torch.tensor([row["label"] for row in batch], device=device)
            loss = model(**encoded, labels=labels).loss
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            total += float(loss.detach()) * len(batch)
        print(f"epoch {epoch + 1}/{epochs} loss {total / len(train):.4f}", flush=True)

    model.eval()

    def score(rows: list[dict[str, str]]) -> np.ndarray:
        out: list[float] = []
        with torch.inference_mode():
            for start in range(0, len(rows), batch_size):
                batch = rows[start : start + batch_size]
                encoded = tokenizer(
                    [row["model_text"] for row in batch],
                    padding=True,
                    truncation=True,
                    max_length=max_length,
                    return_tensors="pt",
                ).to(device)
                logits = model(**encoded).logits
                out.extend(torch.softmax(logits, dim=-1)[:, 1].tolist())
        return np.asarray(out)

    return (model, tokenizer), evaluate(data, score)


def evaluate(data: dict[str, list[dict[str, str]]], score) -> dict[str, Any]:
    labels = {name: np.asarray([row["label"] for row in rows]) for name, rows in data.items()}
    scores = {name: score(rows) for name, rows in data.items()}
    thresholds = {
        "fpr_5pct": threshold_at_fpr(labels["validation"], scores["validation"], 0.05),
        "fpr_1pct": threshold_at_fpr(labels["validation"], scores["validation"], 0.01),
        "half": 0.5,
    }
    return {
        "split_sizes": {
            name: {"samples": len(rows), "positives": int(labels[name].sum())}
            for name, rows in data.items()
        },
        "validation": {
            name: metrics(labels["validation"], scores["validation"], value)
            for name, value in thresholds.items()
        },
        "test": {
            name: metrics(labels["test"], scores["test"], value)
            for name, value in thresholds.items()
        },
        # KoPI 밖의 한국어 메일. 템플릿을 외웠는지 드러내는 유일한 검사다.
        "smoke_out_of_domain": {
            "scores": {
                row["id"]: round(float(value), 3)
                for row, value in zip(data["smoke"], scores["smoke"], strict=True)
            },
            **{
                name: metrics(labels["smoke"], scores["smoke"], value)
                for name, value in thresholds.items()
            },
        }
        if "smoke" in data
        else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("tfidf", "transformer"), required=True)
    parser.add_argument("--cleaned", type=Path, default=CLEANED)
    parser.add_argument(
        "--conflicts",
        type=Path,
        default=CONFLICTS,
        help="라벨 충돌 행을 제외한다. --conflicts NONE 이면 전부 사용",
    )
    parser.add_argument(
        "--dataset-label",
        default="KoPI-Bench 이메일 정제본 (검수 전 후보)",
        help="산출물에 기록할 학습 데이터 출처 표기",
    )
    parser.add_argument("--base-model", default="klue/roberta-small")
    parser.add_argument("--epochs", type=int, default=3)
    # 모델 상한(512)까지 쓴다. 256이면 학습 입력 17%가 잘리고, 그중 130행은
    # 공격 문구까지 잘려 본문이 짝의 정상본과 같은데 라벨만 인젝션이 된다(#86).
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    # 0.5/0.75는 임시 운영값이다. smoke에서 Transformer 점수 순위는 분리되지만
    # 이 임계값으로 공격 3건을 모두 놓친다. 배포 전 별도 데이터로 임계값을 검증해야 한다.
    parser.add_argument("--suspicious", type=float, default=0.5)
    parser.add_argument("--danger", type=float, default=0.75)
    args = parser.parse_args()

    conflicts = None if str(args.conflicts) == "NONE" else args.conflicts
    rows = load_rows(args.cleaned, conflicts)
    data = split_rows(rows)
    if SMOKE.is_file():
        data["smoke"] = [
            json.loads(line) for line in SMOKE.read_text(encoding="utf-8").splitlines() if line
        ]
        for row in data["smoke"]:
            row["model_text"] = fields_to_model_text(body_text=row["mail_text"])
    for name in SPLITS:
        counts = Counter(row["original_label"] for row in data[name])
        print(
            f"{name}={len(data[name])} 인젝션={counts['인젝션']} 정상={counts['정상']}", flush=True
        )

    if args.model == "tfidf":
        pipeline, report = train_tfidf(data, args.seed)
        MODELS.mkdir(parents=True, exist_ok=True)
        import joblib
        import sklearn

        joblib.dump(
            {
                "format_version": 1,
                "sklearn_version": sklearn.__version__,
                "dataset": args.dataset_label,
                "pipeline": pipeline,
                "threshold": args.danger,
                "suspicious_threshold": args.suspicious,
                "danger_threshold": args.danger,
                # 영어 모델의 0.05 운영 상한은 한국어 점수 분포에 맞지 않는다.
                "max_operational_suspicious_threshold": 1.0,
                "report": report,
            },
            MODELS / "korean_tfidf.joblib",
        )
        report["artifact"] = "src/detector/models/korean_tfidf.joblib"
    else:
        (model, tokenizer), report = train_transformer(
            data, args.seed, args.base_model, args.epochs, args.max_length
        )
        out = MODELS / "korean-transformer"
        out.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(out)
        tokenizer.save_pretrained(out)
        suspicious, danger = args.suspicious, args.danger
        (out / "prodog_transformer.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "dataset": args.dataset_label,
                    "base_model": args.base_model,
                    # 조각 상한은 추론 때 모델에서 읽는다. 여기 적으면 둘이 어긋난다(#86).
                    "train_max_length": args.max_length,
                    "suspicious_threshold": suspicious,
                    "danger_threshold": float(max(danger, np.nextafter(suspicious, 1.0))),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        report["artifact"] = "src/detector/models/korean-transformer"

    report["model"] = args.model
    report["rows_used"] = len(rows)
    report["conflicts_excluded"] = conflicts is not None
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"korean-{args.model}-metrics.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["test"], ensure_ascii=False, indent=2))
    print(f"report -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
