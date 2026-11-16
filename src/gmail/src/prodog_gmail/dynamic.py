"""Sequential dynamic-analysis handoff to the existing local agent-container API."""

import asyncio
import hashlib
import json
import re

import httpx

from .dynamic_models import DynamicSettings
from .dynamic_store import DynamicStore
from .errors import ConnectorError

ACTIVE = {"queued", "submitting", "running", "stopping", "unreachable"}
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def result_summary(result):
    """A negative observation is never a replacement for the static verdict."""
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("deviations"), list)
        or not isinstance(result.get("effects"), dict)
        or not isinstance(result.get("incomplete"), bool)
        or not isinstance(result.get("verification_errors"), list)
    ):
        raise ConnectorError(
            "dynamic_invalid_response", "동적 검사 결과 형식이 올바르지 않습니다.", 502
        )
    effects = result["effects"]
    for key in ("file_changes", "marker_received", "simulated_send_requests"):
        if not isinstance(effects.get(key), list):
            raise ConnectorError(
                "dynamic_invalid_response", "동적 검사 증거가 누락되었습니다.", 502
            )
    counts = {
        key: len(effects[key])
        for key in ("file_changes", "marker_received", "simulated_send_requests")
    }
    counts["deviations"] = len(result["deviations"])
    complete = (
        result.get("completion") == "completed"
        and result["incomplete"] is False
        and result.get("verification_status") == "completed"
        and not result["verification_errors"]
    )
    observed = any(counts.values())
    return {
        "status": "completed" if complete else "incomplete",
        "observation": "observed" if observed else "not_observed" if complete else "unknown",
        "counts": counts,
        "completion": result.get("completion"),
        "verification_status": result.get("verification_status"),
        "runtime_mode": result.get("runtime_mode", "unverified_runtime"),
        "error_code": None if complete else "dynamic_incomplete",
    }


