"""Recover only owned workers and test the same image/network used for analysis."""
import datetime as dt
import json
import uuid
from urllib.parse import urlsplit

from docker.errors import NotFound

from .evidence import event, file_events, read_archive
from .rules import evaluate

WORKER_SECURITY = {"cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
                   "mem_limit": "256m", "pids_limit": 64}


class EnvironmentFailure(RuntimeError):
    pass


def interrupted_result(run_id, container, records, files, errors, previous=None):
    return {"run_id": run_id,
            "created_at": container.attrs.get("Created") if container else dt.datetime.now(dt.timezone.utc).isoformat(),
            "subject": "", "connection": {}, "calls": None, "limits": {}, "elapsed_seconds": None,
            "runtime_mode": "docker", **(previous or {}), **evaluate(records, "interrupted"),
            "status": "interrupted", "phase": "finished", "recovery_required": False,
            "container_id": container.id if container else None, "summary": files.get("summary.txt", ""),
            "verification_status": "failed", "verification_errors": errors}


def recover_reservations(store, current=None):
    recovered = []
    for path in store.root.glob("*/request.json"):
        run_id = path.parent.name
        if run_id == current:
            continue
        directory = store.directory(run_id)
        reservation = json.loads(path.read_text(encoding="utf-8"))
        digest = reservation.get("request_hash")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise EnvironmentFailure("invalid_reservation")
        if (directory / "result.json").exists():
            store.load(run_id)
            continue
        # Absence is not proof of non-execution. Keep the idempotency tombstone.
        records = store.events(run_id) if (directory / "events.jsonl").exists() else []
        summary = (directory / "summary.txt").read_text(encoding="utf-8") if (directory / "summary.txt").exists() else ""
        records.append(event(run_id, "manager", "runtime_interrupted", error="execution_outcome_unknown"))
        result = interrupted_result(run_id, None, records, {"summary.txt": summary}, ["execution_outcome_unknown"])
        store.save(run_id, records, result, summary)
        recovered.append(run_id)
    return recovered


def recover_workers(client, store, network, owner):
    recovered = []
    for container in client.containers.list(all=True, filters={"label": "agent.run_id"}):
        labels = container.labels
        run_id = labels.get("agent.run_id", "")
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        owned = labels.get("agent.owner") == owner
        if not owned:
            if network not in networks:
                continue
            # Older workers can be adopted only through an exact persisted ID match.
            try:
                owned = not labels.get("agent.owner") and store.load(run_id).get("container_id") == container.id
            except (OSError, ValueError):
                owned = False
            if not owned:
                raise EnvironmentFailure("unknown_worker")
        container.reload()
        if container.status not in ("exited", "dead", "created"):
            container.stop(timeout=2)
            container.reload()
        if container.status not in ("exited", "dead", "created"):
            raise EnvironmentFailure("worker_stop_failed")
        if not labels.get("agent.diagnostic"):
            directory = store.directory(run_id)
            capture = True
            saved = None
            if (directory / "result.json").exists():
                # A corrupt/incomplete archive must not authorize evidence deletion.
                saved = store.load(run_id)
                late_worker = saved.get("container_id") is None and "execution_outcome_unknown" in saved.get("verification_errors", [])
                if saved.get("container_id") != container.id and not late_worker:
                    raise EnvironmentFailure("evidence_container_mismatch")
                store.events(run_id)
                capture = late_worker or saved.get("recovery_required") is True
            if capture:
                files, errors = read_archive(container.get_archive("/work")[0])
                records = store.events(run_id) if (directory / "events.jsonl").exists() else []
                metadata = {"run_id", "source", "event_id", "occurred_at"}
                persisted = {json.dumps({k: v for k, v in r.items() if k not in metadata}, sort_keys=True)
                             for r in records if r.get("source") == "agent"}
                logs = container.logs(stdout=True, stderr=False).splitlines()
                for line in logs:
                    try:
                        data = json.loads(line)
                        kind = data.pop("event_type")
                        for key in ("run_id", "source", "event_id", "occurred_at"):
                            data.pop(key, None)
                        if json.dumps({"event_type": kind, **data}, sort_keys=True) not in persisted:
                            records.append(event(run_id, "agent", kind, **data))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        errors.append("recovered_log_incomplete")
                if not any(e.get("event_type") == "file_verified" for e in records):
                    records += file_events(run_id, files)
                records.append(event(run_id, "manager", "runtime_interrupted"))
                result = interrupted_result(run_id, container, records, files,
                                            ["controller_interrupted", *errors], previous=saved)
                store.save(run_id, records, result, result["summary"])
        try:
            container.remove(force=True)
        except NotFound:
            pass
        recovered.append(run_id)
    return recovered


