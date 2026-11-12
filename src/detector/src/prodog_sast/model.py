from __future__ import annotations

import hashlib
import warnings
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.exceptions import InconsistentVersionWarning
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import FeatureUnion, Pipeline

from .data import ROW_GROUP_PREFIX, Sample

# Derived from the 95th percentile of benign validation scores in the current model.
DEFAULT_SUSPICIOUS_THRESHOLD = 0.0400186634741522
# Operational safe gate chosen on the Quadrat-IPI Clinton development set.
MAX_OPERATIONAL_SUSPICIOUS_THRESHOLD = 0.05
SPLITS = ("train", "validation", "test")
DEFAULT_BATCH_SIZE = 2048
# The danger line must be stricter than the review budget, or it lands on the gate.
DEFAULT_DANGER_FPR = 0.002
# alpha=1e-6 drove the scores to exactly 0 and 1, which left the thresholds at
# 3e-06 and 0.99999 and cost ROC AUC; 1e-4 calibrated slightly better but passed
# fewer benign mails. 1e-5 keeps the best benign pass rate at the same AUC.
DEFAULT_ALPHA = 1e-5
DEFAULT_EPOCHS = 3

SampleStream = Callable[[], Iterable[Sample]]


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


@dataclass(frozen=True)
class SplitPlan:
    """Which split every (source, group) belongs to, plus the training weights."""

    split_of: dict[tuple[str, str], str]
    weights: dict[tuple[str, int], float]
    split_samples: dict[str, int]
    split_group_counts: dict[str, int]
    source_split_samples: dict[str, dict[str, int]]
    source_split_group_counts: dict[str, dict[str, int]]
    group_overlap: dict[str, bool]
    row_group_fallback: bool


def build_pipeline(seed: int = 42, *, alpha: float = 1e-6) -> Pipeline:
    """Hashed word+char features so the vocabulary never has to fit in memory.

    A vocabulary-based TfidfVectorizer cannot build this corpus: the measured
    density is ~2.7k non-zeros per email, so the training matrix alone needs
    about 6 GB before the intermediate vocabulary is counted. HashingVectorizer
    is stateless, which lets `train` fit the whole 2/3 training share in
    minibatches instead of sampling the corpus down.
    """
    features = FeatureUnion(
        [
            (
                "word",
                HashingVectorizer(
                    ngram_range=(1, 2),
                    n_features=2**20,
                    alternate_sign=False,
                    binary=True,
                    norm="l2",
                    dtype=np.float32,
                ),
            ),
            (
                "char",
                HashingVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    n_features=2**20,
                    alternate_sign=False,
                    binary=True,
                    norm="l2",
                    dtype=np.float32,
                ),
            ),
        ]
    )
    classifier = SGDClassifier(
        loss="log_loss",
        alpha=alpha,
        max_iter=100,
        tol=1e-4,
        average=True,
        random_state=seed,
    )
    return Pipeline([("features", features), ("classifier", classifier)])


def _as_stream(samples: Sequence[Sample] | SampleStream) -> SampleStream:
    if callable(samples):
        return samples
    return lambda: samples


def _group_order(source: str, groups: Iterable[str], seed: int) -> list[str]:
    return sorted(
        groups,
        key=lambda group: hashlib.sha256(f"{seed}:{source}:{group}".encode()).digest(),
    )


# Square-root damping on each source's size: unweighted, LLMail's 200k attacks drown
# every other source; fully equalised, a few hundred rows reach a weight near 140,
# which pushes the logistic scores to exactly 0 or 1 and costs three points of ROC AUC.
SOURCE_WEIGHT_POWER = 0.5
# No single mail may count for more than this many ordinary mails. LLMail ships 160
# benign probe mails against 200k attacks, and balancing those two sides exactly would
# hand each probe mail a weight above 100 and saturate the model again.
MAX_SAMPLE_WEIGHT = 32.0


