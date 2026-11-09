"""실행 한 건의 실제 데이터 흐름을 LangGraph StateGraph로 따라간다 (대시보드 흐름 패널용).

가짜 데이터는 없다. 각 노드는 실행이 남긴 실제 이벤트를 소비해 State를 채우고,
분기도 실제 이벤트(도구 호출 유무, final, phase, 실험 CSV 포함 여부)로 결정한다.
판정 노드는 rules.evaluate 를 다시 실행해 저장된 result.json 과 대조한다.

진행 중인 실행은 아직 도착하지 않은 이벤트에서 멈춘다. 그 노드가 '현재 노드'(waiting)가 되고,
이후 노드는 실행하지 않는다. 대시보드는 폴링할 때마다 다시 계산해 흐름이 앞으로 나아가게 한다.
"""
from __future__ import annotations

import copy
import csv
import operator
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from .rules import evaluate

FINAL_PHASES = ("finished", "storage_error")
STEP_EVENTS = {("manager", "llm_request"), ("manager", "llm_response"), ("agent", "llm_response"),
               ("agent", "tool_requested"), ("agent", "tool_result"), ("agent", "final")}

NODE_INFO = {
    "submit_request": ("실행 요청", "app.js · run_experiments.py → POST /runs"),
    "launch_run": ("실행 생성", "RunManager.launch: run_id·토큰·MARKER"),
    "spawn_container": ("작업 컨테이너", "DockerRuntime.execute: stdin job"),
    "llm_gateway": ("LLM 호출", "agent → :8001/llm → infer → Ollama"),
    "execute_tools": ("도구 실행", "agent-container/tools.py"),
    "agent_final": ("에이전트 종료", "agent.py final 이벤트"),
    "collect_evidence": ("증거 수집", "/work 아카이브 · receiver 로그"),
    "evaluate_rules": ("판정", "rules.evaluate: 이탈 / 효과"),
    "persist_result": ("결과 저장", "EvidenceStore.save → data/runs"),
    "cleanup_container": ("컨테이너 정리", "저장 후 작업 컨테이너 제거"),
    "dashboard_view": ("대시보드 표시", "app.js 폴링 → presentation.js"),
    "record_experiment_row": ("실험 기록", "run_experiments.py → results.csv"),
}

# 패널 배치: (열, 행). 노드가 바뀌면 여기에 추가한다. 없으면 프론트엔드가 마지막 행에 둔다.
LAYOUT = {
    "__start__": (0, 0), "submit_request": (1, 0), "launch_run": (2, 0), "spawn_container": (3, 0),
    "llm_gateway": (3, 1), "agent_final": (2, 1), "collect_evidence": (1, 1), "evaluate_rules": (0, 1),
    "execute_tools": (3, 2), "dashboard_view": (2, 2), "cleanup_container": (1, 2), "persist_result": (0, 2),
    "record_experiment_row": (2, 3), "__end__": (1, 3),
}


# ── 이벤트 공급원 ──────────────────────────────────────────────────────
class Feed:
    """한 실행의 이벤트 스냅숏. 노드가 조건에 맞는 다음 이벤트를 소비한다.

    manager 이벤트(동기 기록)와 agent 이벤트(컨테이너 로그 폴링으로 늦게 도착)가 섞여 있으므로
    파일 순서가 아니라 출처·종류·tool_call_id 로 짝을 맞춘다.
    """

    def __init__(self, events, run, result, experiments):
        self.events, self.run, self.result, self.experiments = list(events), run or {}, result, experiments or {}
        self.used: set[str] = set()

    def done(self):
        return self.run.get("phase") in FINAL_PHASES

    def peek(self, pred):
        return next((e for e in self.events if e.get("event_id") not in self.used and pred(e)), None)

    def take(self, pred):
        hit = self.peek(pred)
        if hit:
            self.used.add(hit["event_id"])
        return hit


def is_(source, kind, **match):
    return lambda e: (e.get("source") == source and e.get("event_type") == kind
                      and all(e.get(k) == v for k, v in match.items()))


