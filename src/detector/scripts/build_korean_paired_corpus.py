"""Build a carrier-paired Korean mail corpus so the carrier text cannot act as a label cue.

기존 `docs/dataset/kopi-email-cleaned.jsonl`은 운반 메일 하나당 라벨이 하나뿐이다. 그래서
모델이 "붙은 줄이 지시문인가"를 배우지 않고 "이 운반 틀이 정상으로 본 것인가"를 외웠다
(이슈 #84). 여기서는 모든 운반 메일이 `정상`과 `인젝션` 양쪽에 모두 등장하도록 다시 만든다.

짝을 만드는 방법은 두 가지다.

1. 공격 문구 위치를 아는 인젝션 행: 문구만 떼어 같은 운반의 정상본을 만든다.
2. 원래 정상 행: 코퍼스 안에 이미 있는 공격 문구 풀에서 하나를 골라 삽입해 공격본을 만든다.
   새 문구를 지어내지 않는다. 문구 유형 다양화는 별도 이슈다.

출력 스키마는 입력과 같아서 `train_korean_detector.py`가 `--corpus`로 그대로 읽는다.

    python src/detector/scripts/build_korean_paired_corpus.py
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CLEANED = ROOT / "docs/dataset/kopi-email-cleaned.jsonl"
CONFLICTS = ROOT / "docs/dataset/kopi-email-label-conflicts.jsonl"
OUTPUT = ROOT / "docs/dataset/kopi-email-paired.jsonl"
MANIFEST = ROOT / "src/detector/reports/korean-paired-corpus-manifest.json"

# KoPI-Bench_메일_PDF_개별JSON_공격기법 홀드아웃 3종이 쓰는 원본 표 CSV 행.
# 그 운반 메일을 학습에서 빼야 홀드아웃이 처음 보는 메일로 남는다.
HOLDOUT_SOURCE_ROWS = {45, 118, 123}


def source_row_numbers(value: Any) -> set[int]:
    return {int(found) for found in re.findall(r"\d+", str(value))}


def load_rows(cleaned: Path, conflicts: Path | None) -> list[dict[str, Any]]:
    rows = [
        json.loads(line) for line in cleaned.read_text(encoding="utf-8").splitlines() if line
    ]
    if conflicts and conflicts.is_file():
        skip = {
            json.loads(line)["source_rows"]
            for line in conflicts.read_text(encoding="utf-8").splitlines()
            if line
        }
        rows = [row for row in rows if row["source_rows"] not in skip]
    return rows


def tidy(text: str) -> str:
    """Close the gap a removed phrase leaves without reflowing the rest of the mail."""
    text = re.sub(r"[ \t]*\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def strip_phrase(text: str, phrase: str) -> str:
    return tidy(text.replace(phrase, "", 1))


def insert_phrase(text: str, phrase: str, position: str) -> str:
    if position == "앞":
        return tidy(f"{phrase}\n{text}")
    if position == "뒤":
        return tidy(f"{text}\n{phrase}")
    lines = text.split("\n")
    if len(lines) < 2:
        midpoint = len(text) // 2
        return tidy(f"{text[:midpoint]}\n{phrase}\n{text[midpoint:]}")
    middle = len(lines) // 2
    return tidy("\n".join([*lines[:middle], phrase, *lines[middle:]]))


def emit(
    row: dict[str, Any], label: str, mail_text: str, phrase: str, origin: str
) -> dict[str, Any]:
    return {
        "source_rows": row["source_rows"],
        "selection": row.get("selection", ""),
        "original_label": label,
        # 짝의 두 행이 같은 분할에 들어가게 하려면 공격본에만 문구를 남겨야 한다.
        # train_korean_detector.template_key 가 이 문구를 떼고 그룹 키를 만든다.
        "attack_position_status": "짝 구성" if label == "인젝션" else "해당 없음",
        "suspected_attack_phrase": phrase if label == "인젝션" else "",
        "mail_text": mail_text,
        "pair_origin": origin,
    }


def carrier_key(row: dict[str, Any]) -> str:
    """공격 문구를 뗀 본문. 짝의 두 행이 같은 값을 갖는다."""
    return re.sub(r"\s+", "", row["mail_text"].replace(row["suspected_attack_phrase"], ""))


def build(rows: list[dict[str, Any]], seed: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed)
    dropped: Counter[str] = Counter()

    kept: list[dict[str, Any]] = []
    for row in rows:
        if source_row_numbers(row["source_rows"]) & HOLDOUT_SOURCE_ROWS:
            dropped["홀드아웃 운반 메일"] += 1
            continue
        kept.append(row)

    located: list[tuple[dict[str, Any], str]] = []
    benign_only: list[dict[str, Any]] = []
    for row in kept:
        phrase = row["suspected_attack_phrase"].strip()
        if row["original_label"] == "정상":
            benign_only.append(row)
        elif phrase and phrase in row["mail_text"]:
            located.append((row, phrase))
        else:
            # 문구 위치를 모르면 정상본을 만들 수 없다. 운반만 한쪽 라벨로 남으면
            # 고치려는 shortcut 이 그대로 돌아오므로 버린다.
            dropped["공격 문구 위치 미확인"] += 1

    phrase_pool = sorted({phrase for _, phrase in located})
    if not phrase_pool:
        raise ValueError("공격 문구 풀이 비었다")

    positions = Counter()
    for row, phrase in located:
        text = row["mail_text"]
        index = text.index(phrase)
        tail = index + len(phrase) >= len(text) - 2
        positions["앞" if index <= 2 else ("뒤" if tail else "중간")] += 1
    position_names = list(positions)
    position_weights = [positions[name] for name in position_names]

    paired: list[dict[str, Any]] = []
    for row, phrase in located:
        attacked = tidy(row["mail_text"])
        benign = strip_phrase(row["mail_text"], phrase)
        if not benign or phrase in benign:
            dropped["문구 제거 실패"] += 1
            continue
        paired.append(emit(row, "인젝션", attacked, phrase, "원본 공격"))
        paired.append(emit(row, "정상", benign, "", "공격 문구 제거"))

    for row in benign_only:
        benign = tidy(row["mail_text"])
        phrase = rng.choice(phrase_pool)
        position = rng.choices(position_names, weights=position_weights, k=1)[0]
        attacked = insert_phrase(benign, phrase, position)
        if phrase in benign:
            dropped["정상 본문에 이미 문구 존재"] += 1
            continue
        paired.append(emit(row, "정상", benign, "", "원본 정상"))
        paired.append(emit(row, "인젝션", attacked, phrase, f"문구 삽입({position})"))

    manifest = {
        "입력": str(CLEANED.relative_to(ROOT)),
        "출력": str(OUTPUT.relative_to(ROOT)),
        "seed": seed,
        "입력_행": len(rows),
        "출력_행": len(paired),
        "짝_수": len(paired) // 2,
        # 같은 운반 메일에 다른 공격 문구가 붙은 행들이 있어 짝 수보다 적다.
        "고유_운반_메일": len({carrier_key(row) for row in paired}),
        "짝_출처": dict(Counter(row["pair_origin"] for row in paired)),
        "라벨": dict(Counter(row["original_label"] for row in paired)),
        "고유_공격_문구": len(phrase_pool),
        "삽입_위치_분포_근거": dict(positions),
        "제외": dict(dropped),
        "홀드아웃_제외_원본표_행": sorted(HOLDOUT_SOURCE_ROWS),
    }
    return paired, manifest


def check(paired: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    """운반 메일이 양쪽 라벨에 모두 있는지, 정상본에 공격 문구가 남지 않았는지 확인한다."""
    labels = Counter(row["original_label"] for row in paired)
    assert labels["정상"] == labels["인젝션"] == len(paired) // 2, f"라벨 불균형: {labels}"

    by_carrier: dict[str, set[str]] = {}
    for row in paired:
        by_carrier.setdefault(carrier_key(row), set()).add(row["original_label"])
    unpaired = [key for key, found in by_carrier.items() if found != {"정상", "인젝션"}]
    assert not unpaired, f"한쪽 라벨만 있는 운반 메일 {len(unpaired)}건"

    for row in paired:
        if row["original_label"] == "정상":
            assert row["suspected_attack_phrase"] == "", "정상 행에 공격 문구 필드가 남았다"
        else:
            assert row["suspected_attack_phrase"] in row["mail_text"], "공격본에 문구가 없다"

    for row in paired:
        assert not source_row_numbers(row["source_rows"]) & HOLDOUT_SOURCE_ROWS, (
            "홀드아웃 운반 메일이 남았다"
        )

    assert manifest["고유_운반_메일"] == len(by_carrier), (
        f"운반 수 불일치: {manifest['고유_운반_메일']} vs {len(by_carrier)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleaned", type=Path, default=CLEANED)
    parser.add_argument("--conflicts", type=Path, default=CONFLICTS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    rows = load_rows(args.cleaned, args.conflicts)
    paired, manifest = build(rows, args.seed)
    check(paired, manifest)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in paired), encoding="utf-8"
    )
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
