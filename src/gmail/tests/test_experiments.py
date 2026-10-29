import asyncio
import json

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from prodog_gmail.app import create_app
from prodog_gmail.config import Settings
from prodog_gmail.experiment_model import model_reply, openrouter_models
from prodog_gmail.experiments import ExperimentInput, Experiments, observations, run_agent
from prodog_gmail.sandbox import ExperimentError, Sandbox

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "test.db",
    )


def headers(client):
    token = client.get("/api/session").json()["csrf_token"]
    return {"Origin": BASE, "X-CSRF-Token": token}


def test_experiments_require_session_csrf_and_do_not_need_gmail(settings, monkeypatch):
    async def fake_execute(self, record):
        await asyncio.Event().wait()

    monkeypatch.setattr("prodog_gmail.experiments.Experiments.execute", fake_execute)
    with TestClient(create_app(settings), base_url=BASE) as client:
        assert client.get("/api/experiments").status_code == 401
        assert client.post("/api/experiments", json={}).status_code == 403
        payload = {"provider": "ollama", "model": "test", "mail": "회의는 3시", "task": "요약해줘"}
        response = client.post("/api/experiments", headers=headers(client), json=payload)
        assert response.status_code == 202
        run_id = response.json()["id"]
        assert client.get("/api/experiments/" + run_id).status_code == 200
        assert (
            client.post("/api/experiments", headers=headers(client), json=payload).status_code
            == 409
        )
        client.cookies.clear()
        client.get("/api/session")
        assert client.get("/api/experiments/" + run_id).status_code == 404


def test_remote_opt_in_and_api_key_required_for_paid_openrouter(settings):
    with TestClient(create_app(settings), base_url=BASE) as client:
        payload = {
            "provider": "openrouter",
            "model": "qwen/test:free",
            "mail": "test",
            "task": "요약",
        }
        assert (
            client.post("/api/experiments", headers=headers(client), json=payload).status_code
            == 400
        )
        payload.update(allow_remote=True, model="paid-model")
        assert (
            client.post("/api/experiments", headers=headers(client), json=payload).status_code
            == 503
        )


def test_model_tool_request_roundtrip_and_length_failure(settings):
    async def check():
        def reply(request):
            body = json.loads(request.content)
            assert body["tools"][0]["function"]["name"] == "run_shell"
            assert "format" not in body
            return httpx.Response(
                200,
                json={
                    "model": "test",
                    "done_reason": "length",
                    "message": {"role": "assistant", "content": "unfinished"},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            with pytest.raises(Exception, match="output_limit"):
                await model_reply(settings, client, "ollama", "test", [])

    asyncio.run(check())


def test_local_tool_loop_disables_thinking_to_preserve_action_token_budget(settings):
    async def check():
        def reply(request):
            payload = json.loads(request.content)
            assert payload["think"] is False
            assert payload["options"]["num_predict"] == 1024
            assert payload["options"]["num_ctx"] == 8192
            return httpx.Response(
                200,
                json={
                    "model": "qwen3:4b",
                    "done_reason": "stop",
                    "message": {"role": "assistant", "content": "done"},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            result = await model_reply(settings, client, "ollama", "qwen3:4b", [])
            assert result["message"]["content"] == "done"

    asyncio.run(check())


def test_queued_auto_run_rejects_replaced_ollama_weights(settings, monkeypatch):
    async def check():
        class EmptySandbox:
            def __init__(self, run_id):
                pass

            async def start(self, mail):
                return "test-image"

            async def snapshot(self):
                return {"files": [], "complete": True}

            async def remove(self):
                pass

        def response(request):
            assert request.url.path == "/api/show", "replacement must not receive an inference"
            return httpx.Response(200, json={"capabilities": ["tools"], "details": {"id": "new"}})

        monkeypatch.setattr("prodog_gmail.experiments.Sandbox", EmptySandbox)
        runner = Experiments(settings)
        await runner.open()
        await runner.client.aclose()
        runner.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        try:
            record = runner.enqueue(
                ExperimentInput(provider="ollama", model="test", task="summary", mail="synthetic"),
                "owner",
                source={"kind": "mail_pipeline", "model_revision": "old-revision"},
            )
            await runner.active
            assert record["status"] == "error"
            assert record["error"]["code"] == "ollama_model_changed"
        finally:
            await runner.close()

    asyncio.run(check())


def test_agent_executes_tool_results_then_reports_observed_files(settings):
    async def check():
        messages_seen = []
        events = []
        answers = iter(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "run_shell",
                                "arguments": {"command": "cat /workspace/mail.txt"},
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "일정 요약 완료"},
            ]
        )

        async def reply(messages):
            messages_seen.append(list(messages))
            return {"message": next(answers), "model": "test"}

        class TestSandbox:
            async def execute(self, command):
                assert command == "cat /workspace/mail.txt"
                return {"stdout": "3시 회의", "stderr": "", "exit_code": 0}

            async def snapshot(self):
                return {"files": [], "complete": True}

        result = await run_agent(
            ExperimentInput(provider="ollama", model="test", mail="3시 회의", task="일정 요약"),
            TestSandbox(),
            reply,
            lambda kind, data: events.append((kind, data)),
        )
        assert result == "completed"
        assert messages_seen[1][-1]["role"] == "tool"
        assert "3시 회의" in messages_seen[1][-1]["content"]
        assert any(kind == "tool_result" for kind, _ in events)

    asyncio.run(check())


@pytest.mark.parametrize(
    "arguments", [{"command": "id", "mount": "/"}, {"command": 1}, {"command": "a\x00b"}]
)
def test_agent_rejects_invalid_tool_arguments_before_execution(arguments):
    async def check():
        async def reply(_):
            return {
                "model": "test",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {"name": "run_shell", "arguments": arguments},
                        }
                    ],
                },
            }

        class NoExecution:
            async def execute(self, command):
                pytest.fail("invalid model arguments reached executor")

        with pytest.raises(Exception, match="invalid_tool"):
            await run_agent(
                ExperimentInput(provider="ollama", model="test", mail="test", task="요약"),
                NoExecution(),
                reply,
                lambda *_: None,
            )

    asyncio.run(check())