NETWORK_PROBE = r'''
import json, os, pathlib, socket, sys
targets = json.loads(sys.argv[1])
checks = {"non_root": os.getuid() != 0,
          "docker_socket_hidden": not pathlib.Path('/var/run/docker.sock').exists(),
          "workspace_clean": not any(pathlib.Path('/work').iterdir())}
path = pathlib.Path('/work/prodog-diagnostic.txt')
path.write_text('synthetic')
checks['workspace_writable'] = path.read_text() == 'synthetic'
for name, host, port, allowed in targets:
    try:
        with socket.create_connection((host, port), timeout=2):
            connected = True
    except socket.gaierror:
        checks[name] = False  # Failed DNS resolution does not prove isolation.
        continue
    except (ConnectionRefusedError, TimeoutError, OSError):
        connected = False
    checks[name] = connected == allowed
print(json.dumps(checks))
'''


def check_network(client, image, network, owner, llm_url, receiver_url):
    worker, receiver = urlsplit(llm_url), urlsplit(receiver_url)
    targets = [["llm_gateway", worker.hostname, worker.port or 80, True],
               ["receiver", receiver.hostname, receiver.port or 80, True],
               ["management_blocked", worker.hostname, 8000, False],
               ["external_blocked", "1.1.1.1", 443, False]]
    control = client.containers.get("agent-container-control-1")
    for name, info in control.attrs["NetworkSettings"]["Networks"].items():
        if name != network and info.get("IPAddress"):
            targets.append(["management_other_interface_blocked", info["IPAddress"], 8000, False])
    container = None
    try:
        container = client.containers.create(
            image, command=["python", "-I", "-c", NETWORK_PROBE, json.dumps(targets)],
            network=network, auto_remove=False,
            labels={"agent.owner": owner, "agent.run_id": uuid.uuid4().hex,
                    "agent.diagnostic": "network"}, **WORKER_SECURITY)
        container.start()
        status = container.wait(timeout=20)
        container.reload()
        checks = json.loads(container.logs(stdout=True, stderr=False))
        expected = {row[0] for row in targets} | {
            "non_root", "docker_socket_hidden", "workspace_clean", "workspace_writable"}
        if not isinstance(checks, dict) or set(checks) != expected:
            raise EnvironmentFailure("invalid_diagnostic_result")
        checks["no_host_mounts"] = not container.attrs.get("Mounts")
        checks["only_work_network"] = set(container.attrs["NetworkSettings"]["Networks"]) == {network}
        return {"verified": status["StatusCode"] == 0 and all(value is True for value in checks.values()),
                "checks": checks}
    finally:
        if container is not None:
            container.remove(force=True)


def prepare(client, store, image, network, llm_url, receiver_url, current=None):
    report = {"ready": False, "error": None, "checks": {}, "recovered": [],
              "checked_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    stage = "runtime_recovery_failed"
    try:
        owner = store.owner_id()
        report["recovered"] = recover_workers(client, store, network, owner)
        report["recovered"] += recover_reservations(store, current=current)
        report["checks"]["cleanup"] = True
        stage = "isolation_check_failed"
        net = client.networks.get(network)
        artifact = client.images.get(image)
        report["checks"]["internal_network"] = net.attrs.get("Internal") is True
        report["checks"]["no_image_volumes"] = not artifact.attrs["Config"].get("Volumes")
        if not all(report["checks"].values()):
            raise EnvironmentFailure(stage)
        probe = check_network(client, artifact.id, network, owner, llm_url, receiver_url)
        report["checks"].update(probe.get("checks", {}))
        if probe.get("verified") is not True:
            raise EnvironmentFailure(stage)
        report.update(ready=True, image_id=artifact.id, network_id=net.id, owner=owner)
    except Exception as exc:  # noqa: BLE001 - Any preflight failure must block execution.
        report["error"] = str(exc) if isinstance(exc, EnvironmentFailure) else stage
    # Historical report only; every startup/run performs a fresh check.
    temporary = store.root / "environment.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    temporary.replace(store.root / "environment.json")
    return report
