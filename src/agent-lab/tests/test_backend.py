import asyncio
import json

import httpx
import pytest

from backend.api import create_dashboard_app
from backend.llm import ConnectionConfig, LLMAdapter, LLMError
from backend.runs import RunManager
from backend.worker_api import create_worker_app

TOOLS = [{"type": "function", "function": {"name": "read_email",
          "parameters": {"type": "object", "properties": {}}}}]
MESSAGES = [{"role": "user", "content": "Read the test email."}]
CONFIG = {"provider": "ollama", "base_url": "http://ollama.test:11434", "model": "test"}


def response(reason="tool_calls", arguments='{}'):
    return httpx.Response(200, json={"choices": [{
        "finish_reason": reason, "message": {
            "content": "test", "tool_calls": [{"id": "call-1", "type": "function",
            "function": {"name": "read_email", "arguments": arguments}}]
            if reason in ("tool_calls", "length") else []}}]})


def scenario(test):
    asyncio.run(test())


def test_normal_and_limit():
    async def test():
        seen = []
        def upstream(request):
            seen.append(json.loads(request.content))
            assert str(request.url) == "http://ollama.test:11434/v1/chat/completions"
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
            manager = RunManager(LLMAdapter(provider))
            run = manager.create(ConnectionConfig(**CONFIG), TOOLS)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(
                    app=create_worker_app(manager)), base_url="http://worker") as worker:
                body = {"run_id": run["run_id"], "messages": MESSAGES, "tools": TOOLS}
                headers = {"Authorization": "Bearer " + run["execution_token"]}
                for _ in range(8):
                    res = await worker.post("/llm", json=body, headers=headers)
                    assert res.status_code == 200
                    assert res.json()["tool_calls"] == [
                        {"id": "call-1", "name": "read_email", "arguments": {}}]
                ninth = await worker.post("/llm", json=body, headers=headers)
                assert ninth.status_code == 429
                assert ninth.json()["error"] == "limit_reached"
                assert manager.get(run["run_id"]).status == "limit_reached"
                assert len(seen) == 8
                assert all(p["max_tokens"] == 2048 for p in seen)
    scenario(test)


def test_auth_stop_and_tools_never_call_provider():
    async def test():
        seen = []
        def upstream(request):
            seen.append(request)
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
            manager = RunManager(LLMAdapter(provider))
            run = manager.create(ConnectionConfig(**CONFIG), TOOLS)
            app = create_worker_app(manager)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://worker") as worker:
                body = {"run_id": run["run_id"], "messages": MESSAGES, "tools": TOOLS}
                headers = {"Authorization": "Bearer " + run["execution_token"]}
                assert (await worker.post("/llm", json=body)).status_code == 401
                assert (await worker.post("/llm", json=body, headers={
                    "Authorization": "Bearer wrong"})).status_code == 401
                changed = body | {"tools": []}
                assert (await worker.post("/llm", json=changed, headers=headers)).status_code == 422
                manager.stop(run["run_id"])
                assert (await worker.post("/llm", json=body, headers=headers)).status_code == 401
                assert not seen
                assert [(r.path, r.methods) for r in app.routes] == [("/llm", {"POST"})]
                assert (await worker.post("/runs", json={})).status_code == 404
                assert (await worker.get("/docs")).status_code == 404
    scenario(test)


def test_length_skips_even_incomplete_tool_arguments():
    async def test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: response("length", '{"unfinished":'))) as provider:
            manager = RunManager(LLMAdapter(provider))
            run = manager.create(ConnectionConfig(**CONFIG), TOOLS)
            result = await manager.infer(run["run_id"], run["execution_token"], MESSAGES, TOOLS)
            assert result["status"] == "limit_reached"
            assert result["run_status"] == "limit_reached"
            assert result["tool_calls"] == []
    scenario(test)


