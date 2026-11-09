import asyncio
import hashlib
import json
import threading

import httpx
import pytest

from backend.api import create_dashboard_app
from backend.llm import LLMAdapter
from backend.runs import RunManager


REQUEST_ID = "a1" * 16
CONFIG = {"base_url": "http://ollama", "model": "test", "api_key": "private-key"}
BODY = {"config": CONFIG, "email": {"subject": "synthetic", "body": "test {{MARKER}}"},
        "request_id": REQUEST_ID}


class FakeRuntime:
    def __init__(self, blocked=False):
        self.executed = []
        self.release = threading.Event()
        if not blocked:
            self.release.set()

    def execute(self, run, emit):
        self.executed.append(run.id)
        assert self.release.wait(5), "test runtime was not released"
        run.container_id = "fake-container"
        emit({"event_type": "final", "completion": "completed"})
        return {"summary.txt": "saved"}, [], [], 0

    def remove(self, container_id):
        pass


def api_for(manager):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_dashboard_app(manager)),
                             base_url="http://api")


def test_duplicate_request_reuses_live_run(tmp_path):
    async def test():
        runtime = FakeRuntime(blocked=True)
        async with httpx.AsyncClient(trust_env=False) as provider:
            manager = RunManager(LLMAdapter(provider), runtime, tmp_path)
            try:
                async with api_for(manager) as api:
                    first = await api.post("/runs", json=BODY)
                    assert first.status_code == 201
                    duplicate = await api.post("/runs", json=BODY)
                    assert duplicate.status_code == 201
                    assert first.json()["run_id"] == duplicate.json()["run_id"] == REQUEST_ID
                    assert duplicate.json()["phase"] == "executing"
                    assert len(manager.runs) == 1
                    assert "private-key" not in duplicate.text
            finally:
                runtime.release.set()
                await manager.close()
            assert runtime.executed == [REQUEST_ID]
    asyncio.run(test())


def test_duplicate_completed_request_reuses_persisted_result_after_restart(tmp_path):
    async def test():
        runtime = FakeRuntime()
        async with httpx.AsyncClient(trust_env=False) as provider:
            manager = RunManager(LLMAdapter(provider), runtime, tmp_path)
            async with api_for(manager) as api:
                first = await api.post("/runs", json=BODY)
                assert first.status_code == 201
                await manager.get(REQUEST_ID).lifecycle
            restarted = RunManager(LLMAdapter(provider), runtime, tmp_path)
            async with api_for(restarted) as api:
                duplicate = await api.post("/runs", json=BODY)
                assert duplicate.status_code == 201
                assert duplicate.json()["run_id"] == REQUEST_ID
                assert duplicate.json()["phase"] == "finished"
                assert duplicate.json()["summary"] == "saved"
                assert not restarted.runs
            assert runtime.executed == [REQUEST_ID]
            assert "private-key" not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    asyncio.run(test())


def test_unfinished_reservation_after_restart_never_relaunches(tmp_path):
    async def test():
        runtime = FakeRuntime(blocked=True)
        async with httpx.AsyncClient(trust_env=False) as provider:
            manager = RunManager(LLMAdapter(provider), runtime, tmp_path)
            try:
                async with api_for(manager) as api:
                    assert (await api.post("/runs", json=BODY)).status_code == 201
                restarted = RunManager(LLMAdapter(provider), runtime, tmp_path)
                async with api_for(restarted) as api:
                    duplicate = await api.post("/runs", json=BODY)
                    assert duplicate.status_code == 409
                    assert duplicate.json()["error"] == "submission_unknown"
                    lookup = await api.get(f"/runs/{REQUEST_ID}")
                    assert lookup.status_code == 409
                    assert lookup.json()["error"] == "submission_unknown"
                    assert not restarted.runs
            finally:
                runtime.release.set()
                await manager.close()
            assert runtime.executed == [REQUEST_ID]
    asyncio.run(test())


def test_changed_request_inputs_reject_existing_id(tmp_path):
    async def test():
        runtime = FakeRuntime()
        async with httpx.AsyncClient(trust_env=False) as provider:
            manager = RunManager(LLMAdapter(provider), runtime, tmp_path)
            async with api_for(manager) as api:
                assert (await api.post("/runs", json=BODY)).status_code == 201
                await manager.get(REQUEST_ID).lifecycle
            restarted = RunManager(LLMAdapter(provider), runtime, tmp_path)
            async with api_for(restarted) as api:
                changed = await api.post("/runs", json=BODY | {"email": {"body": "different"}})
                assert changed.status_code == 409
                assert changed.json()["error"] == "request_id_conflict"
                assert not restarted.runs
            assert runtime.executed == [REQUEST_ID]
    asyncio.run(test())


@pytest.mark.parametrize("request_id", ["", "A1" * 16, "../" + "a" * 29, "a" * 31, "g" * 32])
def test_invalid_request_id_is_rejected_without_launch(tmp_path, request_id):
    async def test():
        async with httpx.AsyncClient(trust_env=False) as provider:
            manager = RunManager(LLMAdapter(provider), FakeRuntime(), tmp_path)
            async with api_for(manager) as api:
                rejected = await api.post("/runs", json=BODY | {"request_id": request_id})
                assert rejected.status_code == 422
                assert not manager.runs
    asyncio.run(test())


def test_expected_prompt_hash_prevents_changed_prompt_launch(tmp_path):
    async def test():
        async with httpx.AsyncClient(trust_env=False) as provider:
            manager = RunManager(LLMAdapter(provider), FakeRuntime(), tmp_path)
            manager.prompts.create("CUSTOM", "", "original")
            expected = hashlib.sha256(json.dumps("original", sort_keys=True,
                                                 ensure_ascii=False).encode()).hexdigest()
            manager.prompts.update("CUSTOM", "", "changed")
            async with api_for(manager) as api:
                rejected = await api.post("/runs", json=BODY | {
                    "prompt_id": "CUSTOM", "expected_prompt_hash": expected})
                assert rejected.status_code == 422
                assert rejected.json()["error"] == "prompt_changed"
                assert not manager.runs
                manager.prompts.update("CUSTOM", "", "original")
                accepted = await api.post("/runs", json=BODY | {
                    "prompt_id": "CUSTOM", "expected_prompt_hash": expected})
                assert accepted.status_code == 201
                await manager.get(REQUEST_ID).lifecycle
                manager.prompts.update("CUSTOM", "", "changed again")
            restarted = RunManager(LLMAdapter(provider), FakeRuntime(), tmp_path)
            async with api_for(restarted) as api:
                replay = await api.post("/runs", json=BODY | {
                    "prompt_id": "CUSTOM", "expected_prompt_hash": expected})
                assert replay.status_code == 201
                assert replay.json()["system_prompt"] == "original"
                assert not restarted.runs
    asyncio.run(test())
