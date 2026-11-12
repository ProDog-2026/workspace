import io
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent-container"))
from agent import run_agent
from events import Events
from tools import Tools, definitions
from backend.mock_tools import definitions as control_definitions
from backend.rules import evaluate


MOCK_FIELDS = {
    "search_emails": ("query",),
    "get_email": ("email_id",),
    "delete_email": ("email_id",),
    "modify_email": ("email_id", "change"),
    "create_draft": ("recipient", "subject", "body"),
    "search_contacts": ("query",),
    "create_email_filter": ("query", "action"),
    "search_calendar_events": ("query",),
    "create_calendar_event": ("title", "start_time", "end_time"),
    "update_calendar_event": ("event_id", "change"),
    "add_calendar_event_participants": ("event_id", "participants"),
    "search_cloud_files": ("query",),
    "read_cloud_file": ("file_id",),
    "edit_cloud_file": ("file_id", "content"),
    "share_cloud_file": ("file_id", "recipient", "permission"),
    "delete_cloud_file": ("file_id",),
    "search_local_files": ("query",),
    "read_local_file": ("path",),
    "edit_local_file": ("path", "content"),
    "delete_local_file": ("path",),
    "run_command": ("command",),
    "manage_process": ("action", "process"),
    "web_search": ("query",),
    "read_webpage": ("url",),
    "browser_action": ("action", "target", "value"),
    "http_request": ("method", "url", "body"),
    "read_chat_messages": ("channel",),
    "send_chat_message": ("channel", "message"),
    "read_memory": ("query",),
    "write_memory": ("key", "value"),
    "schedule_task": ("task", "when"),
    "manage_skill": ("name", "content"),
    "delegate_task": ("task", "agent"),
}


def test_all_tools_are_registered_once_with_explicit_arguments():
    schemas = [entry["function"] for entry in definitions()]
    names = [entry["name"] for entry in schemas]
    assert len(names) == len(set(names)) == 38
    assert set(names) == set(MOCK_FIELDS) | {"read_email", "save_summary", "send_email", "write_file", "fetch_url"}
    for entry in schemas:
        if entry["name"] in MOCK_FIELDS:
            params = entry["parameters"]
            assert params["required"] == list(MOCK_FIELDS[entry["name"]])
            assert set(params["properties"]) == set(params["required"])
            assert all(prop["type"] == "string" for prop in params["properties"].values())
            assert params["additionalProperties"] is False


def test_control_and_worker_receive_identical_schemas():
    original = Path(__file__).resolve().parents[1] / "control-dashboard/backend/tool_definitions.json"
    assert definitions() == json.loads(original.read_text(encoding="utf-8")) + control_definitions()


@pytest.mark.parametrize("name,fields", MOCK_FIELDS.items())
def test_mock_calls_have_no_external_or_file_effects(tmp_path, monkeypatch, name, fields):
    def forbidden(*args, **kwargs):
        pytest.fail("a mock tool attempted a real process or network operation")

    monkeypatch.setattr("tools.httpx.Client", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    worker = Tools({"body": "synthetic"}, tmp_path / "work", "http://receiver:8080")
    sentinel = tmp_path / "work" / "existing.txt"
    sentinel.write_text("do not modify", encoding="utf-8")
    args = {field: "../../existing.txt; https://example.test" for field in fields}

    status, result = worker.execute(name, args)

    assert status == "simulated"
    assert result["simulated"] is True and result["performed"] is False
    assert sentinel.read_text(encoding="utf-8") == "do not modify"
    assert sorted(p.name for p in sentinel.parent.iterdir()) == ["existing.txt"]
    assert worker.execute(name, args | {"unexpected": "x"})[0] == "blocked"
    assert worker.execute(name, args | {fields[0]: 4})[0] == "blocked"


def test_mock_tool_request_and_result_are_recorded_for_the_agent(tmp_path):
    sink, payloads = io.StringIO(), []
    args = {"command": "echo synthetic"}

    def request(payload):
        payloads.append(payload)
        if len(payloads) == 1:
            return {"run_status": "running", "tool_calls": [
                {"id": "mock-call", "name": "run_command", "arguments": args}]}
        assert json.loads(payload["messages"][-1]["content"])["performed"] is False
        return {"run_status": "completed", "tool_calls": []}

    worker = Tools({}, tmp_path, "http://receiver:8080")
    assert run_agent({"run_id": "synthetic"}, worker, Events("synthetic", sink=sink), request) == "completed"
    events = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert any(e["event_type"] == "tool_requested" and e["name"] == "run_command" and e["arguments"] == args for e in events)
    assert any(e["event_type"] == "tool_result" and e["name"] == "run_command" and e["status"] == "simulated" for e in events)
    manager_event = {"source": "manager", "event_type": "llm_response", "event_id": "manager-1",
                     "response": {"tool_calls": [{"name": "run_command", "arguments": args}]}}
    result = evaluate([manager_event], "completed")
    assert result["deviations"] == [{"tool": "run_command", "arguments": args,
                                    "source": "manager", "event_id": "manager-1"}]
