import asyncio
import json

import httpx
import pytest
from cryptography.fernet import Fernet

from prodog_gmail.config import Settings
from prodog_gmail.errors import ConnectorError
from prodog_gmail.experiments import ExperimentInput, Experiments
from prodog_gmail.sandbox import ExperimentError, Sandbox

REMOVAL_FAILURES = [
    pytest.param(ExperimentError("cleanup_failed", "safe removal detail"), id="removal"),
    pytest.param(ExperimentError("command_timeout", "safe timeout detail"), id="timeout"),
    pytest.param(ExperimentError("docker_unavailable", "safe Docker detail"), id="unavailable"),
    pytest.param(ConnectionResetError("PRIVATE_TRANSPORT_DETAIL"), id="transport"),
    pytest.param(RuntimeError("PRIVATE_UNEXPECTED_DETAIL"), id="unexpected"),
    pytest.param(TimeoutError("PRIVATE_TIMEOUT_DETAIL"), id="raw-timeout"),
]


@pytest.fixture
def runner(tmp_path):
    return Experiments(
        Settings(
            _env_file=None,
            token_encryption_key=Fernet.generate_key().decode(),
            database_path=tmp_path / "test.db",
        )
    )


def record(number, status="error", code="cleanup_failed"):
    return {
        "id": f"{number:032x}",
        "status": status,
        "error": {"code": code, "message": "test"} if code else None,
        "created_at": f"{number:04d}",
        "finished_at": None,
        "events": [],
        "owner": "owner",
    }


def persist(runner, *records):
    runner.directory.mkdir(parents=True, exist_ok=True)
    for item in records:
        runner.save(item)


def spec():
    return ExperimentInput(provider="ollama", model="test", mail="mail", task="summarize")


@pytest.fixture
def synthetic_execution(runner, monkeypatch):
    async def start(self, mail):
        return "synthetic-image"

    async def snapshot(self):
        return {"files": [], "complete": True}

    async def post(*args, **kwargs):
        return httpx.Response(200, json={"capabilities": ["tools"]})

    async def run_agent(*args):
        return "completed"

    monkeypatch.setattr(Sandbox, "start", start)
    monkeypatch.setattr(Sandbox, "snapshot", snapshot)
    monkeypatch.setattr(runner.client, "post", post)
    monkeypatch.setattr("prodog_gmail.experiments.run_agent", run_agent)


@pytest.mark.parametrize(
    "status,code",
    [
        ("running", None),
        ("cancelling", None),
        ("error", "cleanup_failed"),
    ],
)
def test_startup_retries_known_cleanup_and_persists_resolution(runner, monkeypatch, status, code):
    async def check():
        item = record(1, status, code)
        persist(runner, item)
        removed = []

        async def remove(self):
            removed.append(self.name)

        monkeypatch.setattr(Sandbox, "remove", remove)
        try:
            await runner.open()
            assert removed == ["prodog-exp-" + item["id"]]
            assert runner.records[item["id"]]["error"]["code"] == "server_restarted"
            saved = runner.cipher.decrypt(
                json.loads((runner.directory / (item["id"] + ".json")).read_text())[
                    "encrypted"
                ].encode()
            )
            assert b'"cleanup_failed"' not in saved
        finally:
            await runner.close()

    asyncio.run(check())


