"""Two real application sessions, synthetic providers, isolated temporary databases."""
import json
import uuid

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_app import BASE, FakeGoogle, callback, oauth_start, post

from prodog_gmail.app import COOKIE, create_app
from prodog_gmail.config import Settings


class Providers:
    def __init__(self):
        self.google = FakeGoogle()
        self.connections = {}
        self.writes = []
        self.fail_delete = False
        self.fail_auth = False

    def __call__(self, request):
        if request.url.host != "synthetic.supabase.co":
            return self.google(request)
        body = json.loads(request.content) if request.content else None
        if request.url.path == "/auth/v1/token":
            import base64
            claims = json.loads(base64.urlsafe_b64decode(
                body["id_token"].split('.')[1] + '=='))
            sub = claims["sub"]
            return httpx.Response(200, json={"user": {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, sub)),
                "identities": [{"provider": "google", "identity_data": {
                    "sub": "attacker" if self.fail_auth else sub}}],
            }})
        if request.method == "DELETE":
            if self.fail_delete:
                return httpx.Response(503)
            owner = request.url.params["user_id"].removeprefix("eq.")
            email = request.url.params["email"].removeprefix("eq.")
            self.connections.pop((owner, email), None)
            return httpx.Response(204)
        self.writes.extend(body)
        if request.url.path.endswith("/gmail_connections"):
            row = body[0]
            self.connections[(row["user_id"], row["email"])] = row
            return httpx.Response(201, json=[{**row, "id": "conn:" + row["user_id"]}])
        if request.url.path.endswith("/mail_messages"):
            return httpx.Response(201, json=[{**r, "id": "mail:" + r["user_id"] + r["gmail_id"]}
                                            for r in body])
        return httpx.Response(201, json=[])


def application(tmp_path, provider):
    settings = Settings(
        _env_file=None, app_mode="google", google_client_id="fake-client",
        google_client_secret="fake-secret", token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "isolated.db", supabase_url="https://synthetic.supabase.co",
        supabase_service_key="synthetic-service-key", supabase_user_id=str(uuid.uuid4()),
    )
    return create_app(settings, transport=httpx.MockTransport(provider))


def login(client, code):
    client.cookies.clear()
    state = oauth_start(client)["state"][0]
    result = callback(client, state, code)
    assert result.headers["location"] == "/?connected=1"
    return client.cookies.get(COOKIE)


def select(client, cookie):
    client.cookies.clear()
    client.cookies.set(COOKIE, cookie)


def test_google_login_storage_and_disconnect_are_owned_by_each_user(tmp_path):
    provider = Providers()
    app = application(tmp_path, provider)
    with TestClient(app, base_url=BASE) as client:
        alice = login(client, "alice")
        assert post(client, "/api/sync").status_code == 200
        alice_owner = next(iter(provider.connections))[0]
        bob = login(client, "bob")
        assert post(client, "/api/sync").status_code == 200
        assert len({row["user_id"] for row in provider.writes}) == 2
        bob_owner = app.state.store.session(bob)["user_id"]
        assert alice_owner != bob_owner
        a_account = app.state.store.session(alice)["account_id"]
        b_account = app.state.store.session(bob)["account_id"]
        with app.state.store.connection() as conn:
            for job_id, owner in (("alice-job", a_account), ("bob-job", b_account)):
                conn.execute("INSERT INTO dynamic_jobs VALUES "
                             "(?, ?, NULL, NULL, 'failed', 1, 1, '{}')",
                             (job_id, owner))
        assert client.get("/api/dynamic/jobs/alice-job").status_code == 404
        # Supplying someone else's owner never changes the server-derived account.
        select(client, alice)
        mail = client.get("/api/mails/same-id", params={"user_id": bob_owner}).json()
        assert mail["thread_id"] == "Bearer access-alice"
        assert post(client, "/api/disconnect?user_id=" + bob_owner).status_code == 200
        assert app.state.store.session(alice) is None
        assert set(provider.connections) == {(bob_owner, "bob@example.invalid")}
        with app.state.store.connection() as conn:
            assert conn.execute("SELECT id FROM dynamic_jobs").fetchall()[0][0] == "bob-job"
        select(client, bob)
        assert client.get("/api/session").json()["authenticated"] is True
        assert client.get("/api/mails/same-id").json()["thread_id"] == "Bearer access-bob"
        with app.state.store.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


def test_supabase_identity_must_match_verified_google_subject(tmp_path):
    provider = Providers()
    provider.fail_auth = True
    app = application(tmp_path, provider)
    with TestClient(app, base_url=BASE) as client:
        state = oauth_start(client)["state"][0]
        response = callback(client, state, "alice")
        assert response.headers["location"] == "/?error=identity_unavailable"
        assert client.get("/api/session").json()["authenticated"] is False
        assert not provider.connections


def test_legacy_records_are_preserved_without_granting_new_login_access(tmp_path):
    from prodog_gmail.store import Store
    settings = Settings(
        _env_file=None, token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "legacy.db",
    )
    store = Store(settings)
    old = store.account("alice@example.invalid", "google")
    store.save_metadata(old, {"id": "old-mail", "subject": "Legacy private record"})
    session = store.new_session(old)
    migrated = Store(settings)
    assert migrated.session(session) is None
    with migrated.connection() as conn:
        mode = conn.execute("SELECT mode FROM accounts WHERE id=?", (old,)).fetchone()[0]
        assert mode == "legacy-google"
        assert conn.execute("SELECT COUNT(*) FROM mails").fetchone()[0] == 1
    # A fresh verified connection is distinct and cannot see the archived account.
    secret = migrated.new_session()
    new = migrated.complete_oauth(secret, "alice@example.invalid", {}, lambda *_: {},
                                 google_sub="verified-alice", user_id="verified-owner")
    assert migrated.session(new)["account_id"] != old
    assert Store(settings).session(new)["user_id"] == "verified-owner"


def test_failed_cleanup_blocks_reads_and_sync_but_can_retry(tmp_path):
    provider = Providers()
    app = application(tmp_path, provider)
    with TestClient(app, base_url=BASE) as client:
        login(client, "alice")
        assert post(client, "/api/sync").status_code == 200
        provider.fail_delete = True
        assert post(client, "/api/disconnect").status_code == 502
        assert client.get("/api/session").json()["cleanup_pending"] is True
        assert client.get("/api/mails").status_code == 401
        assert post(client, "/api/sync").status_code == 401
        assert client.get("/api/dynamic/jobs").status_code == 401
        # Even after losing the old cookie, the same verified identity can finish cleanup.
        login(client, "alice")
        assert client.get("/api/session").json()["cleanup_pending"] is True
        assert client.get("/api/mails").status_code == 401
        provider.fail_delete = False
        assert post(client, "/api/disconnect").status_code == 200
        assert not provider.connections
