from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import TextIO


def _write_pair(handle: TextIO, record: dict[str, object], group: str) -> None:
    for field, label in (("attack_emails", 1), ("benign_emails", 0)):
        sample = {
            "group_id": f"llmail-{group}",
            "body_text": record[field],
            "prompt_injection": label,
            "source_dataset": "llmail-inject",
        }
        handle.write(json.dumps(sample, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create leakage-safe LLMail train and external-test JSONL files"
    )
    parser.add_argument("archive")
    parser.add_argument("--train", default="data/processed/llmail-train.jsonl")
    parser.add_argument("--test", default="data/processed/llmail-external-test.jsonl")
    args = parser.parse_args()

    with zipfile.ZipFile(args.archive) as archive:
        with archive.open("llmail_inject_dataset.json") as handle:
            records = json.load(handle)

    train_path = Path(args.train)
    test_path = Path(args.test)
    train_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.parent.mkdir(parents=True, exist_ok=True)
    counts = {"train_pairs": 0, "test_pairs": 0}
    with train_path.open("w", encoding="utf-8") as train_handle, test_path.open(
        "w", encoding="utf-8"
    ) as test_handle:
        for record in records:
            digest = hashlib.sha256(record["attack_prompt"].encode("utf-8")).hexdigest()
            if int(digest[:8], 16) % 5 == 0:
                _write_pair(test_handle, record, digest)
                counts["test_pairs"] += 1
            else:
                _write_pair(train_handle, record, digest)
                counts["train_pairs"] += 1
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