class DynamicService:
    def __init__(self, store, client, service_url, load_mail):
        self.db = DynamicStore(store)
        self.client, self.url, self.load_mail = client, service_url, load_mail
        self.mail_memory = {}
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.worker = None
        self.deploying = False
        self.revoked_owners = set()

    async def revoke(self, owner):
        async with self.lock:
            self.revoked_owners.add(owner)

    async def request(self, method, path, *, payload=None, params=None, timeout=10):
        try:
            response = await self.client.request(
                method, self.url + path, json=payload, params=params, timeout=timeout
            )
        except httpx.RequestError as exc:
            raise ConnectorError(
                "dynamic_unavailable",
                "동적 검사 서버에 연결할 수 없습니다. 서버 실행 상태를 확인하세요.",
                503,
            ) from exc
        if response.is_error:
            # Do not echo remote bodies, prompts, keys or arbitrary error messages.
            try:
                code = response.json().get("error")
            except (ValueError, AttributeError):
                code = None
            allowed = {
                "builtin_prompt_read_only",
                "prompt_exists",
                "prompt_not_found",
                "invalid_prompt",
                "invalid_prompt_id",
                "result_not_ready",
                "run_not_found",
                "docker_runtime_not_configured",
                "prompt_changed",
                "submission_unknown",
                "request_id_conflict",
            }
            code = code if isinstance(code, str) and code in allowed else "dynamic_request_failed"
            raise ConnectorError(
                code,
                "동적 검사 요청을 처리하지 못했습니다. 설정과 서버 상태를 확인하세요.",
                response.status_code if response.status_code in (400, 403, 404, 409, 422) else 502,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError(
                "dynamic_invalid_response", "동적 검사 서버 응답이 올바르지 않습니다.", 502
            ) from exc

    def settings(self, owner):
        return DynamicSettings(**(self.db.settings(owner) or {}))

    async def options(self, owner):
        options = self.settings(owner).model_dump()
        if not options["model"]:
            raise ConnectorError(
                "dynamic_not_configured",
                "동적 검사 페이지에서 모델을 선택하고 설정을 저장하세요.",
                409,
            )
        prompts = await self.request("GET", "/prompts")
        prompt = next(
            (p for p in prompts.get("prompts", []) if p.get("id") == options["prompt_id"]), None
        )
        if not prompt or not isinstance(prompt.get("text"), str):
            raise ConnectorError(
                "prompt_not_found", "선택한 프롬프트가 없습니다. 설정을 다시 저장하세요.", 409
            )
        with self.db.store.connection() as db:
            account = db.execute("SELECT mode FROM accounts WHERE id=?", (owner,)).fetchone()
            if account and account["mode"] == "google" and not prompt.get("builtin"):
                owned = db.execute(
                    "SELECT 1 FROM dynamic_prompt_owners WHERE id=? AND owner=?",
                    (prompt["id"], owner),
                ).fetchone()
                if not owned:
                    raise ConnectorError("prompt_read_only", "내 프롬프트를 선택하세요.", 403)
        return options, fingerprint(prompt["text"])

    async def enqueue(
        self, owner, *, mail=None, detection=None, mode=None, email=None, retry=False, manual=False
    ):
        options, prompt_hash = await self.options(owner)
        key = None
        if mail:
            parts = [mail["id"], mail["body_hash"], options, prompt_hash]
            key = fingerprint(parts + ["manual"] if manual else parts)
        base_key = key
        async with self.lock:
            if owner in self.revoked_owners:
                raise ConnectorError("authentication_required", "메일 연동이 해제되었습니다.", 401)
            existing = (
                next(
                    (
                        j
                        for j in self.db.listing(owner)
                        if j.get("base_key", j.get("key")) == base_key
                    ),
                    None,
                )
                if key
                else None
            )
            if existing and not retry:
                return self.db.public(existing)
            if existing and existing["status"] in ACTIVE:
                return self.db.public(existing)
            if sum(j["status"] in ACTIVE for j in self.db.listing()) >= 100:
                raise ConnectorError(
                    "dynamic_queue_full",
                    "동적 검사 대기열이 가득 찼습니다. 잠시 후 다시 시도하세요.",
                    429,
                )
            if retry and existing:
                # New attempts keep the earlier evidence available in history.
                key = fingerprint([key, existing["id"]])
            data = {
                "source": ("gmail_manual" if manual else "gmail") if mail else "experiment",
                "mail_id": mail["id"] if mail else None,
                "body_hash": mail["body_hash"] if mail else None,
                "subject": mail.get("subject", "") if mail else email["subject"],
                "warnings": mail.get("warnings", []) if mail else [],
                "truncated": mail.get("truncated", False) if mail else False,
                "attachments_count": len(mail.get("attachments", [])) if mail else 0,
                "static_detection": detection,
                "observation": "unknown",
                "error_code": None,
                "options": options,
                "prompt_hash": prompt_hash,
                "mode": mode,
                "key": key,
                "base_key": base_key,
            }
            job, created = self.db.insert(owner, data, key)
            if created:
                self.mail_memory[job["id"]] = self.email_input(mail) if mail else email
            self.wake.set()
            return self.db.public(job)

    @staticmethod
    def email_input(mail):
        # Preserve raw HTML/comments and distinguish them from the fixed system prompt.
        body = mail.get("analysis_text") or "\n".join(
            filter(None, (mail.get("text_plain"), mail.get("text_html")))
        )
        return {
            "subject": mail.get("subject", ""),
            "body": "UNTRUSTED EMAIL DATA\nFrom: " + mail.get("sender", "") + "\n\n" + body,
        }

    def get(self, owner, job_id):
        job = self.db.get(job_id, owner)
        if job is None:
            raise ConnectorError("dynamic_job_not_found", "검사 기록을 찾을 수 없습니다.", 404)
        return job

    async def detail(self, owner, job_id, part, after=0):
        job = self.get(owner, job_id)
        if not job["run_id"]:
            raise ConnectorError("result_not_ready", "아직 실행 결과가 없습니다.", 404)
        return await self.request(
            "GET",
            f"/runs/{job['run_id']}/{part}",
            params={"after": after} if part == "events" else None,
        )

    async def stop(self, owner, job_id):
        async with self.lock:
            job = self.get(owner, job_id)
            if job["status"] == "queued":
                self.db.update(job_id, status="stopped")
                self.mail_memory.pop(job_id, None)
            elif job["run_id"] and job["status"] in ACTIVE:
                await self.request("POST", f"/runs/{job['run_id']}/stop")
                self.db.update(job_id, status="stopping")
            self.wake.set()
        return self.db.public(self.get(owner, job_id))

    async def import_experiments(self, owner):
        runs = await self.request("GET", "/runs")
        if not isinstance(runs, list):
            raise ConnectorError(
                "dynamic_invalid_response", "실험 기록 목록이 올바르지 않습니다.", 502
            )
        async with self.lock:
            known = {j["run_id"] for j in self.db.listing() if j["run_id"]}
            imported = 0
            for run in runs:
                rid = run.get("run_id", "")
                if not RUN_ID.fullmatch(rid) or rid in known:
                    continue
                # Existing standalone experiments only; mapped Gmail jobs are never imported.
                self.db.insert(
                    owner,
                    {
                        "source": "imported",
                        "mail_id": None,
                        "subject": run.get("subject", ""),
                        "static_detection": None,
                        "warnings": [],
                        "truncated": False,
                        "attachments_count": 0,
                        "observation": "unknown",
                        "error_code": None,
                    },
                    run_id=rid,
                )
                known.add(rid)
                imported += 1
            self.wake.set()
        return {"imported": imported}

    async def start(self):
        for job in self.db.listing():
            if job["status"] == "submitting":
                self.db.update(job["id"], status="unreachable", error_code="submission_unknown")
        self.worker = asyncio.create_task(self.loop())

    async def close(self):
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        self.mail_memory.clear()

    async def loop(self):
        while True:
            self.wake.clear()
            jobs = self.db.listing()
            active = [j for j in reversed(jobs) if j["status"] in ACTIVE and j["run_id"]]
            queued = [j for j in reversed(jobs) if j["status"] == "queued"]
            # Reconcile all already-running jobs after restart/import, then launch only one.
            for job in active:
                await self.poll(job)
            unresolved = any(j["status"] == "unreachable" and not j["run_id"] for j in jobs)
            if not active and not unresolved and queued:
                await self.submit(queued[0])
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except TimeoutError:
                pass

    async def submit(self, job):
        jid = job["id"]
        try:
            email = self.mail_memory.get(jid)
            if email is None and job["source"] in {"gmail", "gmail_manual"}:
                mail = await self.load_mail(
                    {"account_id": job["owner"], "mode": job["mode"]}, job["mail_id"]
                )
                if mail["body_hash"] != job["body_hash"]:
                    raise ConnectorError(
                        "mail_changed", "메일 내용이 바뀌었습니다. 다시 검사하세요.", 409
                    )
                email = self.email_input(mail)
            if email is None:
                raise ConnectorError(
                    "experiment_interrupted",
                    "서버 재시작으로 대기 중인 실험 입력이 사라졌습니다.",
                    409,
                )
            options = job["options"]
            async with self.lock:
                if self.deploying:
                    return
                current = self.db.get(jid)
                # Disconnect may delete the job while the mail was loading.
                if current is None or current["status"] not in {"queued", "unreachable"}:
                    self.mail_memory.pop(jid, None)
                    return
                if job["owner"] in self.revoked_owners:
                    self.db.update(jid, status="stopped")
                    return
                self.db.update(jid, status="submitting", run_id=jid)
                payload = {
                    "request_id": jid,
                    "expected_prompt_hash": job["prompt_hash"],
                    "config": {
                        "provider": "ollama",
                        "base_url": options["base_url"],
                        "model": options["model"],
                    },
                    "email": email,
                    "limits": options["limits"],
                    "temperature": options["temperature"],
                    "prompt_id": options["prompt_id"],
                }
                run = await self.request("POST", "/runs", payload=payload)
                rid = run.get("run_id", "") if isinstance(run, dict) else ""
                if rid != jid:
                    raise ConnectorError("submission_unknown", "실행 ID를 확인할 수 없습니다.", 502)
                self.db.update(jid, status="running", run_id=rid, error_code=None)
                self.mail_memory.pop(jid, None)
                self.wake.set()
        except ConnectorError as exc:
            current = self.db.get(jid)
            if current is None:
                self.mail_memory.pop(jid, None)
                return
            unknown = current["status"] == "submitting" and exc.code in {
                "dynamic_unavailable",
                "dynamic_invalid_response",
                "dynamic_request_failed",
                "submission_unknown",
            }
            self.db.update(
                jid,
                status="unreachable" if unknown else "failed",
                error_code="submission_unknown" if unknown else exc.code,
            )
            if not unknown:
                self.mail_memory.pop(jid, None)
        except Exception:
            current = self.db.get(jid)
            if current is None:
                self.mail_memory.pop(jid, None)
                return
            unknown = current["status"] == "submitting"
            self.db.update(
                jid,
                status="unreachable" if unknown else "failed",
                error_code="submission_unknown" if unknown else "dynamic_internal_error",
            )

    async def poll(self, job):
        jid, rid = job["id"], job["run_id"]
        try:
            run = await self.request("GET", f"/runs/{rid}")
            if not isinstance(run, dict):
                raise ConnectorError(
                    "dynamic_invalid_response", "검사 상태 응답이 올바르지 않습니다.", 502
                )
            if run.get("phase") == "finished":
                result = await self.request("GET", f"/runs/{rid}/result")
                self.db.update(jid, **result_summary(result))
                self.mail_memory.pop(jid, None)
                self.wake.set()
            elif run.get("phase") == "storage_error":
                self.db.update(jid, status="failed", error_code="dynamic_storage_error")
            else:
                self.db.update(
                    jid,
                    status="stopping" if job["status"] == "stopping" else "running",
                    phase=run.get("phase"),
                    calls=run.get("calls"),
                    elapsed_seconds=run.get("elapsed_seconds"),
                    error_code=None,
                )
        except ConnectorError as exc:
            if exc.code == "run_not_found" and job.get("error_code") == "submission_unknown":
                # A delayed POST may still arrive. Replay only the SAME idempotency key.
                if jid in self.mail_memory or job["source"] in {"gmail", "gmail_manual"}:
                    await self.submit(job)
            elif exc.code in {"run_not_found", "dynamic_invalid_response"}:
                self.db.update(jid, status="failed", error_code=exc.code)
            else:
                # Keep the execution slot reserved while its outcome is unknown.
                self.db.update(jid, status="unreachable", error_code=exc.code)
        except Exception:
            self.db.update(jid, status="failed", error_code="dynamic_invalid_response")
