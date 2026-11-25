from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

CONFIG_NAME = "prodog_transformer.json"
DEFAULT_MODEL_PATH = "models/quadrat-transformer"
VERDICT_RANK = {"safe": 0, "suspicious": 1, "danger": 2}


# 조각이 겹치는 토큰 수. 한국어 코퍼스 공격 문구 136종의 최대 길이가 41토큰이라
# 문구가 조각 경계에 걸려도 다음 조각에는 온전히 들어간다.
WINDOW_OVERLAP_TOKENS = 64


def window_limit(model: Any, tokenizer: Any) -> int:
    """조각 하나에 넣을 최대 토큰 수.

    설정 파일에 적지 않는다. 상한은 모델의 위치 임베딩 개수가 정하고, 그 값을
    글자 수 같은 다른 단위로 한 번 더 적어두면 둘이 어긋난다(이슈 #86).
    RoBERTa는 max_position_embeddings 가 514지만 앞 2칸을 padding_idx 로 쓰므로
    실제로 쓸 수 있는 길이는 토크나이저가 들고 있는 512다.
    """
    return min(int(model.config.max_position_embeddings), int(tokenizer.model_max_length))


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

    # 토크나이저는 빈 리스트에 IndexError 를 낸다. 지우지 말 것.
    if not texts:
        return []

    config = artifact["config"]
    suspicious = float(config["suspicious_threshold"])
    danger = float(config["danger_threshold"])
    scores = [0.0] * len(texts)

    # 조각 나누기는 토크나이저가 한다. 글자 수로 잘라 넘기면 토큰 상한과 어긋나
    # 조각 뒷부분이 조용히 버려진다(이슈 #86).
    tokenizer = artifact["tokenizer"]
    encoded = tokenizer(
        list(texts),
        truncation=True,
        max_length=window_limit(artifact["model"], tokenizer),
        stride=WINDOW_OVERLAP_TOKENS,
        return_overflowing_tokens=True,
    )
    owners = encoded["overflow_to_sample_mapping"]
    chunks = encoded["input_ids"]
    pad_id = int(tokenizer.pad_token_id or 0)

    with torch.inference_mode():
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            width = max(len(ids) for ids in batch)
            encoded_batch = {
                "input_ids": torch.tensor([ids + [pad_id] * (width - len(ids)) for ids in batch]),
                "attention_mask": torch.tensor(
                    [[1] * len(ids) + [0] * (width - len(ids)) for ids in batch]
                ),
            }
            encoded_batch = {
                key: value.to(artifact["device"]) for key, value in encoded_batch.items()
            }
            probabilities = torch.softmax(artifact["model"](**encoded_batch).logits, dim=-1)[:, 1]
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
        probability = max(
            result["prompt_injection_probability"]
            for result in (lexical, semantic)
            if result["verdict"] == final_verdict
        )
        combined.append(
            {
                "verdict": final_verdict,
                "prompt_injection_probability": probability,
                "decided_by": decided_by,
                "detectors": {"tfidf": lexical, "transformer": semantic},
            }
        )
    return combined