def test_timeout_before_call_and_completed_run():
    async def test():
        seen = []
        def upstream(request):
            seen.append(request)
            return response("stop")
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
            manager = RunManager(LLMAdapter(provider))
            run = manager.create(ConnectionConfig(**CONFIG), [])
            manager.get(run["run_id"]).started -= 601
            from backend.runs import RunError
            with pytest.raises(RunError, match="limit_reached"):
                await manager.infer(run["run_id"], run["execution_token"], MESSAGES, [])
            assert not seen
            completed = manager.create(ConnectionConfig(**CONFIG), [])
            await manager.infer(completed["run_id"], completed["execution_token"], MESSAGES, [])
            with pytest.raises(RunError, match="run_not_running"):
                await manager.infer(completed["run_id"], completed["execution_token"], MESSAGES, [])
            assert len(seen) == 1
    scenario(test)


def test_stop_cancels_pending_and_concurrent_request_is_rejected():
    async def test():
        started = asyncio.Event()
        cancelled = asyncio.Event()
        async def upstream(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
            manager = RunManager(LLMAdapter(provider))
            run = manager.create(ConnectionConfig(**CONFIG), TOOLS)
            from backend.runs import RunError
            pending = asyncio.create_task(manager.infer(
                run["run_id"], run["execution_token"], MESSAGES, TOOLS))
            await started.wait()
            with pytest.raises(RunError, match="request_in_progress"):
                await manager.infer(run["run_id"], run["execution_token"], MESSAGES, TOOLS)
            manager.stop(run["run_id"])
            with pytest.raises(RunError, match="run_stopped"):
                await pending
            assert cancelled.is_set()
            assert manager.get(run["run_id"]).calls == 1
            assert manager.get(run["run_id"]).status == "stopped"
    scenario(test)


def test_dashboard_settings_secrets_and_shared_run():
    async def test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response())) as provider:
            manager = RunManager(LLMAdapter(provider))
            dashboard = create_dashboard_app(manager)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=dashboard),
                                         base_url="http://dashboard") as api:
                saved = await api.post("/settings", json=CONFIG | {"api_key": "private-key"})
                assert saved.status_code == 201
                assert "private-key" not in saved.text
                run = (await api.post("/runs", json={
                    "settings_id": saved.json()["settings_id"], "tools": TOOLS})).json()
                status = await api.get("/runs/" + run["run_id"])
                assert "execution_token" not in run
                assert manager.get(run["run_id"]).token not in status.text
                assert "private-key" not in status.text
                invalid = await api.post("/settings", json=CONFIG | {
                    "api_key": "private-key", "extra": "private-key"})
                assert invalid.status_code == 422 and "private-key" not in invalid.text
                await api.post("/runs/" + run["run_id"] + "/stop")
                assert manager.get(run["run_id"]).status == "stopped"
    scenario(test)


def test_connection_check_performs_real_tool_roundtrip():
    async def test():
        requests = []
        def upstream(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                out = response().json()
                out["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "connection_probe"
                return httpx.Response(200, json=out)
            messages = body["messages"]
            assert messages[-1]["role"] == "tool"
            assert messages[-1]["tool_call_id"] == "call-1"
            assert messages[-2]["tool_calls"][0]["id"] == "call-1"
            assert json.loads(messages[-1]["content"])["ok"]
            return response("stop")
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
            result = await LLMAdapter(provider).check_connection(ConnectionConfig(**CONFIG))
            assert result["status"] == "ready"
            assert len(requests) == 2
    scenario(test)


def test_upstream_error_body_is_not_exposed():
    async def test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(401, text="private-key"))) as provider:
            with pytest.raises(LLMError) as caught:
                await LLMAdapter(provider).chat(MESSAGES, [], ConnectionConfig(**CONFIG))
            assert str(caught.value) == "authentication_failed"
    scenario(test)


