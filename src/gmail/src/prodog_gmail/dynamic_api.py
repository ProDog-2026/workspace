"""Authenticated adapter routes; the browser never talks to the control API directly."""

import re

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from .dynamic import ACTIVE
from .dynamic_models import DynamicSettings, PromptInput, TestEmail, local_url
from .errors import ConnectorError
from .scan import scan_mail


def install_dynamic_routes(app, session, csrf, limit_requests, get_mail):
    def identity(request, *, write=False, gmail=False):
        secret, current = csrf(request) if write else session(request, required=gmail)
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
            "prompts": [{**p, "editable": p["id"] in editable} for p in data["prompts"]],
        }

    @app.post("/api/dynamic/prompts")
    async def new_prompt(request: Request, body: PromptInput):
        owner, _ = identity(request, write=True)
        result = await app.state.dynamic.request("POST", "/prompts", payload=body.model_dump())
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
    async def submit_mail(request: Request, message_id: str, retry: bool = False):
        owner, current = identity(request, write=True, gmail=True)
        service = app.state.dynamic
        if not service.settings(owner).auto_submit and not retry:
            return {"skipped": True, "reason": "automatic_disabled"}
        parsed = await get_mail(current, message_id)
        detection = scan_mail(app.state.detector, parsed)
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
        return {"job": await app.state.dynamic.enqueue(owner, email=body.model_dump())}

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

    return cancel_owner
