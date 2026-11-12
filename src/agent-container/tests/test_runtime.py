import json
import types
import pytest
from backend.runtime import DockerRuntime
from backend.runs import RunManager
from backend.llm import LLMAdapter, ConnectionConfig

@pytest.mark.parametrize("log_timeouts", [0, 1, 3])
@pytest.mark.parametrize("wrapped", [False, True])
def test_runtime_keeps_container_and_passes_token_only_through_stdin(monkeypatch, log_timeouts, wrapped):
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
        reads = 0
        def logs(self,**kw):
            from requests.exceptions import ReadTimeout, ConnectionError
            from urllib3.exceptions import ReadTimeoutError
            self.reads += 1
            if self.reads <= log_timeouts:
                if wrapped:
                    raise ConnectionError(ReadTimeoutError(None, "/logs", "SYNTHETIC_DOCKER_BODY"))
                raise ReadTimeout("SYNTHETIC_DOCKER_BODY")
            return b'{"event_type":"final","completion":"completed"}\n'
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
        networks=types.SimpleNamespace(get=lambda name:types.SimpleNamespace(id="network-id",attrs={"Internal":True})),close=lambda:None)
    monkeypatch.setattr(docker,"from_env",lambda **kw:client)
    manager=RunManager(LLMAdapter(None))
    created=manager.create(ConnectionConfig(base_url="http://ollama",model="test"),[])
    run=manager.get(created["run_id"]);run.email={"body":"test"};run.marker="MK-test"
    run.environment={"ready":True,"network_id":"network-id","image_id":"sha256:tested-image","owner":"synthetic-owner"}
    events=[]
    if log_timeouts == 3:
        from backend.runtime import RuntimeFailure
        with pytest.raises(RuntimeFailure, match="docker_response_timeout"):
            DockerRuntime().execute(run, events.append)
        assert not c.removed
        return
    files,errors,receipts,code=DockerRuntime().execute(run,events.append)
    assert files["summary.txt"]=="ok" and not errors and code==0
    assert json.loads(sent[0])["token"]==created["execution_token"]
    assert not c.removed


def test_remove_already_absent_container_is_success(monkeypatch):
    from docker.errors import NotFound
    import docker
    closed = []
    def absent(name):
        raise NotFound("synthetic absent")
    client = types.SimpleNamespace(containers=types.SimpleNamespace(get=absent),
                                   close=lambda: closed.append(True))
    monkeypatch.setattr(docker, "from_env", lambda **kwargs: client)
    DockerRuntime().remove("synthetic-already-removed")
    assert closed == [True]


def test_connection_failure_is_not_a_read_timeout():
    from requests.exceptions import ConnectionError
    from backend.runtime import is_read_timeout
    assert not is_read_timeout(ConnectionError("synthetic connection refused"))