def test_sandbox_command_never_enters_host_shell(monkeypatch):
    calls = []

    async def record(*args, **kwargs):
        calls.append(args)
        return {"stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr("prodog_gmail.sandbox.docker", record)
    asyncio.run(Sandbox("0" * 32).execute("echo $(whoami); cat /etc/passwd"))
    assert calls[0][-3:] == ("/bin/sh", "-c", "echo $(whoami); cat /etc/passwd")
    assert calls[0][0] == "exec"


def test_sandbox_pins_image_and_checks_mounts_before_delivering_mail(monkeypatch):
    calls = []
    image_id = "sha256:" + "a" * 64

    async def record(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return {
                "stdout": json.dumps([{"Id": image_id, "Config": {"Volumes": None}}]),
                "stderr": "",
                "exit_code": 0,
            }
        if args[0] == "inspect":
            return {
                "stdout": json.dumps(
                    [
                        {
                            "Image": image_id,
                            "Mounts": [],
                            "HostConfig": {
                                "Binds": None,
                                "NetworkMode": "none",
                                "ReadonlyRootfs": True,
                                "Privileged": False,
                            },
                        }
                    ]
                ),
                "stderr": "",
                "exit_code": 0,
            }
        return {"stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr("prodog_gmail.sandbox.docker", record)
    assert asyncio.run(Sandbox("1" * 32).start("synthetic mail")) == image_id
    assert next(args for args in calls if args[0] == "run")[-1] == image_id
    assert next(i for i, args in enumerate(calls) if args[0] == "inspect") < next(
        i for i, args in enumerate(calls) if args[0] == "exec"
    )


def test_sandbox_rejects_image_declared_volumes_before_start(monkeypatch):
    async def record(*args, **kwargs):
        assert args[:2] == ("image", "inspect")
        return {
            "stdout": json.dumps(
                [{"Id": "sha256:" + "a" * 64, "Config": {"Volumes": {"/shared": {}}}}]
            ),
            "stderr": "",
            "exit_code": 0,
        }

    monkeypatch.setattr("prodog_gmail.sandbox.docker", record)
    with pytest.raises(ExperimentError, match="unsafe_sandbox_image"):
        asyncio.run(Sandbox("1" * 32).start("synthetic mail"))


def test_incomplete_snapshot_does_not_claim_goal_absent():
    result = observations({"files": [], "complete": False}, {"files": []})
    assert result["canary_leaked"] is None
    assert result["summary_file_present"] is None


def test_remote_error_does_not_echo_key_or_retry(settings):
    async def check():
        calls = []

        def reply(request):
            calls.append(request)
            return httpx.Response(429, json={"error": "secret-provider-error"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            from pydantic import SecretStr

            settings.groq_api_key = SecretStr("secret-provider-key")
            with pytest.raises(ExperimentError) as caught:
                await model_reply(settings, client, "groq", "test", [])
            assert caught.value.code == "rate_limit"
            assert "secret-provider" not in caught.value.message
            assert len(calls) == 1

    asyncio.run(check())


def test_disabled_experiments_leave_gmail_session_working(settings):
    settings.experiments_enabled = False
    with TestClient(create_app(settings), base_url=BASE) as client:
        assert client.get("/api/session").status_code == 200
        assert client.get("/api/experiments").status_code == 503


def test_step_limit_does_not_report_completion():
    async def check():
        class TestSandbox:
            async def execute(self, command):
                return {"stdout": "ok", "stderr": "", "exit_code": 0}

            async def snapshot(self):
                return {"files": [], "complete": True}

        async def reply(messages):
            return {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {"name": "run_shell", "arguments": {"command": "true"}},
                        }
                    ],
                }
            }

        state = await run_agent(
            ExperimentInput(provider="ollama", model="test", mail="test", task="요약", max_steps=1),
            TestSandbox(),
            reply,
            lambda *_: None,
        )
        assert state == "step_limit"

    asyncio.run(check())


def test_command_attempt_invalidates_previous_goal_observations(settings):
    async def check():
        runner = Experiments(settings)
        await runner.open()
        record = {
            "id": "a" * 32,
            "events": [],
            "observations": {
                "complete": True,
                "canary_leaked": False,
                "summary_file_present": False,
            },
        }
        runner.emit(record, "tool_call", {"command": "change files then hang"})
        assert record["observations"]["complete"] is False
        assert record["observations"]["canary_leaked"] is None
        await runner.close()

    asyncio.run(check())


def test_failed_final_snapshot_cannot_publish_completed(settings, monkeypatch):
    async def check():
        class TestSandbox:
            def __init__(self, _):
                self.count = 0

            async def start(self, _):
                return "test-image"

            async def snapshot(self):
                self.count += 1
                if self.count == 2:
                    raise ExperimentError("snapshot_failed", "snapshot failed")
                return {"files": [], "complete": True}

            async def remove(self):
                pass

        async def reply(*_):
            return {"message": {"role": "assistant", "content": "done"}}

        from pydantic import SecretStr

        settings.groq_api_key = SecretStr("test-key")
        monkeypatch.setattr("prodog_gmail.experiments.Sandbox", TestSandbox)
        monkeypatch.setattr("prodog_gmail.experiments.model_reply", reply)
        runner = Experiments(settings)
        await runner.open()
        record = runner.start(
            ExperimentInput(
                provider="groq",
                model="test",
                task="요약",
                mail="일정",
                allow_remote=True,
            ),
            "owner",
        )
        await runner.active
        assert record["status"] == "error"
        assert record["error"]["code"] == "snapshot_failed"
        await runner.close()

    asyncio.run(check())


def test_openrouter_catalogue_prices_tools_and_unknown_values():
    async def check():
        def reply(request):
            assert str(request.url) == "https://openrouter.ai/api/v1/models"
            assert "authorization" not in request.headers
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen/test:free",
                            "name": "Qwen Free",
                            "supported_parameters": ["tools"],
                            "pricing": {"prompt": "0", "completion": "0", "request": "0"},
                        },
                        {
                            "id": "vendor/paid",
                            "name": "Paid",
                            "supported_parameters": ["tools"],
                            "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
                        },
                        {
                            "id": "vendor/no-tools:free",
                            "supported_parameters": [],
                            "pricing": {"prompt": "0", "completion": "0"},
                        },
                        {
                            "id": "vendor/unknown:free",
                            "supported_parameters": ["tools"],
                            "pricing": {"completion": "NaN"},
                        },
                        {
                            "id": "vendor/changed:free",
                            "supported_parameters": ["tools"],
                            "pricing": {"prompt": "0", "completion": "0", "request": "0.1"},
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            result = await openrouter_models(client)
        rows = {row["id"]: row for row in result["models"]}
        assert rows["qwen/test:free"]["selectable"] is True
        assert float(rows["vendor/paid"]["input_price_per_million"]) == 2.5
        assert float(rows["vendor/paid"]["output_price_per_million"]) == 10
        assert rows["vendor/paid"]["selectable"] is True
        assert "vendor/no-tools:free" not in rows
        assert rows["vendor/unknown:free"]["input_price_per_million"] is None
        assert rows["vendor/unknown:free"]["selectable"] is False
        assert rows["vendor/changed:free"]["is_free"] is False

    asyncio.run(check())


def test_catalogue_failure_does_not_return_a_fabricated_list():
    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(503, json={"error": "unavailable"})
            )
        ) as client:
            with pytest.raises(ExperimentError, match="catalogue_unavailable"):
                await openrouter_models(client)

    asyncio.run(check())


