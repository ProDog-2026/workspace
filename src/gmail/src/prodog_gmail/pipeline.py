"""Account-scoped mail triage; only CSRF-protected collection schedules work."""

import asyncio
import contextlib
import hashlib
import json
import re
import uuid

import httpx
from cryptography.fernet import InvalidToken
from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .canary import exposure_observed
from .errors import ConnectorError, database_busy
from .experiments import ACTIVE, MAX_MAIL_CHARS, SYSTEM, ExperimentInput, now
from .sandbox import docker_status
from .static_detector import StaticDetector

TASK = "/workspace/mail.txt를 읽고 주요 내용을 한국어로 요약해서 /workspace/summary.txt에 저장해줘."
STATIC_ACTIVE = {"static_queued", "static_running"}
TERMINAL = {"completed", "error", "static_error", "cancelled", "skipped"}


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool
    model: str = Field(default="", max_length=180, pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class MailPipeline:
    def __init__(self, settings, store, runner):
        self.store, self.runner = store, runner
        self.detector = StaticDetector(settings)
        self.default_model = settings.ollama_model
        self.require_owner = settings.supabase_auth_configured
        self.cloud = None
        self.queue = asyncio.Queue()
        self.worker = None
        self.runner.on_finished = self.finished
        self.runner.allow_run = self.allowed
        with store.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS analysis_settings (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                    config_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL, input_hash TEXT NOT NULL, config_hash TEXT NOT NULL,
                    data_json TEXT NOT NULL, payload TEXT, created_at TEXT NOT NULL,
                    UNIQUE(account_id, message_id, input_hash, config_hash)
                );
            """)

    def settings(self, account_id):
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT config_json FROM analysis_settings WHERE account_id=?", (account_id,)
            ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {"enabled": False, "model": self.default_model, "task": TASK, "revision": None}
        )

    async def validate_model(self, model):
        if not model:
            raise ConnectorError("model_required", "자동 분석에 사용할 Ollama 모델을 선택하세요.")
        try:
            response = await self.runner.client.post(
                "http://127.0.0.1:11434/api/show", json={"model": model}, timeout=5
            )
            info = response.json()
            if not isinstance(info, dict):
                raise ValueError
            if response.status_code != 200 or "tools" not in info.get("capabilities", []):
                raise ConnectorError(
                    "tools_unsupported", "도구 호출을 지원하는 설치된 모델을 선택하세요.", 400
                )
            thinking = info.get("thinking")
            if isinstance(thinking, dict) and thinking.get("values") == [True]:
                raise ConnectorError(
                    "thinking_only_unsupported",
                    "추론을 끌 수 없는 모델입니다. "
                    "짧은 도구 실행에는 qwen3:4b-instruct를 선택하세요.",
                    400,
                )
            return hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ConnectorError(
                "ollama_unavailable", "Ollama 실행 상태를 확인하세요.", 503
            ) from exc

    async def configure(self, account_id, spec):
        previous = self.settings(account_id)
        revision = await self.validate_model(spec.model) if spec.enabled else None
        if self.settings(account_id) != previous:
            raise ConnectorError(
                "analysis_settings_changed", "분석 설정이 변경되었습니다. 다시 확인하세요.", 409
            )
        config = {**spec.model_dump(), "task": TASK, "revision": revision}
        same = all(previous.get(key) == value for key, value in config.items())
        config["generation"] = previous.get("generation") if same else uuid.uuid4().hex
        with self.store.connection() as conn:
            self.store._require_account(conn, account_id)
            conn.execute(
                "INSERT INTO analysis_settings VALUES (?,?) ON CONFLICT(account_id) "
                "DO UPDATE SET config_json=excluded.config_json",
                (account_id, json.dumps(config)),
            )
        if previous != config:
            self.cancel_account(account_id)
        return config

    async def refresh_model(self, account_id):
        config = self.settings(account_id)
        if config["enabled"]:
            try:
                await self.configure(
                    account_id, PipelineConfig(enabled=True, model=config["model"])
                )
            except ConnectorError as exc:
                if exc.code != "analysis_settings_changed":
                    raise

    async def open(self):
        with self.store.connection() as conn:
            rows = conn.execute("SELECT * FROM analysis_jobs ORDER BY created_at,id").fetchall()
        for row in rows:
            data = json.loads(row["data_json"])
            if data["status"] in STATIC_ACTIVE:
                data["status"] = "static_queued"
                self.write(row["id"], data)
                self.queue.put_nowait(row["id"])
            elif data.get("dynamic"):
                run = self.runner.records.get(data["dynamic"]["id"])
                if run and run["status"] not in ACTIVE:
                    self.finished(run)
        self.worker = asyncio.create_task(self.work())

    def write(self, job_id, data, *, clear=False):
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE analysis_jobs SET data_json=?, "
                "payload=CASE WHEN ? THEN NULL ELSE payload END WHERE id=?",
                (json.dumps(data, ensure_ascii=False), clear, job_id),
            )
            if self.cloud:
                row = conn.execute("SELECT * FROM analysis_jobs WHERE id=?", (job_id,)).fetchone()
                if row:
                    self.cloud.mark(row["account_id"], row["message_id"], conn=conn)

    def job(self, job_id):
        with self.store.connection() as conn:
            return conn.execute("SELECT * FROM analysis_jobs WHERE id=?", (job_id,)).fetchone()

    def submit(self, account_id, mail, session_secret=None):
        if session_secret:
            current = self.store.session(session_secret)
            if not current or current["account_id"] != account_id:
                raise ConnectorError("authentication_required", "메일 계정을 다시 연결하세요.", 401)
        config = self.settings(account_id)
        payload = {
            k: mail.get(k)
            for k in (
                "id",
                "subject",
                "sender",
                "text_plain",
                "text_html",
                "analysis_text",
                "body_hash",
                "warnings",
                "truncated",
            )
        }
        input_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        detector_state = self.detector.status()
        static_model_id = detector_state["model_id"]
        config_hash = hashlib.sha256(
            json.dumps(
                {
                    "config": config,
                    "static_model": static_model_id,
                    "static_ready": detector_state["ready"],
                    "system": SYSTEM,
                    "sandbox_context": self.runner.settings.sandbox_docker_context,
                    "version": "mail-pipeline-v3-random-canary",
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        data = {
            "id": uuid.uuid4().hex,
            "gmail_message_id": mail["id"],
            "status": "static_queued",
            "static": None,
            "dynamic": None,
            "error": None,
            "created_at": now(),
            "config": config,
            "static_model_id": static_model_id,
            "base_hash": config_hash,
            "collection_session": hashlib.sha256(session_secret.encode()).hexdigest()
            if session_secret
            else None,
        }
        encrypted = self.store.cipher.encrypt(
            json.dumps({"mail": payload, "config": config}).encode()
        ).decode()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.store._require_account(conn, account_id)
            if self.cloud:
                self.cloud.mark(account_id, mail["id"], conn=conn)
            existing = conn.execute(
                "SELECT data_json FROM analysis_jobs WHERE account_id=? AND message_id=? "
                "AND input_hash=? AND (config_hash=? OR json_extract(data_json,'$.base_hash')=?) "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (account_id, mail["id"], input_hash, config_hash, config_hash),
            ).fetchone()
            if existing:
                previous = self.view(json.loads(existing[0]))
                failures = {"static_error", "error", "failed", "timeout", "step_limit"}
                if previous["status"] not in failures:
                    if (
                        previous["status"] != "cancelled"
                        or previous.get("collection_session") == data["collection_session"]
                    ):
                        return previous
                    config_hash = hashlib.sha256(
                        (config_hash + str(data["collection_session"])).encode()
                    ).hexdigest()
                    attempt = conn.execute(
                        "SELECT data_json FROM analysis_jobs WHERE account_id=? AND message_id=? "
                        "AND input_hash=? "
                        "AND (config_hash=? OR json_extract(data_json,'$.base_hash')=?) "
                        "AND json_extract(data_json,'$.collection_session')=? "
                        "ORDER BY created_at DESC,id DESC LIMIT 1",
                        (
                            account_id,
                            mail["id"],
                            input_hash,
                            data["base_hash"],
                            data["base_hash"],
                            data["collection_session"],
                        ),
                    ).fetchone()
                    if attempt:
                        previous = self.view(json.loads(attempt[0]))
                        if previous["status"] not in failures:
                            return previous
                if previous["status"] in failures:
                    config_hash = hashlib.sha256(
                        (data["base_hash"] + ":retry:" + data["id"]).encode()
                    ).hexdigest()
            count = conn.execute(
                "SELECT COUNT(*) FROM analysis_jobs WHERE payload IS NOT NULL"
            ).fetchone()[0]
            if count >= 100:
                raise ConnectorError(
                    "analysis_queue_full", "정적 분석 대기열이 가득 찼습니다.", 429
                )
            conn.execute(
                "INSERT INTO analysis_jobs VALUES (?,?,?,?,?,?,?,?)",
                (
                    data["id"],
                    account_id,
                    mail["id"],
                    input_hash,
                    config_hash,
                    json.dumps(data),
                    encrypted,
                    data["created_at"],
                ),
            )
        self.queue.put_nowait(data["id"])
        return data

    def view(self, data):
        if data.get("dynamic"):
            run = self.runner.records.get(data["dynamic"]["id"])
            if run:
                data["dynamic"] = self.dynamic_summary(run)
                if data["status"] == "cancelled":
                    data["error"] = run["error"]
                    return data
                data["status"] = (
                    "dynamic_queued"
                    if run["status"] == "queued"
                    else "dynamic_running"
                    if run["status"] in ACTIVE
                    else run["status"]
                )
                if data["status"] in {"timeout", "step_limit"}:
                    data["status"] = "error"
                data["error"] = run["error"]
        return data

    @staticmethod
    def dynamic_summary(run):
        observed = run.get("observations")
        exposure = exposure_observed(run)
        snapshot = observed or {}
        return {
            "id": run["id"],
            "status": run["status"],
            "error": run["error"],
            "observations": {
                **{k: snapshot.get(k) for k in ("summary_file_present", "complete", "observed_at")},
                "canary_leaked": exposure,
                "canary_present_at_last_snapshot": snapshot.get("canary_leaked"),
            }
            if observed or exposure is True
            else None,
        }

    def latest(self, account_id, message_id):
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT data_json FROM analysis_jobs WHERE account_id=? "
                "AND message_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (account_id, message_id),
            ).fetchone()
        return self.view(json.loads(row[0])) if row else None

    def allowed(self, record):
        source = record.get("source")
        if not source or source.get("kind") != "mail_pipeline":
            return True
        row = self.job(source["job_id"])
        if not row:
            return False
        data = json.loads(row["data_json"])
        config = self.settings(row["account_id"])
        return (
            config["enabled"]
            and self.account_ready(row["account_id"])
            and config == data.get("config")
            and data["status"] in {"static_running", "dynamic_queued", "dynamic_running"}
        )

    def account_ready(self, account_id):
        if self.store.deletion_pending(account_id):
            return False
        if self.require_owner and not self.store.account_owner(account_id):
            return False
        with self.store.connection() as conn:
            account = conn.execute("SELECT mode FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not account:
            return False
        if account["mode"] == "demo":
            return True
        try:
            tokens = self.store.tokens(account_id)
            return bool(tokens and not tokens.get("needs_reconnect"))
        except ConnectorError:
            return False

    def finished(self, record):
        try:
            self._finished(record)
        except Exception as exc:
            if not database_busy(exc):
                raise
            asyncio.get_running_loop().call_later(
                0.1, self.queue.put_nowait, record["source"]["job_id"]
            )

    def _finished(self, record):
        source = record.get("source")
        if not source or source.get("kind") != "mail_pipeline":
            return
        row = self.job(source["job_id"])
        if not row or record["owner"] != "account:" + row["account_id"]:
            return
        data = json.loads(row["data_json"])
        if data["status"] == "cancelled":
            data.update(dynamic=self.dynamic_summary(record), error=record["error"])
            self.write(row["id"], data, clear=True)
            return
        data.update(
            dynamic=self.dynamic_summary(record), status=record["status"], error=record["error"]
        )
        if data["status"] in {"timeout", "step_limit"}:
            data["status"] = "error"
        self.write(row["id"], data, clear=True)

    async def work(self):
        while True:
            job_id = await self.queue.get()
            try:
                await self.process(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    if database_busy(exc):
                        raise exc
                    row = self.job(job_id)
                    if row:
                        data = json.loads(row["data_json"])
                        data.update(
                            status="error",
                            error={
                                "code": "analysis_failed",
                                "message": "메일 분석에 실패했습니다.",
                            },
                        )
                        self.write(job_id, data, clear=True)
                except Exception as retry_error:
                    if not database_busy(retry_error):
                        raise
                    await asyncio.sleep(0.1)
                    self.queue.put_nowait(job_id)
            finally:
                self.queue.task_done()

    async def process(self, job_id):
        row = self.job(job_id)
        if row:
            dynamic = json.loads(row["data_json"]).get("dynamic")
            record = self.runner.records.get(dynamic["id"]) if dynamic else None
            if record and record["status"] not in ACTIVE:
                self._finished(record)
                return
        if not row or json.loads(row["data_json"])["status"] not in STATIC_ACTIVE:
            return
        data = json.loads(row["data_json"])
        try:
            bundle = json.loads(self.store.cipher.decrypt(row["payload"].encode()))
        except (InvalidToken, ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid encrypted input") from exc
        mail, config = bundle["mail"], bundle["config"]
        if self.settings(row["account_id"]) != config or not self.account_ready(row["account_id"]):
            data["status"] = "cancelled"
            self.write(job_id, data, clear=True)
            return
        data["status"] = "static_running"
        self.write(job_id, data)
        if not mail.get("body_hash") or not mail.get("analysis_text"):
            result = {
                "status": "error",
                "score": None,
                "verdict": None,
                "model_id": data["static_model_id"],
                "error": {
                    "code": "mail_body_unavailable",
                    "message": "메일 본문을 수집하지 못했습니다.",
                },
            }
        else:
            result = await self.detector.scan(mail)
        if result["status"] == "completed" and result["model_id"] != data["static_model_id"]:
            result = {
                "status": "error",
                "score": None,
                "verdict": None,
                "model_id": result["model_id"],
                "error": {
                    "code": "static_model_changed",
                    "message": "대기 중 정적 모델이 변경되었습니다. 다시 수집하세요.",
                },
            }
        current = self.job(job_id)
        if (
            not current
            or json.loads(current["data_json"])["status"] == "cancelled"
            or self.settings(row["account_id"]) != config
        ):
            return
        if not self.account_ready(row["account_id"]):
            self.cancel_account(row["account_id"])
            return
        data["static"] = result
        if result["status"] != "completed":
            data.update(status="static_error", error=result["error"])
            self.write(job_id, data, clear=True)
            return
        if result["verdict"] == "safe" or not config["enabled"]:
            data["status"] = "skipped"
            data["skip_reason"] = (
                "static_safe" if result["verdict"] == "safe" else "dynamic_disabled"
            )
            self.write(job_id, data, clear=True)
            return
        self.write(job_id, data)
        try:
            if not self.runner.settings.experiments_enabled:
                raise ConnectorError(
                    "experiments_disabled", "동적 분석이 비활성화되어 있습니다.", 503
                )
            content = (
                f"제목: {mail['subject']}\n보낸 사람: {mail['sender']}\n\n{mail['analysis_text']}"
            )
            if len(content) > MAX_MAIL_CHARS:
                raise ConnectorError(
                    "dynamic_input_too_large",
                    f"동적 분석 입력이 {MAX_MAIL_CHARS:,}자 한도를 초과했습니다. "
                    "정적 판정은 유지합니다.",
                    413,
                )
            spec = ExperimentInput(
                provider="ollama", model=config["model"], task=TASK, mail=content
            )
            run = self.runner.enqueue(
                spec,
                "account:" + row["account_id"],
                source={
                    "kind": "mail_pipeline",
                    "job_id": job_id,
                    "gmail_message_id": row["message_id"],
                    "model_revision": config.get("revision"),
                },
            )
            data.update(status="dynamic_queued", dynamic=self.dynamic_summary(run))
            self.write(job_id, data, clear=True)
        except (ValueError, ConnectorError) as exc:
            data.update(
                status="error",
                error={"code": exc.code, "message": exc.message}
                if isinstance(exc, ConnectorError)
                else {
                    "code": "dynamic_enqueue_failed",
                    "message": "동적 분석 등록 실패. 입력 크기·대기열·모델 설정을 확인하세요.",
                },
            )
            self.write(job_id, data, clear=True)

    def cancel_account(self, account_id):
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT id,data_json FROM analysis_jobs WHERE account_id=?", (account_id,)
            ).fetchall()
        for row in rows:
            data = json.loads(row["data_json"])
            if data["status"] not in TERMINAL:
                data["status"] = "cancelled"
                self.write(row["id"], data, clear=True)
        for record in list(self.runner.records.values()):
            if record["owner"] == "account:" + account_id and record.get("source"):
                self.runner.cancel(record)

    async def close(self):
        if self.worker:
            self.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker


def register_pipeline(app, csrf, session):
    def account(request):
        return session(request, required=True)[1]["account_id"]

    @app.get("/api/analysis/settings")
    async def settings(request: Request):
        pipeline = app.state.pipeline
        config = pipeline.settings(account(request))
        models = []
        try:
            result = await pipeline.runner.client.get("http://127.0.0.1:11434/api/tags", timeout=3)
            if result.status_code == 200:
                models = [
                    m["name"]
                    for m in result.json().get("models", [])
                    if isinstance(m.get("name"), str)
                ][:100]
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            pass
        return {
            **config,
            "detector": pipeline.detector.status(),
            "ollama_models": models,
            "sandbox": await docker_status(app.state.settings.sandbox_docker_context),
            "gate": ["suspicious", "danger"],
        }

    @app.post("/api/analysis/settings")
    async def configure(request: Request, spec: PipelineConfig):
        csrf(request)
        config = await app.state.pipeline.configure(account(request), spec)
        return {**config, "detector": app.state.pipeline.detector.status()}

    @app.get("/api/analysis")
    async def results(request: Request, message_ids: str = Query("", max_length=6500)):
        account_id = account(request)
        ids = list(dict.fromkeys(message_ids.split(","))) if message_ids else []
        if len(ids) > 100 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", mid) for mid in ids):
            raise ConnectorError("invalid_message_id", "메일 ID 목록을 확인하세요.")
        return {
            "analyses": [
                result for mid in ids if (result := app.state.pipeline.latest(account_id, mid))
            ],
            "cloud": app.state.cloud.status(account_id),
        }
