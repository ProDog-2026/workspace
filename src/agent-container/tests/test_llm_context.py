"""Long synthetic mail stays intact across the Ollama context retry."""
import asyncio
import copy
import json

import httpx
import pytest
from backend.llm import ConnectionConfig, LLMAdapter, LLMError
from backend.runs import RunManager

CONFIG = ConnectionConfig(base_url="http://ollama.test:11434/v1", model="test")
OVERFLOW = "request (12000 tokens) exceeds the available context size (4096 tokens), try increasing it"


def test_long_mail_retry_and_followup_keep_full_input_and_context():
    async def test():
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "read-1", "type": "function", "function": {"name": "read_email", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "read-1", "content": "SYNTHETIC_MAIL " * 20000 + "END_MARKER"},
        ]
        original, seen = copy.deepcopy(messages), []

        def upstream(request):
            payload = json.loads(request.content)
            seen.append((request.url.path, payload))
            if request.url.path == "/v1/chat/completions":
                return httpx.Response(400, json={"error": {"message": OVERFLOW}})
            assert payload["messages"][-1]["content"] == original[-1]["content"]
            assert payload["messages"][0]["tool_calls"][0]["function"]["arguments"] == {}
            assert payload["messages"][-1]["tool_name"] == "read_email"
            assert payload["truncate"] is False and payload["shift"] is False
            assert payload["options"] == {"num_ctx": 14336, "num_predict": 2048, "temperature": 0.1}
            return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {
                "role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "save_summary", "arguments": {"summary": "synthetic"}}}]},
                "prompt_eval_count": 12000, "eval_count": 10})

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            manager = RunManager(LLMAdapter(client))
            run = manager.create(CONFIG, [])
            first = await manager.infer(run["run_id"], run["execution_token"], messages, [])
            assert first["tool_calls"][0]["name"] == "save_summary"
            assert first["tool_calls"][0]["id"]
            assert first["usage"]["context_tokens"] == 14336
            assert first["finish_reason"] == "tool_calls"
            await manager.infer(run["run_id"], run["execution_token"], messages, [])
            assert [path for path, _ in seen] == ["/v1/chat/completions", "/api/chat", "/api/chat"]
            assert messages == original
    asyncio.run(test())


@pytest.mark.parametrize("error,expected,calls", [
    ("request (48452 tokens) exceeds the available context size (4096 tokens)", "context_limit", 1),
    ("request (90000 tokens) exceeds the available context size (4096 tokens)", "context_limit", 1),
    ("unsupported tools: SYNTHETIC_PRIVATE_TEXT", "provider_rejected_request", 1),
    (OVERFLOW, "context_limit", 2),
])
def test_retry_is_bounded_and_provider_bodies_are_not_recorded(error, expected, calls):
    async def test():
        seen = []
        def upstream(request):
            seen.append(request)
            return httpx.Response(400, json={"error": {"message": error}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            manager = RunManager(LLMAdapter(client))
            run = manager.create(CONFIG, [])
            with pytest.raises(LLMError) as caught:
                await manager.infer(run["run_id"], run["execution_token"], [{"role": "user", "content": "test"}], [])
            assert caught.value.code == expected and len(seen) == calls
            response = manager.get(run["run_id"]).events[-1]
            assert response["event_type"] == "llm_response" and response["response"]["error"] == expected
            assert "SYNTHETIC_PRIVATE_TEXT" not in json.dumps(response)
    asyncio.run(test())


def test_native_length_response_never_executes_partial_tool_arguments():
    async def test():
        def upstream(request):
            return httpx.Response(200, json={"done": True, "done_reason": "length", "message": {
                "role": "assistant", "content": "", "tool_calls": [{"function": {
                    "name": "save_summary", "arguments": "{unfinished"}}]}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            result = await LLMAdapter(client).chat([], [], CONFIG, context_tokens=16384)
            assert result.status == "limit_reached" and result.tool_calls == []
    asyncio.run(test())


def test_long_input_timeout_is_distinct_from_connection_timeout():
    async def test():
        def upstream(request):
            raise httpx.ReadTimeout("SYNTHETIC_PROVIDER_TEXT", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            manager = RunManager(LLMAdapter(client))
            run = manager.create(CONFIG, [])
            manager.get(run["run_id"]).context_tokens = 8192
            with pytest.raises(LLMError, match="long_input_timeout"):
                await manager.infer(run["run_id"], run["execution_token"], [], [])
            assert manager.get(run["run_id"]).status == "timeout"
    asyncio.run(test())
