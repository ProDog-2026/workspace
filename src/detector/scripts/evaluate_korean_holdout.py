"""Score the KoPI-Bench hidden-technique holdout mails and their payload-free controls.

홀드아웃은 `KoPI-Bench_메일_PDF_개별JSON_공격기법` 폴더의 은닉 기법 3종이다.

**이 데이터는 보고 전용이다.** 학습에도, 임계값 조정에도 쓰지 않는다. 한 번 튜닝에 쓰면
처음 보는 공격 문구에 대한 일반화를 재는 수단이 사라진다. 이 스크립트는 점수를 출력만 하고
어떤 값도 모델이나 설정에 되먹이지 않는다.

대조군은 공격본에서 숨겨진 지시 영역만 제거해 즉석에서 만든다. 공격본과 대조군의 점수 순위가
뒤집혀 있으면(공격본 <= 대조군) 임계값을 어떻게 잡아도 그 사례는 분리할 수 없다. 그래서
탐지 건수와 함께 순위 역전 여부를 함께 본다.

    python src/detector/scripts/evaluate_korean_holdout.py \
        ../KoPI-Bench_메일_PDF_개별JSON_공격기법 \
        --output src/detector/reports/korean-holdout-hidden3-metrics.json
"""

from __future__ import annotations

import argparse
import email
import json
import re
from email import policy
from pathlib import Path
from typing import Any

from prodog_sast.email_text import eml_bytes_to_model_text
from prodog_sast.model import load_artifact, predict
from prodog_sast.transformer import combine_predictions, load_transformer, predict_transformer

CASES = ("01_white_text", "02_html_comment", "03_css_hidden")
HIDDEN_REGIONS = (
    re.compile(r'<div style="background:#ffffff;color:#ffffff">.*?</div>', re.S),
    re.compile(r"<!--\s*AI 분석기에게:.*?-->", re.S),
    re.compile(r'<div class="bench-hidden-instruction".*?</div>', re.S),
)


def strip_hidden(raw: bytes) -> bytes:
    """공격본에서 숨겨진 지시 영역만 떼어 같은 메일의 정상본을 만든다."""
    message = email.message_from_bytes(raw, policy=policy.default)
    removed = 0
    for part in message.walk():
        if part.get_content_type() != "text/html":
            continue
        html = part.get_content()
        for pattern in HIDDEN_REGIONS:
            html, found = pattern.subn("", html)
            removed += found
        part.set_content(html, subtype="html")
    if removed != 1:
        raise ValueError(f"숨김 영역을 1개 제거해야 하는데 {removed}개를 제거했다")
    out = message.as_bytes()
    if "AI 분석기에게".encode() in out:
        raise ValueError("정상본에 공격 문구가 남았다")
    return out


def score(texts: list[str], tf_model: Path, transformer_model: Path) -> list[dict[str, Any]]:
    results = predict(load_artifact(tf_model), texts)
    if not transformer_model.exists():
        return results
    return combine_predictions(
        results, predict_transformer(load_transformer(transformer_model), texts)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("holdout_dir", type=Path, help="은닉 기법 3종 폴더")
    parser.add_argument("--model", type=Path, default=Path("models/korean_tfidf.joblib"))
    parser.add_argument(
        "--transformer-model", type=Path, default=Path("models/korean-transformer")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    names: list[str] = []
    labels: list[str] = []
    texts: list[str] = []
    for case in CASES:
        raw = (args.holdout_dir / f"{case}.eml").read_bytes()
        names.extend([case, case])
        labels.extend(["공격", "대조군"])
        texts.extend([eml_bytes_to_model_text(raw), eml_bytes_to_model_text(strip_hidden(raw))])

    results = score(texts, args.model, args.transformer_model)

    rows: list[dict[str, Any]] = []
    for name, label, result in zip(names, labels, results, strict=True):
        rows.append({"사례": name, "구분": label, **result})

    attacks = {row["사례"]: row for row in rows if row["구분"] == "공격"}
    controls = {row["사례"]: row for row in rows if row["구분"] == "대조군"}
    flagged = {"suspicious", "danger"}
    inverted = [
        case
        for case in CASES
        if attacks[case]["prompt_injection_probability"]
        <= controls[case]["prompt_injection_probability"]
    ]
    summary = {
        "정탐": sum(1 for case in CASES if attacks[case]["verdict"] in flagged),
        "미탐": sum(1 for case in CASES if attacks[case]["verdict"] not in flagged),
        "오탐": sum(1 for case in CASES if controls[case]["verdict"] in flagged),
        "정상_통과": sum(1 for case in CASES if controls[case]["verdict"] not in flagged),
        # 공격본 점수가 대조군보다 낮으면 어떤 임계값으로도 분리할 수 없다.
        "순위_역전": inverted,
        "순위_정상": len(CASES) - len(inverted),
    }

    report = {
        "홀드아웃": str(args.holdout_dir),
        "용도": "보고 전용. 학습과 임계값 조정에 사용하지 않는다.",
        "모델": {"tfidf": str(args.model), "transformer": str(args.transformer_model)},
        "요약": summary,
        "사례별": rows,
    }

    print(f"{'사례':16s} {'공격':>9s} {'대조군':>9s}  공격 판정   순위")
    for case in CASES:
        attack = attacks[case]["prompt_injection_probability"]
        control = controls[case]["prompt_injection_probability"]
        mark = "역전" if case in inverted else "정상"
        print(f"{case:16s} {attack:9.4f} {control:9.4f}  {attacks[case]['verdict']:10s} {mark}")
    print()
    print(json.dumps(summary, ensure_ascii=False))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"report -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