def experiment_rows(root):
    rows = {}
    if not root:
        return rows
    for path in sorted(Path(root).glob("*/results.csv")):
        try:
            with path.open(encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if row.get("run_id"):
                        rows[row["run_id"]] = row | {"experiment": path.parent.name}
        except (OSError, UnicodeDecodeError, csv.Error):
            continue
    return rows


# ── State ─────────────────────────────────────────────────────────────
class FlowState(TypedDict, total=False):
    run_id: str
    path: Annotated[list[str], operator.add]
    anomalies: Annotated[list[str], operator.add]
    waiting: str | None
    request: dict
    container_id: str | None
    llm_calls: list[dict]
    last_tool_calls: list[dict]
    tools: list[dict]
    tool_blocked_limit: bool
    final: dict | None
    evidence: dict
    evaluation: dict
    phase: str | None
    experiment: dict | None


def node(fn):
    """대기 중이면 아무것도 하지 않는다. 일을 한 노드만 path 에 남는다."""
    def wrapped(state: FlowState, config):
        if state.get("waiting"):
            return {}
        out = fn(state, config["configurable"]["feed"])
        out.setdefault("path", [fn.__name__])
        return out
    wrapped.__name__ = fn.__name__
    return wrapped


def wait(name, reason):
    return {"path": [name], "waiting": reason}


@node
def submit_request(state, feed):
    run = feed.run
    return {"run_id": run.get("run_id"), "request": {
        "subject": run.get("subject"), "model": (run.get("connection") or {}).get("model"),
        "limits": run.get("limits"), "prompt_version": run.get("prompt_version"),
        "temperature": run.get("temperature"), "created_at": run.get("created_at")}}


@node
def launch_run(state, feed):
    return {"phase": feed.run.get("phase")}


@node
def spawn_container(state, feed):
    cid = feed.run.get("container_id")
    if not cid and not feed.done() and not feed.peek(is_("manager", "llm_request")):
        return wait("spawn_container", "작업 컨테이너 생성 대기")
    return {"container_id": cid}


@node
def llm_gateway(state, feed):
    req = feed.take(is_("manager", "llm_request"))
    if req is None:
        if not feed.done() and not feed.peek(is_("agent", "final")):
            return wait("llm_gateway", "다음 LLM 요청 대기")
        # infer 가 한도로 거절하면 이벤트 없이 429 → 에이전트 종료로 이어진다.
        return {"last_tool_calls": []}
    resp = feed.take(is_("manager", "llm_response"))
    if resp is None:
        if not feed.done():
            out = wait("llm_gateway", f"LLM 응답 대기 (호출 #{req.get('call_number')})")
            return out | {"llm_calls": state.get("llm_calls", []) + [
                {"call_number": req.get("call_number"), "requested_at": req.get("occurred_at")}]}
        return {"last_tool_calls": [], "anomalies": [f"llm_request #{req.get('call_number')} 의 응답 없음"]}
    response = resp.get("response") or {}
    calls = response.get("tool_calls") or []
    return {"llm_calls": state.get("llm_calls", []) + [{
        "call_number": req.get("call_number"), "requested_at": req.get("occurred_at"),
        "responded_at": resp.get("occurred_at"), "finish_reason": response.get("finish_reason"),
        "status": response.get("status"), "tool_calls": [c.get("name") for c in calls]}],
        "last_tool_calls": calls}


@node
def execute_tools(state, feed):
    tools, anomalies, blocked = list(state.get("tools", [])), [], False
    feed.take(is_("agent", "llm_response"))  # 에이전트 측 응답 사본
    for call in state.get("last_tool_calls", []):
        cid = call.get("id")
        if not feed.peek(is_("agent", "tool_result", tool_call_id=cid)) and not feed.done():
            return wait("execute_tools", f"{call.get('name')} 실행 결과 대기") | {"tools": tools}
        req = feed.take(is_("agent", "tool_requested", tool_call_id=cid))
        res = feed.take(is_("agent", "tool_result", tool_call_id=cid))
        if res is None:
            anomalies.append(f"{call.get('name')}({cid}) tool_result 없음")
            continue
        tools.append({"name": res.get("name"), "status": res.get("status"),
                      "arguments": (req or {}).get("arguments"), "result": res.get("result")})
        if res.get("status") == "blocked" and (res.get("result") or {}).get("error") == "tool_limit":
            blocked = True
            break
    return {"tools": tools, "tool_blocked_limit": blocked, "anomalies": anomalies}


@node
def agent_final(state, feed):
    feed.take(is_("agent", "llm_response"))  # 마지막(도구 없는) 응답의 에이전트 측 사본
    final = feed.take(is_("agent", "final"))
    if final is None and not feed.done():
        return wait("agent_final", "에이전트 종료 대기")
    return {"final": {k: final.get(k) for k in ("completion", "limit_kind", "error", "occurred_at") if k in final}
            if final else None,
            "anomalies": [] if final else ["agent final 이벤트 없음 (중지·컨테이너 오류 가능)"]}


@node
def collect_evidence(state, feed):
    if not feed.done():
        return wait("collect_evidence", "아카이브·수신 기록 검증 중")
    files = [e.get("path") for e in feed.events if is_("verifier", "file_verified")(e)]
    receipts = [e.get("query") or e.get("path") for e in feed.events if is_("receiver", "request_received")(e)]
    errors = [e.get("error") for e in feed.events
              if e.get("source") == "manager" and e.get("event_type") in ("runtime_error", "cleanup_failed")]
    leftover = [f"{e['source']}:{e['event_type']}" for e in feed.events
                if e.get("event_id") not in feed.used and (e.get("source"), e.get("event_type")) in STEP_EVENTS]
    return {"evidence": {"files": files, "receiver": receipts, "errors": errors},
            "anomalies": [f"그래프가 소비하지 않은 이벤트: {', '.join(leftover)}"] if leftover else []}


@node
def evaluate_rules(state, feed):
    stored = feed.result or {}
    ev = evaluate(feed.events, stored.get("completion") or feed.run.get("status"))
    anomalies = [] if stored else ["result.json 없음"]
    for key in ("completion", "deviations", "effects"):
        if stored and ev.get(key) != stored.get(key):
            anomalies.append(f"rules.evaluate 재계산 {key} 가 result.json 과 다름")
    return {"evaluation": {k: ev[k] for k in ("completion", "incomplete", "deviations", "effects")},
            "anomalies": anomalies}


@node
def persist_result(state, feed):
    return {"phase": feed.run.get("phase")}


@node
def cleanup_container(state, feed):
    err = (feed.result or {}).get("cleanup_error")
    return {"anomalies": [f"cleanup_error: {err}"] if err else []}


@node
def dashboard_view(state, feed):
    return {}


@node
def record_experiment_row(state, feed):
    row = feed.experiments[state["run_id"]]
    anomalies = []
    if row.get("completion") != state["evaluation"]["completion"]:
        anomalies.append("results.csv completion 이 판정과 다름")
    if (row.get("deviation") == "True") != bool(state["evaluation"]["deviations"]):
        anomalies.append("results.csv deviation 이 판정과 다름")
    keep = ("experiment", "model", "case_id", "case_type", "repeat", "completion", "deviation", "effect", "missing_facts")
    return {"experiment": {k: row.get(k) for k in keep}, "anomalies": anomalies}


# ── 분기 (대기 중이면 루프에 빠지지 않도록 앞쪽 경로로 흘려보낸다) ─────────────
def after_llm(state):
    return "도구 호출" if state.get("last_tool_calls") and not state.get("waiting") else "종료/한도"


def after_tools(state):
    return "도구 한도" if state.get("tool_blocked_limit") or state.get("waiting") else "다음 LLM 호출"


def after_persist(state):
    return "저장 실패: 컨테이너 보존" if state.get("phase") == "storage_error" else "저장 완료"


def after_cleanup(state, config):
    return "실험 스크립트" if state.get("run_id") in config["configurable"]["feed"].experiments else "대시보드"


def build_graph():
    g = StateGraph(FlowState)
    for fn in (submit_request, launch_run, spawn_container, llm_gateway, execute_tools, agent_final,
               collect_evidence, evaluate_rules, persist_result, cleanup_container, dashboard_view,
               record_experiment_row):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "submit_request")
    g.add_edge("submit_request", "launch_run")
    g.add_edge("launch_run", "spawn_container")
    g.add_edge("spawn_container", "llm_gateway")
    g.add_conditional_edges("llm_gateway", after_llm, {"도구 호출": "execute_tools", "종료/한도": "agent_final"})
    g.add_conditional_edges("execute_tools", after_tools, {"다음 LLM 호출": "llm_gateway", "도구 한도": "agent_final"})
    g.add_edge("agent_final", "collect_evidence")
    g.add_edge("collect_evidence", "evaluate_rules")
    g.add_edge("evaluate_rules", "persist_result")
    g.add_conditional_edges("persist_result", after_persist,
                            {"저장 완료": "cleanup_container", "저장 실패: 컨테이너 보존": END})
    g.add_conditional_edges("cleanup_container", after_cleanup,
                            {"대시보드": "dashboard_view", "실험 스크립트": "record_experiment_row"})
    g.add_edge("dashboard_view", END)
    g.add_edge("record_experiment_row", END)
    return g.compile()


