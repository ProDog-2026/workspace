import asyncio
import io
import json
import tarfile
import threading

import httpx
import pytest
from backend.evidence import read_archive
from backend.llm import LLMAdapter
from backend.runs import RunManager
from backend.api import create_dashboard_app


def test_runtime_failure_cancels_inference_and_keeps_worker_for_recovery(tmp_path):
    from backend.llm import ConnectionConfig
    from backend.runtime import RuntimeFailure
    from backend.runs import RunError

    async def test():
        started = asyncio.Event()
        async def upstream(request):
            started.set()
            await asyncio.Event().wait()

        class Runtime:
            mode = "docker"
            def execute(self, run, emit):
                run.container_id = "synthetic-retained-worker"
                raise RuntimeFailure("docker_response_timeout")
            def remove(self, cid):
                pytest.fail("worker evidence was not collected")

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            manager = RunManager(LLMAdapter(client), Runtime(), tmp_path)
            info = manager.create(ConnectionConfig(base_url="http://ollama.test", model="test"), [])
            run = manager.get(info["run_id"])
            infer = asyncio.create_task(manager.infer(run.id, run.token, [{"role": "user", "content": "synthetic"}], []))
            await started.wait()
            await manager._execute(run)
            with pytest.raises(RunError):
                await infer
            result = manager.store.load(run.id)
            assert result["recovery_required"] is True and result["error_code"] == "docker_response_timeout"
            assert run.pending is None and manager.environment_report["ready"] is False
            responses = [e for e in manager.store.events(run.id) if e["event_type"] == "llm_response"]
            assert responses[-1]["response"]["error"] == "docker_response_timeout"
    asyncio.run(test())


def test_archive_rejects_links_traversal_and_devices():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        for name, kind in [("work/summary.txt", tarfile.REGTYPE),
                           ("work/files/ok.txt", tarfile.REGTYPE),
                           ("work/files/link", tarfile.SYMTYPE),
                           ("../host", tarfile.REGTYPE),
                           ("work/files/hard", tarfile.LNKTYPE),
                           ("work/files/device", tarfile.CHRTYPE)]:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = "/etc/passwd" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
            if kind == tarfile.REGTYPE:
                info.size = 2
                tar.addfile(info, io.BytesIO(b"ok"))
            else:
                tar.addfile(info)
    files, errors = read_archive([data.getvalue()])
    assert files == {"summary.txt": "ok", "files/ok.txt": "ok"}
    assert len(errors) == 4


def test_managed_run_persists_before_cleanup_and_api_hides_token(tmp_path):
    class FakeRuntime:
        removed = False
        def execute(self, run, emit):
            run.container_id = "fake-container"
            assert run.token
            assert "{{MARKER}}" not in run.email["body"]
            emit({"event_type": "tool_result", "name": "send_email", "status": "simulated"})
            emit({"event_type": "final", "completion": "completed"})
            return {"summary.txt": "saved", "files/test.txt": "changed"}, [], [{
                "source": "receiver", "event_type": "request_received",
                "query": run.marker}], 0
        def remove(self, cid):
            assert list(tmp_path.glob("*/result.json"))
            self.removed = True
    async def test():
        runtime = FakeRuntime()
        async with httpx.AsyncClient(trust_env=False) as client:
            manager = RunManager(LLMAdapter(client), runtime, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(
                    app=create_dashboard_app(manager)), base_url="http://api") as api:
                res = await api.post("/runs", json={"config": {
                    "base_url": "http://ollama", "model": "test", "api_key": "private"},
                    "email": {"body": "marker {{MARKER}}"}})
                assert res.status_code == 201
                assert "execution_token" not in res.text and "private" not in res.text
                run_id = res.json()["run_id"]
                await manager.get(run_id).lifecycle
                result = (await api.get(f"/runs/{run_id}/result")).json()
                assert result.get("summary") == "saved", (result, manager.get(run_id).phase, list(tmp_path.rglob("*")))
                assert result["completion"] == "completed"
                assert runtime.removed
                assert result["effects"]["marker_received"][0]["source"] == "receiver"
                saved = (tmp_path / run_id / "result.json").read_text()
                assert "private" not in saved
                restarted = RunManager(LLMAdapter(client), runtime, tmp_path)
                assert restarted.view(run_id)["summary"] == "saved"
                assert restarted.events_for(run_id)
                assert len(restarted.listing()) == 1
    asyncio.run(test())


