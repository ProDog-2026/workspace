"""List 정상 rows that contain an attack phrase marked in 인젝션 rows, for label review."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / "docs/dataset/kopi-email-cleaned.jsonl"
OUTPUT = ROOT / "docs/dataset/kopi-email-label-conflicts.jsonl"
# ponytail: 짧은 구절은 평범한 메일 문장과 겹친다. 길이 기준만 쓰고 유사도 비교는 넣지 않았다.
MIN_PHRASE = 12


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def find_conflicts(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    phrases = {
        squash(row["suspected_attack_phrase"]) for row in rows if row["original_label"] == "인젝션"
    }
    phrases = {phrase for phrase in phrases if len(phrase) >= MIN_PHRASE}
    conflicts = []
    for row in rows:
        if row["original_label"] != "정상":
            continue
        mail = squash(row["mail_text"])
        matched = sorted((phrase for phrase in phrases if phrase in mail), key=len, reverse=True)
        if matched:
            conflicts.append(
                {
                    "source_rows": row["source_rows"],
                    "original_label": row["original_label"],
                    "matched_attack_phrases": matched,
                    "mail_text": row["mail_text"],
                    "review": "TBD",
                }
            )
    return conflicts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in args.input.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    conflicts = find_conflicts(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for conflict in conflicts:
            handle.write(json.dumps(conflict, ensure_ascii=False) + "\n")
    benign = sum(1 for row in rows if row["original_label"] == "정상")
    print(f"{len(conflicts)} / {benign} 정상 rows contain an 인젝션 attack phrase")


if __name__ == "__main__":
    main()
