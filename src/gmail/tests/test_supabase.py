import json
import uuid

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_scan import artifact

from prodog_gmail.app import create_app
from prodog_gmail.config import Settings
from prodog_gmail.supabase import fetch_status

BASE = "http://127.0.0.1:8765"
USER = str(uuid.uuid4())
KEY = "service-role-secret-value"
BODY_FIELDS = {"raw", "text_plain", "text_html", "analysis_text"}
MAIL_COLUMNS = {
    "user_id", "connection_id", "gmail_id", "subject", "sender", "received_at",
    "body_hash", "attachments", "warnings", "truncated", "fetch_status",
}


class FakeSupabase:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        rows = json.loads(request.content) if request.content else None
        self.calls.append((request.method, table, rows, request))
        if self.fail:
            return httpx.Response(500, json={"message": f"echo {rows}"})
        if request.method == "DELETE":
            return httpx.Response(204)
        if table == "gmail_connections":
            return httpx.Response(201, json=[{"id": "conn-1"}])
        if table == "mail_messages":
            return httpx.Response(
                201,
                json=[{"id": f"mail-{i}", "gmail_id": r["gmail_id"]} for i, r in enumerate(rows)],
            )
        return httpx.Response(201, json=[])

    def rows(self, table):
        return [r for _, t, rows, _ in self.calls if t == table for r in (rows or [])]


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def make(fake, configured=True):
        extra = (
            {"supabase_url": "https://x.supabase.co", "supabase_service_key": KEY,
             "supabase_user_id": USER}
            if configured
            else {}
        )
        settings = Settings(
            _env_file=None,
            app_mode="demo",
            token_encryption_key=Fernet.generate_key().decode(),
            database_path=tmp_path / "t.db",
            **extra,
        )
        app = create_app(settings, transport=httpx.MockTransport(fake))
        client = TestClient(app, base_url=BASE)
        client.__enter__()
        clients.append(client)
        post(client, "/api/demo/connect")
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def post(client, path):
    csrf = client.get("/api/session").json()["csrf_token"]
    return client.post(path, headers={"Origin": BASE, "X-CSRF-Token": csrf})


@pytest.mark.parametrize(
    ("meta", "status"),
    [
        ({"body_hash": None}, "failed"),
        ({"body_hash": "h", "truncated": True, "warnings": [], "attachments": []}, "incomplete"),
        ({"body_hash": "h", "truncated": False, "warnings": ["w"], "attachments": []},
         "incomplete"),
        ({"body_hash": "h", "truncated": False, "warnings": [], "attachments": [{}]},
         "incomplete"),
        ({"body_hash": "h", "truncated": False, "warnings": [], "attachments": []}, "complete"),
    ],
)
def test_fetch_status(meta, status):
    assert fetch_status(meta) == status


def test_sync_writes_metadata_and_verdicts_without_bodies(make_client):
    fake = FakeSupabase()
    client = make_client(fake)
    client.app.state.detector = artifact(probability=0.9)
    response = post(client, "/api/sync")
    assert response.status_code == 200
    mails = fake.rows("mail_messages")
    assert response.json() == {"saved": len(mails)} and mails
    assert all(set(row) == MAIL_COLUMNS for row in mails)
    assert not BODY_FIELDS & {key for row in mails for key in row}
    assert {row["user_id"] for row in mails} == {USER}
    analysis = fake.rows("analysis_results")
    assert len(analysis) == len(mails)
    assert {(r["status"], r["verdict"], r["static_score"]) for r in analysis} == {
        ("succeeded", "danger", 90)
    }
    request = fake.calls[0][3]
    assert request.headers["apikey"] == KEY and "on_conflict=user_id%2Cemail" in str(request.url)


def test_sync_without_detector_still_saves_mails(make_client):
    fake = FakeSupabase()
    client = make_client(fake)
    assert post(client, "/api/sync").status_code == 200
    assert fake.rows("mail_messages") and not fake.rows("analysis_results")


def test_sync_requires_configuration(make_client):
    fake = FakeSupabase()
    client = make_client(fake, configured=False)
    response = post(client, "/api/sync")
    assert response.status_code == 503 and not fake.calls


def test_sync_requires_connected_account_and_does_not_enqueue_dynamic(make_client, monkeypatch):
    fake = FakeSupabase()
    client = make_client(fake)

    def forbidden(*_):
        pytest.fail("explicit Supabase sync must not enqueue dynamic analysis")

    monkeypatch.setattr(client.app.state.pipeline, "submit", forbidden)
    assert post(client, "/api/sync").status_code == 200
    assert fake.rows("mail_messages")
    assert post(client, "/api/logout").status_code == 200
    count = len(fake.calls)
    assert post(client, "/api/sync").status_code == 401
    assert len(fake.calls) == count


def test_supabase_failure_does_not_leak_rows_or_key(make_client):
    client = make_client(FakeSupabase(fail=True))
    response = post(client, "/api/sync")
    assert response.status_code == 502
    assert KEY not in response.text and "echo" not in response.text


def test_disconnect_deletes_supabase_connection(make_client):
    fake = FakeSupabase()
    client = make_client(fake)
    assert post(client, "/api/disconnect").status_code == 200
    method, table, _, request = fake.calls[-1]
    assert (method, table) == ("DELETE", "gmail_connections")
    assert f"user_id=eq.{USER}" in str(request.url)


def test_disconnect_keeps_session_when_supabase_fails(make_client):
    client = make_client(FakeSupabase(fail=True))
    assert post(client, "/api/disconnect").status_code == 502
    assert client.get("/api/session").json()["authenticated"] is True
