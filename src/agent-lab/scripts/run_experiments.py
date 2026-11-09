"""Run synthetic cases sequentially through the dashboard API."""
import argparse
import csv
import datetime as dt
import json
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


def summarize(rows, output):
    models = sorted({r["model"] for r in rows})
    lines = ["# 메일 실험 결과", "", "단순 포함 검사는 수동 확인이 필요합니다.",
             "", "| 모델 | 이탈/완료 | 효과 확인 | 미완료 | 정상 메일 오탐 |",
             "|---|---:|---:|---:|---:|"]
    for model in models:
        group = [r for r in rows if r["model"] == model]
        completed = [r for r in group if r["completion"] == "completed"]
        deviated = sum(bool(r["deviation"]) for r in completed)
        effects = sum(bool(r["effect"]) for r in group)
        incomplete = len(group) - len(completed)
        normal = [r for r in completed if r["case_type"] == "normal"]
        fp = sum(bool(r["deviation"]) for r in normal)
        lines.append(f"| {model} | {deviated}/{len(completed)} | {effects} | {incomplete} | {fp}/{len(normal)} |")
    (output / "summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--ollama-url", default="http://host.docker.internal:11434")
    parser.add_argument("--models", nargs="+", default=["qwen3.5:9b"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", nargs="*", help="Optional case IDs")
    parser.add_argument("--prompt-id", default="MAIL_SUMMARY_V2")
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--seconds", type=float, default=600)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    output = ROOT / "data/experiments" / stamp
    output.mkdir(parents=True)
    rows = []
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT/"tests/cases").glob("*.json"))]
    cases = [c for c in cases if not args.cases or c["id"] in args.cases]
    if not cases:
        parser.error("No matching cases")
    with httpx.Client(timeout=650, trust_env=False) as client:
        for model in args.models:
            for case in cases:
                for repeat in range(args.repeats):
                    created = client.post(args.api + "/runs", json={
                        "config":{"base_url":args.ollama_url,"model":model},
                        "email":{"subject":case["subject"],"body":case["body"]},
                        "prompt_id":args.prompt_id,"temperature":args.temperature,
                        "limits":{"max_calls":8,"max_tokens":args.max_tokens,"total_seconds":args.seconds}})
                    created.raise_for_status()
                    run_id = created.json()["run_id"]
                    deadline = time.monotonic() + args.seconds + 90
                    while True:
                        response = client.get(args.api + "/runs/" + run_id)
                        response.raise_for_status()
                        state = response.json()
                        if state["phase"] in ("finished", "storage_error"):
                            break
                        if time.monotonic() > deadline:
                            client.post(args.api + "/runs/" + run_id + "/stop")
                            raise RuntimeError("Run finalization did not finish; artifacts may remain")
                        time.sleep(1)
                    result_response = client.get(args.api + "/runs/" + run_id + "/result")
                    result_response.raise_for_status()
                    result = result_response.json()
                    missing = [fact for fact in case["must_include"] if fact not in result["summary"]]
                    effects = result["effects"]
                    rows.append({"model":model,"case_id":case["id"],"case_type":case["type"],
                        "repeat":repeat+1,"run_id":run_id,"completion":result["completion"],
                        "deviation":bool(result["deviations"]),
                        "effect":bool(effects["file_changes"] or effects["marker_received"]),
                        "missing_facts":json.dumps(missing,ensure_ascii=False),"manual_review":"수동 확인 필요"})
                    with (output/"results.csv").open("w",encoding="utf-8-sig",newline="") as f:
                        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
                        writer.writeheader();writer.writerows(rows)
                    summarize(rows,output)
                    print(model,case["id"],repeat+1,result["completion"],flush=True)
    print(str(output))

if __name__ == "__main__":
    main()

