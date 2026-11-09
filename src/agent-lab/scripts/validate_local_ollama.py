"""Optional host-process smoke. Does NOT validate Docker isolation."""
import argparse
import asyncio
import datetime as dt
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"control-dashboard"))
from backend.api import create_dashboard_app
from backend.llm import LLMAdapter
from backend.runs import RunManager
from backend.worker_api import create_worker_app
from backend.main import EmbeddedServer


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


class HostSmokeRuntime:
    mode = "host_process_no_docker"
    def __init__(self,worker_port,receiver_port,receipts):
        self.worker_port,self.receiver_port,self.receipts=worker_port,receiver_port,receipts
        self.paths={}
    def execute(self,run,emit):
        directory=ROOT/"data/local-smoke-work"/run.id
        directory.mkdir(parents=True)
        self.paths[run.id]=directory
        receiver=f"http://127.0.0.1:{self.receiver_port}"
        email={k:v.replace("http://receiver:8080",receiver) for k,v in run.email.items()}
        job={"run_id":run.id,"token":run.token,"llm_url":f"http://127.0.0.1:{self.worker_port}/llm",
             "email":email,"receiver_url":receiver,"work_dir":str(directory)}
        child=subprocess.Popen([sys.executable,str(ROOT/"agent-container/main.py")],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
        def collect():
            for line in child.stdout:
                try:emit(json.loads(line))
                except ValueError:pass
        reader=threading.Thread(target=collect,daemon=True);reader.start()
        child.stdin.write((json.dumps(job)+"\n").encode());child.stdin.close()
        try:
            while child.poll() is None:
                if time.monotonic()-run.started>run.limits.total_seconds or run.status=="stopped":
                    if run.status!="stopped":run.status="timeout"
                    child.terminate()
                time.sleep(.1)
            reader.join(3)
            files={str(p.relative_to(directory)).replace("\\","/"):p.read_text(encoding="utf-8")
                   for p in directory.rglob("*") if p.is_file() and not p.is_symlink()}
            receipts=[dict(r,source="receiver") for r in self.receipts if run.marker in json.dumps(r)]
            return files,[],receipts,child.returncode
        finally:
            if child.poll() is None:child.terminate()
            child.wait(timeout=5)
    def remove(self,container_id):
        pass  # Fixture files intentionally remain as host-mode validation artifacts.


async def main():
    ports=set()
    while len(ports)<3:ports.add(free_port())
    dashboard,worker,receiver_port=ports
    receipts=[]
    receiver=subprocess.Popen([sys.executable,"-m","uvicorn","receiver.main:app",
        "--host","127.0.0.1","--port",str(receiver_port),"--no-access-log"],
        cwd=ROOT/"control-dashboard",stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
    def receive_logs():
        for line in receiver.stdout:
            try:receipts.append(json.loads(line))
            except ValueError:pass
    threading.Thread(target=receive_logs,daemon=True).start()
    async with httpx.AsyncClient(timeout=650,trust_env=False) as client:
        manager=RunManager(LLMAdapter(client),HostSmokeRuntime(worker,receiver_port,receipts),ROOT/"data/runs")
        servers=[EmbeddedServer(uvicorn.Config(app,host="127.0.0.1",port=port,access_log=False,log_level="error"))
                 for app,port in [(create_dashboard_app(manager),dashboard),(create_worker_app(manager),worker)]]
        tasks=[asyncio.create_task(s.serve()) for s in servers]
        try:
            for _ in range(100):
                if all(s.started for s in servers):
                    try:
                        await client.get(f"http://127.0.0.1:{receiver_port}/ready",timeout=1)
                        break
                    except httpx.HTTPError:pass
                await asyncio.sleep(.1)
            else:raise RuntimeError("smoke_servers_not_ready")
            output=ROOT/"data/experiments"/("local-smoke-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
            output.mkdir(parents=True)
            results=[]
            for case_id in ("normal-01","injection-03"):
                case=json.loads((ROOT/"tests/cases"/(case_id+".json")).read_text(encoding="utf-8"))
                res=await client.post(f"http://127.0.0.1:{dashboard}/runs",json={
                    "config":{"base_url":"http://127.0.0.1:11434","model":"qwen3.5:9b"},
                    "email":{"subject":case["subject"],"body":case["body"]},
                    "limits":{"max_calls":8,"max_tokens":8192,"total_seconds":600}})
                res.raise_for_status();run_id=res.json()["run_id"]
                print("Started",case_id,run_id,flush=True)
                await manager.get(run_id).lifecycle
                result=(await client.get(f"http://127.0.0.1:{dashboard}/runs/{run_id}/result")).json()
                result["validation_mode"]="host_process_no_docker"
                result["case_id"]=case_id
                results.append(result)
                print(case_id,result["completion"],"deviations",len(result["deviations"]),
                      "marker_received",bool(result["effects"]["marker_received"]),flush=True)
            (output/"results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
            print("Artifacts:",output,flush=True)
        finally:
            await manager.close()
            for s in servers:s.should_exit=True
            await asyncio.gather(*tasks,return_exceptions=True)
            receiver.terminate();receiver.wait(timeout=5)

if __name__=="__main__":
    asyncio.run(main())

