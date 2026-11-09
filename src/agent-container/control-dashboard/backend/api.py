"""Minimal trusted local dashboard API; settings and runs are memory-only."""
import asyncio
import uuid
import json
import ipaddress
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .flow import experiment_rows, trace
from .llm import ConnectionConfig
from .llm import LLMError
from .prompts import DEFAULT_PROMPT_ID
from .runs import Limits, RunManager, RunError
from .worker_api import install_errors


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: ConnectionConfig | None = None
    settings_id: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
    email: dict[str, str] | None = None
    temperature: float = Field(default=0.1, ge=0, le=2)
    prompt_id: str = DEFAULT_PROMPT_ID


class PromptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    description: str = ""
    text: str


class CheckConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: ConnectionConfig
    max_tokens: int = Field(default=2048, ge=1, le=32768)


def create_dashboard_app(manager: RunManager):
    app = FastAPI(title="Agent control backend — phase 1")
    install_errors(app)

    @app.middleware("http")
    async def block_work_network(request: Request, call_next):
        cidr = os.getenv("WORK_NET_CIDR")
        if cidr and request.client:
            try:
                if ipaddress.ip_address(request.client.host) in ipaddress.ip_network(cidr):
                    return JSONResponse({"error": "management_api_not_available_on_work_network"}, status_code=403)
            except ValueError:
                pass
        return await call_next(request)

    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        # 재빌드 후 브라우저가 이전 JS/CSS를 쓰지 않도록 매번 ETag로 재검증하게 한다.
        response = await call_next(request)
        path = request.url.path
        if request.method == "GET" and (path == "/" or path.endswith((".js", ".css", ".html"))):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.post("/settings", status_code=201)
    async def save_settings(config: ConnectionConfig):
        settings_id = uuid.uuid4().hex
        manager.settings[settings_id] = config.model_copy(deep=True)
        return {"settings_id": settings_id, **config.public()}

    @app.post("/runs", status_code=201)
    async def create_run(body: CreateRun):
        if (body.config is None) == (body.settings_id is None):
            raise RunError("provide_config_or_settings_id", 422)
        config = body.config
        if body.settings_id:
            config = manager.settings.get(body.settings_id)
            if config is None:
                raise RunError("settings_not_found", 404)
        if body.email is not None:
            if not body.email.get("body", "").strip():
                raise RunError("email_body_required", 422)
            tools = json.loads(Path(__file__).with_name("tool_definitions.json").read_text(encoding="utf-8"))
            return manager.launch(config, tools, body.email, body.limits, body.temperature, body.prompt_id)
        # Manual protocol testing remains available, but credentials stay server-side.
        created = manager.create(config, body.tools, body.limits, body.temperature, body.prompt_id)
        return {k: v for k, v in created.items() if k != "execution_token"}

    @app.get("/prompts")
    async def list_prompts():
        return {"prompts": manager.prompts.listing(), "default": DEFAULT_PROMPT_ID}

    @app.post("/prompts", status_code=201)
    async def create_prompt(body: PromptBody):
        return manager.prompts.create(body.id or "", body.description, body.text)

    @app.put("/prompts/{prompt_id}")
    async def update_prompt(prompt_id: str, body: PromptBody):
        return manager.prompts.update(prompt_id, body.description, body.text)

    @app.delete("/prompts/{prompt_id}")
    async def delete_prompt(prompt_id: str):
        manager.prompts.delete(prompt_id)
        return {"deleted": prompt_id}

    @app.get("/ollama/models")
    async def ollama_models(base_url: str):
        try:
            checked = ConnectionConfig(base_url=base_url, model="x").base_url
        except ValueError:
            raise RunError("invalid_base_url", 422) from None
        try:
            return {"models": await manager.adapter.list_models(checked)}
        except LLMError as exc:
            raise RunError(exc.code, 502) from None

    @app.get("/runs")
    async def list_runs():
        return manager.listing()

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str):
        return manager.view(run_id)

    @app.get("/runs/{run_id}/events")
    async def get_events(run_id: str, after: int = Query(default=0, ge=0)):
        records = manager.events_for(run_id)
        return {"events": records[after:], "next": len(records)}

    @app.get("/runs/{run_id}/result")
    async def get_result(run_id: str):
        return manager.result_for(run_id)

    @app.get("/runs/{run_id}/flow")
    async def get_flow(run_id: str):
        """LangGraph로 실제 이벤트를 따라간 노드별 흐름과 State (진행 중이면 현재 노드에서 멈춤)."""
        run = manager.view(run_id)
        events = list(manager.events_for(run_id))
        result = None
        if run.get("phase") == "finished":
            try:
                result = manager.result_for(run_id)
            except RunError:
                result = None
        root = os.getenv("EXPERIMENTS_ROOT") or (manager.store.root.parent / "experiments" if manager.store else None)
        return trace(run, events, result, experiment_rows(root))

    @app.post("/runs/{run_id}/stop")
    async def stop_run(run_id: str):
        return manager.stop(run_id)

    @app.post("/connections/check")
    async def check_connection(body: CheckConnection):
        try:
            return await asyncio.wait_for(
                manager.adapter.check_connection(body.config, body.max_tokens), timeout=600)
        except asyncio.TimeoutError:
            raise RunError("connection_check_timeout", 504) from None

    frontend = Path(__file__).resolve().parents[1] / "frontend"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app