def test_cleanup_failure_blocks_the_next_container_run(tmp_path):
    class Runtime:
        dirty = False
        executions = 0

        def prepare(self, store):
            return {"ready": not self.dirty, "error": "runtime_recovery_failed" if self.dirty else None}

        def execute(self, run, emit):
            self.executions += 1
            run.container_id = "retained-worker"
            emit({"event_type": "final", "completion": "completed"})
            return {"summary.txt": "synthetic"}, [], [], 0

        def remove(self, container_id):
            self.dirty = True
            raise OSError("synthetic Docker failure")

    async def scenario():
        runtime = Runtime()
        manager = RunManager(LLMAdapter(None), runtime, tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=create_dashboard_app(manager)), base_url="http://api") as api:
            body = {"config": {"base_url": "http://ollama", "model": "test"},
                    "email": {"body": "synthetic"}}
            first = await api.post("/runs", json=body)
            await manager.get(first.json()["run_id"]).lifecycle
            assert manager.result_for(first.json()["run_id"])["cleanup_error"]
            second = await api.post("/runs", json=body)
            assert second.status_code == 409
            assert second.json()["error"] == "environment_not_ready"
            assert runtime.executions == 1
            status = (await api.get("/environment")).json()
            assert status["ready"] is False
    asyncio.run(scenario())


def test_failed_isolation_check_does_not_create_a_worker(tmp_path):
    class Runtime:
        def prepare(self, store):
            return {"ready": False, "error": "isolation_check_failed"}

        def execute(self, run, emit):
            pytest.fail("Unsafe environment must never receive the email")

    async def scenario():
        manager = RunManager(LLMAdapter(None), Runtime(), tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=create_dashboard_app(manager)), base_url="http://api") as api:
            response = await api.post("/runs", json={
                "config": {"base_url": "http://ollama", "model": "test"},
                "email": {"body": "synthetic"}})
            assert response.status_code == 409
            assert not manager.runs
            assert (await api.get("/environment")).json()["ready"] is False
    asyncio.run(scenario())


def test_active_run_is_not_recovered_and_idempotent_replay_does_not_probe(tmp_path):
    class Runtime:
        checks = 0
        release = threading.Event()

        def prepare(self, store):
            self.checks += 1
            return {"ready": True, "checks": {"internal_network": True}}

        def execute(self, run, emit):
            assert self.release.wait(5)
            emit({"event_type": "final", "completion": "completed"})
            return {}, [], [], 0

        def remove(self, container_id):
            pass

    async def scenario():
        runtime = Runtime()
        manager = RunManager(LLMAdapter(None), runtime, tmp_path)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(
                    app=create_dashboard_app(manager)), base_url="http://api") as api:
                body = {"config": {"base_url": "http://ollama", "model": "test"},
                        "email": {"body": "synthetic"}, "request_id": "cd" * 16}
                first = await api.post("/runs", json=body)
                assert first.status_code == 201
                check = await api.post("/environment/check")
                assert check.json() == {"ready": False, "error": "environment_busy"}
                replay = await api.post("/runs", json=body)
                assert replay.status_code == 201
                assert replay.json()["run_id"] == first.json()["run_id"]
                assert runtime.checks == 1
        finally:
            runtime.release.set()
            await manager.close()
    asyncio.run(scenario())


def test_runtime_boundary_change_invalidates_the_successful_check(tmp_path):
    from backend.environment import EnvironmentFailure

    class Runtime:
        def prepare(self, store):
            return {"ready": True}

        def execute(self, run, emit):
            raise EnvironmentFailure("network_changed")

        def remove(self, container_id):
            pass

    async def scenario():
        manager = RunManager(LLMAdapter(None), Runtime(), tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=create_dashboard_app(manager)), base_url="http://api") as api:
            response = await api.post("/runs", json={
                "config": {"base_url": "http://ollama", "model": "test"},
                "email": {"body": "synthetic"}})
            run_id = response.json()["run_id"]
            await manager.get(run_id).lifecycle
            assert (await api.get("/environment")).json()["ready"] is False
            assert manager.result_for(run_id)["network_isolation"] == "unverified"
    asyncio.run(scenario())


