import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from prodog_gmail import dynamic_readiness, sandbox
from prodog_gmail.errors import ConnectorError
from prodog_gmail.pipeline import MailPipeline


@pytest.fixture
def checks(monkeypatch):
    outcomes = {
        "sandbox": {
            "ready": True,
            "message": "Linux 실험 이미지 준비됨",
            "context": "test-vm",
            "image_id": "sha256:" + "a" * 64,
        },
        "model": "test-revision",
        "cleanup": None,
    }

    def result(name):
        value = outcomes[name]
        if isinstance(value, Exception):
            raise value
        return value

    async def docker_status(context):
        assert context == "test-vm"
        return result("sandbox")

    async def validate_model(model):
        assert model == "test-model"
        return result("model")

    monkeypatch.setattr(sandbox, "docker_status", docker_status)
    runner = SimpleNamespace(
        settings=SimpleNamespace(sandbox_docker_context="test-vm"),
        _check_cleanup=lambda: result("cleanup"),
    )
    return dynamic_readiness, runner, validate_model, outcomes


def check(checks):
    helper, runner, validate_model, _ = checks
    return asyncio.run(helper.check_readiness(runner, "test-model", validate_model))


def test_ready_preserves_revision_image_and_returns_same_status(checks):
    status = check(checks)
    assert set(status) == {"ready", "sandbox", "model", "cleanup", "message"}
    assert status["ready"] is True
    assert status["sandbox"] == checks[3]["sandbox"]
    assert status["model"]["revision"] == "test-revision"
    assert status["cleanup"]["ready"] is True
    assert checks[0].require_ready(status) is status
    json.dumps(status)


@pytest.mark.parametrize(
    "component,value,code,http_status",
    [
        (
            "sandbox",
            {"ready": False, "message": "VM과 Docker 엔진을 확인하세요.", "context": "test-vm"},
            "sandbox_unavailable",
            503,
        ),
        (
            "model",
            ConnectorError("ollama_unavailable", "Ollama를 실행하세요.", 503),
            "ollama_unavailable",
            503,
        ),
        ("model", ConnectorError("model_required", "모델을 선택하세요."), "model_required", 400),
        (
            "model",
            ConnectorError("tools_unsupported", "도구 지원 모델을 선택하세요."),
            "tools_unsupported",
            400,
        ),
        (
            "model",
            ConnectorError("thinking_only_unsupported", "추론을 끌 수 있는 모델을 선택하세요."),
            "thinking_only_unsupported",
            400,
        ),
        (
            "cleanup",
            ConnectorError("cleanup_blocked", "이전 실험 환경 제거를 확인하세요.", 503),
            "cleanup_blocked",
            503,
        ),
    ],
)
def test_expected_failure_preserves_actionable_reason(checks, component, value, code, http_status):
    checks[3][component] = value
    status = check(checks)
    assert status["ready"] is False
    assert status[component]["ready"] is False
    message = value.message if isinstance(value, ConnectorError) else value["message"]
    assert status["message"] == status[component]["message"] == message
    assert all(status[name]["ready"] for name in {"sandbox", "model", "cleanup"} - {component})
    with pytest.raises(ConnectorError) as caught:
        checks[0].require_ready(status)
    assert (caught.value.code, caught.value.status, caught.value.message) == (
        code,
        http_status,
        message,
    )


def test_all_checks_report_failures_in_sandbox_model_cleanup_order(checks):
    checks[3].update(
        sandbox={"ready": False, "message": "VM을 확인하세요.", "context": "test-vm"},
        model=ConnectorError("tools_unsupported", "다른 모델을 선택하세요."),
        cleanup=ConnectorError("cleanup_blocked", "이전 환경을 확인하세요.", 503),
    )
    status = check(checks)
    assert all(status[name]["ready"] is False for name in ("sandbox", "model", "cleanup"))
    with pytest.raises(ConnectorError) as caught:
        checks[0].require_ready(status)
    assert caught.value.code == "sandbox_unavailable"
    status["sandbox"]["ready"] = True
    with pytest.raises(ConnectorError) as caught:
        checks[0].require_ready(status)
    assert caught.value.code == "tools_unsupported"


