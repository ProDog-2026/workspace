"""Read-only full-team preflight; never imports the app or deserializes model files."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import http.client
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
TRANSFORMER_FILES = (
    "config.json",
    "model.safetensors",
    "prodog_transformer.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)
BACKEND_PACKAGES = (
    "fastapi",
    "uvicorn",
    "httpx",
    "cryptography",
    "pydantic-settings",
    "python-dotenv",
    "prodog-sast",
)


def read_env(path):
    """Flat dotenv subset: no expansion, multiline values or execution."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)", line)
        if not match:
            raise ValueError("Unsupported dotenv syntax")
        key, value = match.groups()
        if value.startswith(("'", '"')):
            quoted = re.fullmatch(r"""(['"])(.*?)\1(?:\s+#.*)?""", value)
            if not quoted:
                raise ValueError("Unsupported dotenv syntax")
            value = quoted[2]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if "$" in value:
            raise ValueError("Dotenv expansion is not supported by this preflight")
        values[key] = value
    return values


def command(args, cwd):
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=8,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""


def local_json(path, payload=None):
    """Fixed loopback metadata only: no proxy, redirects, generation or model pulls."""
    connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=3)
    try:
        connection.request(
            "GET" if payload is None else "POST",
            path,
            body=None if payload is None else json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read(1024 * 1024 + 1)
        if response.status != 200 or len(raw) > 1024 * 1024:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, http.client.HTTPException):
        return {}
    finally:
        connection.close()


def jwt_role(key):
    try:
        payload = key.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["role"]
    except (ValueError, IndexError, KeyError, TypeError, binascii.Error):
        return None


def present(value):
    return bool(value.strip()) and not value.upper().startswith(
        ("YOUR_", "REPLACE", "CHANGEME", "CHANGE_ME", "<")
    )


def https_origin(value):
    try:
        url = urlsplit(value)
        return bool(
            url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
            and url.path in ("", "/")
            and not url.query
            and not url.fragment
            and (url.port is None or url.port > 0)
            and "\\" not in value
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and "YOUR_PROJECT" not in value.upper()
        )
    except ValueError:
        return False


def runtime(python, packages, cwd):
    code = (
        "import sys,json,importlib.metadata as m; "
        "print(json.dumps({'python':list(sys.version_info[:3]),"
        f"'packages':{{n:m.version(n) for n in {packages!r}}}}}))"
    )
    try:
        result = json.loads(command([str(python), "-I", "-B", "-c", code], cwd))
        if not isinstance(result, dict):
            return {}
        return result
    except ValueError:
        return {}


def version(value):
    match = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", value)
    return tuple(int(part or 0) for part in match.groups()) if match else (0, 0, 0)


