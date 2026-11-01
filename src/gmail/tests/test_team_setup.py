"""Read-only onboarding checks, using synthetic config and external-runtime doubles."""

import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/check_team_setup.py"


def test_help_works_without_project_dependencies_or_local_config(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "read-only" in result.stdout.lower()
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def checker():
    assert SCRIPT.is_file(), "The fresh-clone preflight is missing"
    spec = importlib.util.spec_from_file_location("check_team_setup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jwt(role):
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return "header." + payload + ".signature"


@pytest.fixture
def ready(checker, tmp_path, monkeypatch):
    monkeypatch.setattr(checker.os, "environ", {})
    monkeypatch.setattr(checker.platform, "system", lambda: "Darwin")
    backend = tmp_path / "src/gmail"
    dashboard = tmp_path / "src/dashboard"
    detector = tmp_path / "src/detector"
    for directory in (backend, dashboard, detector):
        directory.mkdir(parents=True)
    env = {
        "APP_MODE": "google",
        "TOKEN_ENCRYPTION_KEY": "A" * 43 + "=",
        "GOOGLE_CLIENT_ID": "synthetic-client",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "SUPABASE_URL": "https://synthetic.supabase.co",
        "SUPABASE_ANON_KEY": jwt("anon"),
        "SUPABASE_SERVICE_KEY": "sb_secret_synthetic",
        "SANDBOX_DOCKER_CONTEXT": "prodog-vm",
        "OLLAMA_MODEL": "synthetic-instruct",
    }
    (backend / ".env").write_text("\n".join(f"{k}={v}" for k, v in env.items()))
    (dashboard / ".env.local").write_text(
        f"VITE_SUPABASE_URL={env['SUPABASE_URL']}\nVITE_SUPABASE_ANON_KEY={jwt('anon')}\n"
    )
    for path in (
        backend / ".venv/bin/python",
        detector / ".venv/bin/python",
        detector / "models/prompt_injection.joblib",
        *(
            detector / "models/quadrat-transformer" / name
            for name in (
                "config.json",
                "model.safetensors",
                "prodog_transformer.json",
                "special_tokens_map.json",
                "tokenizer.json",
                "tokenizer_config.json",
                "vocab.txt",
            )
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic artifact; never deserialize")
    model_files = [
        detector / "models/prompt_injection.joblib",
        *sorted((detector / "models/quadrat-transformer").iterdir()),
    ]
    (detector / "model-artifacts.sha256").write_text(
        "\n".join(
            hashlib.sha256(path.read_bytes()).hexdigest()
            + "  "
            + path.relative_to(detector).as_posix()
            for path in model_files
        )
        + "\n"
    )
    (dashboard / "node_modules/vite/package.json").parent.mkdir(parents=True)
    (dashboard / "node_modules/vite/package.json").write_text("{}")

    def command(args, cwd):
        if args[0] == "node":
            assert args[1:] == ["--version"]
            return "v22.15.0"
        if args[0] == "docker":
            offset = 3 if args[1:3] == ["--context", "prodog-vm"] else 1
            if args[offset:] == ["info", "--format", "{{.OSType}}"]:
                return "linux"
            assert args[offset:] == [
                "image",
                "inspect",
                "prodog-experiment:local",
                "--format",
                "{{json .Config.Volumes}}",
            ]
            return "null"
        if args[0] == "uv":
            assert args[1:] == ["--version"]
            return "uv 0.9.0"
        if args[0] in ("npm", "npm.cmd"):
            assert args[1:] == ["--version"]
            return "10.9.0"
        assert args[0] in {str(backend / ".venv/bin/python"), str(detector / ".venv/bin/python")}
        assert args[1:4] == ["-I", "-B", "-c"]
        if args[0].startswith(str(backend)):
            return json.dumps(
                {
                    "python": [3, 14, 7],
                    "packages": {
                        name: "1.0"
                        for name in (
                            "fastapi",
                            "uvicorn",
                            "httpx",
                            "cryptography",
                            "pydantic-settings",
                            "python-dotenv",
                            "prodog-sast",
                        )
                    },
                }
            )
        return json.dumps(
            {
                "python": [3, 12, 14],
                "packages": {
                    "scikit-learn": "1.6.0",
                    "torch": "2.7.0",
                    "transformers": "4.48.0",
                },
            }
        )

    monkeypatch.setattr(checker, "command", command)

    def local_json(path, payload=None):
        if path == "/api/tags":
            assert payload is None
            return {"models": [{"name": "synthetic-instruct"}]}
        assert path == "/api/show" and payload == {"model": "synthetic-instruct"}
        return {"capabilities": ["completion", "tools"], "thinking": {"values": [False]}}

    monkeypatch.setattr(checker, "local_json", local_json)
    return tmp_path


def test_complete_setup_passes_without_touching_files(checker, ready):
    before = {p: p.read_bytes() for p in ready.rglob("*") if p.is_file()}
    results = checker.check(ready)
    assert results and all(results.values()), results
    assert {p: p.read_bytes() for p in ready.rglob("*") if p.is_file()} == before


def test_synthetic_evaluations_require_an_explicit_context(checker, ready, monkeypatch):
    monkeypatch.setitem(checker.os.environ, "SANDBOX_DOCKER_CONTEXT", "")
    assert checker.check(ready)["Explicit sandbox Docker context"] is False


def test_different_or_missing_release_model_hashes_fail(checker, ready):
    model = ready / "src/detector/models/prompt_injection.joblib"
    model.write_bytes(b"different synthetic model")
    assert checker.check(ready)["Team model SHA256"] is False
    (ready / "src/detector/model-artifacts.sha256").unlink()
    assert checker.check(ready)["Team model SHA256"] is False


@pytest.mark.parametrize(
    "path,failed",
    [
        ("src/gmail/.env", "Backend .env"),
        ("src/dashboard/.env.local", "Dashboard Supabase anon key"),
        ("src/detector/models/prompt_injection.joblib", "Team model files"),
        ("src/detector/models/quadrat-transformer/tokenizer.json", "Team model files"),
        ("src/detector/models/quadrat-transformer/model.safetensors", "Team model files"),
        ("src/dashboard/node_modules/vite/package.json", "Dashboard dependencies"),
    ],
)
def test_missing_fresh_clone_assets_fail(checker, ready, path, failed):
    (ready / path).unlink()
    assert checker.check(ready)[failed] is False


@pytest.mark.parametrize(
    "key,value,failed",
    [
        ("TOKEN_ENCRYPTION_KEY", "bad-key", "TOKEN_ENCRYPTION_KEY"),
        ("GOOGLE_CLIENT_SECRET", "", "Google OAuth settings"),
        ("SUPABASE_SERVICE_KEY", "", "Server Supabase service key"),
        ("SUPABASE_SERVICE_KEY", jwt("anon"), "Server Supabase service key"),
        ("SUPABASE_URL", "https://wrong.supabase.co", "Supabase project/key match"),
        ("APP_MODE", "demo", "APP_MODE=google (full team app)"),
        ("EXPERIMENTS_ENABLED", "false", "EXPERIMENTS_ENABLED"),
        ("SANDBOX_DOCKER_CONTEXT", "bad\ncontext", "Docker Linux engine"),
    ],
)
def test_bad_required_settings_fail(checker, ready, monkeypatch, key, value, failed):
    monkeypatch.setitem(checker.os.environ, key, value)
    assert checker.check(ready)[failed] is False


@pytest.mark.parametrize(
    "value",
    [
        "YOUR_ANON_KEY",
        "sb_secret_hidden",
        jwt("service_role"),
        "sb_publishable_currently_rejected_by_dashboard",
    ],
)
def test_browser_key_matches_actual_dashboard_boundary(checker, ready, monkeypatch, value):
    monkeypatch.setitem(checker.os.environ, "VITE_SUPABASE_ANON_KEY", value)
    assert checker.check(ready)["Dashboard Supabase anon key"] is False


@pytest.mark.parametrize(
    "node,passed",
    [("v22.14.0", False), ("v22.15.0", True), ("v24.0.0", True), ("unparseable", False)],
)
def test_node_minimum_includes_minor_version(checker, ready, monkeypatch, node, passed):
    original = checker.command
    monkeypatch.setattr(
        checker, "command", lambda args, cwd: node if args[0] == "node" else original(args, cwd)
    )
    assert checker.check(ready)["Node >=22.15.0"] is passed


@pytest.mark.parametrize(
    "python,packages",
    [
        ([3, 14, 7], {"scikit-learn": "1.6.0", "torch": "2.7", "transformers": "4.48"}),
        ([3, 12, 14], {"scikit-learn": "1.7.0", "torch": "2.7", "transformers": "4.48"}),
        ([3, 12, 14], {"scikit-learn": "1.6.0"}),
    ],
)
def test_detector_requires_312_and_compatible_packages(
    checker, ready, monkeypatch, python, packages
):
    original = checker.command
    monkeypatch.setattr(
        checker,
        "command",
        lambda args, cwd: (
            json.dumps({"python": python, "packages": packages})
            if args[0].startswith(str(ready / "src/detector"))
            else original(args, cwd)
        ),
    )
    assert checker.check(ready)["Detector Python 3.12 + ML dependencies"] is False


def test_windows_uses_scripts_python_without_lima(checker, ready, monkeypatch):
    monkeypatch.setattr(checker.platform, "system", lambda: "Windows")
    original = checker.command

    def windows_command(args, cwd):
        args = [arg.replace("Scripts/python.exe", "bin/python") for arg in args]
        return original(args, cwd)

    monkeypatch.setattr(checker, "command", windows_command)
    assert all(checker.check(ready).values())


@pytest.mark.parametrize("reply", ["", "windows", "secret@example.invalid\n"])
def test_unreachable_or_non_linux_engine_fails(checker, ready, monkeypatch, reply):
    original = checker.command
    monkeypatch.setattr(
        checker, "command", lambda args, cwd: reply if "info" in args else original(args, cwd)
    )
    assert checker.check(ready)["Docker Linux engine"] is False


def test_declared_image_volumes_are_rejected(checker, ready, monkeypatch):
    original = checker.command
    monkeypatch.setattr(
        checker,
        "command",
        lambda args, cwd: '{"/workspace": {}}' if "inspect" in args else original(args, cwd),
    )
    assert checker.check(ready)["Sandbox image (no declared volumes)"] is False


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"capabilities": ["completion"]},
        {"capabilities": ["tools"], "thinking": {"values": [True]}},
        {"capabilities": None},
    ],
)
def test_ollama_missing_tools_or_thinking_only_fails(checker, ready, monkeypatch, info):
    original = checker.local_json
    monkeypatch.setattr(
        checker,
        "local_json",
        lambda path, payload=None: info if path == "/api/show" else original(path, payload),
    )
    assert checker.check(ready)["Ollama installed tool model"] is False


def test_empty_ollama_setting_can_check_first_installed_model(checker, ready, monkeypatch):
    monkeypatch.setitem(checker.os.environ, "OLLAMA_MODEL", "")
    assert checker.check(ready)["Ollama installed tool model"] is True


def test_config_parsing_quotes_comments_and_safe_failure(checker, tmp_path):
    path = tmp_path / ".env"
    path.write_text('export KEY="a # b" # comment\nOTHER=value # comment\nEMPTY=\n')
    assert checker.read_env(path) == {"KEY": "a # b", "OTHER": "value", "EMPTY": ""}
    path.write_text('SECRET="sensitive@example.invalid\n')
    with pytest.raises(ValueError) as caught:
        checker.read_env(path)
    assert "sensitive" not in str(caught.value)


def test_cli_missing_config_fails_safely_and_creates_nothing(tmp_path):
    assert SCRIPT.is_file(), "The fresh-clone preflight is missing"
    copy = tmp_path / "src/gmail/scripts/check_team_setup.py"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(SCRIPT.read_bytes())
    env = {"PATH": "", "TOKEN_ENCRYPTION_KEY": "private@example.invalid"}
    before = set(tmp_path.rglob("*"))
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(copy)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "FAIL" in result.stdout and "TOKEN_ENCRYPTION_KEY" in result.stdout
    assert "private@example.invalid" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert set(tmp_path.rglob("*")) == before


def test_failed_command_never_returns_secret_output(checker, tmp_path):
    assert (
        checker.command(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys; print('private@example.invalid'); sys.exit(1)",
            ],
            tmp_path,
        )
        == ""
    )
    assert checker.command([str(tmp_path / "missing-command")], tmp_path) == ""