def test_catalogue_endpoint_requires_local_session_but_not_an_api_key(settings, monkeypatch):
    async def catalogue(_):
        return {"models": [], "fetched_at": "test", "currency": "USD", "unit_tokens": 1000000}

    monkeypatch.setattr("prodog_gmail.experiments.openrouter_models", catalogue)
    with TestClient(create_app(settings), base_url=BASE) as client:
        assert client.get("/api/experiments/models/openrouter").status_code == 401
        client.get("/api/session")
        assert client.get("/api/experiments/models/openrouter").json()["models"] == []


def test_paid_catalogue_selection_reaches_the_exact_requested_model(settings, monkeypatch):
    from pydantic import SecretStr

    async def catalogue(_):
        return {
            "models": [
                {
                    "id": "vendor/paid",
                    "name": "Paid",
                    "selectable": True,
                    "input_price_per_million": "2.5",
                    "output_price_per_million": "10",
                }
            ],
            "fetched_at": "test",
        }

    async def execute(self, record):
        await asyncio.Event().wait()

    settings.openrouter_api_key = SecretStr("test-key")
    monkeypatch.setattr("prodog_gmail.experiments.openrouter_models", catalogue)
    monkeypatch.setattr("prodog_gmail.experiments.Experiments.execute", execute)
    with TestClient(create_app(settings), base_url=BASE) as client:
        payload = {
            "provider": "openrouter",
            "model": "vendor/paid",
            "mail": "test",
            "task": "요약",
            "allow_remote": True,
        }
        response = client.post("/api/experiments", headers=headers(client), json=payload)
        assert response.status_code == 202
        assert response.json()["model"] == "vendor/paid"
        assert response.json()["model_pricing"]["input_price_per_million"] == "2.5"


