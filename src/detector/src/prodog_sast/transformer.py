from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

CONFIG_NAME = "prodog_transformer.json"
DEFAULT_MODEL_PATH = "models/quadrat-transformer"
VERDICT_RANK = {"safe": 0, "suspicious": 1, "danger": 2}


def text_windows(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("window size and overlap must satisfy size > overlap >= 0")
    if not text:
        return [""]
    step = size - overlap
    return [text[start : start + size] for start in range(0, len(text), step)]


def load_transformer(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    config_path = path / CONFIG_NAME
    if not config_path.is_file():
        raise ValueError(f"missing Transformer config: {config_path}")
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as error:
        raise ValueError(
            'Transformer dependencies are missing; install with pip install -e ".[transformer]"'
        ) from error

    config = json.loads(config_path.read_text(encoding="utf-8"))
    suspicious = float(config["suspicious_threshold"])
    danger = float(config["danger_threshold"])
    if not 0 <= suspicious < danger <= 1:
        raise ValueError("Transformer thresholds must satisfy 0 <= suspicious < danger <= 1")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForSequenceClassification.from_pretrained(path).to(device)
    model.eval()
    return {
        "model": model,
        "tokenizer": AutoTokenizer.from_pretrained(path),
        "config": config,
        "device": device,
    }


def predict_transformer(
    artifact: dict[str, Any], texts: Sequence[str], *, batch_size: int = 16
) -> list[dict[str, Any]]:
    import torch

    config = artifact["config"]
    size = int(config["window_chars"])
    overlap = int(config["window_overlap"])
    suspicious = float(config["suspicious_threshold"])
    danger = float(config["danger_threshold"])
    flattened: list[str] = []
    owners: list[int] = []
    for owner, text in enumerate(texts):
        windows = text_windows(text, size, overlap)
        flattened.extend(windows)
        owners.extend([owner] * len(windows))

    scores = [0.0] * len(texts)
    with torch.inference_mode():
        for start in range(0, len(flattened), batch_size):
            encoded = artifact["tokenizer"](
                flattened[start : start + batch_size],
                padding=True,
                truncation=True,
                max_length=int(config["max_length"]),
                return_tensors="pt",
            )
            encoded = {key: value.to(artifact["device"]) for key, value in encoded.items()}
            probabilities = torch.softmax(artifact["model"](**encoded).logits, dim=-1)[:, 1]
            for owner, probability in zip(
                owners[start : start + batch_size], probabilities.tolist(), strict=True
            ):
                scores[owner] = max(scores[owner], float(probability))

    def verdict(score: float) -> str:
        if score >= danger:
            return "danger"
        if score >= suspicious:
            return "suspicious"
        return "safe"

    return [
        {
            "prompt_injection_probability": round(score, 6),
            "suspicious_threshold": round(suspicious, 6),
            "danger_threshold": round(danger, 6),
            "verdict": verdict(score),
        }
        for score in scores
    ]


def combine_predictions(
    tfidf: Sequence[dict[str, Any]], transformer: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(tfidf) != len(transformer):
        raise ValueError("detectors returned different result counts")
    combined: list[dict[str, Any]] = []
    for lexical, semantic in zip(tfidf, transformer, strict=True):
        final_rank = max(VERDICT_RANK[lexical["verdict"]], VERDICT_RANK[semantic["verdict"]])
        final_verdict = next(
            verdict for verdict, rank in VERDICT_RANK.items() if rank == final_rank
        )
        decided_by = [
            name
            for name, result in (("tfidf", lexical), ("transformer", semantic))
            if result["verdict"] == final_verdict
        ]
        combined.append(
            {
                "verdict": final_verdict,
                "decided_by": decided_by,
                "detectors": {"tfidf": lexical, "transformer": semantic},
            }
        )
    return combined