def test_missing_npm_is_a_blocker(checker, ready, monkeypatch):
    original = checker.command
    monkeypatch.setattr(
        checker, "command", lambda args, cwd: "" if args[0] == "npm" else original(args, cwd)
    )
    assert checker.check(ready)["npm installed"] is False


def test_main_exit_status_and_output_are_safe(checker, ready, monkeypatch, capsys):
    monkeypatch.setattr(checker, "ROOT", ready)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    assert checker.main() == 0
    monkeypatch.setitem(checker.os.environ, "GOOGLE_CLIENT_SECRET", "")
    assert checker.main() == 1
    output = capsys.readouterr()
    assert "FAIL" in output.out
    assert "hidden-secret" not in output.out + output.err
    assert "sb_secret_synthetic" not in output.out + output.err
    assert "synthetic.supabase.co" not in output.out + output.err


def test_vite_development_local_takes_precedence(checker, ready):
    (ready / "src/dashboard/.env.development.local").write_text(
        "VITE_SUPABASE_ANON_KEY=sb_secret_do_not_print"
    )
    assert checker.check(ready)["Dashboard Supabase anon key"] is False


def test_python_probe_reads_real_runtime_metadata(checker, tmp_path):
    result = checker.runtime(sys.executable, ("pytest",), tmp_path)
    assert result["python"] == list(sys.version_info[:3])
    assert result["packages"]["pytest"]
    assert checker.runtime(sys.executable, ("prodog-nonexistent-package",), tmp_path) == {}


