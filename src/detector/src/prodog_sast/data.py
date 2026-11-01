from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .email_text import fields_to_model_text

LABEL_FIELDS = ("prompt_injection", "label", "is_injection")
GROUP_FIELDS = ("group_id", "scenario_id", "base_email_id", "team_id", "email_id")
ROW_GROUP_PREFIX = "row-"


@dataclass(frozen=True)
class Sample:
    text: str
    label: int
    group: str
    source: str = "unknown"
    split: str | None = None


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
                **{
                    key: record[key]
                    for key in ("split", "source_dataset", "dataset", "source")
                    if key in record
                },
                "group_id": group,
                "subject": str(record.get("scenario", "")),
                "body_text": str(variant.get("external_content", "")),
                "prompt_injection": label,
            }
        )
    return expanded


def iter_samples(path: str | Path) -> Iterator[Sample]:
    """Stream samples so a multi-gigabyte corpus never has to be held in memory."""
    path = Path(path)
    records = (
        expanded
        for record in _records(path)
        for expanded in _expand_paired_record(record)
    )
    for index, record in enumerate(records):
        label_value = _first(record, LABEL_FIELDS, None)
        if label_value is None:
            raise ValueError(f"sample {index}: one of {LABEL_FIELDS} is required")
        source = str(
            _first(record, ("source_dataset", "dataset", "source"), path.stem)
        ).strip()
        if not source:
            source = path.stem
        split = str(record.get("split", "")).strip() or None
        if split not in {None, "train", "validation", "test"}:
            raise ValueError(f"sample {index}: unsupported split: {split!r}")
        group = str(_first(record, GROUP_FIELDS, "")).strip()
        if not group:
            group = f"{ROW_GROUP_PREFIX}{index}"
        if "model_text" in record:
            text = str(record["model_text"])
        else:
            text = fields_to_model_text(
                subject=str(_first(record, ("subject", "title"))),
                body_text=str(_first(record, ("body_text", "body", "text", "content"))),
                body_html=str(_first(record, ("body_html", "html"))),
                sender=str(_first(record, ("from", "sender"))),
                reply_to=str(_first(record, ("reply_to", "reply-to"))),
            )
        yield Sample(
            text=text,
            label=_label(label_value),
            group=group,
            source=source,
            split=split,
        )


def load_samples(path: str | Path) -> tuple[list[Sample], bool]:
    samples = list(iter_samples(path))
    if not samples:
        raise ValueError(f"{path}: no samples")
    used_row_groups = any(
        sample.group.startswith(ROW_GROUP_PREFIX) for sample in samples
    )
    return samples, used_row_groups
