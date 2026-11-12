"""대시보드 흐름 패널과 같은 LangGraph 추적(control-dashboard/backend/flow.py)을 터미널에서 실행한다.

  python dashboard_dataflow_graph.py --replay-all          # data/runs 전체: 경로별 개수와 불일치
  python dashboard_dataflow_graph.py --replay <run_id>     # 1건: 단계별 노드와 바뀐 State
  python dashboard_dataflow_graph.py --live <run_id>       # 실행 중인 대시보드 /runs/{id}/flow 를 폴링
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "control-dashboard"))
from backend.flow import experiment_rows, trace  # noqa: E402

RUNS = ROOT / "data/runs"


def load(run_id):
    d = RUNS / run_id
    result = json.loads((d / "result.json").read_text(encoding="utf-8"))
    events = [json.loads(x) for x in (d / "events.jsonl").read_text(encoding="utf-8").splitlines() if x]
    return result, events


def print_steps(flow, start=0):
    for s in flow["steps"][start:]:
        changed = ", ".join(s["changed"]) or "-"
        print(f"{s['index']:>3}. {s['node']:<22} {('대기: ' + s['waiting']) if s['waiting'] else changed}")


def main():
    p = argparse.ArgumentParser()
    m = p.add_mutually_exclusive_group(required=True)
    m.add_argument("--replay-all", action="store_true")
    m.add_argument("--replay", metavar="RUN_ID")
    m.add_argument("--live", metavar="RUN_ID")
    p.add_argument("--api", default="http://127.0.0.1:8000")
    a = p.parse_args()
    experiments = experiment_rows(ROOT / "data/experiments")

    if a.replay_all:
        flows = [trace(*(lambda r, e: (r, e, r))(*load(d.name)), experiments)
                 for d in sorted(RUNS.iterdir()) if (d / "result.json").exists()]
        print(f"{len(flows)} runs, 불일치 {sum(bool(f['anomalies']) for f in flows)}건")
        for path, n in Counter(" → ".join(f["path"]) for f in flows).most_common():
            print(f"{n:>4}  {path}")
        for f in flows:
            for an in f["anomalies"]:
                print("  !", f["run_id"][:8], an)
    elif a.replay:
        result, events = load(a.replay)
        flow = trace(result, events, result, experiments)
        print_steps(flow)
        print("불일치:", flow["anomalies"] or "없음")
    else:
        shown = 0
        with httpx.Client(timeout=30, trust_env=False) as client:
            while True:
                flow = client.get(f"{a.api}/runs/{a.live}/flow").raise_for_status().json()
                # 대기 단계는 다음 폴링에서 다시 계산되므로 마지막 대기 단계는 다시 출력한다
                done_steps = [s for s in flow["steps"] if not s["waiting"]]
                print_steps({"steps": done_steps}, shown)
                shown = len(done_steps)
                if flow["done"]:
                    print("불일치:", flow["anomalies"] or "없음")
                    break
                print(f"    … 현재 노드 {flow['current']}: {flow['waiting']}", flush=True)
                time.sleep(1.5)


if __name__ == "__main__":
    main()