@pytest.mark.parametrize("component", ["sandbox", "model", "cleanup"])
def test_internal_errors_are_safe_and_do_not_hide_other_results(checks, component):
    checks[3][component] = RuntimeError("PRIVATE_TOKEN=secret")
    status = check(checks)
    assert status["ready"] is False and status[component]["ready"] is False
    assert "PRIVATE" not in json.dumps(status) and "secret" not in json.dumps(status)
    assert any("가" <= char <= "힣" for char in status[component]["message"])
    assert all(status[name]["ready"] for name in {"sandbox", "model", "cleanup"} - {component})
    with pytest.raises(ConnectorError) as caught:
        checks[0].require_ready(status)
    assert caught.value.status == 503
    assert caught.value.message == status[component]["message"]


@pytest.mark.parametrize(
    "component,value",
    [
        ("sandbox", None),
        ("sandbox", {"ready": "yes", "message": "PRIVATE_TOKEN"}),
        ("sandbox", {"ready": True}),
        ("sandbox", {"ready": True, "message": "ok", "image_id": {"secret": "PRIVATE"}}),
        ("model", None),
        ("model", ""),
        ("model", {"secret": "PRIVATE"}),
    ],
)
def test_malformed_dependency_results_fail_closed(checks, component, value):
    checks[3][component] = value
    status = check(checks)
    assert status["ready"] is False and status[component]["ready"] is False
    assert "PRIVATE" not in json.dumps(status)
    with pytest.raises(ConnectorError) as caught:
        checks[0].require_ready(status)
    assert caught.value.status == 503


@pytest.mark.parametrize("component", ["sandbox", "model"])
def test_stalled_checks_are_bounded_and_cancelled(checks, monkeypatch, component):
    original_wait_for = asyncio.wait_for
    cancelled = []

    async def short_wait_for(awaitable, timeout):
        assert 0 < timeout <= 15
        return await original_wait_for(awaitable, min(timeout, 0.01))

    async def stalled(_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(component)

    monkeypatch.setattr(dynamic_readiness.asyncio, "wait_for", short_wait_for)
    if component == "sandbox":
        monkeypatch.setattr(sandbox, "docker_status", stalled)
        status = check(checks)
    else:
        status = asyncio.run(dynamic_readiness.check_readiness(checks[1], "test-model", stalled))
    assert status["ready"] is False and status[component]["ready"] is False
    assert cancelled == [component]


def test_default_context_and_extra_dependency_fields_are_safe(checks, monkeypatch):
    checks[1].settings.sandbox_docker_context = ""

    async def docker_status(context):
        assert context == ""
        return {"ready": True, "message": "준비됨", "private": "PRIVATE_TOKEN"}

    monkeypatch.setattr(sandbox, "docker_status", docker_status)
    status = check(checks)
    assert status["ready"] is True
    assert status["sandbox"] == {"ready": True, "message": "준비됨", "context": "default"}
    assert "PRIVATE" not in json.dumps(status)


def test_real_validators_only_inspect_existing_resources(monkeypatch):
    from prodog_gmail.dynamic_readiness import check_readiness

    async def docker(*args, **kwargs):
        assert kwargs["context"] == "test-vm" and kwargs["timeout"] == 5
        if args == ("info", "--format", "{{.OSType}}"):
            return {"exit_code": 0, "stdout": "linux\n"}
        assert args == ("image", "inspect", sandbox.IMAGE, "--format", "{{.Id}}")
        return {"exit_code": 0, "stdout": "sha256:" + "b" * 64 + "\n"}

    def ollama(request):
        assert request.method == "POST" and request.url.path == "/api/show"
        assert json.loads(request.content) == {"model": "installed-model"}
        return httpx.Response(
            200, json={"capabilities": ["tools"], "thinking": {"values": [False]}}
        )

    monkeypatch.setattr(sandbox, "docker", docker)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(ollama)) as client:
            runner = SimpleNamespace(
                client=client,
                settings=SimpleNamespace(sandbox_docker_context="test-vm"),
                _check_cleanup=lambda: None,
            )

            async def validate(model):
                return await MailPipeline.validate_model(SimpleNamespace(runner=runner), model)

            return await check_readiness(runner, "installed-model", validate)

    status = asyncio.run(run())
    assert status["ready"] is True
    assert status["sandbox"]["image_id"] == "sha256:" + "b" * 64
    assert len(status["model"]["revision"]) == 64
