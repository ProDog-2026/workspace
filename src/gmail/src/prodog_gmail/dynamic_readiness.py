"""Read-only dynamic-analysis admission checks; never start or repair resources."""

import asyncio

from . import sandbox
from .errors import ConnectorError


async def check_readiness(runner, model, validate_model):
    """Return all checks, with the first failure ordered sandbox, model, cleanup."""
    context = runner.settings.sandbox_docker_context

    async def check_sandbox():
        failed = {
            "ready": False,
            "message": "VM·Docker 연결과 실험 이미지 준비 상태를 확인하세요.",
            "context": context or "default",
        }
        try:
            result = await asyncio.wait_for(sandbox.docker_status(context), timeout=15)
            if (
                not isinstance(result, dict)
                or type(result.get("ready")) is not bool
                or not isinstance(result.get("message"), str)
                or not result["message"].strip()
            ):
                return failed
            if "image_id" in result and (
                not isinstance(result["image_id"], str) or not result["image_id"].strip()
            ):
                return failed
            return {
                **failed,
                "ready": result["ready"],
                "message": result["message"],
                **({"image_id": result["image_id"]} if "image_id" in result else {}),
            }
        except Exception:
            return failed

    async def check_model():
        try:
            revision = await asyncio.wait_for(validate_model(model), timeout=6)
            if not isinstance(revision, str) or not revision.strip():
                raise ValueError
            return {"ready": True, "message": "모델 준비됨", "revision": revision}
        except ConnectorError as exc:
            return {"ready": False, "message": exc.message, "code": exc.code}
        except Exception:
            return {
                "ready": False,
                "message": "Ollama 실행 상태와 선택한 모델을 확인하세요.",
                "code": "model_check_failed",
            }

    cleanup = {"ready": True, "message": "이전 실험 환경 정리 확인됨"}
    try:
        runner._check_cleanup()
    except ConnectorError as exc:
        cleanup = {"ready": False, "message": exc.message, "code": exc.code}
    except Exception:
        cleanup = {
            "ready": False,
            "message": "이전 실험 환경의 정리 상태를 확인하세요.",
            "code": "cleanup_check_failed",
        }
    sandbox_status, model_status = await asyncio.gather(check_sandbox(), check_model())
    statuses = (sandbox_status, model_status, cleanup)
    return {
        "ready": all(item["ready"] for item in statuses),
        "sandbox": sandbox_status,
        "model": model_status,
        "cleanup": cleanup,
        "message": next(
            (item["message"] for item in statuses if not item["ready"]),
            "동적 분석 준비가 완료되었습니다.",
        ),
    }


def require_ready(status):
    """Return status or raise the first failure (invalid model: 400; unavailable: 503)."""
    for name in ("sandbox", "model", "cleanup"):
        item = status[name]
        if not item["ready"]:
            code = item.get("code", "sandbox_unavailable")
            invalid_model = name == "model" and code in {
                "model_required",
                "tools_unsupported",
                "thinking_only_unsupported",
            }
            raise ConnectorError(code, item["message"], 400 if invalid_model else 503)
    return status
