import io
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent-container"))
from tools import Tools
from events import Events
from agent import run_agent

def test_tool_paths_and_urls(tmp_path):
    tools = Tools({"body": "mail"}, tmp_path, "http://receiver:8080")
    for path in ("../escape", "/absolute", "C:/host", "a/../../x", "x\\evil"):
        assert tools.execute("write_file", {"path": path, "content": "x"})[0] == "blocked"
    assert tools.execute("write_file", {"path": "ok.txt", "content": "yes"})[0] == "executed_reported"
    assert (tmp_path / "files/ok.txt").read_text() == "yes"
    for url in ("http://receiver:8080.evil/x", "http://evil/", "http://receiver:8081", "http://u@receiver:8080"):
        assert tools.execute("fetch_url", {"url": url})[0] == "blocked"
    assert tools.execute("send_email", {"recipient": "test@example.test", "subject": "", "body": ""})[0] == "simulated"

def test_symlink_rejected(tmp_path):
    (tmp_path / "files").mkdir()
    try:
        (tmp_path / "files/link").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not allow creating symlinks")
    assert Tools({}, tmp_path, "http://receiver").execute(
        "write_file", {"path": "link/escape", "content": "bad"})[0] == "blocked"

def test_agent_roundtrip_and_termination(tmp_path):
    sink = io.StringIO()
    job = {"run_id": "test", "token": "secret-token"}
    seen = []
    def request(payload):
        seen.append(json.loads(json.dumps(payload)))
        if len(seen) == 1:
            return {"run_status": "running", "tool_calls": [
                {"id": "1", "name": "save_summary", "arguments": {"summary": "done"}}]}
        assert payload["messages"][-1]["tool_call_id"] == "1"
        return {"run_status": "completed", "tool_calls": [], "text": "done"}
    assert run_agent(job, Tools({}, tmp_path, "http://receiver"),
                     Events("test", job["token"], sink), request) == "completed"
    assert "secret-token" not in sink.getvalue()
    assert (tmp_path / "summary.txt").read_text() == "done"

def test_agent_limit_never_executes_calls(tmp_path):
    sink = io.StringIO()
    result = run_agent({"run_id": "test"}, Tools({}, tmp_path, "http://receiver"),
                       Events("test", sink=sink), lambda payload: {
                           "run_status": "limit_reached", "tool_calls": [
                               {"id": "1", "name": "save_summary", "arguments": {"summary": "bad"}}]})
    assert result == "limit_reached"
    assert not (tmp_path / "summary.txt").exists()


def test_fetch_is_allowed_only_for_receiver_and_does_not_follow_redirect(tmp_path, monkeypatch):
    import httpx
    real_client = httpx.Client
    seen = []
    def transport(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://external.test/"})
    monkeypatch.setattr("tools.httpx.Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(transport), **kwargs))
    status, result = Tools({}, tmp_path, "http://receiver:8080").execute(
        "fetch_url", {"url": "http://receiver:8080/collect?marker=MK-test"})
    assert status == "executed_reported" and result["http_status"] == 302
    assert seen == ["http://receiver:8080/collect?marker=MK-test"]

