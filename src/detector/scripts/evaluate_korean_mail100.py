"""Score the 100-mail Korean hidden-injection set and its payload-free controls.

데이터는 김서진이 만든 `한국어_메일_100개_PDF_CSV` 폴더다(`emails/*.eml`, `pdfs/*.pdf`,
`한국어_이메일_100개_공격정리.csv`). 학습 코퍼스와 운반 메일·공격 문구가 겹치지 않는다.

**보고 전용이다.** 학습과 임계값 조정에 쓰지 않는다.

메일마다 세 가지를 본다.
1. 추출: 숨긴 문구가 모델 입력의 `[HTML_RAW]`, `[HTML_TEXT]`에 들어가는가
2. 탐지: 공격본과, 숨긴 요소만 뗀 대조군의 판정과 점수 순위
3. 첨부: PDF에서 뽑은 텍스트만 모델에 넣었을 때의 판정. 현재 탐지기는 첨부를 읽지 않으므로
   "첨부 추출을 붙이면 얼마나 잡히는가"를 미리 재는 값이다. pypdf가 없으면 건너뛴다.

    python src/detector/scripts/evaluate_korean_mail100.py <데이터 폴더> \
        --output src/detector/reports/korean-mail100-metrics.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from sklearn.metrics import roc_auc_score

from prodog_sast.cli import _score
from prodog_sast.email_text import eml_bytes_to_model_text, fields_to_model_text

CSV_NAME = "한국어_이메일_100개_공격정리.csv"
FLAGGED = {"suspicious", "danger"}


def strip_hidden(raw: bytes, injection: str) -> bytes:
    """공격본에서 숨긴 지시를 담은 요소 하나만 떼어 같은 메일의 정상본을 만든다."""
    phrase = re.escape(injection)
    pattern = re.compile(
        rf"<div[^>]*>(?:<span[^>]*>)?{phrase}(?:</span>)?</div>|<!--\s*{phrase}\s*-->"
    )
    message = BytesParser(policy=policy.default).parsebytes(raw)
    removed = 0
    for part in message.walk():
        if part.get_content_type() != "text/html":
            continue
        html, found = pattern.subn("", part.get_content())
        removed += found
        part.set_content(html, subtype="html")
    if removed != 1:
        raise ValueError(f"숨김 요소를 1개 제거해야 하는데 {removed}개를 제거했다")
    out = message.as_bytes()
    if injection.encode() in out or "AI 분석기에게".encode() in out:
        raise ValueError("정상본에 공격 문구가 남았다")
    return out


SECTION_START = re.compile(
    r"^(?=\[(?:SUBJECT|FROM|REPLY_TO|TO|PART|HTML_RAW|HTML_TEXT|PLAIN)\])", re.M
)


def section(model_text: str, tag: str) -> str:
    """태그로 시작하는 섹션 전체를 다음 태그 직전까지 모은다. 여러 줄 HTML도 빠지지 않는다."""
    return "\n".join(
        chunk.rstrip("\n") for chunk in SECTION_START.split(model_text) if chunk.startswith(tag)
    )


def pdf_text(path: Path) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def rate(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(1 for row in rows if row[key] in FLAGGED) / len(rows), 4)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--model", type=Path, default=Path("models/korean_tfidf.joblib"))
    parser.add_argument("--transformer-model", type=Path, default=Path("models/korean-transformer"))
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    with (args.dataset_dir / CSV_NAME).open(encoding="utf-8-sig") as handle:
        meta = list(csv.DictReader(handle))

    attack_texts: list[str] = []
    control_texts: list[str] = []
    pdf_texts: list[str] = []
    rows: list[dict[str, Any]] = []
    for item in meta:
        raw = (args.dataset_dir / "emails" / item["메일 파일"]).read_bytes()
        injection = item["인젝션 공격 내용"]
        attack = eml_bytes_to_model_text(raw)
        attack_texts.append(attack)
        control_texts.append(eml_bytes_to_model_text(strip_hidden(raw, injection)))
        extracted = pdf_text(args.dataset_dir / "pdfs" / item["PDF 파일"])
        if extracted is not None:
            pdf_texts.append(fields_to_model_text(body_text=extracted))
        rows.append(
            {
                "번호": item["번호"],
                "기법": item["공격 기법"],
                "HTML_RAW_포함": injection in section(attack, "[HTML_RAW]"),
                "HTML_TEXT_포함": injection in section(attack, "[HTML_TEXT]"),
                # 줄바꿈이 끼어도 잡히도록 공백을 지우고 비교한다.
                "PDF_추출_포함": None
                if extracted is None
                else injection.replace(" ", "") in re.sub(r"\s", "", extracted),
            }
        )

    scored = _score(
        attack_texts + control_texts + pdf_texts,
        args.model,
        args.transformer_model,
        args.batch_size,
    )
    n = len(rows)
    for i, row in enumerate(rows):
        attack, control = scored[i], scored[n + i]
        row.update(
            공격_점수=attack["prompt_injection_probability"],
            공격_판정=attack["verdict"],
            대조군_점수=control["prompt_injection_probability"],
            대조군_판정=control["verdict"],
            순위_역전=attack["prompt_injection_probability"]
            <= control["prompt_injection_probability"],
            # 합친 판정만으로는 어느 모델이 오탐을 냈는지 알 수 없어 모델별 판정도 남긴다.
            모델별={
                name: {
                    "공격_판정": result["verdict"],
                    "대조군_판정": control.get("detectors", {"tfidf": control})[name]["verdict"],
                }
                for name, result in attack.get("detectors", {"tfidf": attack}).items()
            },
        )
        if pdf_texts:
            row.update(
                PDF_점수=scored[2 * n + i]["prompt_injection_probability"],
                PDF_판정=scored[2 * n + i]["verdict"],
            )

    def summarize(group: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "메일": len(group),
            "HTML_RAW_추출률": round(sum(r["HTML_RAW_포함"] for r in group) / len(group), 4),
            "HTML_TEXT_추출률": round(sum(r["HTML_TEXT_포함"] for r in group) / len(group), 4),
            "공격_탐지율": rate(group, "공격_판정"),
            "대조군_오탐률": rate(group, "대조군_판정"),
            "순위_역전": sum(r["순위_역전"] for r in group),
            "AUROC": round(
                roc_auc_score(
                    [1] * len(group) + [0] * len(group),
                    [r["공격_점수"] for r in group] + [r["대조군_점수"] for r in group],
                ),
                4,
            ),
            "모델별": {
                name: {
                    "공격_탐지_건수": sum(r["모델별"][name]["공격_판정"] in FLAGGED for r in group),
                    "대조군_오탐_건수": sum(
                        r["모델별"][name]["대조군_판정"] in FLAGGED for r in group
                    ),
                }
                for name in group[0]["모델별"]
            },
        }
        if pdf_texts:
            out["PDF_추출률"] = round(sum(r["PDF_추출_포함"] for r in group) / len(group), 4)
            out["PDF_탐지율"] = rate(group, "PDF_판정")
        return out

    by_technique: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_technique[row["기법"]].append(row)
    summary = {"전체": summarize(rows)} | {k: summarize(v) for k, v in by_technique.items()}

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "데이터": args.dataset_dir.name,
            "용도": "보고 전용. 학습과 임계값 조정에 사용하지 않는다.",
            # 실제로 점수를 낸 모델만 적는다. Transformer가 없으면 TF-IDF만 남는다.
            "실행한_모델": {
                name: (args.model if name == "tfidf" else args.transformer_model).as_posix()
                for name in rows[0]["모델별"]
            },
            "요약": summary,
            "메일별": rows,
        }
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"report -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
