"""Run a diagnostic container on work-net after Compose is up."""
import json
import sys
from pathlib import Path

def main():
    import docker
    client = None
    container = None
    try:
        client = docker.from_env(timeout=5)
        client.ping()
        code = """import json,socket
results={}
for name,host,port in [('dashboard','control',8000),('worker','control',8001),('receiver','receiver',8080),('internet','1.1.1.1',443)]:
    try:
        with socket.create_connection((host,port),timeout=3): pass
        results[name]=True
    except OSError: results[name]=False
print(json.dumps(results))
"""
        container = client.containers.run("agent-container-worker:phase1",
            command=["python","-c",code],network="work-net",detach=True,auto_remove=False,
            labels={"agent.diagnostic":"network"},cap_drop=["ALL"])
        status=container.wait(timeout=30)
        result=json.loads(container.logs().decode())
        result["verified"]=status["StatusCode"]==0 and result=={
            "dashboard":False,"worker":True,"receiver":True,"internet":False}
    except Exception:
        result={"verified":False,"error":"Docker access unavailable or diagnostic failed"}
    finally:
        if container:container.remove(force=True)
        if client:client.close()
    path=Path(__file__).resolve().parents[1]/"data/network-check.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))
    return 0 if result.get("verified") else 1

if __name__=="__main__":
    sys.exit(main())

