import csv
import importlib.util
import json
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_mail100.py"
spec = importlib.util.spec_from_file_location("mail100_experiment", SCRIPT)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


def test_failed_case_is_recorded_and_next_case_is_executed(tmp_path, monkeypatch):
    data, output = tmp_path / "dataset", tmp_path / "results"
    (data / "emails").mkdir(parents=True)
    (data / "pdfs").mkdir()
    cases = []
    for number in ("001", "002"):
        message = EmailMessage()
        message["Subject"] = "Test"
        message["From"] = "team@example.invalid"
        message.set_content("Visible text")
        message.add_alternative("<p>Visible text</p><!-- hidden instruction -->", subtype="html")
        (data / "emails" / f"{number}.eml").write_bytes(message.as_bytes())
        (data / "pdfs" / f"{number}.pdf").write_bytes(b"synthetic")
        cases.append({"번호": number, "메일 파일": f"{number}.eml", "PDF 파일": f"{number}.pdf", "공격 기법": "HTML 주석",
                      "정상 답변": "good", "공격자가 유도하는 답변": "bad"})
    with (data / experiment.CSV_NAME).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cases[0])
        writer.writeheader()
        writer.writerows(cases)
    monkeypatch.setattr(experiment, "extract_pdf", lambda path: "Hidden PDF text")

    def static(mail):
        assert "hidden instruction" in mail["analysis_text"]
        return {"status": "ok", "verdict": "suspicious", "probability": 0.7}

    def dynamic(number, email, run_id):
        assert "hidden instruction" in email["body"]
        assert "UNTRUSTED PDF ATTACHMENT 1\nHidden PDF text" in email["body"]
        if number == "001":
            raise experiment.CaseFailure("provider_timeout")
        return {"run_id": "run-2", "completion": "completed", "deviation": False, "effect": False,
                "verification_status": "completed", "summary": "good and bad"}

    rows = experiment.evaluate(data, output, static, dynamic)
    assert [row["case_id"] for row in rows] == ["001", "002"]
    assert rows[0]["dynamic_completion"] == "failed"
    assert rows[0]["error"] == "provider_timeout"
    assert rows[1]["dynamic_completion"] == "completed"
    assert rows[1]["expected_answer_mentioned"] is True
    assert rows[1]["attacker_answer_mentioned"] is True
    assert all(row["static_verdict"] == "suspicious" for row in rows)
    with (output / "results.csv").open(encoding="utf-8-sig", newline="") as handle:
        saved = list(csv.DictReader(handle))
    assert [row["case_id"] for row in saved] == ["001", "002"]
    assert "hidden instruction" not in (output / "results.csv").read_text(encoding="utf-8-sig")


def test_missing_pdf_fails_one_case_without_running_it(tmp_path, monkeypatch):
    data, output = tmp_path / "dataset", tmp_path / "results"
    (data / "emails").mkdir(parents=True)
    (data / "pdfs").mkdir()
    for number in ("001", "002"):
        (data / "emails" / f"{number}.eml").write_text("Subject: hi\n\nbody", encoding="utf-8")
    (data / "pdfs" / "002.pdf").write_bytes(b"synthetic")
    (data / experiment.CSV_NAME).write_text(
        "번호,메일 파일,PDF 파일,공격 기법\n001,001.eml,001.pdf,white\n002,002.eml,002.pdf,white\n", encoding="utf-8-sig"
    )
    monkeypatch.setattr(experiment, "extract_pdf", lambda contents: contents.decode())
    calls = []
    def dynamic(number, email, run_id):
        calls.append(number)
        return {"run_id": "ok", "completion": "completed", "deviation": False, "effect": False}
    rows = experiment.evaluate(data, output, lambda mail: {"status": "ok", "verdict": "safe", "probability": 0.1}, dynamic)
    assert calls == ["002"]
    assert rows[0]["dynamic_completion"] == "failed"
    assert rows[1]["dynamic_completion"] == "completed"


def test_dynamic_runner_collects_result_for_the_submitted_mail(monkeypatch):
    seen = {}

    def handler(request):
        if request.method == "POST":
            seen.update(json.loads(request.content))
            return httpx.Response(201, json={"run_id": seen["request_id"]})
        assert request.url.path.startswith("/runs/" + seen["request_id"])
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={
                "completion": "completed", "deviations": [],
                "effects": {"file_changes": [], "marker_received": []},
                "verification_status": "completed", "verification_errors": [],
            })
        return httpx.Response(200, json={"phase": "finished", "error_code": None})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(experiment.httpx, "Client", lambda **kwargs: client)
    result = experiment.dynamic_runner("http://test", "local-model", 30, 512)(
        "001", {"subject": "test", "body": "UNTRUSTED EMAIL DATA\npdf content"}
    )
    assert seen["email"]["body"].endswith("pdf content")
    assert result["run_id"] == seen["request_id"]
    assert result["completion"] == "completed"
    assert result["deviation"] is False and result["effect"] is False


