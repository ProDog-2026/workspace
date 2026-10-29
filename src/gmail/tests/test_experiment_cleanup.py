import asyncio
import json

import pytest
from cryptography.fernet import Fernet

from prodog_gmail.config import Settings
from prodog_gmail.errors import ConnectorError
from prodog_gmail.experiments import ExperimentInput, Experiments
from prodog_gmail.sandbox import ExperimentError, Sandbox


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


def test_unresolved_startup_cleanup_blocks_admission_and_finishes_persisted_queue(
    runner, monkeypatch
):
    async def check():
        failed, queued = record(1), record(2, "queued", None)
        untouched = record(3, "completed", None)
        persist(runner, failed, queued, untouched)
        attempts, finished = [], []

        async def remove(self):
            attempts.append(self.name)
            raise ExperimentError("cleanup_failed", "still running")

        monkeypatch.setattr(Sandbox, "remove", remove)
        runner.on_finished = lambda item: finished.append(item["id"])
        try:
            await runner.open()
            assert attempts == ["prodog-exp-" + failed["id"]]
            assert runner.records[failed["id"]]["error"]["code"] == "cleanup_failed"
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
