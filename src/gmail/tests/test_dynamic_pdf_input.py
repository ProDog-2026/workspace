import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet
from test_dynamic import OPTIONS, AgentService

from prodog_gmail.config import Settings
from prodog_gmail.dynamic import DynamicService
from prodog_gmail.store import Store

BODY = "Please see the attached agenda."
PDF_TEXT = "Meeting agenda: curriculum planning."
MAIL = {
    "id": "meeting-mail",
    "body_hash": "synthetic-mail-hash",
    "subject": "Team meeting",
    "sender": "team@example.invalid",
    "analysis_text": BODY,
    "pdf_texts": [PDF_TEXT],
}
BASE_BODY = f"UNTRUSTED EMAIL DATA\nFrom: {MAIL['sender']}\n\n{BODY}"


@pytest.mark.parametrize("mode", ["linked", "manual", "legacy", "changed_extraction"])
def test_pdf_context_after_worker_restart(tmp_path, mode):
    settings = Settings(
        _env_file=None,
        token_encryption_key=Fernet.generate_key().decode(),
        database_path=tmp_path / "test.db",
    )
    store = Store(settings)
    agent = AgentService()
    owner = store.account("owner@example.invalid", "demo")

    async def load_mail(_current, _mail_id):
        if mode == "changed_extraction":
            return {**MAIL, "pdf_texts": []}
        return MAIL

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(agent)) as client:
            service = DynamicService(store, client, "http://127.0.0.1:8000", load_mail)
            service.db.save_settings(owner, OPTIONS)
            job = await service.enqueue(
                owner,
                mail={**MAIL, "pdf_texts": []} if mode == "legacy" else MAIL,
                detection={"status": "ok", "verdict": "suspicious"} if mode != "manual" else None,
                mode="demo",
                manual=mode == "manual",
            )
            assert PDF_TEXT.encode() not in settings.database_path.read_bytes()
            restarted = DynamicService(store, client, "http://127.0.0.1:8000", load_mail)
            await restarted.submit(restarted.get(owner, job["id"]))
            return job, restarted.get(owner, job["id"])

    original, restored = asyncio.run(exercise())
    assert "pdf_context_hash" not in original
    if mode == "changed_extraction":
        assert restored["status"] == "failed"
        assert agent.submitted == []
        return

    assert len(agent.submitted) == 1
    assert agent.submitted[0]["request_id"] == original["id"]
    expected = BASE_BODY
    if mode == "linked":
        expected += f"\n\nUNTRUSTED PDF ATTACHMENT 1\n{PDF_TEXT}"
    assert agent.submitted[0]["email"]["body"] == expected
