"""Blocking Docker operations run in a worker thread; credentials go through stdin."""
import datetime as dt
import json
import os
import socket
import time

from .evidence import read_archive


class DockerRuntime:
    mode = "docker"
    def __init__(self):
        self.network = os.getenv("WORK_NETWORK", "work-net")
        self.image = os.getenv("AGENT_IMAGE", "agent-container-worker:phase1")
        self.receiver_name = os.getenv("RECEIVER_CONTAINER", "agent-test-receiver")
        self.receiver_url = os.getenv("RECEIVER_URL", "http://receiver:8080")
        self.llm_url = os.getenv("WORKER_LLM_URL", "http://control:8001/llm")

    def execute(self, run, emit):
        import docker
        client = docker.from_env(timeout=15)
        container = None
        started = dt.datetime.now(dt.timezone.utc)
        files, errors, receipts = {}, [], []
        exit_code = None
        try:
            client.networks.get(self.network)
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
                self.image, network=self.network, stdin_open=True, tty=False,
                auto_remove=False, labels={"agent.run_id": run.id},
                cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
                mem_limit="256m", pids_limit=64)
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
            while True:
                logs = container.logs(stdout=True, stderr=False)
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
                container.reload()
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
            except (ValueError, OSError, EOFError) as exc:
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
        finally:
            # Removal is deferred until the manager confirms persistence.
            client.close()

    def remove(self, container_id):
        if not container_id:
            return
        import docker
        client = docker.from_env(timeout=15)
        try:
            client.containers.get(container_id).remove(force=True)
        finally:
            client.close()