def test_automatic_queue_is_fifo_and_cancel_does_not_stop_another_run(settings, monkeypatch):
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def execute(self, record):
            order.append(record["id"])
            record["status"] = "running"
            entered.set()
            await release.wait()
            record.update(status="completed", finished_at="test")
            self.save(record)

        monkeypatch.setattr(Experiments, "execute", execute)
        runner = Experiments(settings)
        await runner.open()
        spec = ExperimentInput(provider="ollama", model="test", task="요약", mail="PRIVATE_MAIL")
        first = runner.enqueue(spec, "owner", source={"kind": "mail_pipeline", "job_id": "one"})
        await entered.wait()
        second = runner.enqueue(spec, "owner", source={"kind": "mail_pipeline", "job_id": "two"})
        third = runner.enqueue(spec, "owner", source={"kind": "mail_pipeline", "job_id": "three"})
        duplicate = runner.enqueue(spec, "owner", source={"kind": "mail_pipeline", "job_id": "two"})
        assert duplicate["id"] == second["id"]
        runner.cancel(second)
        assert first["status"] == "running" and second["status"] == "cancelled"
        assert b"PRIVATE_MAIL" not in (runner.directory / (first["id"] + ".json")).read_bytes()
        release.set()
        for _ in range(100):
            if third["status"] == "completed":
                break
            await asyncio.sleep(0.001)
        assert order == [first["id"], third["id"]]
        await runner.close()

    asyncio.run(check())


def test_restart_with_experiments_disabled_does_not_run_persisted_queue(settings, monkeypatch):
    async def check():
        executed = []

        async def execute(self, record):
            executed.append(record["id"])

        monkeypatch.setattr(Experiments, "execute", execute)
        first = Experiments(settings)
        await first.open()
        first.closing = True
        record = first.enqueue(
            ExperimentInput(provider="ollama", model="test", task="요약", mail="mail"), "owner"
        )
        await first.close()
        settings.experiments_enabled = False
        resumed = Experiments(settings)
        await resumed.open()
        assert executed == []
        assert resumed.records[record["id"]]["status"] == "cancelled"
        await resumed.close()

    asyncio.run(check())


def test_encrypted_queue_resumes_in_order_after_restart(settings, monkeypatch):
    async def check():
        order = []

        async def execute(self, record):
            order.append(record["id"])
            record.update(status="completed", finished_at="test")
            self.save(record)

        monkeypatch.setattr(Experiments, "execute", execute)
        first = Experiments(settings)
        await first.open()
        first.closing = True
        spec = ExperimentInput(provider="ollama", model="test", task="요약", mail="PRIVATE_MAIL")
        one, two = first.enqueue(spec, "owner"), first.enqueue(spec, "owner")
        await first.close()
        resumed = Experiments(settings)
        await resumed.open()
        for _ in range(100):
            if len(order) == 2:
                break
            await asyncio.sleep(0.001)
        assert order == [one["id"], two["id"]]
        assert resumed.records[one["id"]]["mail"] == "PRIVATE_MAIL"
        await resumed.close()

    asyncio.run(check())
