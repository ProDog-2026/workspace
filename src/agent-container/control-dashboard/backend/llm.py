"""Ollama's OpenAI-compatible transport. Never logs credentials or provider bodies."""
import copy
import json
import re
import uuid
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["ollama"] = "ollama"
    base_url: str
    api_key: SecretStr | None = Field(default=None, repr=False)
    model: str = Field(min_length=1)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("HTTP(S) address required")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Credentials, query and fragment are not allowed")
        return value.rstrip("/")

    def public(self):
        return {"provider": self.provider, "base_url": self.base_url,
                "model": self.model, "api_key_configured": bool(self.api_key)}


class LLMError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ChatResult(BaseModel):
    text: str
    tool_calls: list[ToolCall]
    finish_reason: str | None
    status: str
    usage: dict | None = None


class LLMAdapter:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def chat(self, messages, tools, config, max_tokens=2048, temperature=0.1, context_tokens=0):
        # A new provider can reuse this transport after explicit provider support is added.
        base = config.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        headers = {}
        if config.api_key:
            headers["Authorization"] = "Bearer " + config.api_key.get_secret_value()
        payload = {"model": config.model, "messages": messages,
                   "max_tokens": max_tokens, "stream": False, "temperature": temperature}
        if tools:
            payload["tools"] = tools
        try:
            for attempt in range(2):
                endpoint, body = base + "/chat/completions", payload
                if context_tokens:
                    native_messages = copy.deepcopy(messages)
                    names = {}
                    for message in native_messages:
                        message["content"] = message.get("content") or ""
                        for call in message.get("tool_calls") or []:
                            fn = call["function"]
                            names[call["id"]] = fn["name"]
                            if isinstance(fn["arguments"], str):
                                fn["arguments"] = json.loads(fn["arguments"])
                        if message.get("role") == "tool":
                            message["tool_name"] = names.get(message.get("tool_call_id"), "")
                    endpoint = base[:-3] + "/api/chat"
                    body = {"model": config.model, "messages": native_messages, "tools": tools,
                            "stream": False, "truncate": False, "shift": False,
                            "options": {"num_ctx": context_tokens, "num_predict": max_tokens,
                                        "temperature": temperature}}
                response = await self.client.post(endpoint, json=body, headers=headers)
                if not response.is_error:
                    break
                # Read only the numeric overflow report; never persist provider text.
                error = response.json().get("error") if response.status_code in (400, 413) else None
                error = error.get("message") if isinstance(error, dict) else error
                overflow = re.search(r"request \((\d{1,9}) tokens\) exceeds the available context size \((\d{1,9}) tokens\)",
                                     error) if isinstance(error, str) else None
                if overflow:
                    needed = int(overflow[1]) + max_tokens + 256
                    expanded = max(8192, ((needed + 1023) // 1024) * 1024)
                    # shortcut: shared laptops get at most 16K; larger mail needs a dedicated long-context workflow.
                    if attempt or expanded > 16384 or expanded <= context_tokens:
                        raise LLMError("context_limit")
                    context_tokens = expanded
                    continue
                code = {401: "authentication_failed", 403: "authentication_failed",
                        404: "model_or_endpoint_not_found"}.get(response.status_code,
                                                                "provider_rejected_request")
                raise LLMError(code)
        except httpx.TimeoutException:
            raise LLMError("long_input_timeout" if context_tokens else "provider_timeout") from None
        except httpx.RequestError:
            raise LLMError("connection_failed") from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise LLMError("invalid_provider_response") from None
        try:
            raw = response.json()
            if context_tokens:
                if raw.get("done") is not True or raw["message"].get("role") != "assistant":
                    raise ValueError()
                message, reason = raw["message"], raw.get("done_reason")
                if reason != "length":
                    for call in message.get("tool_calls") or []:
                        call.update(id=call.get("id") or uuid.uuid4().hex, type="function")
                    if message.get("tool_calls") and reason == "stop":
                        reason = "tool_calls"
                raw = {"choices": [{"message": message, "finish_reason": reason}],
                       "usage": {"prompt_tokens": raw.get("prompt_eval_count"),
                                 "completion_tokens": raw.get("eval_count"),
                                 "context_tokens": context_tokens}}
            choice = raw["choices"][0]
            message = choice["message"]
            reason = choice.get("finish_reason")
            text = message.get("content") or ""
            if not isinstance(text, str) or (reason is not None and not isinstance(reason, str)):
                raise ValueError()
            # Truncated tool arguments must never be parsed into executable calls.
            calls = []
            if reason != "length":
                for item in message.get("tool_calls") or []:
                    if item.get("type") != "function":
                        raise ValueError()
                    fn = item["function"]
                    args = fn["arguments"]
                    args = json.loads(args) if isinstance(args, str) else args
                    if not isinstance(args, dict):
                        raise ValueError()
                    calls.append(ToolCall(id=item["id"], name=fn["name"], arguments=args))
                if reason == "tool_calls" and not calls:
                    raise ValueError()
            return ChatResult(text=text, tool_calls=calls, finish_reason=reason,
                              status="limit_reached" if reason == "length" else "ok",
                              usage=raw.get("usage"))
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise LLMError("invalid_provider_response") from None

    async def list_models(self, base_url):
        """Models installed in the local Ollama (GET /api/tags)."""
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            response = await self.client.get(base + "/api/tags", timeout=10)
        except httpx.TimeoutException:
            raise LLMError("provider_timeout") from None
        except httpx.RequestError:
            raise LLMError("connection_failed") from None
        if response.is_error:
            raise LLMError("model_or_endpoint_not_found")
        try:
            names = [m["name"] for m in response.json()["models"]]
        except (ValueError, KeyError, TypeError):
            raise LLMError("invalid_provider_response") from None
        return sorted(n for n in names if isinstance(n, str))

    async def check_connection(self, config, max_tokens=2048):
        tools = [{"type": "function", "function": {
            "name": "connection_probe", "description": "Return a connection check marker.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}]
        messages = [{"role": "user", "content":
                     "Call connection_probe exactly once, with no arguments. "
                     "After receiving its result, reply briefly without calling tools again."}]
        first = await self.chat(messages, tools, config, max_tokens)
        if first.status == "limit_reached":
            return {"status": "limit_reached", "stage": "tool_request"}
        if len(first.tool_calls) != 1 or first.tool_calls[0].name != "connection_probe":
            return {"status": "tool_call_not_verified", "stage": "tool_request"}
        call = first.tool_calls[0]
        if call.arguments:
            return {"status": "tool_call_not_verified", "stage": "tool_arguments"}
        messages.append({"role": "assistant", "content": first.text or None,
                         "tool_calls": [{"id": call.id, "type": "function", "function":
                                        {"name": call.name, "arguments": json.dumps(call.arguments)}}]})
        messages.append({"role": "tool", "tool_call_id": call.id,
                         "content": '{"ok":true,"marker":"connection-ok"}'})
        final = await self.chat(messages, tools, config, max_tokens)
        if final.status == "limit_reached":
            return {"status": "limit_reached", "stage": "final_answer"}
        ready = bool(final.text.strip()) and not final.tool_calls and final.finish_reason == "stop"
        return {"status": "ready" if ready else "tool_roundtrip_not_verified",
                "stage": "final_answer"}