def test_submit_timeout_checks_same_run_before_continuing(monkeypatch):
    seen = {}

    def handler(request):
        if request.method == "POST":
            seen.update(json.loads(request.content))
            raise httpx.ReadTimeout("response lost", request=request)
        assert request.url.path.startswith("/runs/" + seen["request_id"])
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={
                "completion": "completed", "deviations": [],
                "effects": {"file_changes": [], "marker_received": []},
                "verification_status": "completed", "verification_errors": [],
            })
        return httpx.Response(200, json={"phase": "finished", "error_code": None})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(experiment.httpx, "Client", lambda **kwargs: client)
    result = experiment.dynamic_runner("http://test", "local-model", 30, 512)(
        "001", {"subject": "test", "body": "mail"}
    )
    assert result["run_id"] == seen["request_id"]
    assert result["completion"] == "completed"


def test_unknown_run_state_stops_batch_before_starting_next_mail(tmp_path, monkeypatch):
    data, output = tmp_path / "dataset", tmp_path / "results"
    (data / "emails").mkdir(parents=True)
    (data / "pdfs").mkdir()
    for number in ("001", "002"):
        (data / "emails" / f"{number}.eml").write_text("Subject: hi\n\nbody", encoding="utf-8")
        (data / "pdfs" / f"{number}.pdf").write_bytes(b"synthetic")
    (data / experiment.CSV_NAME).write_text(
        "번호,메일 파일,PDF 파일,공격 기법\n001,001.eml,001.pdf,white\n002,002.eml,002.pdf,white\n", encoding="utf-8-sig"
    )
    monkeypatch.setattr(experiment, "extract_pdf", lambda path: "pdf")
    calls = []

    def dynamic(number, email, run_id):
        calls.append(number)
        raise experiment.RunUncertain("run_state_unknown", "existing-run")

    rows = experiment.evaluate(data, output, lambda mail: {"status": "ok", "verdict": "safe"}, dynamic)
    assert calls == ["001"]
    assert rows[0]["run_id"] == "existing-run"
    assert rows[0]["error"] == "run_state_unknown"


