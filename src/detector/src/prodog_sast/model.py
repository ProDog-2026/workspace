from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.exceptions import InconsistentVersionWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import FeatureUnion, Pipeline

from .data import Sample


@dataclass(frozen=True)
class Metrics:
    samples: int
    positives: int
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    roc_auc: float


def build_pipeline(seed: int = 42) -> Pipeline:
    features = FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=1,
                    max_df=0.995,
                    sublinear_tf=True,
                    max_features=120_000,
                ),
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    min_df=1,
                    max_features=160_000,
                    sublinear_tf=True,
                ),
            ),
        ]
    )
    classifier = SGDClassifier(
        loss="log_loss",
        class_weight="balanced",
        alpha=1e-5,
        max_iter=100,
        tol=1e-4,
        average=True,
        random_state=seed,
    )
    return Pipeline([("features", features), ("classifier", classifier)])


def _check_partition(name: str, labels: np.ndarray) -> None:
    if len(set(labels.tolist())) != 2:
        raise ValueError(f"{name} split must contain benign and injection samples")


def _split(samples: Sequence[Sample], seed: int) -> dict[str, np.ndarray]:
    if len({sample.group for sample in samples}) < 8:
        raise ValueError("at least 8 distinct groups are required for leakage-safe splitting")
    indexes = np.arange(len(samples))
    labels = np.array([sample.label for sample in samples])
    groups = np.array([sample.group for sample in samples])
    train_split = GroupShuffleSplit(n_splits=1, test_size=0.30, random_state=seed)
    train_idx, holdout_idx = next(train_split.split(indexes, labels, groups))
    holdout_groups = groups[holdout_idx]
    holdout_labels = labels[holdout_idx]
    final_split = GroupShuffleSplit(n_splits=1, test_size=0.50, random_state=seed + 1)
    val_rel, test_rel = next(final_split.split(holdout_idx, holdout_labels, holdout_groups))
    result = {
        "train": indexes[train_idx],
        "validation": holdout_idx[val_rel],
        "test": holdout_idx[test_rel],
    }
    for name, values in result.items():
        _check_partition(name, labels[values])
    return result


def _threshold(labels: np.ndarray, probabilities: np.ndarray, max_fpr: float) -> float:
    candidates = np.unique(
        np.concatenate(
            ([0.0, 0.5, 1.0], probabilities, [np.nextafter(probabilities.max(), 2.0)])
        )
    )
    best: tuple[float, float, float] | None = None
    for threshold in candidates:
        predicted = probabilities >= threshold
        negatives = labels == 0
        positives = labels == 1
        fpr = float(np.sum(predicted & negatives) / max(1, np.sum(negatives)))
        recall = float(np.sum(predicted & positives) / max(1, np.sum(positives)))
        if fpr <= max_fpr:
            candidate = (recall, -fpr, -float(threshold))
            if best is None or candidate > best:
                best = candidate
    if best is None:
        return 1.0
    return -best[2]


def _metrics(labels: np.ndarray, probabilities: np.ndarray, threshold: float) -> Metrics:
    predicted = (probabilities >= threshold).astype(int)
    positives = labels == 1
    negatives = labels == 0
    tp = int(np.sum((predicted == 1) & positives))
    fp = int(np.sum((predicted == 1) & negatives))
    tn = int(np.sum((predicted == 0) & negatives))
    fn = int(np.sum((predicted == 0) & positives))
    return Metrics(
        samples=len(labels),
        positives=int(np.sum(positives)),
        true_positive=tp,
        false_positive=fp,
        true_negative=tn,
        false_negative=fn,
        precision=float(precision_score(labels, predicted, zero_division=0)),
        recall=float(recall_score(labels, predicted, zero_division=0)),
        f1=float(f1_score(labels, predicted, zero_division=0)),
        false_positive_rate=float(fp / max(1, fp + tn)),
        roc_auc=float(roc_auc_score(labels, probabilities)),
    )


def train(
    samples: Sequence[Sample],
    *,
    seed: int = 42,
    max_validation_fpr: float = 0.02,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not 0 <= max_validation_fpr <= 1:
        raise ValueError("max_validation_fpr must be between 0 and 1")
    split = _split(samples, seed)
    texts = np.array([sample.text for sample in samples], dtype=object)
    labels = np.array([sample.label for sample in samples], dtype=int)
    groups = np.array([sample.group for sample in samples], dtype=object)

    pipeline = build_pipeline(seed)
    pipeline.fit(texts[split["train"]].tolist(), labels[split["train"]])
    val_probability = pipeline.predict_proba(texts[split["validation"]].tolist())[:, 1]
    threshold = _threshold(labels[split["validation"]], val_probability, max_validation_fpr)
    test_probability = pipeline.predict_proba(texts[split["test"]].tolist())[:, 1]

    split_groups = {
        name: sorted(set(groups[indexes].tolist())) for name, indexes in split.items()
    }
    artifact = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sklearn_version": sklearn.__version__,
        "pipeline": pipeline,
        "threshold": threshold,
    }
    report = {
        "threshold": threshold,
        "max_validation_fpr": max_validation_fpr,
        "split_samples": {name: int(len(indexes)) for name, indexes in split.items()},
        "split_group_counts": {name: len(values) for name, values in split_groups.items()},
        "group_overlap": {
            "train_validation": bool(
                set(split_groups["train"]) & set(split_groups["validation"])
            ),
            "train_test": bool(set(split_groups["train"]) & set(split_groups["test"])),
            "validation_test": bool(
                set(split_groups["validation"]) & set(split_groups["test"])
            ),
        },
        "validation": asdict(
            _metrics(labels[split["validation"]], val_probability, threshold)
        ),
        "test": asdict(_metrics(labels[split["test"]], test_probability, threshold)),
    }
    artifact["report"] = report
    return artifact, report


def save_artifact(artifact: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path)


def load_artifact(path: str | Path) -> dict[str, Any]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", InconsistentVersionWarning)
            artifact = joblib.load(path)
    except InconsistentVersionWarning as error:
        raise ValueError("model and scikit-learn versions are incompatible") from error
    if not isinstance(artifact, dict) or artifact.get("format_version") != 1:
        raise ValueError("unsupported model artifact")
    if artifact.get("sklearn_version") not in {None, sklearn.__version__}:
        raise ValueError("model and scikit-learn versions are incompatible")
    return artifact


def predict(artifact: dict[str, Any], texts: Sequence[str]) -> list[dict[str, Any]]:
    probabilities = artifact["pipeline"].predict_proba(list(texts))[:, 1]
    threshold = float(artifact["threshold"])
    return [
        {
            "prompt_injection_probability": round(float(probability), 6),
            "threshold": round(threshold, 6),
            "verdict": "suspicious" if probability >= threshold else "benign",
        }
        for probability in probabilities
    ]
