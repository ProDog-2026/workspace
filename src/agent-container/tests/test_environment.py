import io
import json
import tarfile
from types import SimpleNamespace

import pytest
from backend.evidence import EvidenceStore

RUN_ID = "ab" * 16


class Worker:
    def __init__(self, owner, *, network="work-net", diagnostic=False):
        self.id = "synthetic-worker"
        self.labels = {"agent.owner": owner, "agent.run_id": RUN_ID}
        if diagnostic:
            self.labels["agent.diagnostic"] = "network"
        self.status = "running"
        self.attrs = {"NetworkSettings": {"Networks": {network: {}}},
                      "Created": "2026-11-09T00:00:00Z"}
        self.removed = False
        self.fail_remove = False

    def reload(self):
        pass

    def stop(self, timeout):
        self.status = "exited"

    def logs(self, **kwargs):
        return b'{"event_type":"final","completion":"completed"}\n'

    def get_archive(self, path):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            entry = tarfile.TarInfo("work/summary.txt")
            entry.size = 9
            archive.addfile(entry, io.BytesIO(b"synthetic"))
        return [stream.getvalue()], {}

    def remove(self, force):
        if self.fail_remove:
            raise OSError("synthetic removal failure")
        self.removed = True


def client_for(*workers):
    return SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: list(workers)))


def test_restart_saves_interrupted_evidence_before_removing_owned_worker(tmp_path):
    from backend.environment import recover_workers

    store = EvidenceStore(tmp_path)
    owner = store.owner_id()
    assert EvidenceStore(tmp_path).owner_id() == owner
    worker = Worker(owner)
    original = worker.remove

    def remove(force):
        result = store.load(RUN_ID)
        assert result["incomplete"] is True
        assert result["verification_status"] == "failed"
        assert result["summary"] == "synthetic"
        assert store.events(RUN_ID)
        original(force)

    worker.remove = remove
    recovered = recover_workers(client_for(worker), store, "work-net", owner)
    assert worker.status == "exited" and worker.removed
    assert recovered == [RUN_ID]
    assert store.load(RUN_ID)["completion"] == "interrupted"


def test_storage_failure_retains_stopped_worker_and_evidence(tmp_path, monkeypatch):
    from backend.environment import recover_workers

    store = EvidenceStore(tmp_path)
    worker = Worker(store.owner_id())

    def unavailable(*args):
        raise OSError("synthetic full disk")

    monkeypatch.setattr(store, "save", unavailable)
    with pytest.raises(OSError):
        recover_workers(client_for(worker), store, "work-net", store.owner_id())
    assert worker.status == "exited" and not worker.removed


def test_runtime_failure_record_still_requires_worker_evidence_recovery(tmp_path):
    from backend.environment import recover_workers
    store = EvidenceStore(tmp_path)
    worker = Worker(store.owner_id())
    provenance = {"subject": "synthetic original", "connection": {"model": "test-model"},
                  "prompt_version": "test-prompt", "system_prompt": "synthetic instructions",
                  "limits": {"max_calls": 8}, "temperature": 0.1,
                  "error_code": "docker_response_timeout", "calls": 2}
    store.save(RUN_ID, [], {"run_id": RUN_ID, "container_id": worker.id,
                           "recovery_required": True, "summary": "", **provenance}, "")
    recover_workers(client_for(worker), store, "work-net", store.owner_id())
    assert store.load(RUN_ID)["summary"] == "synthetic"
    assert any(e["event_type"] == "final" for e in store.events(RUN_ID))
    assert worker.removed
    result = store.load(RUN_ID)
    assert {k: result[k] for k in provenance} == provenance
    assert result["recovery_required"] is False


def test_worker_recovery_preserves_events_saved_before_result_write_failed(tmp_path):
    from backend.environment import recover_workers

    store = EvidenceStore(tmp_path)
    directory = store.directory(RUN_ID)
    directory.mkdir()
    original = {"event_id": "surviving-manager-event", "event_type": "llm_request", "source": "manager"}
    (directory / "events.jsonl").write_text(json.dumps(original) + "\n")
    worker = Worker(store.owner_id())
    recover_workers(client_for(worker), store, "work-net", store.owner_id())
    assert original in store.events(RUN_ID)
    assert worker.removed


def test_foreign_workers_are_never_stopped_or_deleted(tmp_path):
    from backend.environment import EnvironmentFailure, recover_workers

    store = EvidenceStore(tmp_path)
    foreign = Worker("another-project")
    with pytest.raises(EnvironmentFailure, match="unknown_worker"):
        recover_workers(client_for(foreign), store, "work-net", store.owner_id())
    assert foreign.status == "running" and not foreign.removed
    outside = Worker("another-project", network="other-net")
    assert recover_workers(client_for(outside), store, "work-net", store.owner_id()) == []
    assert not outside.removed


def test_existing_result_survives_cleanup_retry(tmp_path):
    from backend.environment import recover_workers

    store = EvidenceStore(tmp_path)
    worker = Worker(store.owner_id())
    original = {"run_id": RUN_ID, "container_id": worker.id, "summary": "original",
                "phase": "finished", "cleanup_error": "container_cleanup_failed"}
    store.save(RUN_ID, [{"event_id": "original-event"}], original, "original")
    worker.fail_remove = True
    with pytest.raises(OSError):
        recover_workers(client_for(worker), store, "work-net", store.owner_id())
    assert not worker.removed and store.load(RUN_ID) == original
    worker.fail_remove = False
    recover_workers(client_for(worker), store, "work-net", store.owner_id())
    assert worker.removed and store.load(RUN_ID)["summary"] == "original"
    assert store.events(RUN_ID) == [{"event_id": "original-event"}]


