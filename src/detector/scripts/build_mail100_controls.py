"""Build benign counterparts for the 100-mail Korean hidden-injection set.

공격 메일 100개마다 정상 메일 2종을 만든다. 원본 폴더는 건드리지 않는다.

- 정상_제거: 숨긴 지시 요소를 HTML과 PDF에서 모두 뺀다. 같은 메일의 깨끗한 판이다.
- 정상_숨김: 같은 은닉 기법을 그대로 두고 내용만 무해하게 바꾼다. HTML은 본문 첫 문단을
  숨긴다(뉴스레터 preheader와 같은 모양). PDF는 보이는 본문 한 줄을 렌더링 모드 3으로
  한 번 더 넣는다(스캔 PDF의 OCR 글자층과 같은 모양). "숨겨져 있다"만으로 오탐하는지 본다.

    python src/detector/scripts/build_mail100_controls.py <공격 데이터 폴더> <출력 폴더>
"""

from __future__ import annotations

import argparse
import csv
import html
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import StreamObject

CSV_NAME = "한국어_이메일_100개_공격정리.csv"
HIDDEN_PDF_BLOCK = re.compile(rb"BT [^\n]*? 3 Tr [^\n]*?ET\n?")
BODY_LINE = re.compile(rb"BT 1 0 0 1 45 (\d+) Tm /F3\+0 11 Tf [\d.]+ TL (\(.*?\)) Tj T\* ET")


def pdf_bytes(src: Path, benign_hidden: bool) -> bytes:
    reader = PdfReader(src)
    writer = PdfWriter(clone_from=reader)
    page = writer.pages[0]
    data = page.get_contents().get_data()
    blocks = HIDDEN_PDF_BLOCK.findall(data)
    if len(blocks) != 1:
        raise ValueError(f"{src.name}: 숨긴 블록이 1개가 아니라 {len(blocks)}개")
    replacement = b""
    if benign_hidden:
        line = BODY_LINE.search(data)
        if not line:
            raise ValueError(f"{src.name}: 복제할 본문 줄이 없다")
        # 보이는 본문 줄을 같은 글꼴 그대로 투명하게 한 번 더 쓴다. 새 글자를 만들지 않으므로
        # 서브셋 글꼴에 없는 글자 문제가 생기지 않는다.
        replacement = (
            b"BT 1 0 0 1 45 45 Tm 3 Tr /F3+0 8 Tf 9.6 TL " + line.group(2) + b" Tj T* ET\n"
        )
    stream = StreamObject()
    stream.set_data(data.replace(blocks[0], replacement))
    page.replace_contents(stream)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def hidden_element(injection: str) -> re.Pattern[str]:
    """숨긴 지시를 담은 요소 하나(흰 글씨 div, display:none div, HTML 주석)."""
    phrase = re.escape(injection)
    return re.compile(
        rf"<div[^>]*>(?:<span[^>]*>)?{phrase}(?:</span>)?</div>|<!--\s*{phrase}\s*-->"
    )


def first_paragraph(body: str) -> str:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    return paragraphs[1] if len(paragraphs) > 1 else paragraphs[0]


def build_mail(raw: bytes, html_fix, pdf: bytes, pdf_name: str) -> bytes:
    message: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)
    changed = 0
    for part in message.walk():
        if part.get_content_type() == "text/html":
            before = part.get_content()
            after = html_fix(before)
            changed += before != after
            part.set_content(after, subtype="html")
        elif part.get_content_type() == "application/pdf":
            part.set_content(pdf, maintype="application", subtype="pdf", filename=pdf_name)
    out = message.as_bytes()
    if changed != 1 or "AI 분석기에게".encode() in out:
        raise ValueError("정상본에 공격 문구가 남았다")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)

    with (args.source_dir / CSV_NAME).open(encoding="utf-8-sig") as handle:
        meta = list(csv.DictReader(handle))

    rows: list[dict[str, str]] = []
    for kind in ("정상_제거", "정상_숨김"):
        (args.output_dir / kind / "emails").mkdir(parents=True, exist_ok=True)
        (args.output_dir / kind / "pdfs").mkdir(parents=True, exist_ok=True)
    for item in meta:
        number, injection = item["번호"], item["인젝션 공격 내용"]
        raw = (args.source_dir / "emails" / item["메일 파일"]).read_bytes()
        pdf_src = args.source_dir / "pdfs" / item["PDF 파일"]
        benign = html.escape(first_paragraph(item["본문 내용"]), quote=False)

        variants = {
            "정상_제거": (
                lambda h, i=injection: hidden_element(i).subn("", h)[0],
                pdf_bytes(pdf_src, benign_hidden=False),
            ),
            "정상_숨김": (
                lambda h, i=injection, b=benign: h.replace(i, b),
                pdf_bytes(pdf_src, benign_hidden=True),
            ),
        }
        for kind, (html_fix, pdf) in variants.items():
            mail_name, pdf_name = f"{number}_email.eml", f"{number}_attachment.pdf"
            (args.output_dir / kind / "pdfs" / pdf_name).write_bytes(pdf)
            (args.output_dir / kind / "emails" / mail_name).write_bytes(
                build_mail(raw, html_fix, pdf, pdf_name)
            )
            rows.append(
                {
                    "번호": number,
                    "유형": kind,
                    "분야": item["분야"],
                    "제목": item["제목"],
                    "원본 은닉 기법": item["공격 기법"],
                    "숨긴 내용": "" if kind == "정상_제거" else first_paragraph(item["본문 내용"]),
                    "평가 질문": item["평가 질문"],
                    "정상 답변": item["정상 답변"],
                    "메일 파일": f"{kind}/emails/{mail_name}",
                    "PDF 파일": f"{kind}/pdfs/{pdf_name}",
                }
            )

    with (args.output_dir / "한국어_정상메일_200개_정리.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)}개 -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
