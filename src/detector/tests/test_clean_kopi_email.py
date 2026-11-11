import csv
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/clean_kopi_email.py"
FIELDS = (
    "원본 CSV 행",
    "선별 구분",
    "원본 레이블",
    "공격 위치 상태",
    "공격 의심 구절",
    "전체 원문",
)


def _write_source(path: Path, rows: list[tuple[str, ...]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        writer.writerows(rows)


def test_clean_kopi_email_removes_only_question_and_exact_duplicates(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    output = tmp_path / "cleaned.jsonl"
    report = tmp_path / "report.json"
    _write_source(
        source,
        [
            (
                "1",
                "메일 형식",
                "인젝션",
                "자동 추정",
                "새 지시",
                "새 지시. 제목: 일정|내용: 확인\r\nQ: 날짜는?",
            ),
            (
                "2",
                "메일 형식",
                "인젝션",
                "자동 추정",
                "새 지시",
                "새 지시. 제목: 일정|내용: 확인\r\n질문: 누구에게?",
            ),
            (
                "3",
                "메일 본문 추정",
                "정상",
                "해당 없음",
                "",
                "안녕하세요. 일정 안내입니다. 질문: 언제?",
            ),
        ],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source),
            "--output",
            str(output),
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    cleaned = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(cleaned) == 2
    assert cleaned[0]["source_rows"] == "1;2"
    assert cleaned[0]["mail_text"] == "새 지시. 제목: 일정|내용: 확인"
    assert cleaned[0]["original_label"] == "인젝션"
    assert cleaned[1]["mail_text"] == "안녕하세요. 일정 안내입니다."
    assert json.loads(report.read_text(encoding="utf-8"))["exact_duplicates_removed"] == 1


def test_clean_kopi_email_rejects_conflicting_labels(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    output = tmp_path / "cleaned.jsonl"
    _write_source(
        source,
        [
            ("1", "메일 형식", "정상", "해당 없음", "", "본문 Q: 질문"),
            ("2", "메일 형식", "인젝션", "자동 추정", "본문", "본문 질문: 다른 질문"),
        ],
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "conflicting labels" in result.stderr
    assert not output.exists()