def test_inflight_deadline_and_worker_cannot_override_connection():
    async def test():
        async def upstream(request):
            await asyncio.sleep(1)
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
            from backend.runs import Limits, RunError
            manager = RunManager(LLMAdapter(provider))
            run = manager.create(ConnectionConfig(**CONFIG), TOOLS,
                                 Limits(total_seconds=0.02, max_tokens=1024))
            with pytest.raises(RunError, match="limit_reached"):
                await manager.infer(run["run_id"], run["execution_token"], MESSAGES, TOOLS)
            assert manager.get(run["run_id"]).limit_kind == "total_time"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(
                    app=create_worker_app(manager)), base_url="http://worker") as worker:
                rejected = await worker.post("/llm", json={
                    "run_id": run["run_id"], "messages": MESSAGES, "tools": TOOLS,
                    "model": "override", "api_key": "private-key"})
                assert rejected.status_code == 422
                assert "private-key" not in rejected.text
    scenario(test)




def test_prompts_models_and_temperature_options():
    async def test():
        seen = []
        def upstream(request):
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "b:1"}, {"name": "a:1"}]})
            seen.append(json.loads(request.content))
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as provider:
            manager = RunManager(LLMAdapter(provider))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(
                    app=create_dashboard_app(manager)), base_url="http://dash") as dash:
                prompts = (await dash.get("/prompts")).json()
                assert [p["id"] for p in prompts["prompts"]] == ["MAIL_SUMMARY_V2", "MAIL_SUMMARY_V3", "MAIL_ASSISTANT_V4"]
                assert prompts["prompts"][0]["builtin"] is True and "Policy" in prompts["prompts"][0]["text"]
                assert (await dash.put("/prompts/MAIL_SUMMARY_V2", json={"text": "x"})).status_code == 403
                assert (await dash.delete("/prompts/MAIL_SUMMARY_V3")).status_code == 403
                assert (await dash.post("/prompts", json={"id": "bad id", "text": "x"})).status_code == 422
                assert (await dash.post("/prompts", json={"id": "MY_1", "description": "d", "text": " "})).status_code == 422
                made = await dash.post("/prompts", json={"id": "MY_1", "description": "d", "text": "Say hi"})
                assert made.status_code == 201
                assert (await dash.post("/prompts", json={"id": "MY_1", "text": "x"})).status_code == 409
                assert (await dash.put("/prompts/MY_1", json={"description": "e", "text": "Say bye"})).json()["text"] == "Say bye"
                assert any(p["id"] == "MY_1" and not p["builtin"] for p in (await dash.get("/prompts")).json()["prompts"])
                assert (await dash.delete("/prompts/MY_1")).status_code == 200
                assert (await dash.delete("/prompts/MY_1")).status_code == 404
                models = await dash.get("/ollama/models", params={"base_url": "http://ollama.test:11434"})
                assert models.json() == {"models": ["a:1", "b:1"]}
                assert (await dash.get("/ollama/models", params={"base_url": "ftp://x"})).status_code == 422
                bad = await dash.post("/runs", json={"config": CONFIG, "prompt_id": "NOPE"})
                assert bad.status_code == 422 and bad.json()["error"] == "prompt_not_found"
                assert (await dash.post("/runs", json={"config": CONFIG, "temperature": 3})).status_code == 422
                run = (await dash.post("/runs", json={"config": CONFIG, "temperature": 0.7,
                                                      "prompt_id": "MAIL_SUMMARY_V3", "tools": TOOLS})).json()
                assert run["temperature"] == 0.7 and run["prompt_version"] == "MAIL_SUMMARY_V3"
                token = manager.get(run["run_id"]).token
                async with httpx.AsyncClient(transport=httpx.ASGITransport(
                        app=create_worker_app(manager)), base_url="http://worker") as worker:
                    await worker.post("/llm", json={"run_id": run["run_id"], "messages": MESSAGES, "tools": TOOLS},
                                      headers={"Authorization": "Bearer " + token})
                assert seen[0]["temperature"] == 0.7
    scenario(test)
