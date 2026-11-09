"""The worker listener exposes only POST /llm."""
from typing import Any

from fastapi import FastAPI, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .llm import LLMError
from .runs import RunManager, RunError


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    tools: list[dict[str, Any]] = Field(default_factory=list)


def install_errors(app):
    from .prompts import PromptError
    @app.exception_handler(PromptError)
    async def prompt_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status_code)

    @app.exception_handler(RunError)
    async def run_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status_code)

    @app.exception_handler(LLMError)
    async def llm_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default includes input values, which could contain secrets.
        return JSONResponse({"error": "invalid_request",
                             "fields": [list(e["loc"]) for e in exc.errors()]}, status_code=422)


def create_worker_app(manager: RunManager):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_errors(app)

    @app.post("/llm")
    async def llm(body: WorkerRequest, authorization: str | None = Header(default=None)):
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        return await manager.infer(body.run_id, token, body.messages, body.tools)

    return app

