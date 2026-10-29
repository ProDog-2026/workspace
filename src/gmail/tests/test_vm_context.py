import asyncio

import pytest
from cryptography.fernet import Fernet

from prodog_gmail.config import Settings
from prodog_gmail.experiments import ExperimentInput, Experiments
from prodog_gmail.sandbox import ExperimentError, Sandbox, docker, docker_status


def test_docker_uses_explicit_context_without_host_shell(monkeypatch):
    calls = []

    async def capture(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("synthetic launch boundary")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    with pytest.raises(ExperimentError):
        asyncio.run(docker("info", context="prodog-vm"))
    assert calls[0][0] == ("docker", "--context", "prodog-vm", "info")
    assert "shell" not in calls[0][1]


def test_sandbox_exec_and_cleanup_keep_context(monkeypatch):
    calls = []

    async def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("prodog_gmail.sandbox.docker", capture)
    box = Sandbox("a" * 32, context="prodog-vm")
    asyncio.run(box.execute("echo $(whoami)"))
    asyncio.run(box.remove())
    assert all(kwargs["context"] == "prodog-vm" for _, kwargs in calls)
    assert calls[0][0][-3:] == ("/bin/sh", "-c", "echo $(whoami)")


def test_missing_vm_never_falls_back_to_desktop(monkeypatch):
    calls = []

    async def unavailable(*args, **kwargs):
        calls.append(kwargs.get("context"))
        return {"exit_code": 1, "stdout": "", "stderr": "unavailable"}

    monkeypatch.setattr("prodog_gmail.sandbox.docker", unavailable)
    result = asyncio.run(docker_status("prodog-vm"))
    assert result["ready"] is False and result["context"] == "prodog-vm"
    assert calls == ["prodog-vm"]


def test_unbound_queued_run_uses_selected_vm_before_any_command(tmp_path, monkeypatch):
    async def check():
        settings = Settings(
            _env_file=None,
            database_path=tmp_path / "test.db",
            token_encryption_key=Fernet.generate_key().decode(),
            sandbox_docker_context="prodog-vm",
        )
        runner = Experiments(settings)
        monkeypatch.setattr(runner, "_schedule", lambda: None)
        calls = []

        class Box:
            def __init__(self, _run_id, *, context=""):
                self.context = context
                calls.append(("create", context))

            async def start(self, _mail):
                raise ExperimentError("synthetic_stop", "test")

            async def remove(self):
                calls.append(("remove", self.context))

        monkeypatch.setattr("prodog_gmail.experiments.Sandbox", Box)
        try:
            await runner.open()
            item = runner.enqueue(
                ExperimentInput(provider="ollama", model="test", task="test", mail="synthetic"),
                "owner",
            )
            item.pop("sandbox_context")
            await runner.execute(item)
            assert item["sandbox_context"] == "prodog-vm"
            assert calls == [("create", "prodog-vm"), ("remove", "prodog-vm")]
        finally:
            await runner.close()

    asyncio.run(check())


def test_started_legacy_cleanup_does_not_switch_to_new_vm(tmp_path, monkeypatch):
    async def check():
        runner = Experiments(
            Settings(
                _env_file=None,
                database_path=tmp_path / "test.db",
                token_encryption_key=Fernet.generate_key().decode(),
                sandbox_docker_context="prodog-vm",
            )
        )
        monkeypatch.setattr(runner, "_schedule", lambda: None)
        runner.directory.mkdir()
        runner.save(
            {
                "id": "1" * 32,
                "status": "running",
                "error": None,
                "created_at": "test",
                "events": [],
                "owner": "synthetic",
            }
        )
        seen = []

        async def remove(self):
            seen.append(self.context)

        monkeypatch.setattr(Sandbox, "remove", remove)
        try:
            await runner.open()
            assert seen == [""]
        finally:
            await runner.close()

    asyncio.run(check())


def test_queued_runs_record_context_and_recovery_uses_record_not_new_settings(
    tmp_path, monkeypatch
):
    async def check():
        settings = Settings(
            _env_file=None,
            database_path=tmp_path / "test.db",
            token_encryption_key=Fernet.generate_key().decode(),
            sandbox_docker_context="prodog-vm",
        )
        runner = Experiments(settings)
        monkeypatch.setattr(runner, "_schedule", lambda: None)
        await runner.open()
        item = runner.enqueue(
            ExperimentInput(provider="ollama", model="test", task="test", mail="synthetic"), "owner"
        )
        assert item["sandbox_context"] == "prodog-vm"
        item.update(status="running")
        runner.save(item)
        await runner.close()
        settings.sandbox_docker_context = "another-context"
        restored = Experiments(settings)
        monkeypatch.setattr(restored, "_schedule", lambda: None)
        seen = []

        async def remove(self):
            seen.append(self.context)

        monkeypatch.setattr(Sandbox, "remove", remove)
        try:
            await restored.open()
            assert seen == ["prodog-vm"]
        finally:
            await restored.close()

    asyncio.run(check())


@pytest.mark.parametrize(
    "context", ["--host=tcp://remote", "bad name", "../context", "a;echo test"]
)
def test_context_is_only_a_local_context_name(context):
    with pytest.raises(ValueError):
        Sandbox("a" * 32, context=context)