def test_legacy_worker_requires_matching_persisted_container_id(tmp_path):
    from backend.environment import EnvironmentFailure, recover_workers

    store = EvidenceStore(tmp_path)
    worker = Worker(None)
    del worker.labels["agent.owner"]
    store.save(RUN_ID, [], {"run_id": RUN_ID, "container_id": "different"}, "")
    with pytest.raises(EnvironmentFailure, match="unknown_worker"):
        recover_workers(client_for(worker), store, "work-net", store.owner_id())
    assert not worker.removed
    store.save(RUN_ID, [], {"run_id": RUN_ID, "container_id": worker.id}, "")
    recover_workers(client_for(worker), store, "work-net", store.owner_id())
    assert worker.removed


def test_missing_worker_finishes_reservation_as_unknown_without_relaunch(tmp_path):
    from backend.environment import recover_reservations

    store = EvidenceStore(tmp_path)
    directory = store.directory(RUN_ID)
    directory.mkdir()
    reservation = json.dumps({"request_hash": "ef" * 32})
    (directory / "request.json").write_text(reservation)
    assert recover_reservations(store) == [RUN_ID]
    result = store.load(RUN_ID)
    assert result["incomplete"] is True and result["completion"] == "interrupted"
    assert result["verification_errors"] == ["execution_outcome_unknown"]
    assert (directory / "request.json").read_text() == reservation
    assert recover_reservations(store) == []


def test_current_reservation_is_not_mistaken_for_interrupted_work(tmp_path):
    from backend.environment import recover_reservations

    store = EvidenceStore(tmp_path)
    directory = store.directory(RUN_ID)
    directory.mkdir()
    (directory / "request.json").write_text(json.dumps({"request_hash": "ef" * 32}))
    assert recover_reservations(store, current=RUN_ID) == []
    assert not (directory / "result.json").exists()


def test_reservation_repair_preserves_partial_evidence(tmp_path):
    from backend.environment import recover_reservations

    store = EvidenceStore(tmp_path)
    directory = store.directory(RUN_ID)
    directory.mkdir()
    (directory / "request.json").write_text(json.dumps({"request_hash": "ef" * 32}))
    original = {"event_id": "surviving-event", "event_type": "llm_response", "response": "synthetic"}
    (directory / "events.jsonl").write_text(json.dumps(original) + "\n")
    (directory / "summary.txt").write_text("surviving summary")
    recover_reservations(store)
    assert store.events(RUN_ID)[0] == original
    assert store.load(RUN_ID)["summary"] == "surviving summary"
    assert (directory / "summary.txt").read_text() == "surviving summary"


def test_late_worker_merges_its_tool_events_with_unknown_reservation(tmp_path):
    from backend.environment import recover_reservations, recover_workers

    store = EvidenceStore(tmp_path)
    directory = store.directory(RUN_ID)
    directory.mkdir()
    (directory / "request.json").write_text(json.dumps({"request_hash": "ef" * 32}))
    recover_reservations(store)
    worker = Worker(store.owner_id())
    worker.logs = lambda **kwargs: b'{"event_type":"tool_result","name":"send_email","status":"simulated"}\n'
    recover_workers(client_for(worker), store, "work-net", store.owner_id())
    records = store.events(RUN_ID)
    assert any(e.get("error") == "execution_outcome_unknown" for e in records)
    assert any(e.get("name") == "send_email" and e.get("status") == "simulated" for e in records)
    assert worker.removed


def test_unreadable_partial_evidence_blocks_reservation_repair(tmp_path):
    from backend.environment import recover_reservations

    store = EvidenceStore(tmp_path)
    directory = store.directory(RUN_ID)
    directory.mkdir()
    (directory / "request.json").write_text(json.dumps({"request_hash": "ef" * 32}))
    (directory / "events.jsonl").write_text("incomplete JSON")
    with pytest.raises(ValueError):
        recover_reservations(store)
    assert (directory / "events.jsonl").read_text() == "incomplete JSON"
    assert not (directory / "result.json").exists()


@pytest.mark.parametrize("unsafe", ["network", "image", "probe", "cleanup"])
def test_failed_isolation_never_reports_ready(tmp_path, monkeypatch, unsafe):
    from backend import environment

    store = EvidenceStore(tmp_path)
    client = client_for()
    client.networks = SimpleNamespace(get=lambda name: SimpleNamespace(
        id="network-id", attrs={"Internal": unsafe != "network"}))
    client.images = SimpleNamespace(get=lambda name: SimpleNamespace(
        id="sha256:synthetic", attrs={"Config": {"Volumes": {"/shared": {}} if unsafe == "image" else None}}))

    def probe(*args):
        if unsafe == "cleanup":
            raise OSError("synthetic diagnostic cleanup failure")
        return {"verified": unsafe != "probe"}

    monkeypatch.setattr(environment, "check_network", probe)
    result = environment.prepare(client, store, "image", "work-net", "http://control:8001/llm", "http://receiver:8080")
    assert result["ready"] is False
    assert result["error"]