@pytest.mark.parametrize("failure", REMOVAL_FAILURES)
@pytest.mark.parametrize("status", ["running", "cancelling", "error"])
def test_unresolved_startup_cleanup_blocks_admission_and_finishes_persisted_queue(
    runner, monkeypatch, failure, status
):
    async def check():
        failed, queued = record(1, status), record(2, "queued", None)
        failed["observations"] = {"complete": False, "canary_leaked": None}
        untouched = record(3, "completed", None)
        persist(runner, failed, queued, untouched)
        attempts, finished = [], []

        async def remove(self):
            attempts.append(self.name)
            raise failure

        monkeypatch.setattr(Sandbox, "remove", remove)
        runner.on_finished = lambda item: finished.append(item["id"])
        try:
            await runner.open()
            assert attempts == ["prodog-exp-" + failed["id"]]
            assert runner.records[failed["id"]]["error"]["code"] == "cleanup_failed"
            saved = json.loads(
                runner.cipher.decrypt(
                    json.loads((runner.directory / (failed["id"] + ".json")).read_text())[
                        "encrypted"
                    ].encode()
                )
            )
            assert saved["error"]["code"] == "cleanup_failed"
            assert saved["status"] == "error" and saved["finished_at"]
            assert saved["observations"] == failed["observations"]
            assert "PRIVATE_" not in json.dumps(saved)
            assert runner.records[queued["id"]]["status"] == "error"
            assert runner.records[queued["id"]]["error"]["code"] == "cleanup_blocked"
            assert queued["id"] in finished
            assert not runner.pending and runner.active is None
            for submit in (runner.start, runner.enqueue):
                with pytest.raises(ConnectorError, match="cleanup_blocked") as caught:
                    submit(spec(), "owner")
                assert caught.value.status == 503
            assert len(runner.records) == 3
            assert len(list(runner.directory.glob("*.json"))) == 3
        finally:
            await runner.close()

    asyncio.run(check())


@pytest.mark.parametrize("failure", REMOVAL_FAILURES)
def test_remove_failure_persists_barrier_until_confirmed_restart_cleanup(
    runner, monkeypatch, synthetic_execution, failure
):
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        attempts = []
        first = None

        async def docker(*args, **kwargs):
            assert args[:2] == ("rm", "--force")
            assert kwargs["timeout"] == 20 and kwargs["context"] == "prodog-vm"
            attempts.append(args[2])
            entered.set()
            await release.wait()
            if args[2] == "prodog-exp-" + first["id"]:
                raise failure
            return {"exit_code": 0, "stdout": "", "stderr": ""}

        monkeypatch.setattr("prodog_gmail.sandbox.docker", docker)
        runner.settings.sandbox_docker_context = "prodog-vm"
        try:
            await runner.open()
            first = runner.start(spec(), "owner")
            await entered.wait()
            observed = first["observations"].copy()
            queued = runner.enqueue(spec(), "owner")
            release.set()
            assert await asyncio.gather(runner.active, return_exceptions=True) == [None]
            await asyncio.sleep(0)
            assert first["status"] == "error" and first["finished_at"]
            assert first["error"]["code"] == "cleanup_failed"
            if isinstance(failure, ExperimentError):
                assert first["error"]["message"] == failure.message
            assert "PRIVATE_" not in json.dumps(first)
            assert first["observations"] == observed
            assert first["events"][-2]["data"] == first["error"]
            assert first["events"][-1]["data"] == {"status": "error"}
            assert queued["error"]["code"] == "cleanup_blocked"
            assert attempts == ["prodog-exp-" + first["id"]]
            for submit in (runner.start, runner.enqueue):
                with pytest.raises(ConnectorError, match="cleanup_blocked"):
                    submit(spec(), "owner")
        finally:
            release.set()
            await runner.close()

        restored = Experiments(runner.settings)
        try:
            await restored.open()
            assert restored.records[first["id"]]["error"]["code"] == "cleanup_failed"
            assert restored.records[first["id"]]["observations"] == observed
            with pytest.raises(ConnectorError, match="cleanup_blocked"):
                restored.start(spec(), "owner")
        finally:
            await restored.close()

        async def removed(*args, **kwargs):
            assert args == ("rm", "--force", "prodog-exp-" + first["id"])
            assert kwargs["context"] == "prodog-vm"
            return {"exit_code": 0, "stdout": "", "stderr": ""}

        monkeypatch.setattr("prodog_gmail.sandbox.docker", removed)
        recovered = Experiments(runner.settings)
        recovered.closing = True
        try:
            await recovered.open()
            item = recovered.records[first["id"]]
            assert item["error"]["code"] == "server_restarted"
            assert item["observations"] == observed
            assert recovered.start(spec(), "owner")["status"] == "queued"
        finally:
            await recovered.close()

    asyncio.run(check())


