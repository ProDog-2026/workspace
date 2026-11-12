"""Single-mail tests stay with the app user when the Gmail session changes."""

from test_auth_binding import A, connect, post
from test_auth_binding import owned_client as owned_client
from test_dynamic_admission import ready

PAYLOAD = {"provider": "ollama", "model": "synthetic", "task": "요약", "mail": "테스트 메일"}


def start(client, monkeypatch):
    async def metadata_ready(*args):
        return ready()

    monkeypatch.setattr("prodog_gmail.experiments.check_readiness", metadata_ready)
    monkeypatch.setattr(client.app.state.experiments, "_schedule", lambda: None)
    client.headers["Authorization"] = "Bearer user-a"
    response = client.post(
        "/api/experiments",
        json=PAYLOAD,
        headers={
            "Origin": "http://127.0.0.1:8765",
            "X-CSRF-Token": client.get("/api/session").json()["csrf_token"],
        },
    )
    assert response.status_code == 202
    return response.json()["id"]


def test_single_mail_run_uses_verified_user_without_gmail(owned_client, monkeypatch):
    client, _ = owned_client
    run_id = start(client, monkeypatch)
    assert client.app.state.experiments.records[run_id]["owner"] == "user:" + A
    assert post(client, "/api/logout").status_code == 200
    assert client.app.state.experiments.records[run_id]["status"] == "cancelled"


def test_single_mail_history_survives_cookie_rotation_and_gmail_connection(
    owned_client, monkeypatch
):
    client, _ = owned_client
    run_id = start(client, monkeypatch)
    client.cookies.clear()
    assert client.get("/api/session").status_code == 200
    assert client.get("/api/experiments/" + run_id).status_code == 200
    connect(client)
    assert client.get("/api/experiments/" + run_id).status_code == 200
    assert any(run["id"] == run_id for run in client.get("/api/experiments").json()["runs"])
    client.headers["Authorization"] = "Bearer user-b"
    assert client.get("/api/session").status_code == 200
    assert client.get("/api/experiments/" + run_id).status_code == 404