@pytest.mark.parametrize("python,packages", [([3, 10, 9], {}), ([3, 14, 7], {})])
def test_backend_old_python_or_missing_dependencies_fail(
    checker, ready, monkeypatch, python, packages
):
    original = checker.command
    monkeypatch.setattr(
        checker,
        "command",
        lambda args, cwd: (
            json.dumps({"python": python, "packages": packages})
            if args[0].startswith(str(ready / "src/gmail"))
            else original(args, cwd)
        ),
    )
    assert checker.check(ready)["Gmail Python >=3.11 + dependencies"] is False


def test_timeout_is_a_safe_failure(checker, monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private@example.invalid", 8, output="hidden-secret")

    monkeypatch.setattr(checker.subprocess, "run", timeout)
    assert checker.command(["unavailable"], tmp_path) == ""


@pytest.mark.parametrize(
    "status,raw,expected",
    [
        (200, b'{"models":[]}', {"models": []}),
        (302, b'{"secret":"hidden"}', {}),
        (200, b"invalid private@example.invalid", {}),
        (200, b"[]", {}),
        (200, b"x" * (1024 * 1024 + 1), {}),
    ],
)
def test_ollama_metadata_does_not_follow_redirects_or_accept_bad_replies(
    checker,
    monkeypatch,
    status,
    raw,
    expected,
):
    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 11434, 3)

        def request(self, method, path, body, headers):
            assert (method, path, body) == ("GET", "/api/tags", None)

        def getresponse(self):
            return self

        @property
        def status(self):
            return status

        def read(self, size):
            assert size == 1024 * 1024 + 1
            return raw

        def close(self):
            pass

    monkeypatch.setattr(checker.http.client, "HTTPConnection", Connection)
    assert checker.local_json("/api/tags") == expected
