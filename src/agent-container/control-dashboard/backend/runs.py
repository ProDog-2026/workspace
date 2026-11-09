"""In-memory run state, scoped bearer tokens and authoritative call limits."""
import asyncio
import copy
import hashlib
import os
import secrets
import time
import uuid
import json
import datetime as dt
from pathlib import Path
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from .llm import LLMAdapter, LLMError, ConnectionConfig
from .evidence import EvidenceStore, event, file_events
from .rules import evaluate
from .prompts import DEFAULT_PROMPT_ID, PromptStore


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_calls: int = Field(default=8, ge=1, le=100)
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    total_seconds: float = Field(default=600, gt=0, le=3600)


class RunError(Exception):
    def __init__(self, code, status_code=409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


@dataclass(repr=False)
class Run:
    id: str
    config: ConnectionConfig
    tools: list
    limits: Limits
    token: str = field(repr=False)
    started: float = field(default_factory=time.monotonic)
    status: str = "running"
    calls: int = 0
    limit_kind: str | None = None
    pending: asyncio.Task | None = field(default=None, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    email: dict = field(default_factory=dict)
    marker: str = ""
    container_id: str | None = None
    events: list = field(default_factory=list)
    phase: str = "manual"
    elapsed_seconds: float | None = None
    created_at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    lifecycle: asyncio.Task | None = field(default=None, repr=False)
    temperature: float = 0.1
    prompt_id: str = DEFAULT_PROMPT_ID
    prompt_text: str = ""

    def public(self):
        return {"run_id": self.id, "status": self.status, "calls": self.calls,
                "limit_kind": self.limit_kind, "limits": self.limits.model_dump(),
                "connection": self.config.public(), "phase": self.phase,
                "container_id": self.container_id, "created_at": self.created_at,
                "temperature": self.temperature, "prompt_version": self.prompt_id,
                "subject": self.email.get("subject", ""), "system_monitor": "not_connected",
                "elapsed_seconds": self.elapsed_seconds if self.elapsed_seconds is not None
                    else round(time.monotonic() - self.started, 2)}


class RunManager:
    def __init__(self, adapter: LLMAdapter, runtime=None, data_root=None):
        self.adapter = adapter
        self.runs: dict[str, Run] = {}
        self.settings: dict[str, ConnectionConfig] = {}
        self.runtime = runtime
        self.store = EvidenceStore(data_root) if data_root else None
        self.prompts = PromptStore(Path(data_root) / "prompts.json" if data_root else None)

    def create(self, config, tools, limits=None, temperature=0.1, prompt_id=DEFAULT_PROMPT_ID,
               *, run_id=None):
        prompt = self.prompts.get(prompt_id)
        if prompt is None:
            raise RunError("prompt_not_found", 422)
        run = Run(temperature=temperature, prompt_id=prompt_id, prompt_text=prompt["text"], id=run_id or uuid.uuid4().hex, config=config.model_copy(deep=True),
                  tools=copy.deepcopy(tools), limits=(limits or Limits()).model_copy(deep=True),
                  token=secrets.token_urlsafe(32))
        self.runs[run.id] = run
        return run.public() | {"execution_token": run.token}

    def get(self, run_id):
        if run_id not in self.runs:
            raise RunError("run_not_found", 404)
        return self.runs[run_id]

    def stop(self, run_id):
        run = self.get(run_id)
        if run.status == "running":
            run.status = "stopped"
            run.token = ""
            if run.pending:
                run.pending.cancel()
        return run.public()

    async def infer(self, run_id, token, messages, tools):
        run = self.get(run_id)
        async with run.lock:
            if not token or not run.token or not secrets.compare_digest(token.encode(), run.token.encode()):
                raise RunError("invalid_execution_token", 401)
            if run.status != "running":
                raise RunError("run_not_running")
            remaining = run.limits.total_seconds - (time.monotonic() - run.started)
            if remaining <= 0 or run.calls >= run.limits.max_calls:
                run.status = "limit_reached"
                run.limit_kind = "total_time" if remaining <= 0 else "llm_calls"
                raise RunError("limit_reached", 429)
            if tools != run.tools:
                raise RunError("tool_definitions_changed", 422)
            if run.pending is not None:
                raise RunError("request_in_progress")
            run.calls += 1  # Reserve before awaiting I/O; provider failures also count.
            task = asyncio.create_task(self.adapter.chat(
                copy.deepcopy(messages), run.tools, run.config, run.limits.max_tokens, run.temperature))
            run.pending = task
            self.record(run, "manager", "llm_request", call_number=run.calls)
        try:
            result = await asyncio.wait_for(task, timeout=remaining)
            self.record(run, "manager", "llm_response", response=result.model_dump())
            if run.status != "running":
                raise RunError("run_not_running")
            if time.monotonic() - run.started >= run.limits.total_seconds:
                run.status, run.limit_kind = "limit_reached", "total_time"
                raise RunError("limit_reached", 429)
            if result.status == "limit_reached":
                run.status, run.limit_kind = "limit_reached", "output_tokens"
            elif result.finish_reason == "stop" and not result.tool_calls:
                run.status = "completed"
            return result.model_dump() | {"run_id": run.id, "run_status": run.status}
        except asyncio.TimeoutError:
            if run.status == "stopped":
                raise RunError("run_stopped") from None
            run.status, run.limit_kind = "limit_reached", "total_time"
            raise RunError("limit_reached", 429) from None
        except asyncio.CancelledError:
            if run.status == "stopped":
                raise RunError("run_stopped") from None
            raise
        except LLMError as exc:
            if run.status == "stopped":
                raise RunError("run_stopped") from None
            run.status = "timeout" if exc.code == "provider_timeout" else "error"
            raise
        finally:
            async with run.lock:
                run.pending = None

    async def close(self):
        for run in self.runs.values():
            if run.lifecycle and not run.lifecycle.done():
                self.stop(run.id)
        lifecycles = [r.lifecycle for r in self.runs.values() if r.lifecycle]
        if lifecycles:
            await asyncio.gather(*lifecycles, return_exceptions=True)
        tasks = []
        for run in self.runs.values():
            if run.pending:
                tasks.append(run.pending)
                run.pending.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def create_worker_container(self, run_id):
        run = self.get(run_id)
        loop = asyncio.get_running_loop()
        def emit(data):
            loop.call_soon_threadsafe(self.agent_event, run, data)
        return await asyncio.to_thread(self.runtime.execute, run, emit)

    async def cleanup_worker_container(self, run_id):
        await asyncio.to_thread(self.runtime.remove, self.get(run_id).container_id)

    def record(self, run, source, kind, **data):
        record = event(run.id, source, kind, **data)
        serialized = json.dumps(record, ensure_ascii=False)
        for secret in (run.token, run.config.api_key.get_secret_value() if run.config.api_key else ""):
            if secret:
                serialized = serialized.replace(secret, "[REDACTED]")
        run.events.append(json.loads(serialized))

    def agent_event(self, run, data):
        kind = data.get("event_type", "unknown")
        clean = {k: v for k, v in data.items() if k not in
                 ("run_id", "source", "event_id", "event_type", "occurred_at")}
        self.record(run, "agent", kind, **clean)

    def _replay_request(self, request_id, request_hash):
        """A reservation without a result is ambiguous, never permission to relaunch."""
        try:
            reservation = json.loads((self.store.directory(request_id) / "request.json")
                                     .read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise RunError("submission_unknown") from None
        if not isinstance(reservation, dict) or not reservation.get("request_hash"):
            raise RunError("submission_unknown")
        if reservation["request_hash"] != request_hash:
            raise RunError("request_id_conflict")
        return self.view(request_id)

    def launch(self, config, tools, email, limits=None, temperature=0.1, prompt_id=DEFAULT_PROMPT_ID,
               *, request_id=None, expected_prompt_hash=None):
        if self.store is None:
            raise RunError("docker_runtime_not_configured", 503)
        limits = limits or Limits()
        if request_id is not None:
            try:
                directory = self.store.directory(request_id)
            except ValueError:
                raise RunError("invalid_request_id", 422) from None
            # Credentials may rotate without changing the requested execution. Only
            # the digest is persisted; email bodies and credentials are not stored here.
            request_hash = hashlib.sha256(json.dumps({
                "config": config.model_dump(exclude={"api_key"}), "tools": tools,
                "email": {k: str(email.get(k, "")) for k in ("subject", "body")},
                "limits": limits.model_dump(), "temperature": temperature,
                "prompt_id": prompt_id, "expected_prompt_hash": expected_prompt_hash,
            }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            existing = self._replay_request(request_id, request_hash)
            if existing is not None:
                return existing
            if request_id in self.runs or (directory / "result.json").exists():
                raise RunError("request_id_conflict")
        if self.runtime is None:
            raise RunError("docker_runtime_not_configured", 503)
        prompt = self.prompts.get(prompt_id)
        if prompt is None:
            raise RunError("prompt_not_found", 422)
        if expected_prompt_hash is not None:
            actual_hash = hashlib.sha256(json.dumps(prompt["text"], sort_keys=True,
                                                     ensure_ascii=False).encode()).hexdigest()
            if actual_hash != expected_prompt_hash:
                raise RunError("prompt_changed", 422)
        if request_id is not None:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                # Exclusive creation serializes competing submissions, including
                # other processes. Flush the reservation before any task is scheduled.
                with (directory / "request.json").open("x", encoding="utf-8") as handle:
                    json.dump({"request_hash": request_hash}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                return self._replay_request(request_id, request_hash)
            except OSError:
                raise RunError("reservation_storage_failed", 503) from None
        info = self.create(config, tools, limits, temperature, prompt_id, run_id=request_id)
        run = self.get(info["run_id"])
        run.marker = "MK-" + run.id
        run.email = {k: str(email.get(k, "")).replace("{{MARKER}}", run.marker)
                     for k in ("subject", "body")}
        run.phase = "executing"
        run.lifecycle = asyncio.create_task(self._execute(run))
        return run.public()

    async def _execute(self, run):
        files, errors = {}, []
        try:
            files, errors, receipts, exit_code = await self.create_worker_container(run.id)
            await asyncio.sleep(0)  # Drain events scheduled by the Docker thread.
            finals = [e for e in run.events if e.get("event_type") == "final" and e["source"] == "agent"]
            if run.status not in ("stopped", "timeout", "limit_reached", "error"):
                completion = finals[-1].get("completion") if finals else None
                run.status = completion if completion in ("completed", "limit_reached", "error") else "error"
                if exit_code not in (None, 0) and run.status == "completed":
                    run.status = "error"
            run.phase = "verifying"
            for item in receipts + file_events(run.id, files):
                self.record(run, item["source"], item["event_type"], **{
                    k: v for k, v in item.items() if k not in
                    ("run_id", "source", "event_id", "event_type", "occurred_at")})
        except Exception:
            if run.status not in ("stopped", "timeout"):
                run.status = "error"
            errors.append("docker_execution_or_verification_failed")
            self.record(run, "manager", "runtime_error", error=errors[-1])
        finally:
            run.token = ""
        run.elapsed_seconds = round(time.monotonic() - run.started, 2)
        if run.limit_kind == "total_time" and run.status == "limit_reached":
            run.status = "timeout"
        result = {**run.public(), **evaluate(run.events, run.status),
                  "runtime_mode": getattr(self.runtime, "mode", "unverified_runtime"),
                  "summary": files.get("summary.txt", ""),
                  "verification_errors": errors,
                  "verification_status": "failed" if errors else "completed",
                  "prompt_version": run.prompt_id, "system_prompt": run.prompt_text,
                  "temperature": run.temperature,
                  "test_set_version": "MAIL_CASES_V2"}
        # Never remove the container if persistence fails: retain it for recovery.
        try:
            self.store.save(run.id, run.events, result | {"phase": "finished"}, result["summary"])
        except OSError:
            run.phase = "storage_error"
            return
        try:
            await self.cleanup_worker_container(run.id)
        except Exception:
            self.record(run, "manager", "cleanup_failed", error="container_cleanup_failed")
            result["cleanup_error"] = "container_cleanup_failed"
            self.store.save(run.id, run.events, result | {"phase": "finished"}, result["summary"])
        run.phase = "finished"

    def view(self, run_id):
        if run_id in self.runs:
            return self.get(run_id).public()
        try:
            return self.store.load(run_id)
        except (OSError, ValueError, AttributeError):
            try:
                if self.store and (self.store.directory(run_id) / "request.json").exists():
                    raise RunError("submission_unknown")
            except ValueError:
                pass
            raise RunError("run_not_found", 404) from None

    def listing(self):
        archived = {r["run_id"]: r for r in self.store.listing()} if self.store else {}
        archived.update({k: r.public() for k, r in self.runs.items()})
        return sorted(archived.values(), key=lambda r: r["created_at"], reverse=True)

    def events_for(self, run_id):
        if run_id in self.runs:
            return self.runs[run_id].events
        try:
            return self.store.events(run_id)
        except (OSError, ValueError, AttributeError):
            raise RunError("run_not_found", 404) from None

    def result_for(self, run_id):
        try:
            return self.store.load(run_id)
        except (OSError, ValueError, AttributeError):
            raise RunError("result_not_ready", 404) from None