@pytest.mark.parametrize("failure", [None, *REMOVAL_FAILURES])
@pytest.mark.parametrize("cancel_stage", ["execution", "cleanup"])
@pytest.mark.parametrize("cancel_first", [False, True], ids=["shutdown", "cancel-then-shutdown"])
def test_cancel_and_shutdown_wait_for_cleanup_and_persist_its_outcome(
    runner, monkeypatch, synthetic_execution, failure, cancel_stage, cancel_first
):
    async def check():
        started, removing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        attempts = []

        async def start(self, mail):
            started.set()
            await asyncio.Event().wait()

        async def docker(*args, **kwargs):
            assert args[:2] == ("rm", "--force")
            attempts.append(args[2])
            removing.set()
            await release.wait()
            if failure is not None:
                raise failure
            return {"exit_code": 0, "stdout": "", "stderr": ""}

        if cancel_stage == "execution":
            monkeypatch.setattr(Sandbox, "start", start)
        monkeypatch.setattr("prodog_gmail.sandbox.docker", docker)
        closing = None
        try:
            await runner.open()
            item = runner.start(spec(), "owner")
            if cancel_stage == "execution":
                await started.wait()
            else:
                await removing.wait()
            if cancel_first:
                runner.cancel(item)
            closing = asyncio.create_task(runner.close())
            await removing.wait()
            await asyncio.sleep(0)
            assert not closing.done()
            assert item["status"] == ("cancelling" if cancel_first else "running")
            release.set()
            assert await asyncio.gather(closing, return_exceptions=True) == [None]
            expected = "error" if failure is not None else "cancelled"
            assert item["status"] == expected and item["finished_at"]
            assert attempts == ["prodog-exp-" + item["id"]]
            saved = json.loads(
                runner.cipher.decrypt(
                    json.loads((runner.directory / (item["id"] + ".json")).read_text())[
                        "encrypted"
                    ].encode()
                )
            )
            assert saved == item
            assert item["events"][-1]["data"] == {"status": expected}
            if failure is not None:
                assert item["error"]["code"] == "cleanup_failed"
                assert "PRIVATE_" not in json.dumps(item)
                with pytest.raises(ConnectorError, match="cleanup_blocked"):
                    runner.start(spec(), "owner")
            else:
                assert item["error"] is None
                assert runner.start(spec(), "owner")["status"] == "queued"
        finally:
            release.set()
            if closing:
                await asyncio.gather(closing, return_exceptions=True)
            await runner.close()

    asyncio.run(check())


def test_runtime_cleanup_failure_blocks_waiting_runs_and_retains_failed_record(runner, monkeypatch):
    async def check():
        persist(runner, *(record(i, "completed", None) for i in range(1, 52)))
        await runner.open()
        entered, release = asyncio.Event(), asyncio.Event()

        async def execute(self, item):
            entered.set()
            await release.wait()
            item.update(status="error", error={"code": "cleanup_failed", "message": "test"})
            # Make this unresolved record the oldest when retention runs next.
            item["created_at"] = "0000"
            self.save(item)

        monkeypatch.setattr(Experiments, "execute", execute)
        try:
            first = runner.enqueue(spec(), "owner")
            await entered.wait()
            second = runner.enqueue(spec(), "owner")
            release.set()
            await runner.active
            await asyncio.sleep(0)
            assert runner.records[second["id"]]["status"] == "error"
            assert runner.records[second["id"]]["error"]["code"] == "cleanup_blocked"
            with pytest.raises(ConnectorError, match="cleanup_blocked"):
                runner.enqueue(spec(), "owner")
            assert first["id"] in runner.records
            assert (runner.directory / (first["id"] + ".json")).exists()
        finally:
            release.set()
            await runner.close()

    asyncio.run(check())


def test_retention_preserves_cleanup_failure_discovered_during_scheduling(runner, monkeypatch):
    async def check():
        persist(runner, *(record(i, "completed", None) for i in range(1, 52)))
        try:
            await runner.open()
            oldest = runner.records[f"{1:032x}"]

            def scheduling_failure():
                oldest.update(status="error", error={"code": "cleanup_failed", "message": "test"})
                runner.save(oldest)

            monkeypatch.setattr(runner, "_schedule", scheduling_failure)
            runner.enqueue(spec(), "owner")
            assert oldest["id"] in runner.records
            assert (runner.directory / (oldest["id"] + ".json")).exists()
        finally:
            await runner.close()

    asyncio.run(check())
