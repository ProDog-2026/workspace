import asyncio
import io
import json
import tarfile

import httpx
from backend.evidence import read_archive
from backend.llm import LLMAdapter
from backend.runs import RunManager
from backend.api import create_dashboard_app


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


def test_management_rejects_work_network(monkeypatch):
    monkeypatch.setenv("WORK_NET_CIDR", "172.29.80.0/24")
    async def test():
        async with httpx.AsyncClient(trust_env=False) as provider:
            app = create_dashboard_app(RunManager(LLMAdapter(provider)))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(
                    app=app, client=("172.29.80.9", 1234)), base_url="http://control") as api:
                assert (await api.get("/runs")).status_code == 403
    asyncio.run(test())