def test_simultaneous_replay_checks_reservation_after_waiting_for_preflight(tmp_path):
    class Runtime:
        checks = 0
        checking = threading.Event()
        checked = threading.Event()
        finished = threading.Event()

        def prepare(self, store):
            self.checks += 1
            self.checking.set()
            assert self.checked.wait(5)
            return {"ready": True}

        def execute(self, run, emit):
            assert self.finished.wait(5)
            emit({"event_type": "final", "completion": "completed"})
            return {}, [], [], 0

        def remove(self, container_id):
            pass

    async def scenario():
        runtime = Runtime()
        manager = RunManager(LLMAdapter(None), runtime, tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=create_dashboard_app(manager)), base_url="http://api") as api:
            body = {"config": {"base_url": "http://ollama", "model": "test"},
                    "email": {"body": "synthetic"}, "request_id": "cd" * 16}
            first = asyncio.create_task(api.post("/runs", json=body))
            try:
                assert await asyncio.to_thread(runtime.checking.wait, 1)
                replay = asyncio.create_task(api.post("/runs", json=body))
                await asyncio.sleep(0.02)
                runtime.checked.set()
                responses = await asyncio.gather(first, replay)
                assert [r.status_code for r in responses] == [201, 201]
                assert runtime.checks == 1
            finally:
                runtime.checked.set()
                runtime.finished.set()
                await asyncio.gather(first, return_exceptions=True)
                await manager.close()
    asyncio.run(scenario())


def test_recovery_refreshes_live_storage_error_state_from_saved_evidence(tmp_path):
    class Runtime:
        recovering = False

        def prepare(self, store):
            if not self.recovering:
                return {"ready": True}
            store.save("cd" * 16, [{"event_type": "runtime_interrupted"}],
                       {"run_id": "cd" * 16, "phase": "finished", "status": "interrupted"}, "")
            return {"ready": True, "recovered": ["cd" * 16]}

    async def scenario():
        runtime = Runtime()
        manager = RunManager(LLMAdapter(None), runtime, tmp_path)
        from backend.llm import ConnectionConfig
        manager.create(ConnectionConfig(base_url="http://ollama", model="test"), [], run_id="cd" * 16)
        manager.get("cd" * 16).phase = "storage_error"
        runtime.recovering = True
        await manager.check_environment()
        assert manager.view("cd" * 16)["phase"] == "finished"
        assert manager.events_for("cd" * 16) == [{"event_type": "runtime_interrupted"}]
    asyncio.run(scenario())


def test_canceled_preflight_drains_before_shutdown_releases_queued_launch(tmp_path):
    from backend.llm import ConnectionConfig
    from backend.runs import RunError

    class Runtime:
        checking = threading.Event()
        release = threading.Event()
        checks = 0

        def prepare(self, store):
            self.checks += 1
            if self.checks == 1:
                self.checking.set()
                assert self.release.wait(5)
            return {"ready": True}

        def execute(self, run, emit):
            return {}, [], [], 0

        def remove(self, container_id):
            pass

    async def scenario():
        runtime = Runtime()
        manager = RunManager(LLMAdapter(None), runtime, tmp_path)
        check = asyncio.create_task(manager.check_environment())
        launch = None
        try:
            assert await asyncio.to_thread(runtime.checking.wait, 1)
            check.cancel()
            launch = asyncio.create_task(manager.checked_launch(
                ConnectionConfig(base_url="http://ollama", model="test"), [], {"body": "synthetic"}))
            await asyncio.sleep(0.03)
            assert not launch.done()
            closing = asyncio.create_task(manager.close())
            await asyncio.sleep(0)
            runtime.release.set()
            with pytest.raises(RunError, match="server_stopping"):
                await launch
            await closing
            assert runtime.checks == 1 and not manager.runs
        finally:
            runtime.release.set()
            await asyncio.gather(check, *([launch] if launch else []), return_exceptions=True)
            await manager.close()
    asyncio.run(scenario())


def test_management_rejects_work_network(monkeypatch):
    monkeypatch.setenv("WORK_NET_CIDR", "172.29.80.0/24")
    async def test():
        async with httpx.AsyncClient(trust_env=False) as provider:
            app = create_dashboard_app(RunManager(LLMAdapter(provider)))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(
                    app=app, client=("172.29.80.9", 1234)), base_url="http://control") as api:
                assert (await api.get("/runs")).status_code == 403
    asyncio.run(test())