def _label_balanced_weights(
    train_counts: dict[tuple[str, int], int],
) -> dict[tuple[str, int], float]:
    """Damp each (source, label) cell by size, then equalise the two labels globally.

    Forcing both sides of every source to exactly equal mass was tried and rejected:
    it starves the small sources' attack side, which dragged the safe gate to zero and
    cut the benign pass rate from 98% to 37%. The weights come out mean-1, so the cap
    below is expressed directly in "ordinary mails".
    """
    total = sum(train_counts.values())
    damped = {cell: count**SOURCE_WEIGHT_POWER for cell, count in train_counts.items()}
    label_mass = {
        label: sum(
            value for (_, cell_label), value in damped.items() if cell_label == label
        )
        for label in (0, 1)
    }
    if not all(label_mass.values()):
        raise ValueError("training split must contain benign and injection samples")
    return {
        (source, label): min(
            (total / 2)
            / (
                label_mass[label]
                * count ** (1 - SOURCE_WEIGHT_POWER)
            ),
            MAX_SAMPLE_WEIGHT,
        )
        for (source, label), count in train_counts.items()
    }


def _plan(samples: SampleStream, seed: int) -> SplitPlan:
    """First pass: assign splits by (source, group) without holding any text."""
    preassigned: dict[str, dict[str, str]] = defaultdict(dict)
    free_groups: dict[str, set[str]] = defaultdict(set)
    counts: Counter[tuple[str, str, int]] = Counter()
    row_group_fallback = False

    for sample in samples():
        counts[(sample.source, sample.group, sample.label)] += 1
        if sample.group.startswith(ROW_GROUP_PREFIX):
            row_group_fallback = True
        if sample.split is None:
            free_groups[sample.source].add(sample.group)
        else:
            previous = preassigned[sample.source].setdefault(sample.group, sample.split)
            if previous != sample.split:
                raise ValueError(
                    f"source {sample.source!r} group {sample.group!r} crosses splits"
                )
    if not counts:
        raise ValueError("no samples")

    split_of: dict[tuple[str, str], str] = {}
    mixed = sorted(set(preassigned) & set(free_groups))
    if mixed:
        raise ValueError(
            f"source {mixed[0]!r} mixes pre-assigned and unassigned splits"
        )
    for source, groups in preassigned.items():
        if set(groups.values()) != set(SPLITS):
            raise ValueError(
                f"source {source!r} must assign every sample to train, validation, and test"
            )
        for group, split in groups.items():
            split_of[(source, group)] = split
    for source, groups in free_groups.items():
        ordered = _group_order(source, groups, seed)
        if len(ordered) < 3:
            raise ValueError(f"source {source!r} requires at least 3 distinct groups")
        train_count = max(1, min(len(ordered) - 2, len(ordered) * 2 // 3))
        validation_count = (len(ordered) - train_count) // 2
        for position, group in enumerate(ordered):
            if position < train_count:
                split_of[(source, group)] = "train"
            elif position < train_count + validation_count:
                split_of[(source, group)] = "validation"
            else:
                split_of[(source, group)] = "test"

    by_source_split: Counter[tuple[str, str, int]] = Counter()
    groups_by_source_split: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (source, group, label), count in counts.items():
        split = split_of[(source, group)]
        by_source_split[(source, split, label)] += count
        groups_by_source_split[(source, split)].add(group)

    sources = sorted({source for source, _ in split_of})
    split_samples = {
        split: sum(
            by_source_split[(source, split, label)]
            for source in sources
            for label in (0, 1)
        )
        for split in SPLITS
    }
    for split in SPLITS:
        present = {
            label
            for source in sources
            for label in (0, 1)
            if by_source_split[(source, split, label)]
        }
        if present != {0, 1}:
            raise ValueError(f"{split} split must contain benign and injection samples")

    plan_groups = {
        split: {key for key, value in split_of.items() if value == split}
        for split in SPLITS
    }
    train_counts = {
        (source, label): by_source_split[(source, "train", label)]
        for source in sources
        for label in (0, 1)
        if by_source_split[(source, "train", label)]
    }
    return SplitPlan(
        split_of=split_of,
        weights=_label_balanced_weights(train_counts),
        split_samples=split_samples,
        split_group_counts={
            split: len(values) for split, values in plan_groups.items()
        },
        source_split_samples={
            source: {
                split: sum(by_source_split[(source, split, label)] for label in (0, 1))
                for split in SPLITS
            }
            for source in sources
        },
        source_split_group_counts={
            source: {
                split: len(groups_by_source_split[(source, split)]) for split in SPLITS
            }
            for source in sources
        },
        group_overlap={
            "train_validation": bool(plan_groups["train"] & plan_groups["validation"]),
            "train_test": bool(plan_groups["train"] & plan_groups["test"]),
            "validation_test": bool(
                plan_groups["validation"] & plan_groups["test"]
            ),
        },
        row_group_fallback=row_group_fallback,
    )


def _batches(
    samples: Iterable[Sample],
    split_of: dict[tuple[str, str], str],
    want: str,
    batch_size: int,
) -> Iterator[list[Sample]]:
    batch: list[Sample] = []
    for sample in samples:
        if split_of[(sample.source, sample.group)] != want:
            continue
        batch.append(sample)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _score_split(
    pipeline: Pipeline,
    samples: SampleStream,
    plan: SplitPlan,
    want: str,
    batch_size: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    labels: list[int] = []
    sources: list[str] = []
    chunks: list[np.ndarray] = []
    for batch in _batches(samples(), plan.split_of, want, batch_size):
        chunks.append(
            pipeline.predict_proba([sample.text for sample in batch])[:, 1]
        )
        labels.extend(sample.label for sample in batch)
        sources.extend(sample.source for sample in batch)
    return (
        np.array(labels, dtype=int),
        np.concatenate(chunks),
        np.array(sources, dtype=object),
    )


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


def _safe_threshold(
    labels: np.ndarray,
    probabilities: np.ndarray,
    max_attack_safe_rate: float,
) -> float:
    attacks = probabilities[labels == 1]
    candidates = np.unique(np.concatenate(([0.0], attacks, [1.0])))
    allowed = [
        float(threshold)
        for threshold in candidates
        if float(np.mean(attacks < threshold)) <= max_attack_safe_rate
    ]
    return max(allowed, default=0.0)


def _triage_counts(
    labels: np.ndarray,
    probabilities: np.ndarray,
    suspicious_threshold: float,
    danger_threshold: float,
) -> dict[str, dict[str, int]]:
    def counts(mask: np.ndarray) -> dict[str, int]:
        values = probabilities[mask]
        return {
            "safe": int(np.sum(values < suspicious_threshold)),
            "suspicious": int(
                np.sum(
                    (values >= suspicious_threshold) & (values < danger_threshold)
                )
            ),
            "danger": int(np.sum(values >= danger_threshold)),
        }

    return {"benign": counts(labels == 0), "injection": counts(labels == 1)}


def _triage_by_source(
    labels: np.ndarray,
    probabilities: np.ndarray,
    sources: np.ndarray,
    suspicious_threshold: float,
    danger_threshold: float,
) -> dict[str, dict[str, dict[str, int]]]:
    return {
        source: _triage_counts(
            labels[sources == source],
            probabilities[sources == source],
            suspicious_threshold,
            danger_threshold,
        )
        for source in sorted(set(sources.tolist()))
    }


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


def _safe_gate_rates(
    labels: np.ndarray, probabilities: np.ndarray, gate: float
) -> dict[str, float]:
    attacks = probabilities[labels == 1]
    benign = probabilities[labels == 0]
    return {
        "attack_safe_rate": float(np.mean(attacks < gate)) if len(attacks) else 0.0,
        "benign_safe_rate": float(np.mean(benign < gate)) if len(benign) else 0.0,
        "review_rate": float(np.mean(probabilities >= gate)),
    }


PERCENTILES = (1, 5, 25, 50, 75, 95, 99, 99.5)


def _score_percentiles(
    labels: np.ndarray, probabilities: np.ndarray
) -> dict[str, dict[str, float]]:
    """Publish the score distribution so threshold choices can be re-derived."""
    return {
        name: {
            f"p{percentile:g}": float(np.percentile(values, percentile))
            for percentile in PERCENTILES
        }
        for name, values in (
            ("benign", probabilities[labels == 0]),
            ("injection", probabilities[labels == 1]),
        )
        if len(values)
    }


def train(
    samples: Sequence[Sample] | SampleStream,
    *,
    seed: int = 42,
    max_validation_fpr: float = 0.02,
    max_validation_attack_safe_rate: float = 0.005,
    max_validation_danger_fpr: float = DEFAULT_DANGER_FPR,
    alpha: float = DEFAULT_ALPHA,
    epochs: int = DEFAULT_EPOCHS,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not 0 <= max_validation_fpr <= 1:
        raise ValueError("max_validation_fpr must be between 0 and 1")
    if not 0 <= max_validation_danger_fpr <= 1:
        raise ValueError("max_validation_danger_fpr must be between 0 and 1")
    if not 0 <= max_validation_attack_safe_rate < 1:
        raise ValueError("max_validation_attack_safe_rate must be between 0 and 1")
    if epochs < 1:
        raise ValueError("epochs must be at least 1")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    stream = _as_stream(samples)
    plan = _plan(stream, seed)

    pipeline = build_pipeline(seed, alpha=alpha)
    features = pipeline.named_steps["features"]
    classifier = pipeline.named_steps["classifier"]
    features.fit([""])  # HashingVectorizer is stateless; this only marks it fitted.
    classes = np.array([0, 1])
    for _ in range(epochs):
        for batch in _batches(stream(), plan.split_of, "train", batch_size):
            classifier.partial_fit(
                features.transform([sample.text for sample in batch]),
                np.array([sample.label for sample in batch], dtype=int),
                classes=classes,
                sample_weight=np.array(
                    [plan.weights[(sample.source, sample.label)] for sample in batch]
                ),
            )

    val_labels, val_probability, val_sources = _score_split(
        pipeline, stream, plan, "validation", batch_size
    )
    # The safe gate is set by how many attacks may be let through, the danger line by
    # how many benign mails may be called malicious outright. The danger budget has to
    # be tighter than the review budget: picking both at the same 2% FPR puts the two
    # thresholds on top of each other and leaves no suspicious band at all.
    suspicious_threshold = _safe_threshold(
        val_labels, val_probability, max_validation_attack_safe_rate
    )
    danger_threshold = _threshold(val_labels, val_probability, max_validation_danger_fpr)
    suspicious_band_collapsed = suspicious_threshold >= danger_threshold
    if suspicious_band_collapsed:
        suspicious_threshold = float(np.nextafter(danger_threshold, 0.0))

    test_labels, test_probability, test_sources = _score_split(
        pipeline, stream, plan, "test", batch_size
    )

    artifact = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sklearn_version": sklearn.__version__,
        "pipeline": pipeline,
        # Keep threshold for artifacts created before the three-level verdict contract.
        "threshold": danger_threshold,
        "suspicious_threshold": suspicious_threshold,
        "danger_threshold": danger_threshold,
    }
    report = {
        "threshold": danger_threshold,
        "suspicious_threshold": suspicious_threshold,
        "danger_threshold": danger_threshold,
        "suspicious_band_collapsed": suspicious_band_collapsed,
        "max_validation_fpr": max_validation_fpr,
        "max_validation_attack_safe_rate": max_validation_attack_safe_rate,
        "max_validation_danger_fpr": max_validation_danger_fpr,
        "threshold_policy": {
            "suspicious": (
                "highest score keeping validation attacks below the safe gate at or "
                f"under {max_validation_attack_safe_rate:.3%}"
            ),
            "danger": (
                "lowest score whose validation benign false-positive rate stays at or "
                f"under {max_validation_danger_fpr:.3%}, maximising attack recall"
            ),
            "review_budget": (
                "validation benign review rate is compared against max_validation_fpr "
                f"({max_validation_fpr:.3%}) and reported, not enforced"
            ),
        },
        "features": "hashing word(1,2) + char_wb(3,5), 2^20 buckets each, binary + l2",
        "classifier": f"SGDClassifier(log_loss, alpha={alpha}, average) via partial_fit",
        "epochs": epochs,
        "batch_size": batch_size,
        "row_id_group_fallback": plan.row_group_fallback,
        "split_samples": plan.split_samples,
        "split_group_counts": plan.split_group_counts,
        "source_split_samples": plan.source_split_samples,
        "source_split_group_counts": plan.source_split_group_counts,
        "training_weight": {
            "by_source_label": {
                f"{source}:{label}": weight
                for (source, label), weight in sorted(plan.weights.items())
            }
        },
        "group_overlap": plan.group_overlap,
        "validation": asdict(
            _metrics(val_labels, val_probability, danger_threshold)
        ),
        "test": asdict(_metrics(test_labels, test_probability, danger_threshold)),
        "validation_safe_gate": asdict(
            _metrics(val_labels, val_probability, suspicious_threshold)
        ),
        "test_safe_gate": asdict(
            _metrics(test_labels, test_probability, suspicious_threshold)
        ),
        "validation_safe_gate_rates": _safe_gate_rates(
            val_labels, val_probability, suspicious_threshold
        ),
        "test_safe_gate_rates": _safe_gate_rates(
            test_labels, test_probability, suspicious_threshold
        ),
        "validation_score_percentiles": _score_percentiles(val_labels, val_probability),
        "test_score_percentiles": _score_percentiles(test_labels, test_probability),
        "validation_review_budget_met": bool(
            _safe_gate_rates(val_labels, val_probability, suspicious_threshold)[
                "benign_safe_rate"
            ]
            >= 1 - max_validation_fpr
        ),
        "validation_triage": _triage_counts(
            val_labels, val_probability, suspicious_threshold, danger_threshold
        ),
        "test_triage": _triage_counts(
            test_labels, test_probability, suspicious_threshold, danger_threshold
        ),
        "validation_triage_by_source": _triage_by_source(
            val_labels,
            val_probability,
            val_sources,
            suspicious_threshold,
            danger_threshold,
        ),
        "test_triage_by_source": _triage_by_source(
            test_labels,
            test_probability,
            test_sources,
            suspicious_threshold,
            danger_threshold,
        ),
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
    trained_threshold = float(
        artifact.get("suspicious_threshold", DEFAULT_SUSPICIOUS_THRESHOLD)
    )
    artifact["trained_suspicious_threshold"] = trained_threshold
    artifact["suspicious_threshold"] = min(
        trained_threshold, MAX_OPERATIONAL_SUSPICIOUS_THRESHOLD
    )
    return artifact


def predict(artifact: dict[str, Any], texts: Sequence[str]) -> list[dict[str, Any]]:
    probabilities = artifact["pipeline"].predict_proba(list(texts))[:, 1]
    suspicious_threshold = float(
        artifact.get("suspicious_threshold", DEFAULT_SUSPICIOUS_THRESHOLD)
    )
    danger_threshold = float(artifact.get("danger_threshold", artifact["threshold"]))
    if not 0 <= suspicious_threshold < danger_threshold <= 1:
        raise ValueError("artifact thresholds must satisfy 0 <= suspicious < danger <= 1")

    def verdict(probability: float) -> str:
        if probability >= danger_threshold:
            return "danger"
        if probability >= suspicious_threshold:
            return "suspicious"
        return "safe"

    return [
        {
            "prompt_injection_probability": round(float(probability), 6),
            "suspicious_threshold": round(suspicious_threshold, 6),
            "danger_threshold": round(danger_threshold, 6),
            "verdict": verdict(float(probability)),
        }
        for probability in probabilities
    ]
