"""Run the trusted static ML detector in its own dependency environment."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024
TIMEOUT_SECONDS = 30
MAIL_FIELDS = ("subject", "sender", "text_plain", "text_html", "analysis_text")
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scan_collected_mail.py"
SETUP_MESSAGE = (
    "정적 분석 준비가 필요합니다. 탐지기 전용 Python 3.12 환경에 "
    "scikit-learn 1.6·PyTorch·Transformers와 팀 학습 모델을 준비하고 "
    "STATIC_PYTHON 및 STATIC_MODEL_PATH 설정을 확인하세요."
)
TRANSFORMER_FILES = (
    "config.json",
    "model.safetensors",
    "prodog_transformer.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)
VERDICT_RANK = {"safe": 0, "suspicious": 1, "danger": 2}
ERROR_MESSAGES = {
    "static_not_ready": SETUP_MESSAGE,
    "static_invalid_input": "분석할 메일 형식이 올바르지 않습니다.",
    "static_input_too_large": "분석할 메일이 2 MiB 입력 제한을 초과했습니다.",
    "static_start_failed": "정적 ML 분석 프로세스를 시작하지 못했습니다.",
    "static_timeout": "정적 ML 분석 시간이 초과되었습니다.",
    "static_output_limit": "정적 ML 분석 결과가 출력 제한을 초과했습니다.",
    "static_invalid_output": "정적 ML 분석 결과 형식이 올바르지 않습니다.",
    "static_inference_failed": "정적 ML 분석에 실패했습니다. 탐지기 환경과 모델을 확인하세요.",
    "static_model_changed": "분석 중 모델이 변경되었습니다. 다시 분석하세요.",
}


class _OutputLimitError(Exception):
    pass


async def _read_bounded(stream, process):
    chunks = bytearray()
    exceeded = False
    while chunk := await stream.read(4096):
        if not exceeded:
            if len(chunks) + len(chunk) > MAX_OUTPUT_BYTES:
                exceeded = True
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
            else:
                chunks.extend(chunk)
        # Keep draining after killing so full pipes cannot prevent reaping.
    if exceeded:
        raise _OutputLimitError
    return bytes(chunks)


async def _write_input(process, payload):
    try:
        process.stdin.write(payload)
        await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        process.stdin.close()


def _validated_score(value):
    if not isinstance(value, dict) or value.get("error") is not None:
        raise ValueError
    if value.get("status", "completed") != "completed":
        raise ValueError
    numbers = [value.get(key) for key in ("score", "suspicious_threshold", "danger_threshold")]
    if any(type(number) not in (float, int) for number in numbers):
        raise ValueError
    score, suspicious, danger = map(float, numbers)
    if not all(math.isfinite(number) for number in (score, suspicious, danger)):
        raise ValueError
    if not (0 <= score <= 1 and 0 <= suspicious < danger <= 1):
        raise ValueError
    verdict = "danger" if score >= danger else "suspicious" if score >= suspicious else "safe"
    # Fail closed if rounded detector output contradicts its verdict at a threshold.
    if value.get("verdict") != verdict:
        raise ValueError
    return {
        "score": score,
        "verdict": verdict,
        "suspicious_threshold": suspicious,
        "danger_threshold": danger,
    }


def _validated_result(value):
    result = _validated_score(value)
    if "detectors" not in value and "mode" not in value and "decided_by" not in value:
        return result
    detectors = value.get("detectors")
    if value.get("mode") != "ensemble" or not isinstance(detectors, dict):
        raise ValueError
    if set(detectors) != {"tfidf", "transformer"}:
        raise ValueError
    validated = {name: _validated_score(item) for name, item in detectors.items()}
    rank = max(VERDICT_RANK[item["verdict"]] for item in validated.values())
    deciders = [
        name
        for name in ("tfidf", "transformer")
        if VERDICT_RANK[validated[name]["verdict"]] == rank
    ]
    if value.get("decided_by") != deciders or result != validated[deciders[0]]:
        raise ValueError
    return {**result, "mode": "ensemble", "detectors": validated, "decided_by": deciders}


class StaticDetector:
    def __init__(self, settings):
        self.settings = settings
        root = Path(__file__).resolve().parents[3] / "detector"
        executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        self.python = Path(getattr(settings, "static_python", None) or root / ".venv" / executable)
        self.model_path = Path(
            getattr(settings, "static_model_path", None) or root / "models/prompt_injection.joblib"
        )
        self.require_transformer = (
            self.model_path.resolve() == (root / "models/prompt_injection.joblib").resolve()
        )
        self._fingerprint_key = None
        self._model_id = None

    def status(self) -> dict[str, Any]:
        """Readiness checks files only; loading sklearn belongs to the child process."""
        model_id = None
        ensemble = False
        try:
            directory = self.model_path.parent / "quadrat-transformer"
            ensemble = self.require_transformer or directory.exists()
            paths = [self.model_path]
            if ensemble:
                paths.extend(directory / name for name in TRANSFORMER_FILES)
            stats = [path.stat() for path in paths]
            if not all(path.is_file() for path in paths):
                raise OSError
            key = tuple(
                (path, stat.st_size, stat.st_mtime_ns)
                for path, stat in zip(paths, stats, strict=True)
            )
            if key != self._fingerprint_key:
                digest = hashlib.sha256()
                for path in paths:
                    if ensemble:
                        digest.update(str(path.relative_to(self.model_path.parent)).encode())
                    with path.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            digest.update(chunk)
                after = [path.stat() for path in paths]
                if (
                    tuple(
                        (path, stat.st_size, stat.st_mtime_ns)
                        for path, stat in zip(paths, after, strict=True)
                    )
                    != key
                ):
                    raise OSError
                self._model_id = digest.hexdigest()
                self._fingerprint_key = key
            model_id = self._model_id
            ready = (
                self.python.is_file()
                and (os.name == "nt" or os.access(self.python, os.X_OK))
                and SCRIPT.is_file()
            )
        except (OSError, ValueError):
            self._fingerprint_key = None
            self._model_id = None
            ready = False
        return {
            "ready": ready,
            "model_id": model_id,
            "mode": "ensemble" if ensemble else "lexical",
            "message": "팀 ML·Transformer 분석을 사용할 수 있습니다."
            if ready and ensemble
            else "기존 ML 분석을 사용할 수 있습니다."
            if ready
            else SETUP_MESSAGE,
        }

    async def _run(self, payload):
        process = await asyncio.create_subprocess_exec(
            str(self.python),
            "-I",
            str(SCRIPT),
            str(self.model_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_OUTPUT_BYTES,
        )
        tasks = [
            asyncio.create_task(_read_bounded(process.stdout, process)),
            asyncio.create_task(_read_bounded(process.stderr, process)),
            asyncio.create_task(_write_input(process, payload)),
            asyncio.create_task(process.wait()),
        ]
        group = asyncio.gather(*tasks)
        try:
            stdout, _stderr, _, returncode = await asyncio.wait_for(
                asyncio.shield(group), TIMEOUT_SECONDS
            )
            return stdout, returncode
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await asyncio.gather(*tasks, return_exceptions=True)
            # Retrieve the original group's exception after timeout/cancellation.
            if group.done() and not group.cancelled():
                group.exception()

    async def scan(self, mail) -> dict[str, Any]:
        state = self.status()
        model_id = state["model_id"]

        def error(code):
            return {
                "status": "error",
                "score": None,
                "verdict": None,
                "suspicious_threshold": None,
                "danger_threshold": None,
                "model_id": model_id,
                "error": {"code": code, "message": ERROR_MESSAGES[code]},
            }

        if not state["ready"]:
            return error("static_not_ready")
        try:
            if not isinstance(mail, Mapping):
                raise ValueError
            fields = {key: mail.get(key) for key in MAIL_FIELDS if key in mail}
            if any(value is not None and not isinstance(value, str) for value in fields.values()):
                raise ValueError
            payload = json.dumps(fields, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            return error("static_invalid_input")
        if len(payload) > MAX_INPUT_BYTES:
            return error("static_input_too_large")
        try:
            stdout, returncode = await self._run(payload)
        except TimeoutError:
            return error("static_timeout")
        except _OutputLimitError:
            return error("static_output_limit")
        except (OSError, ValueError, NotImplementedError):
            return error("static_start_failed")
        if returncode != 0:
            return error("static_inference_failed")
        try:
            result = _validated_result(json.loads(stdout.decode("utf-8")))
        except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
            return error("static_invalid_output")
        if self.status()["model_id"] != model_id:
            return error("static_model_changed")
        return {"status": "completed", **result, "model_id": model_id, "error": None}
