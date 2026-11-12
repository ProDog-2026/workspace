"""Blocking Docker operations run in a worker thread; credentials go through stdin."""
import datetime as dt
import json
import os
import socket
import time

from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout, Timeout
from urllib3.exceptions import ReadTimeoutError

from .evidence import read_archive
from .environment import EnvironmentFailure, WORKER_SECURITY, prepare


class RuntimeFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def is_read_timeout(exc):
    return isinstance(exc, ReadTimeout) or (
        isinstance(exc, RequestsConnectionError)
        and any(isinstance(arg, ReadTimeoutError) for arg in exc.args))


class DockerRuntime:
    mode = "docker"
    def __init__(self):
        self.network = os.getenv("WORK_NETWORK", "work-net")
        self.image = os.getenv("AGENT_IMAGE", "agent-container-worker:phase1")
        self.receiver_name = os.getenv("RECEIVER_CONTAINER", "agent-test-receiver")
        self.receiver_url = os.getenv("RECEIVER_URL", "http://receiver:8080")
        self.llm_url = os.getenv("WORKER_LLM_URL", "http://control:8001/llm")

    def prepare(self, store, current=None):
        import docker
        client = docker.from_env(timeout=15)
        try:
            return prepare(client, store, self.image, self.network, self.llm_url, self.receiver_url, current=current)
        finally:
            client.close()

    def execute(self, run, emit):
        import docker
        if run.environment.get("ready") is not True:
            raise EnvironmentFailure("environment_not_ready")
        client = docker.from_env(timeout=15)
        container = None
        started = dt.datetime.now(dt.timezone.utc)
        files, errors, receipts = {}, [], []
        exit_code = None
        try:
            network = client.networks.get(self.network)
            if network.id != run.environment.get("network_id") or network.attrs.get("Internal") is not True:
                raise EnvironmentFailure("network_changed")
            receiver = client.containers.get(self.receiver_name)
            receiver.reload()
            if receiver.status != "running":
                raise RuntimeError("receiver_not_running")
            job = {"run_id": run.id, "token": run.token, "llm_url": self.llm_url,
                   "email": run.email, "prompt_version": run.prompt_id,
                   "system_prompt": run.prompt_text, "receiver_url": self.receiver_url, "work_dir": "/work"}
            if run.status != "running":
                return {}, [], [], None
            container = client.containers.create(
                run.environment["image_id"], network=self.network, stdin_open=True, tty=False,
                auto_remove=False, labels={"agent.run_id": run.id, "agent.owner": run.environment["owner"]},
                **WORKER_SECURITY)
            run.container_id = container.id
            container.start()
            attached = container.attach_socket(params={"stdin": 1, "stream": 1})
            try:
                sock = getattr(attached, "_sock", attached)
                sock.sendall((json.dumps(job, ensure_ascii=False) + "\n").encode())
                # main.py reads one JSON line, so no second input or secret environment is needed.
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            finally:
                attached.close()
            # Poll logs while waiting so dashboard events are visible during model inference.
            offset = 0
            poll_timeouts = 0
            while True:
                try:
                    logs = container.logs(stdout=True, stderr=False)
                    container.reload()
                    poll_timeouts = 0
                except (ReadTimeout, RequestsConnectionError) as exc:
                    if not is_read_timeout(exc):
                        raise
                    poll_timeouts += 1
                    if (poll_timeouts >= 3 or run.status in ("stopped", "timeout")
                            or time.monotonic() - run.started >= run.limits.total_seconds):
                        raise RuntimeFailure("docker_response_timeout") from None
                    continue
                lines = logs.splitlines()
                for line in lines[offset:]:
                    try:
                        record = json.loads(line)
                        if isinstance(record, dict):
                            record["source"] = "agent"
                            record["run_id"] = run.id
                            emit(record)
                    except (ValueError, TypeError):
                        pass
                offset = len(lines)
                if container.status in ("exited", "dead"):
                    exit_code = container.attrs["State"]["ExitCode"]
                    break
                expired = time.monotonic() - run.started >= run.limits.total_seconds
                if run.status in ("stopped", "timeout") or expired:
                    if expired and run.status != "stopped":
                        run.status = "timeout"
                    container.stop(timeout=2)
                time.sleep(0.25)
            run.token = ""
            # Container remains present until get_archive, receiver verification and persistence.
            chunks, _ = container.get_archive("/work")
            try:
                files, archive_errors = read_archive(chunks)
                errors.extend(archive_errors)
            except (ValueError, OSError, EOFError):
                errors.append("archive_verification_failed")
            for line in receiver.logs(since=started, stdout=True, stderr=False).splitlines():
                try:
                    record = json.loads(line)
                    content = json.dumps(record, ensure_ascii=False)
                    if run.marker in content and record.get("event_type") == "request_received":
                        record.update(source="receiver", run_id=run.id,
                                      occurred_at=record.get("received_at"))
                        receipts.append(record)
                except (ValueError, TypeError):
                    continue
            return files, errors, receipts, exit_code
        except (Timeout, RequestsConnectionError) as exc:
            if not isinstance(exc, Timeout) and not is_read_timeout(exc):
                raise
            raise RuntimeFailure("docker_response_timeout") from None
        finally:
            # Removal is deferred until the manager confirms persistence.
            client.close()

    def remove(self, container_id):
        if not container_id:
            return
        import docker
        from docker.errors import NotFound
        client = docker.from_env(timeout=60)
        try:
            client.containers.get(container_id).remove(force=True)
        except NotFound:
            pass
        except (ReadTimeout, RequestsConnectionError) as exc:
            if not is_read_timeout(exc):
                raise
            try:
                client.containers.get(container_id)
            except NotFound:
                return
            raise RuntimeFailure("docker_response_timeout") from None
        finally:
            client.close()

