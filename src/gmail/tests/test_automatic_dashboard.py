"""The collected-mail pipeline exports display metadata without raw execution content."""

import asyncio
import json
import time

from test_pipeline import configured as configured
from test_pipeline import post, wait_results


def test_flagged_collected_mail_exports_local_model_for_dashboard(configured):
    client, calls = configured
    assert post(client, "/api/mails?limit=3").status_code == 200
    rows = wait_results(client)
    flagged = next(row for row in rows if row["gmail_message_id"] == "demo-2")
    assert flagged["static"]["verdict"] == "danger"
    assert flagged["dynamic"]["model"] == "test-model"
    assert flagged["dynamic"]["provider"] == "ollama"
    assert flagged["dynamic"]["status"] == "completed"
    assert not {"mail", "events", "task", "_canary"}.intersection(flagged["dynamic"])
    assert len(calls["dynamic"]) == 1
    run = calls["dynamic"][0]
    subject = run["mail"].splitlines()[0].removeprefix("제목:").strip()
    listed = next(
        item for item in client.get("/api/experiments").json()["runs"] if item["id"] == run["id"]
    )
    assert listed["subject"] == subject
    assert "mail" not in listed
    assert client.get(f"/api/experiments/{run['id']}").json()["subject"] == subject
    assert all(
        row["dynamic"] is None and row["status"] == "skipped"
        for row in rows
        if row["static"]["verdict"] == "safe"
    )


def test_running_status_is_persisted_for_cloud_dashboard(configured, monkeypatch):
    client, _ = configured
    release = client.portal.call(asyncio.Event)

    async def running(self, record):
        record["status"] = "running"
        self.emit(record, "status", {"status": "running"})
        await release.wait()
        record["status"] = "completed"
        self.save(record)

    monkeypatch.setattr("prodog_gmail.experiments.Experiments.execute", running)
    try:
        assert post(client, "/api/mails?limit=3").status_code == 200
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            rows = client.get("/api/analysis?message_ids=demo-2").json()["analyses"]
            if rows and (rows[0].get("dynamic") or {}).get("status") == "running":
                break
            time.sleep(0.01)
        assert rows[0]["dynamic"]["status"] == "running"
        job = client.app.state.pipeline.job(rows[0]["id"])
        saved = json.loads(job["data_json"])
        assert saved["status"] == "running"
        assert saved["dynamic"]["model"] == "test-model"
    finally:
        client.portal.call(release.set)