GRAPH = build_graph()


def structure():
    """LangGraph 컴파일 결과에서 노드·엣지를 그대로 꺼낸다."""
    drawn = GRAPH.get_graph()
    nodes = []
    for nid in drawn.nodes:
        title, where = NODE_INFO.get(nid, (nid.strip("_"), ""))
        col, row = LAYOUT.get(nid, (None, None))
        nodes.append({"id": nid, "title": title, "where": where, "col": col, "row": row,
                      "terminal": nid in ("__start__", "__end__")})
    edges = [{"source": e.source, "target": e.target, "label": e.data or "", "conditional": bool(e.conditional)}
             for e in drawn.edges]
    return nodes, edges


def trace(run, events, result=None, experiments=None):
    """실행 한 건을 그래프로 따라가 노드별 단계와 State 스냅숏을 돌려준다."""
    feed = Feed(events, run, result, experiments)
    config = {"configurable": {"feed": feed}, "recursion_limit": 300}
    state: dict = {}
    steps = []
    for update in GRAPH.stream({}, config, stream_mode="updates"):
        for name, delta in update.items():
            if not delta:
                continue
            for key, value in delta.items():
                state[key] = state.get(key, []) + value if key in ("path", "anomalies") else value
            steps.append({"index": len(steps) + 1, "node": name, "waiting": delta.get("waiting"),
                          "changed": sorted(k for k in delta if k != "path"),
                          "delta": copy.deepcopy({k: v for k, v in delta.items() if k != "path"}),
                          "state": copy.deepcopy({k: v for k, v in state.items() if k != "path"})})
    path = state.get("path", [])
    seq = ["__start__", *path] + ([] if state.get("waiting") else ["__end__"])
    visits = {}
    for n in path:
        visits[n] = visits.get(n, 0) + 1
    traversed = {}
    for a, b in zip(seq, seq[1:]):
        traversed[(a, b)] = traversed.get((a, b), 0) + 1
    nodes, edges = structure()
    for n in nodes:
        n["visits"] = visits.get(n["id"], 1 if n["id"] == "__start__" else 0)
        if n["id"] == "__end__":
            n["visits"] = 0 if state.get("waiting") else 1
    for e in edges:
        e["count"] = traversed.get((e["source"], e["target"]), 0)
    return {"run_id": run.get("run_id"), "phase": run.get("phase"), "status": run.get("status"),
            "done": feed.done(), "waiting": state.get("waiting"),
            "current": path[-1] if path and state.get("waiting") else None,
            "path": path, "anomalies": state.get("anomalies", []),
            "nodes": nodes, "edges": edges, "steps": steps}
