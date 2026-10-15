from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .email_text import fields_to_model_text

LABEL_FIELDS = ("prompt_injection", "label", "is_injection")
GROUP_FIELDS = ("group_id", "scenario_id", "base_email_id", "team_id", "email_id")


@dataclass(frozen=True)
class Sample:
    text: str
    label: int
    group: str


def _records(path: Path) -> Iterable[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)
        return
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError(f"{path}:{line_number}: object expected")
                    yield value
        return
    if suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value = value.get("data", value.get("records"))
        if not isinstance(value, list):
            raise ValueError(f"{path}: JSON root must be a list or contain data/records")
        for item in value:
            if not isinstance(item, dict):
                raise ValueError(f"{path}: every sample must be an object")
            yield item
        return
    raise ValueError("training data must be .csv, .json, or .jsonl")


def _first(record: dict[str, Any], names: tuple[str, ...], default: Any = "") -> Any:
    for name in names:
        if name in record and record[name] is not None:
            return record[name]
    return default


def _label(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and value in (0, 1):
        return int(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "attack", "injection", "malicious"}:
        return 1
    if normalized in {"0", "false", "no", "benign", "normal", "clean"}:
        return 0
    raise ValueError(f"unsupported label: {value!r}")


def _expand_paired_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    pair = record.get("pair")
    if not isinstance(pair, dict):
        return [record]
    group = str(record.get("id") or record.get("scenario") or "").strip()
    if not group:
        raise ValueError("paired sample requires id or scenario")
    expanded: list[dict[str, Any]] = []
    for name, label in (("attack", 1), ("benign", 0)):
        variant = pair.get(name)
        if not isinstance(variant, dict):
            raise ValueError(f"paired sample {group}: missing {name} object")
        expanded.append(
            {
                "group_id": group,
                "subject": str(record.get("scenario", "")),
                "body_text": str(variant.get("external_content", "")),
                "prompt_injection": label,
            }
        )
    return expanded


def load_samples(path: str | Path) -> tuple[list[Sample], bool]:
    path = Path(path)
    samples: list[Sample] = []
    used_row_groups = False
    records = (
        expanded
        for record in _records(path)
        for expanded in _expand_paired_record(record)
    )
    for index, record in enumerate(records):
        label_value = _first(record, LABEL_FIELDS, None)
        if label_value is None:
            raise ValueError(f"sample {index}: one of {LABEL_FIELDS} is required")
        group = str(_first(record, GROUP_FIELDS, "")).strip()
        if not group:
            group = f"row-{index}"
            used_row_groups = True
        text = fields_to_model_text(
            subject=str(_first(record, ("subject", "title"))),
            body_text=str(_first(record, ("body_text", "body", "text", "content"))),
            body_html=str(_first(record, ("body_html", "html"))),
            sender=str(_first(record, ("from", "sender"))),
            reply_to=str(_first(record, ("reply_to", "reply-to"))),
        )
        samples.append(Sample(text=text, label=_label(label_value), group=group))
    if not samples:
        raise ValueError(f"{path}: no samples")
    return samples, used_row_groups
