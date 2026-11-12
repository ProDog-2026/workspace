"""User-defined tools only: providers never execute the shell themselves."""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, DecimalException

import httpx

from .sandbox import ExperimentError

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run Linux shell commands. Bash and Python are installed.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_output",
            "description": "Read another page of a prior run_shell output. "
            "Use only when preview_truncated is true or eof is false. "
            "Offsets count Unicode characters, not bytes. Do not read beyond total_chars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output_id": {"type": "string"},
                    "stream": {"type": "string", "enum": ["stdout", "stderr"]},
                    "offset": {"type": "integer", "minimum": 0},
                },
                "required": ["output_id", "stream", "offset"],
                "additionalProperties": False,
            },
        },
    },
]
OLLAMA_INPUT_BYTES = 8192 - 1024 - 512


def ollama_prompt_bytes(messages):
    # Conservative byte upper bound; not a model-specific token count.
    return len(json.dumps([messages, TOOLS], ensure_ascii=False).encode())


def ollama_context_fits(messages):
    return ollama_prompt_bytes(messages) < OLLAMA_INPUT_BYTES


async def openrouter_models(client):
    """Public metadata only; never send API keys, email content, or inference requests."""
    try:
        async with client.stream(
            "GET", "https://openrouter.ai/api/v1/models", timeout=15
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError("catalogue too large")
        rows = json.loads(body)["data"]
        if not isinstance(rows, list):
            raise ValueError("invalid catalogue")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise ExperimentError(
            "catalogue_unavailable", "OpenRouter 모델 목록을 가져오지 못했습니다. 다시 조회하세요."
        ) from exc

    def price(value):
        try:
            number = Decimal(str(value))
            return number if number.is_finite() and 0 <= number <= 1000000 else None
        except (DecimalException, ValueError):
            return None

    models = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        parameters = row.get("supported_parameters")
        if not isinstance(parameters, list) or "tools" not in parameters:
            continue
        model_id = row.get("id", "")
        if not isinstance(model_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,179}", model_id
        ):
            continue
        pricing = row.get("pricing")
        pricing = pricing if isinstance(pricing, dict) else {}
        prompt, completion = price(pricing.get("prompt")), price(pricing.get("completion"))
        free = prompt == completion == price(pricing.get("request", "0")) == Decimal(0)
        name = row.get("name")
        models[model_id] = {
            "id": model_id,
            "name": name[:300] if isinstance(name, str) and name else model_id,
            "input_price_per_million": str(prompt * 1000000) if prompt is not None else None,
            "output_price_per_million": str(completion * 1000000)
            if completion is not None
            else None,
            "is_free": free,
            "selectable": prompt is not None and completion is not None,
        }
    return {
        "models": sorted(models.values(), key=lambda row: (row["name"].casefold(), row["id"])),
        "fetched_at": datetime.now(UTC).isoformat(),
        "currency": "USD",
        "unit_tokens": 1000000,
    }


async def model_reply(settings, client, provider, model, messages):
    payload = {"model": model, "messages": messages, "tools": TOOLS, "stream": False}
    headers = {}
    if provider == "ollama":
        url = "http://127.0.0.1:11434/api/chat"
        # UTF-8 bytes plus template reserve is conservative; never silently trim history.
        if not ollama_context_fits(messages):
            raise ExperimentError(
                "context_limit", "현재 작업·도구 요청 한 묶음이 모델 입력 한도를 초과했습니다."
            )
        payload.update(
            # Reserve the small output budget for actions rather than a long reasoning trace.
            think=False,
            options={"num_ctx": 8192, "num_predict": 1024, "temperature": 0},
            keep_alive=0,
        )
    else:
        is_groq = provider == "groq"
        url = (
            "https://api.groq.com/openai/v1/chat/completions"
            if is_groq
            else "https://openrouter.ai/api/v1/chat/completions"
        )
        key = (settings.groq_api_key if is_groq else settings.openrouter_api_key).get_secret_value()
        if not key:
            raise ExperimentError(
                "api_key_missing", "백엔드 .env에 선택한 제공자의 API 키를 설정하세요."
            )
        headers["Authorization"] = "Bearer " + key
        payload.update(max_tokens=1024, temperature=0, tool_choice="auto")
    try:
        async with client.stream(
            "POST", url, json=payload, headers=headers, timeout=120
        ) as response:
            if response.status_code >= 400:
                code = {401: "api_auth", 403: "api_auth", 429: "rate_limit"}.get(
                    response.status_code, "model_request_failed"
                )
                raise ExperimentError(
                    code,
                    f"모델 요청 실패 (HTTP {response.status_code}). "
                    "모델명·도구 지원·할당량을 확인하세요.",
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 262144:
                    raise ExperimentError(
                        "model_output_limit", "모델 응답 크기가 제한을 초과했습니다."
                    )
        raw = json.loads(body)
        if provider == "ollama":
            message = raw["message"]
            limited = raw.get("done_reason") == "length"
            usage = {k: raw.get(k) for k in ("prompt_eval_count", "eval_count")}
            if isinstance(usage["prompt_eval_count"], int) and usage["prompt_eval_count"] >= 7168:
                raise ExperimentError("context_limit", "모델 입력이 컨텍스트 한도에 도달했습니다.")
        else:
            choice = raw["choices"][0]
            message, limited = choice["message"], choice.get("finish_reason") == "length"
            usage = raw.get("usage")
        if limited:
            raise ExperimentError(
                "model_output_limit",
                "모델 출력 토큰 한도에 도달했습니다. 완료나 안전으로 판정하지 않습니다.",
            )
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("invalid assistant message")
        if not isinstance(message.get("content", "") or "", str):
            raise ValueError("invalid content")
        return {
            "message": message,
            "model": raw.get("model", model),
            "usage": usage,
            "provider": raw.get("provider", provider),
        }
    except httpx.TimeoutException as exc:
        raise ExperimentError("model_timeout", "모델 응답 시간이 초과되었습니다.") from exc
    except httpx.HTTPError as exc:
        raise ExperimentError(
            "model_unavailable", "선택한 모델에 연결할 수 없습니다. 실행 상태를 확인하세요."
        ) from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ExperimentError(
            "invalid_model_response", "모델 응답 형식이 올바르지 않습니다."
        ) from exc
