"""Remove the user question from the KoPI email candidates without changing labels."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / "docs/dataset/KoPI-Bench_이메일_공격위치.csv"
OUTPUT = ROOT / "docs/dataset/kopi-email-cleaned.jsonl"
REPORT = ROOT / "docs/dataset/kopi-email-cleaning-report.json"
QUESTION = re.compile(r"(?<!\w)(?:Q|질문)\s*:", re.IGNORECASE)


def clean(source: Path) -> tuple[list[dict[str, str]], dict[str, object]]:
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "원본 CSV 행",
            "선별 구분",
            "원본 레이블",
            "공격 위치 상태",
            "공격 의심 구절",
            "전체 원문",
        }
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                f"missing KoPI columns: {sorted(required - set(reader.fieldnames or []))}"
            )

        rows: dict[str, dict[str, str]] = {}
        markers: Counter[str] = Counter()
        labels: Counter[str] = Counter()
        selections: Counter[str] = Counter()
        duplicates = 0
        for line, item in enumerate(reader, 2):
            matches = list(QUESTION.finditer(item["전체 원문"]))
            # shortcut: current questions end within 67 chars; revisit for new exports.
            if len(matches) != 1 or len(item["전체 원문"]) - matches[0].start() > 100:
                raise ValueError(f"{source}:{line}: expected one trailing user question")
            marker = matches[0]
            mail = item["전체 원문"][: marker.start()].strip()
            question = item["전체 원문"][marker.end() :].strip()
            if not mail or not question:
                raise ValueError(f"{source}:{line}: empty mail or user question")
            mail = mail.replace("\r\n", "\n").replace("\r", "\n")
            label = item["원본 레이블"]
            if label not in {"정상", "인젝션"}:
                raise ValueError(f"{source}:{line}: unexpected label {label!r}")
            markers[marker.group().split(":")[0].strip().lower()] += 1
            labels[label] += 1
            selections[item["선별 구분"]] += 1
            existing = rows.get(mail)
            if existing:
                if existing["original_label"] != label:
                    raise ValueError(f"{source}:{line}: identical mail has conflicting labels")
                existing["source_rows"] += f";{item['원본 CSV 행']}"
                duplicates += 1
                continue
            rows[mail] = {
                "source_rows": item["원본 CSV 행"],
                "selection": item["선별 구분"],
                "original_label": label,
                "attack_position_status": item["공격 위치 상태"],
                "suspected_attack_phrase": item["공격 의심 구절"],
                "mail_text": mail,
            }

    report = {
        "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
        "input_rows": sum(labels.values()),
        "cleaned_rows": len(rows),
        "exact_duplicates_removed": duplicates,
        "input_labels": dict(labels),
        "input_selection": dict(selections),
        "user_question_markers": dict(markers),
        "status": "candidates_only_labels_and_email_fields_need_review",
    }
    return list(rows.values()), report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    rows, report = clean(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
