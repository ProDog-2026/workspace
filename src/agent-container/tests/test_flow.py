import asyncio
import copy
import uuid
from pathlib import Path

import httpx

from backend.api import create_dashboard_app
from backend.flow import trace
from backend.llm import LLMAdapter
from backend.rules import evaluate
from backend.runs import RunManager

RUN_ID = "a" * 32


def ev(source, kind, **data):
    return {"event_id": uuid.uuid4().hex, "run_id": RUN_ID, "source": source, "event_type": kind,
            "occurred_at": "2026-11-08T00:00:00+00:00", **data}


def response(*calls, finish="tool_calls"):
    return {"text": "", "finish_reason": finish if calls else "stop", "status": "ok",
            "tool_calls": [{"id": f"c-{name}", "name": name, "arguments": {}} for name in calls]}


def real_like_events(tool_steps=(("read_email",), ("save_summary",)), blocked=False):
    """실제 events.jsonl 순서를 재현한다: req1, resp1, req2, agent(1단계)…, resp2, req3, agent(2단계)…

    manager 이벤트는 동기 기록이고 agent 이벤트는 컨테이너 로그 폴링으로 늦게 도착한다.
    """
    def agent_step(calls):
        out = [ev("agent", "llm_response", response=response(*calls))]
        for name in calls:
            status, result = ("blocked", {"error": "tool_limit"}) if blocked else ("executed_reported", {})
            out += [ev("agent", "tool_requested", tool_call_id=f"c-{name}", name=name, arguments={}),
                    ev("agent", "tool_result", tool_call_id=f"c-{name}", name=name, status=status, result=result)]
        return out

    steps = list(tool_steps)
    events = [ev("manager", "llm_request", call_number=1), ev("manager", "llm_response", response=response(*steps[0]))]
    for n, calls in enumerate(steps, start=1):
        if blocked:
            return events + agent_step(calls) + [ev("agent", "final", completion="limit_reached", limit_kind="tool_calls")]
        nxt = steps[n] if n < len(steps) else ()
        events += [ev("manager", "llm_request", call_number=n + 1)] + agent_step(calls) + \
                  [ev("manager", "llm_response", response=response(*nxt))]
    return events + [ev("agent", "llm_response", response=response()), ev("agent", "final", completion="completed"),
                     ev("verifier", "file_verified", path="summary.txt", sha256="0", bytes=1)]


def finished(events, completion="completed", phase="finished"):
    run = {"run_id": RUN_ID, "status": completion, "phase": phase, "subject": "회의",
           "connection": {"model": "qwen3.5:9b"}, "container_id": "c" * 12, "created_at": "2026-11-08"}
    result = run | evaluate(events, completion) | {"summary": "요약"}
    return run, result


def test_finished_run_follows_real_events_without_anomalies():
    events = real_like_events()
    run, result = finished(events)
    flow = trace(run, events, result, {})
    assert flow["path"] == ["submit_request", "launch_run", "spawn_container", "llm_gateway", "execute_tools",
                            "llm_gateway", "execute_tools", "llm_gateway", "agent_final", "collect_evidence",
                            "evaluate_rules", "persist_result", "cleanup_container", "dashboard_view"]
    assert flow["anomalies"] == [] and flow["current"] is None and flow["done"]
    edges = {(e["source"], e["target"]): e["count"] for e in flow["edges"]}
    assert edges[("llm_gateway", "execute_tools")] == 2 and edges[("execute_tools", "llm_gateway")] == 2
    assert edges[("execute_tools", "agent_final")] == 0
    assert {n["id"]: n["visits"] for n in flow["nodes"]}["llm_gateway"] == 3
    # 각 단계는 그 시점의 State 스냅숏을 가진다
    tools_step = [s for s in flow["steps"] if s["node"] == "execute_tools"][-1]
    assert [t["name"] for t in tools_step["state"]["tools"]] == ["read_email", "save_summary"]


def test_in_progress_run_stops_at_the_node_waiting_for_its_events():
    events = real_like_events()
    run, _ = finished(events)
    seen = []
    for k in range(len(events)):
        flow = trace(run | {"phase": "executing", "status": "running"}, events[:k], None, {})
        assert flow["anomalies"] == [] and flow["waiting"]
        assert "__end__" not in flow["path"]
        seen.append(flow["current"])
    assert seen[0] == "llm_gateway" and seen[-1] == "collect_evidence"
    assert "execute_tools" in seen and "agent_final" in seen


def test_tool_limit_storage_error_and_experiment_branches():
    events = real_like_events(tool_steps=(("read_email",),), blocked=True)
    run, result = finished(events, "limit_reached")
    flow = trace(run, events, result, {RUN_ID: {"run_id": RUN_ID, "completion": "limit_reached", "deviation": "False"}})
    assert ("execute_tools", "agent_final") in {(a, b) for a, b in zip(flow["path"], flow["path"][1:])}
    assert flow["path"][-1] == "record_experiment_row" and flow["anomalies"] == []

    events = real_like_events()
    run, result = finished(events, phase="storage_error")
    flow = trace(run, events, result, {})
    assert flow["path"][-1] == "persist_result"


def test_mismatches_between_events_and_result_are_reported():
    events = real_like_events()
    run, result = finished(events)
    tampered = copy.deepcopy(result) | {"deviations": [{"tool": "send_email"}]}
    assert any("deviations" in a for a in trace(run, events, tampered, {})["anomalies"])
    missing = [e for e in events if e["event_type"] != "final"]
    assert any("final" in a for a in trace(run, missing, result, {})["anomalies"])
    extra = events + [ev("agent", "tool_requested", tool_call_id="ghost", name="send_email", arguments={})]
    assert any("소비하지 않은" in a for a in trace(run, extra, result, {})["anomalies"])


def test_flow_endpoint_and_panel_assets(tmp_path):
    async def test():
        async with httpx.AsyncClient(trust_env=False) as provider:
            manager = RunManager(LLMAdapter(provider), data_root=tmp_path / "runs")
            events = real_like_events()
            _, result = finished(events)
            manager.store.save(RUN_ID, events, result, "요약")
            app = create_dashboard_app(manager)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as client:
                flow = (await client.get(f"/runs/{RUN_ID}/flow")).json()
                assert flow["path"][-1] == "dashboard_view" and flow["anomalies"] == []
                assert len([n for n in flow["nodes"] if not n["terminal"]]) == 12
                assert (await client.get("/runs/" + "b" * 32 + "/flow")).status_code == 404
                assert (await client.get("/flow.js")).status_code == 200
                assert "flow-graph" in (await client.get("/")).text
    asyncio.run(test())


def test_flow_panel_does_not_render_untrusted_html():
    source = (Path(__file__).resolve().parents[1] / "control-dashboard/frontend/flow.js").read_text(encoding="utf-8")
    assert "innerHTML" not in source and "textContent" in source