def check(root):
    backend, dashboard, detector = (
        root / "src" / name for name in ("gmail", "dashboard", "detector")
    )
    results = {}
    envs = []
    for directory, files, label in (
        (backend, (".env",), "Backend .env"),
        (
            dashboard,
            (".env", ".env.local", ".env.development", ".env.development.local"),
            "Dashboard dotenv syntax",
        ),
    ):
        values = {}
        try:
            for name in files:
                values.update(read_env(directory / name))
            results[label] = (backend / ".env").is_file() if directory == backend else True
        except (OSError, ValueError):
            results[label] = False
        values.update(os.environ)
        envs.append(values)
    env, front = envs
    results["APP_MODE=google (full team app)"] = env.get("APP_MODE", "demo") == "google"
    results["Loopback URLs (8765 / 5173)"] = env.get(
        "APP_BASE_URL", "http://127.0.0.1:8765"
    ).rstrip("/") == "http://127.0.0.1:8765" and env.get("DASHBOARD_URL", "").rstrip("/") in (
        "",
        "http://127.0.0.1:5173",
    )
    try:
        key = env.get("TOKEN_ENCRYPTION_KEY", "").encode("ascii")
        results["TOKEN_ENCRYPTION_KEY"] = (
            len(base64.b64decode(key, altchars=b"-_", validate=True)) == 32
        )
    except (ValueError, UnicodeError):
        results["TOKEN_ENCRYPTION_KEY"] = False
    results["Google OAuth settings"] = all(
        present(env.get(name, "")) for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
    )
    results["Dashboard Supabase anon key"] = (
        jwt_role(front.get("VITE_SUPABASE_ANON_KEY", "")) == "anon"
    )
    results["Supabase project/key match"] = (
        https_origin(env.get("SUPABASE_URL", ""))
        and https_origin(front.get("VITE_SUPABASE_URL", ""))
        and env.get("SUPABASE_URL", "").rstrip("/") == front.get("VITE_SUPABASE_URL", "")
        and jwt_role(env.get("SUPABASE_ANON_KEY", "")) == "anon"
        and env.get("SUPABASE_ANON_KEY") == front.get("VITE_SUPABASE_ANON_KEY")
    )
    service = env.get("SUPABASE_SERVICE_KEY", "")
    results["Server Supabase service key"] = present(service) and (
        service.startswith("sb_secret_") or jwt_role(service) == "service_role"
    )
    results["EXPERIMENTS_ENABLED"] = env.get("EXPERIMENTS_ENABLED", "true").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )

    executable = "Scripts/python.exe" if platform.system() == "Windows" else "bin/python"
    server = runtime(backend / ".venv" / executable, BACKEND_PACKAGES, backend)
    results["Gmail Python >=3.11 + dependencies"] = tuple(server.get("python", [])) >= (
        3,
        11,
    ) and all(server.get("packages", {}).get(name) for name in BACKEND_PACKAGES)
    python = (
        backend / env["STATIC_PYTHON"]
        if env.get("STATIC_PYTHON")
        else detector / ".venv" / executable
    )
    ml = runtime(python, ("scikit-learn", "torch", "transformers"), backend)
    packages = ml.get("packages", {})
    results["Detector Python 3.12 + ML dependencies"] = (
        ml.get("python", [])[:2] == [3, 12]
        and packages.get("scikit-learn") == "1.6.0"
        and (2, 7, 0) <= version(packages.get("torch", "")) < (3, 0, 0)
        and (4, 48, 0) <= version(packages.get("transformers", "")) < (5, 0, 0)
    )
    model = (
        backend / env["STATIC_MODEL_PATH"]
        if env.get("STATIC_MODEL_PATH")
        else detector / "models/prompt_injection.joblib"
    )
    paths = [model, *(model.parent / "quadrat-transformer" / name for name in TRANSFORMER_FILES)]
    try:
        results["Team model files"] = all(
            path.is_file() and path.stat().st_size > 0 for path in paths
        )
    except (OSError, ValueError):
        results["Team model files"] = False
    try:
        expected = {
            name: checksum
            for checksum, name in (
                line.split(maxsplit=1)
                for line in (detector / "model-artifacts.sha256").read_text().splitlines()
            )
        }
        names = [
            "models/prompt_injection.joblib",
            *("models/quadrat-transformer/" + name for name in TRANSFORMER_FILES),
        ]
        actual = {}
        for name, path in zip(names, paths, strict=True):
            checksum = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    checksum.update(chunk)
            actual[name] = checksum.hexdigest()
        results["Team model SHA256"] = expected == actual
    except (OSError, ValueError):
        results["Team model SHA256"] = False
    results["uv installed"] = bool(command(["uv", "--version"], root))
    results["Node >=22.15.0"] = version(command(["node", "--version"], root)) >= (22, 15, 0)
    npm = "npm.cmd" if platform.system() == "Windows" else "npm"
    results["npm installed"] = bool(command([npm, "--version"], root))
    results["Dashboard dependencies"] = (dashboard / "node_modules/vite/package.json").is_file()

    context = env.get("SANDBOX_DOCKER_CONTEXT", "")
    valid_context = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", context) if context else True
    results["Explicit sandbox Docker context"] = bool(context and valid_context)
    docker = ["docker", "--context", context] if context else ["docker"]
    results["Docker Linux engine"] = (
        bool(valid_context)
        and command([*docker, "info", "--format", "{{.OSType}}"], root) == "linux"
    )
    image = (
        command(
            [
                *docker,
                "image",
                "inspect",
                "prodog-experiment:local",
                "--format",
                "{{json .Config.Volumes}}",
            ],
            root,
        )
        if results["Docker Linux engine"]
        else ""
    )
    results["Sandbox image (no declared volumes)"] = image in ("null", "{}")

    tags = local_json("/api/tags").get("models", [])
    names = (
        [
            item["name"]
            for item in tags
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]
        if isinstance(tags, list)
        else []
    )
    model = env.get("OLLAMA_MODEL") or (names[0] if names else "")
    info = local_json("/api/show", {"model": model}) if model in names else {}
    thinking = info.get("thinking", {})
    results["Ollama installed tool model"] = "tools" in (info.get("capabilities") or []) and not (
        isinstance(thinking, dict) and thinking.get("values") == [True]
    )
    return results


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Read-only full-team setup check (Python 3.11+ stdlib). No installs, downloads, "
            "model loading, inference, app startup, cloud access or config changes. "
            "See docs/team-setup.md."
        )
    )
    parser.parse_args()
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        print(
            "Platform: Apple Silicon Lima/VZ path live-verified previously; not an isolation audit."
        )
    else:
        print("Platform: Windows Docker Desktop / Linux VM compatibility; NOT live-tested here.")
    results = check(ROOT)
    for label, passed in results.items():
        print(f"[{'OK' if passed else 'FAIL'}] {label}")
    print("See docs/team-setup.md for fixes and trusted model SHA256 checks.")
    print(
        "Local checks only: OAuth consent, Auth/RLS, shared schema "
        "and VM isolation are not verified."
    )
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
