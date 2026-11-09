import json
import types
import pytest
from backend.runtime import DockerRuntime
from backend.runs import RunManager
from backend.llm import LLMAdapter, ConnectionConfig

def test_runtime_keeps_container_and_passes_token_only_through_stdin(monkeypatch):
    import io, tarfile, httpx
    docker=pytest.importorskip("docker")
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode="w") as archive:
        info=tarfile.TarInfo("work/summary.txt");info.size=2
        archive.addfile(info,io.BytesIO(b"ok"))
    sent=[]
    class Socket:
        def sendall(self,b):sent.append(b)
        def shutdown(self,*a):pass
        def close(self):pass
    class Container:
        id="container-id";status="exited"
        attrs={"State":{"ExitCode":0}}
        removed=False
        def start(self):pass
        def attach_socket(self,**kw):return Socket()
        def logs(self,**kw):return b'{"event_type":"final","completion":"completed"}\n'
        def reload(self):pass
        def get_archive(self,path):return [stream.getvalue()],{}
    c=Container()
    class Containers:
        def get(self,name):
            return types.SimpleNamespace(status="running",reload=lambda:None,logs=lambda **kw:b"")
        def create(self,*a,**kw):
            assert "environment" not in kw
            assert kw["network"]=="work-net" and kw["auto_remove"] is False
            return c
    client=types.SimpleNamespace(containers=Containers(),
        networks=types.SimpleNamespace(get=lambda name:None),close=lambda:None)
    monkeypatch.setattr(docker,"from_env",lambda **kw:client)
    manager=RunManager(LLMAdapter(None))
    created=manager.create(ConnectionConfig(base_url="http://ollama",model="test"),[])
    run=manager.get(created["run_id"]);run.email={"body":"test"};run.marker="MK-test"
    events=[]
    files,errors,receipts,code=DockerRuntime().execute(run,events.append)
    assert files["summary.txt"]=="ok" and not errors and code==0
    assert json.loads(sent[0])["token"]==created["execution_token"]
    assert not c.removed