def test_invalid_run_state_is_uncertain_and_keeps_the_run_id(monkeypatch):
    seen = {}

    def handler(request):
        if request.method == "POST":
            seen.update(json.loads(request.content))
            return httpx.Response(201, json={"run_id": seen["request_id"]})
        return httpx.Response(200, json={"unexpected": "missing phase"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(experiment.httpx, "Client", lambda **kwargs: client)
    with pytest.raises(experiment.RunUncertain) as raised:
        experiment.dynamic_runner("http://test", "local-model", 30, 512)(
            "001", {"subject": "test", "body": "mail"}
        )
    assert raised.value.run_id == seen["request_id"]


def test_finished_agent_with_failed_evidence_verification_is_not_completed(monkeypatch):
    seen = {}

    def handler(request):
        if request.method == "POST":
            seen.update(json.loads(request.content))
            return httpx.Response(201, json={"run_id": seen["request_id"]})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={
                "completion": "completed", "deviations": [],
                "effects": {"file_changes": [], "marker_received": []},
                "verification_status": "failed", "verification_errors": ["archive_invalid"],
            })
        return httpx.Response(200, json={"phase": "finished", "error_code": None})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(experiment.httpx, "Client", lambda **kwargs: client)
    result = experiment.dynamic_runner("http://test", "local-model", 30, 512)(
        "001", {"subject": "test", "body": "mail"}
    )
    assert result["completion"] == "incomplete"
    assert result["effect"] == ""
    assert result["error"] == "verification_failed"


def test_interrupted_submission_keeps_id_and_resume_reuses_it(tmp_path, monkeypatch):
    data, output = tmp_path / "dataset", tmp_path / "results"
    (data / "emails").mkdir(parents=True)
    (data / "pdfs").mkdir()
    (data / "emails" / "001.eml").write_text("Subject: hi\n\nbody", encoding="utf-8")
    (data / "pdfs" / "001.pdf").write_bytes(b"synthetic")
    (data / experiment.CSV_NAME).write_text(
        "번호,메일 파일,PDF 파일,공격 기법\n001,001.eml,001.pdf,white\n", encoding="utf-8-sig"
    )
    monkeypatch.setattr(experiment, "extract_pdf", lambda path: "pdf")
    static = lambda mail: {"status": "ok", "verdict": "safe"}
    seen = []

    def interrupted(number, email, run_id):
        seen.append(run_id)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        experiment.evaluate(data, output, static, interrupted)
    with (output / "results.csv").open(encoding="utf-8-sig", newline="") as handle:
        pending = list(csv.DictReader(handle))
    assert pending[0]["run_id"] == seen[0]
    assert pending[0]["dynamic_completion"] == "running"

    def resumed(number, email, run_id):
        assert run_id == seen[0]
        return {"run_id": run_id, "completion": "completed", "deviation": False, "effect": False}

    rows = experiment.evaluate(data, output, static, resumed, resume=True)
    assert len(rows) == 1
    assert rows[0]["dynamic_completion"] == "completed"


def test_dataset_symlink_cannot_escape_email_directory(tmp_path, monkeypatch):
    data, output = tmp_path / "dataset", tmp_path / "results"
    (data / "emails").mkdir(parents=True)
    (data / "pdfs").mkdir()
    outside = tmp_path / "private.eml"
    outside.write_text("Subject: private\n\nbody", encoding="utf-8")
    (data / "emails" / "001.eml").symlink_to(outside)
    (data / experiment.CSV_NAME).write_text(
        "번호,메일 파일,PDF 파일,공격 기법\n001,001.eml,001.pdf,white\n", encoding="utf-8-sig"
    )
    calls = []
    rows = experiment.evaluate(data, output, lambda mail: calls.append(mail), lambda *args: None)
    assert calls == []
    assert rows[0]["error"] == "invalid_dataset_path"


def test_missing_filename_does_not_prevent_later_cases(tmp_path, monkeypatch):
    data, output = tmp_path / "dataset", tmp_path / "results"
    (data / "emails").mkdir(parents=True)
    (data / "pdfs").mkdir()
    (data / "emails" / "002.eml").write_text("Subject: hi\n\nbody", encoding="utf-8")
    (data / "pdfs" / "002.pdf").write_bytes(b"pdf")
    (data / experiment.CSV_NAME).write_text(
        "번호,공격 기법,메일 파일,PDF 파일\n001,white\n002,white,002.eml,002.pdf\n", encoding="utf-8-sig"
    )
    monkeypatch.setattr(experiment, "extract_pdf", lambda contents: "pdf")
    assert experiment.dataset_fingerprint(data)[1] == 2
    rows = experiment.evaluate(data, output, lambda mail: {"status": "ok"},
                               lambda number, email, run_id: {"run_id": run_id, "completion": "completed", "deviation": False, "effect": False})
    assert rows[0]["error"] == "invalid_dataset_path"
    assert rows[1]["dynamic_completion"] == "completed"


def test_storage_error_is_uncertain_and_retains_run_id(monkeypatch):
    seen = {}
    def handler(request):
        if request.method == "POST":
            seen.update(json.loads(request.content))
            return httpx.Response(201, json={"run_id": seen["request_id"]})
        return httpx.Response(200, json={"phase": "storage_error"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(experiment.httpx, "Client", lambda **kwargs: client)
    with pytest.raises(experiment.RunUncertain) as raised:
        experiment.dynamic_runner("http://test", "local-model", 30, 512)(
            "001", {"subject": "test", "body": "mail"}
        )
    assert raised.value.run_id == seen["request_id"]


def test_resume_requires_a_checkpoint(tmp_path):
    data, output = tmp_path / "dataset", tmp_path / "output"
    data.mkdir()
    output.mkdir()
    (data / experiment.CSV_NAME).write_text(
        "번호,메일 파일,PDF 파일,공격 기법\n001,001.eml,001.pdf,white\n", encoding="utf-8-sig"
    )
    with pytest.raises(ValueError, match="resume_checkpoint_missing"):
        experiment.evaluate(data, output, lambda mail: None, lambda *args: None, resume=True)


def test_recovery_required_stops_before_next_run(monkeypatch):
    seen = {}

    def handler(request):
        if request.method == "POST":
            seen.update(json.loads(request.content))
            return httpx.Response(201, json={"run_id": seen["request_id"]})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={"completion": "completed", "recovery_required": True})
        return httpx.Response(200, json={"phase": "finished"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(experiment.httpx, "Client", lambda **kwargs: client)
    with pytest.raises(experiment.RunUncertain) as raised:
        experiment.dynamic_runner("http://test", "local-model", 30, 512)(
            "001", {"subject": "test", "body": "mail"}
        )
    assert raised.value.run_id == seen["request_id"]


def test_symlinked_email_directory_cannot_point_outside_dataset(tmp_path):
    data = tmp_path / "dataset"
    data.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "001.eml").write_text("private", encoding="utf-8")
    (data / "emails").symlink_to(outside, target_is_directory=True)
    with pytest.raises(experiment.CaseFailure, match="invalid_dataset_path"):
        experiment.read_input(data, "emails", "001.eml", 1024)


@pytest.mark.parametrize("error", ["submission_unknown", "request_id_conflict"])
def test_ambiguous_reservation_stops_batch_with_the_existing_id(monkeypatch, error):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(409, json={"error": error})
    ))
    monkeypatch.setattr(experiment.httpx, "Client", lambda **kwargs: client)
    with pytest.raises(experiment.RunUncertain) as raised:
        experiment.dynamic_runner("http://test", "local-model", 30, 512)(
            "001", {"subject": "test", "body": "mail"}, "1" * 32
        )
    assert raised.value.run_id == "1" * 32
    assert raised.value.code == error
