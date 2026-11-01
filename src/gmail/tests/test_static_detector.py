"""Boundary tests need no sklearn; real ML is explicitly opt-in."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from prodog_gmail import static_detector as adapter

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "src/gmail/scripts/scan_collected_mail.py"
VALID = {
    "score": 0.4,
    "verdict": "suspicious",
    "suspicious_threshold": 0.05,
    "danger_threshold": 0.8,
}
MAIL = {
    "subject": "합성 메일",
    "sender": "sender@example.invalid",
    "text_plain": "meeting",
    "text_html": "<p hidden>hidden payload</p><!-- retained comment -->",
    "analysis_text": "fallback",
}


@pytest.fixture
def detector(tmp_path):
    model = tmp_path / "trusted model.joblib"
    model.write_bytes(b"test artifact, never loaded by sklearn")
    return adapter.StaticDetector(
        SimpleNamespace(static_python=Path(sys.executable), static_model_path=model)
    )


def install_child(monkeypatch, code, detector):
    """Replace only the interpreter entry point; keep actual pipes/process lifecycle."""
    real_spawn = asyncio.create_subprocess_exec
    processes = []

    async def spawn(*argv, **kwargs):
        assert argv == (str(detector.python), "-I", str(SCRIPT), str(detector.model_path))
        assert "shell" not in kwargs
        process = await real_spawn(sys.executable, "-I", "-c", code, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(adapter.asyncio, "create_subprocess_exec", spawn)
    return processes


def assert_error(result, code):
    assert result["status"] == "error"
    assert result["score"] is None
    assert result["verdict"] is None
    assert result["suspicious_threshold"] is None
    assert result["danger_threshold"] is None
    assert result["error"]["code"] == code
    serialized = json.dumps(result)
    assert "sender@example.invalid" not in serialized
    assert "private-secret" not in serialized


def test_missing_model_never_launches_and_explains_setup(tmp_path, monkeypatch):
    detector = adapter.StaticDetector(
        SimpleNamespace(static_python=Path(sys.executable), static_model_path=tmp_path / "absent")
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("missing model must not launch a subprocess")

    monkeypatch.setattr(adapter.asyncio, "create_subprocess_exec", forbidden)
    status = detector.status()
    assert status["ready"] is False
    assert status["model_id"] is None
    assert "모델" in status["message"]
    assert str(tmp_path) not in status["message"]
    assert_error(asyncio.run(detector.scan(MAIL)), "static_not_ready")


def test_default_locations_do_not_depend_on_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    detector = adapter.StaticDetector(SimpleNamespace())
    assert detector.model_path == ROOT / "src/detector/models/prompt_injection.joblib"
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    assert detector.python == ROOT / "src/detector/.venv" / executable


def test_missing_python_is_not_ready_but_model_still_has_identity(detector, tmp_path):
    original_id = detector.status()["model_id"]
    detector.python = tmp_path / "absent-python"
    status = detector.status()
    assert status["ready"] is False
    assert status["model_id"] == original_id
    assert "Python" in status["message"]


def test_fingerprint_cached_until_size_or_mtime_changes(detector, monkeypatch):
    expected = detector.status()["model_id"]
    assert detector.status()["model_id"] == expected
    original = Path.open

    def no_rehash(path, *args, **kwargs):
        if path == detector.model_path:
            pytest.fail("unchanged artifact was rehashed")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", no_rehash)
        assert detector.status()["model_id"] == expected
    old_stat = detector.model_path.stat()
    changed = b"x" * old_stat.st_size
    detector.model_path.write_bytes(changed)
    os.utime(detector.model_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000))
    changed_id = detector.status()["model_id"]
    assert changed_id != expected
    detector.model_path.write_bytes(b"new longer model")
    assert detector.status()["model_id"] not in {expected, changed_id}
    detector.model_path.unlink()
    assert detector.status()["model_id"] is None


def test_inference_policy_change_invalidates_cached_identity(detector, tmp_path, monkeypatch):
    policy = tmp_path / "policy.py"
    policy.write_text("version = 1\n")
    monkeypatch.setattr(adapter, "POLICY_FILES", (policy,), raising=False)
    first = detector.status()["model_id"]
    policy.write_text("version = 22\n")
    assert detector.status()["model_id"] != first


def test_redundant_analysis_text_does_not_reject_a_supported_unicode_body(detector, monkeypatch):
    body = "😀" * 300_000
    captured = []

    async def run(payload):
        captured.append(json.loads(payload))
        return json.dumps(VALID).encode(), 0

    monkeypatch.setattr(detector, "_run", run)
    result = asyncio.run(detector.scan({"text_plain": body, "analysis_text": body}))
    assert result["status"] == "completed"
    assert captured == [{"text_plain": body}]


@pytest.mark.parametrize(
    ("score", "verdict"), [(0.0, "safe"), (0.05, "suspicious"), (0.8, "danger"), (1.0, "danger")]
)
def test_real_pipes_accept_valid_boundary_results(detector, monkeypatch, score, verdict):
    output = dict(VALID, score=score, verdict=verdict)
    processes = install_child(
        monkeypatch,
        "import json,sys; mail=json.load(sys.stdin); "
        f"assert mail == { {k: v for k, v in MAIL.items() if k != 'analysis_text'}!r}; "
        f"print({json.dumps(output)!r})",
        detector,
    )
    result = asyncio.run(detector.scan(MAIL))
    assert result == {
        **output,
        "status": "completed",
        "model_id": detector.status()["model_id"],
        "error": None,
    }
    assert processes[0].returncode == 0


@pytest.mark.parametrize(
    "output",
    [
        "not JSON private-secret",
        "[]",
        "null",
        "{}",
        json.dumps(dict(VALID, score=float("nan"))),
        json.dumps(dict(VALID, score=float("inf"))),
        json.dumps(dict(VALID, score=-0.1)),
        json.dumps(dict(VALID, score=1.1)),
        json.dumps(dict(VALID, score=True)),
        json.dumps(dict(VALID, score="0.4")),
        json.dumps(dict(VALID, verdict="safe")),
        json.dumps(dict(VALID, verdict="unknown")),
        json.dumps(dict(VALID, suspicious_threshold=0.8)),
        json.dumps(dict(VALID, suspicious_threshold=-0.1)),
        json.dumps(dict(VALID, danger_threshold=1.1)),
        json.dumps(dict(VALID, danger_threshold=float("nan"))),
        json.dumps(dict(VALID, danger_threshold=True)),
        json.dumps(dict(VALID, error={"code": "private-secret"})),
        json.dumps(VALID) + "\nprivate-secret",
    ],
)
def test_invalid_child_output_never_becomes_safe(detector, monkeypatch, output):
    install_child(monkeypatch, f"import sys; sys.stdin.read(); print({output!r})", detector)
    assert_error(asyncio.run(detector.scan(MAIL)), "static_invalid_output")


def test_nonzero_exit_is_sanitized_even_with_valid_json(detector, monkeypatch):
    install_child(
        monkeypatch,
        f"import sys; sys.stdin.read(); print({json.dumps(VALID)!r}); "
        "sys.stderr.write('/private-secret sender@example.invalid'); sys.exit(1)",
        detector,
    )
    assert_error(asyncio.run(detector.scan(MAIL)), "static_inference_failed")


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_excess_output_kills_and_reaps_child(detector, monkeypatch, stream):
    processes = install_child(
        monkeypatch,
        f"import sys,time; sys.{stream}.write('x'*1000000); sys.{stream}.flush(); time.sleep(60)",
        detector,
    )
    assert_error(asyncio.run(detector.scan(MAIL)), "static_output_limit")
    assert processes[0].returncode is not None


def test_timeout_kills_and_reaps_child(detector, monkeypatch):
    monkeypatch.setattr(adapter, "TIMEOUT_SECONDS", 0.1)
    processes = install_child(monkeypatch, "import time; time.sleep(60)", detector)
    assert_error(asyncio.run(detector.scan(MAIL)), "static_timeout")
    assert processes[0].returncode is not None


def test_cancellation_kills_and_reaps_child(detector, monkeypatch):
    processes = install_child(monkeypatch, "import time; time.sleep(60)", detector)

    async def check():
        task = asyncio.create_task(detector.scan(MAIL))
        while not processes:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None

    asyncio.run(check())


@pytest.mark.parametrize("exception", [OSError, NotImplementedError])
def test_spawn_failure_is_sanitized(detector, monkeypatch, exception):
    async def broken(*args, **kwargs):
        raise exception("/private-secret sender@example.invalid")

    monkeypatch.setattr(adapter.asyncio, "create_subprocess_exec", broken)
    assert_error(asyncio.run(detector.scan(MAIL)), "static_start_failed")


@pytest.mark.parametrize(
    ("mail", "code"),
    [
        ([], "static_invalid_input"),
        ({"text_plain": {}}, "static_invalid_input"),
        ({"text_plain": "가" * (2 * 1024 * 1024 // 3)}, "static_input_too_large"),
    ],
)
def test_bad_or_oversize_input_never_starts_child(detector, monkeypatch, mail, code):
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid input must not start a subprocess")

    monkeypatch.setattr(adapter.asyncio, "create_subprocess_exec", forbidden)
    assert_error(asyncio.run(detector.scan(mail)), code)


def test_changed_model_during_scan_does_not_publish_wrong_identity(detector, monkeypatch):
    processes = install_child(
        monkeypatch,
        f"import sys,time; sys.stdin.read(); time.sleep(0.15); print({json.dumps(VALID)!r})",
        detector,
    )

    async def check():
        task = asyncio.create_task(detector.scan(MAIL))
        while not processes:
            await asyncio.sleep(0.01)
        detector.model_path.write_bytes(b"replaced artifact")
        return await task

    assert_error(asyncio.run(check()), "static_model_changed")


@pytest.fixture
def isolated_worker(tmp_path):
    script = tmp_path / "src/gmail/scripts/scan_collected_mail.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, script)
    package = tmp_path / "src/detector/src/prodog_sast"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(ROOT / "src/detector/src/prodog_sast/email_text.py", package / "email_text.py")
    return script, package


def run_worker(script, payload, model="/private-secret trusted model.joblib"):
    return subprocess.run(
        [sys.executable, "-I", str(script), model],
        input=payload,
        capture_output=True,
        timeout=10,
    )


def test_worker_uses_real_converter_and_calls_predict_once(isolated_worker):
    script, package = isolated_worker
    (package / "model.py").write_text(
        "from pathlib import Path\n"
        "calls = 0\n"
        'print("import private-secret")\n'
        "def load_artifact(path):\n"
        '    assert path == Path("/private-secret trusted model.joblib")\n'
        '    return {"loaded": True}\n'
        "def predict(artifact, texts):\n"
        "    global calls\n"
        "    calls += 1\n"
        "    assert calls == 1\n"
        '    assert artifact == {"loaded": True}\n'
        "    assert len(texts) == 1\n"
        "    text = texts[0]\n"
        '    assert "[SUBJECT] 합성 메일" in text\n'
        '    assert "[FROM] sender@example.invalid" in text\n'
        '    assert "[PLAIN] meeting" in text\n'
        '    assert "[HTML_RAW]" in text and "hidden payload" in text\n'
        '    assert "<!-- retained comment -->" in text\n'
        '    print("inference private-secret")\n'
        '    return [{"prompt_injection_probability": 0.4, "verdict": "suspicious",\n'
        '             "suspicious_threshold": 0.05, "danger_threshold": 0.8}]\n',
        encoding="utf-8",
    )
    result = run_worker(script, json.dumps(MAIL).encode())
    assert result.returncode == 0
    assert result.stderr == b""
    assert json.loads(result.stdout) == VALID


@pytest.mark.parametrize(
    "payload",
    [b"[]", b"broken private-secret", b"x" * (2 * 1024 * 1024 + 1)],
    ids=["not-object", "invalid-json", "oversized"],
)
def test_worker_rejects_invalid_input_before_loading_model(isolated_worker, payload):
    script, package = isolated_worker
    (package / "model.py").write_text('raise RuntimeError("private-secret")\n')
    result = run_worker(script, payload)
    assert result.returncode == 1
    assert result.stderr == b""
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "static_inference_failed"
    assert "private-secret" not in error["message"]


def test_worker_dependency_or_model_exception_is_sanitized(isolated_worker):
    script, package = isolated_worker
    (package / "model.py").write_text(
        'raise RuntimeError("/private-secret sender@example.invalid")\n'
    )
    result = run_worker(script, json.dumps(MAIL).encode())
    assert result.returncode == 1
    assert result.stderr == b""
    assert "private-secret" not in result.stdout.decode()
    assert json.loads(result.stdout)["error"]["code"] == "static_inference_failed"


@pytest.mark.skipif(
    os.environ.get("PRODOG_STATIC_ML_INTEGRATION") != "1",
    reason="opt in with a trusted real artifact and detector Python; never trains a team model",
)
def test_opt_in_real_ml_artifact():
    detector = adapter.StaticDetector(
        SimpleNamespace(
            static_python=Path(os.environ["PRODOG_STATIC_TEST_PYTHON"]),
            static_model_path=Path(os.environ["PRODOG_STATIC_TEST_MODEL"]),
        )
    )
    assert detector.status()["ready"] is True
    result = asyncio.run(detector.scan(MAIL))
    assert result["status"] == "completed", result["error"]
    assert result["verdict"] in {"safe", "suspicious", "danger"}
    assert 0 <= result["score"] <= 1


def test_unreadable_model_has_sanitized_not_ready_status(detector, monkeypatch):
    def unreadable(*args, **kwargs):
        raise PermissionError("/private-secret sender@example.invalid")

    monkeypatch.setattr(Path, "open", unreadable)
    state = detector.status()
    assert state["ready"] is False
    assert state["model_id"] is None
    assert "private-secret" not in state["message"]


def test_malformed_configured_path_is_not_ready(detector):
    detector.model_path = Path("private-secret\0invalid")
    state = detector.status()
    assert state["ready"] is False
    assert state["model_id"] is None
    assert "private-secret" not in state["message"]


@pytest.mark.parametrize("extra", [0, 1])
def test_stdout_limit_includes_trailing_whitespace(detector, monkeypatch, extra):
    output = json.dumps(VALID)
    output += " " * (32 * 1024 - len(output) + extra)
    install_child(
        monkeypatch, f"import sys; sys.stdin.read(); sys.stdout.write({output!r})", detector
    )
    result = asyncio.run(detector.scan(MAIL))
    if extra:
        assert_error(result, "static_output_limit")
    else:
        assert result["status"] == "completed"


def test_invalid_utf8_output_is_sanitized(detector, monkeypatch):
    install_child(
        monkeypatch, "import sys; sys.stdin.read(); sys.stdout.buffer.write(b'\\xff')", detector
    )
    assert_error(asyncio.run(detector.scan(MAIL)), "static_invalid_output")


def ensemble_output():
    lexical = dict(VALID, score=0.1, verdict="suspicious")
    semantic = dict(
        VALID, score=0.04, verdict="danger", suspicious_threshold=0.01, danger_threshold=0.03
    )
    return {
        **semantic,
        "mode": "ensemble",
        "decided_by": ["transformer"],
        "detectors": {"tfidf": lexical, "transformer": semantic},
    }


def test_ensemble_uses_risk_stage_not_largest_probability(detector, monkeypatch):
    output = ensemble_output()
    install_child(
        monkeypatch, f"import sys; sys.stdin.read(); print({json.dumps(output)!r})", detector
    )
    result = asyncio.run(detector.scan(MAIL))
    assert result["verdict"] == "danger"
    assert result["score"] == 0.04
    assert result["detectors"] == output["detectors"]
    assert result["decided_by"] == ["transformer"]
    assert result["mode"] == "ensemble"


@pytest.mark.parametrize("change", ["missing", "invalid_score", "downgrade", "wrong_decider"])
def test_malformed_ensemble_never_becomes_safe(detector, monkeypatch, change):
    output = ensemble_output()
    if change == "missing":
        del output["detectors"]["transformer"]
    elif change == "invalid_score":
        output["detectors"]["tfidf"]["score"] = float("nan")
    elif change == "downgrade":
        output.update(output["detectors"]["tfidf"])
    else:
        output["decided_by"] = ["tfidf"]
    install_child(
        monkeypatch, f"import sys; sys.stdin.read(); print({json.dumps(output)!r})", detector
    )
    assert_error(asyncio.run(detector.scan(MAIL)), "static_invalid_output")


TRANSFORMER_FILES = (
    "config.json",
    "model.safetensors",
    "prodog_transformer.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)


def test_transformer_files_are_required_and_part_of_model_identity(detector):
    lexical_id = detector.status()["model_id"]
    directory = detector.model_path.parent / "quadrat-transformer"
    directory.mkdir()
    assert detector.status()["ready"] is False
    for name in TRANSFORMER_FILES:
        (directory / name).write_bytes(name.encode())
    state = detector.status()
    assert state["ready"] is True and state["mode"] == "ensemble"
    assert state["model_id"] != lexical_id
    (directory / "tokenizer_config.json").write_bytes(b"changed tokenizer")
    assert detector.status()["model_id"] != state["model_id"]
    (directory / "model.safetensors").unlink()
    assert detector.status()["ready"] is False


def test_default_team_model_requires_transformer_even_if_entire_directory_is_missing(
    tmp_path, monkeypatch
):
    module = tmp_path / "src/gmail/src/prodog_gmail/static_detector.py"
    monkeypatch.setattr(adapter, "__file__", str(module))
    model = tmp_path / "src/detector/models/prompt_injection.joblib"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"trusted fixture")
    detector = adapter.StaticDetector(SimpleNamespace(static_python=Path(sys.executable)))
    assert detector.status()["mode"] == "ensemble"
    assert detector.status()["ready"] is False


def test_worker_invokes_team_transformer_and_preserves_both_results(isolated_worker):
    script, package = isolated_worker
    (package / "model.py").write_text(
        "def load_artifact(path): return {}\n"
        f"def predict(artifact,texts): return {[dict(VALID, prompt_injection_probability=0.4)]!r}\n"
    )
    (package / "transformer.py").write_text(
        "def load_transformer(path): return {}\n"
        "def predict_transformer(artifact,texts):\n"
        "    assert 'hidden payload' in texts[0]\n"
        "    return [{'prompt_injection_probability':0.95,'verdict':'danger',"
        "'suspicious_threshold':0.9,'danger_threshold':0.94}]\n"
        "def combine_predictions(lexical,semantic):\n"
        "    return [{'verdict':'danger','decided_by':['transformer'],"
        "'detectors':{'tfidf':lexical[0],'transformer':semantic[0]}}]\n"
    )
    model = package.parents[1] / "models/prompt_injection.joblib"
    (model.parent / "quadrat-transformer").mkdir(parents=True)
    result = run_worker(script, json.dumps(MAIL).encode(), str(model))
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["mode"] == "ensemble"
    assert output["verdict"] == "danger" and output["score"] == 0.95
    assert output["detectors"]["tfidf"]["score"] == 0.4
    assert output["decided_by"] == ["transformer"]
