"""Authenticated adapter routes; the browser never talks to the control API directly."""

import asyncio
import base64
import binascii
import hashlib
import re
import uuid

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from .dynamic import ACTIVE
from .dynamic_models import DynamicSettings, PromptInput, TestEmail, local_url
from .errors import ConnectorError
from .file_scan import decode_file
from .mail import MAX_RAW_BYTES, mail_metadata, parse_gmail_message
from .pdf_text import MAX_PDF_BYTES, MAX_PDF_TEXT_CHARS, extract_pdf_text
from .scan import cacheable_detection, model_key, scan_mail


def install_dynamic_routes(app, session, csrf, limit_requests, get_mail):
    def identity(request, *, write=False, gmail=False):
        secret, current = csrf(request) if write else session(request, required=gmail)
        if app.state.settings.app_mode == "google":
            _, current = session(request, required=True)
        if not current:
            raise ConnectorError(
                "authentication_required", "페이지를 새로고침해 세션을 연결하세요.", 401
            )
        if gmail and (not current["account_id"] or current["mode"] != app.state.settings.app_mode):
            raise ConnectorError("authentication_required", "먼저 메일 계정을 연결해 주세요.", 401)
        owner = current["account_id"] or "session:" + current["id_hash"]
        if write:
            limit_requests(secret, "dynamic", 90)
        return owner, current

    @app.get("/api/dynamic/settings")
    async def settings(request: Request):
        owner, _ = identity(request)
        data = app.state.dynamic.settings(owner).model_dump()
        return {
            "settings": data,
            "configured": bool(data["model"]),
            "static_configured": app.state.detector is not None,
            "service_url": app.state.settings.dynamic_service_url,
        }

    @app.put("/api/dynamic/settings")
    async def save_settings(request: Request, body: DynamicSettings):
        owner, _ = identity(request, write=True)
        app.state.dynamic.db.save_settings(owner, body.model_dump())
        return {"settings": body.model_dump(), "configured": bool(body.model)}

    @app.get("/api/dynamic/deployment")
    async def deployment_status(request: Request):
        identity(request)
        return await app.state.deployment.status()

    @app.post("/api/dynamic/deployment", status_code=202)
    async def deploy(request: Request):
        identity(request, write=True)
        return await app.state.deployment.start()

    @app.get("/api/dynamic/models")
    async def models(request: Request, base_url: str):
        identity(request)
        try:
            checked = local_url(base_url, docker_host=True)
        except ValueError as exc:
            raise ConnectorError("invalid_request", "로컬 Ollama 주소를 입력하세요.", 422) from exc
        return await app.state.dynamic.request(
            "GET", "/ollama/models", params={"base_url": checked}
        )

    @app.post("/api/dynamic/check")
    async def check(request: Request, body: DynamicSettings):
        identity(request, write=True)
        if not body.model:
            raise ConnectorError("dynamic_not_configured", "모델을 선택하세요.", 422)
        return await app.state.dynamic.request(
            "POST",
            "/connections/check",
            payload={
                "config": {"provider": "ollama", "base_url": body.base_url, "model": body.model},
                "max_tokens": body.limits.max_tokens,
            },
            timeout=610,
        )

    @app.get("/api/dynamic/prompts")
    async def prompts(request: Request):
        owner, _ = identity(request)
        data = await app.state.dynamic.request("GET", "/prompts")
        with app.state.store.connection() as db:
            editable = {
                r[0]
                for r in db.execute("SELECT id FROM dynamic_prompt_owners WHERE owner=?", (owner,))
            }
        return {
            **data,
            "prompts": [{**p, "editable": p["id"] in editable} for p in data["prompts"]
                        if app.state.settings.app_mode != "google"
                        or p.get("builtin") or p["id"] in editable],
        }

    @app.post("/api/dynamic/prompts")
    async def new_prompt(request: Request, body: PromptInput):
        owner, _ = identity(request, write=True)
        payload = body.model_dump()
        if app.state.settings.app_mode == "google":
            prefix = hashlib.sha256(owner.encode()).hexdigest()[:20]
            payload["id"] = "u_" + prefix + "_" + (body.id or uuid.uuid4().hex)[:17]
        result = await app.state.dynamic.request("POST", "/prompts", payload=payload)
        with app.state.store.connection() as db:
            db.execute("INSERT INTO dynamic_prompt_owners VALUES (?, ?)", (result["id"], owner))
        return result

    def prompt_owner(owner, prompt_id):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", prompt_id):
            raise ConnectorError("invalid_request", "프롬프트 ID를 확인하세요.", 422)
        with app.state.store.connection() as db:
            row = db.execute(
                "SELECT owner FROM dynamic_prompt_owners WHERE id=?", (prompt_id,)
            ).fetchone()
        if not row or row[0] != owner:
            raise ConnectorError(
                "prompt_read_only", "기존·다른 계정의 프롬프트는 새 ID로 복사해 사용하세요.", 403
            )

    @app.put("/api/dynamic/prompts/{prompt_id}")
    async def update_prompt(request: Request, prompt_id: str, body: PromptInput):
        owner, _ = identity(request, write=True)
        prompt_owner(owner, prompt_id)
        return await app.state.dynamic.request(
            "PUT", f"/prompts/{prompt_id}", payload=body.model_dump()
        )

    @app.delete("/api/dynamic/prompts/{prompt_id}")
    async def delete_prompt(request: Request, prompt_id: str):
        owner, _ = identity(request, write=True)
        prompt_owner(owner, prompt_id)
        result = await app.state.dynamic.request("DELETE", f"/prompts/{prompt_id}")
        with app.state.store.connection() as db:
            db.execute("DELETE FROM dynamic_prompt_owners WHERE id=?", (prompt_id,))
        return result

    @app.post("/api/dynamic/mail/{message_id}")
    async def submit_mail(
        request: Request, message_id: str, retry: bool = False, start: bool = False
    ):
        owner, current = identity(request, write=True, gmail=True)
        service = app.state.dynamic
        if not (start or retry or service.settings(owner).auto_submit):
            return {"skipped": True, "reason": "automatic_disabled"}

        store, detector = app.state.store, app.state.detector
        key = model_key(detector)
        cached = store.cached_scans(owner, [message_id], key).get(message_id)
        parsed = await get_mail(current, message_id)
        if parsed["body_hash"] is None:
            return {"skipped": True, "reason": "static_not_ready"}
        detection = None
        coverage_changed = False
        if cached and cached.get("body_hash") == parsed["body_hash"]:
            previous = cached["attachments"]
            current_files = parsed["attachments"]
            coverage_changed = previous != current_files
            if not coverage_changed:
                detection = cached["detection"]
            elif any(
                saved.get("pdf_status") == "parsed" and fresh.get("pdf_status") != "parsed"
                for saved, fresh in zip(previous, current_files, strict=False)
            ):
                raise ConnectorError(
                    "pdf_extraction_changed",
                    "정적 검사에 사용한 PDF 텍스트를 다시 읽지 못했어요. 잠시 후 다시 시도하세요.",
                    409,
                )
        if detection is None:
            detection = scan_mail(detector, parsed)
            if detection and detection["status"] == "ok":
                cacheable = cacheable_detection(detection)
                store.save_metadata(owner, mail_metadata(parsed), preserve_scan=not cacheable)
                if cacheable:
                    store.save_scan(owner, message_id, key, detection)
                if coverage_changed and cached["detection"]:
                    body_fields = ("status", "verdict", "probability", "static_score", "thresholds")
                    previous_body = {field: cached["detection"].get(field) for field in body_fields}
                    current_body = {field: detection.get(field) for field in body_fields}
                    if previous_body != current_body:
                        if not cacheable:
                            raise ConnectorError(
                                "static_scan_failed",
                                "첨부파일 검사를 완료하지 못했어요. 잠시 후 다시 시도해 주세요.",
                                503,
                            )
                        raise ConnectorError(
                            "static_result_changed",
                            "메일 본문의 정적 검사 결과가 바뀌었어요. "
                            "메일 목록을 새로고침해 주세요.",
                            409,
                        )
        if not detection or detection.get("status") != "ok":
            return {"skipped": True, "reason": "static_not_ready"}
        if detection.get("verdict") not in {"suspicious", "danger"}:
            return {"skipped": True, "reason": "not_flagged"}
        job = await service.enqueue(
            owner, mail=parsed, detection=detection, mode=current["mode"], retry=retry
        )
        return JSONResponse({"job": job, "skipped": False}, status_code=202)

    @app.post("/api/dynamic/experiments", status_code=202)
    async def experiment(request: Request, body: TestEmail):
        owner, _ = identity(request, write=True)
        pdf_messages = {
            "invalid": "올바른 PDF 파일을 선택하세요.",
            "encrypted": "암호화된 PDF는 사용할 수 없습니다.",
            "no_text": "PDF에서 텍스트를 찾지 못했습니다.",
            "limit": "PDF 처리 한도를 초과했습니다.",
        }
        if body.eml:
            raw = decode_file(body.eml, ".eml", MAX_RAW_BYTES)
            if not re.match(br"[!-9;-~]+:[^\r\n]*\r?\n", raw) or not re.search(br"\r?\n\r?\n", raw):
                raise ConnectorError("invalid_eml", "올바른 EML 파일을 선택하세요.", 422)
            supplemental = None
            if body.pdf:
                pdf_payload = decode_file(body.pdf, ".pdf", MAX_PDF_BYTES)
                status, _ = await asyncio.to_thread(
                    extract_pdf_text, pdf_payload, MAX_PDF_TEXT_CHARS
                )
                if status != "parsed":
                    raise ConnectorError("pdf_" + status, pdf_messages[status], 422)
                supplemental = (body.pdf.filename, pdf_payload)
            parsed = await asyncio.to_thread(
                parse_gmail_message,
                {"raw": base64.urlsafe_b64encode(raw).decode()},
                supplemental_pdf=supplemental,
            )
            if parsed["body_hash"] is None:
                raise ConnectorError("invalid_eml", "EML 파일을 읽을 수 없습니다.", 422)
            email = app.state.dynamic.email_input(parsed, pdf_texts=parsed["pdf_texts"])
            if not email["subject"]:
                email["subject"] = body.eml.filename
            meta = {
                "warnings": parsed["warnings"],
                "truncated": parsed["truncated"],
                "attachments_count": len(parsed["attachments"]),
                "pdf_attachments_count": len(parsed["pdf_texts"]),
            }
            return {"job": await app.state.dynamic.enqueue(
                owner, email=email, experiment_meta=meta
            )}
        email = {"subject": body.subject, "body": body.body}
        if body.pdf:
            try:
                payload = base64.b64decode(body.pdf.data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ConnectorError("invalid_pdf", "PDF 파일을 읽을 수 없습니다.", 422) from exc
            if len(payload) > MAX_PDF_BYTES:
                raise ConnectorError("pdf_limit", "PDF는 1MB 이하여야 합니다.", 422)
            status, text = await asyncio.to_thread(extract_pdf_text, payload, MAX_PDF_TEXT_CHARS)
            if status != "parsed":
                raise ConnectorError("pdf_" + status, pdf_messages[status], 422)
            email["body"] += "\n\nUNTRUSTED PDF ATTACHMENT 1\n" + text
        return {"job": await app.state.dynamic.enqueue(
            owner, email=email, experiment_pdf=bool(body.pdf)
        )}

    @app.post("/api/dynamic/manual-mail/{message_id}", status_code=202)
    async def manual_mail(request: Request, message_id: str):
        owner, current = identity(request, write=True, gmail=True)
        parsed = await get_mail(current, message_id)
        job = await app.state.dynamic.enqueue(
            owner, mail=parsed, mode=current["mode"], manual=True, retry=True
        )
        return {"job": job}

    @app.post("/api/dynamic/import")
    async def import_experiments(request: Request):
        owner, _ = identity(request, write=True)
        if app.state.settings.app_mode == "google":
            raise ConnectorError(
                "ownership_unknown", "소유자를 확인할 수 없는 기록은 가져올 수 없어요.", 403
            )
        return await app.state.dynamic.import_experiments(owner)

    @app.get("/api/dynamic/jobs")
    async def jobs(request: Request):
        owner, _ = identity(request)
        service = app.state.dynamic
        return {"jobs": [service.db.public(j) for j in service.db.listing(owner)]}

    @app.get("/api/dynamic/jobs/{job_id}")
    async def job(request: Request, job_id: str):
        owner, _ = identity(request)
        service = app.state.dynamic
        return service.db.public(service.get(owner, job_id))

    @app.post("/api/dynamic/jobs/{job_id}/stop")
    async def stop(request: Request, job_id: str):
        owner, _ = identity(request, write=True)
        return await app.state.dynamic.stop(owner, job_id)

    @app.get("/api/dynamic/jobs/{job_id}/{part}")
    async def detail(request: Request, job_id: str, part: str, after: int = Query(0, ge=0)):
        owner, _ = identity(request)
        if part not in {"events", "result", "flow"}:
            raise ConnectorError("invalid_request", "지원하지 않는 조회입니다.", 404)
        return await app.state.dynamic.detail(owner, job_id, part, after)

    async def cancel_owner(owner):
        await app.state.dynamic.revoke(owner)
        for job in app.state.dynamic.db.listing(owner):
            if job["status"] in ACTIVE:
                try:
                    await app.state.dynamic.stop(owner, job["id"])
                except ConnectorError:
                    # A failed remote stop must remain visible to the worker, not become "safe".
                    app.state.dynamic.db.update(job["id"], error_code="disconnect_stop_failed")
        # Keep mappings until every remote deletion succeeds, so a retry has exact IDs.
        for job in app.state.dynamic.db.listing(owner):
            if job["run_id"]:
                await app.state.dynamic.request("DELETE", f"/runs/{job['run_id']}", timeout=25)
            app.state.dynamic.mail_memory.pop(job["id"], None)
        with app.state.store.connection() as db:
            prompts = db.execute(
                "SELECT id FROM dynamic_prompt_owners WHERE owner=?", (owner,)
            ).fetchall()
        for prompt in prompts:
            try:
                await app.state.dynamic.request("DELETE", f"/prompts/{prompt[0]}")
            except ConnectorError as exc:
                if exc.code != "prompt_not_found":
                    raise
        with app.state.store.connection() as db:
            for table in ("dynamic_jobs", "dynamic_settings", "dynamic_prompt_owners"):
                db.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))

    return cancel_owner
