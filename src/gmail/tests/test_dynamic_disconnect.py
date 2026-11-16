import asyncio

from cryptography.fernet import Fernet

from prodog_gmail.config import Settings
from prodog_gmail.dynamic import DynamicService
from prodog_gmail.store import Store


def test_worker_survives_job_deleted_while_reading_mail(tmp_path):
    # Disconnect deletes the job row while submit() is awaiting the mail body.
    settings = Settings(_env_file=None, token_encryption_key=Fernet.generate_key().decode(),
                        database_path=tmp_path / "test.db")
    store = Store(settings)
    service = None

    async def load_mail(_account, _mail_id):
        with store.connection() as db:
            db.execute("DELETE FROM dynamic_jobs")
        raise RuntimeError("account is gone")

    async def run():
        nonlocal service
        service = DynamicService(store, None, "http://dynamic.invalid", load_mail)
        job, _ = service.db.insert("owner-1", {"source": "gmail", "mode": "google", "mail_id": "m1",
                                                "body_hash": "h", "options": {}, "prompt_hash": "p"})
        await service.start()
        await asyncio.sleep(0.2)
        alive = not service.worker.done()
        await service.close()
        return alive, service.db.get(job["id"])

    alive, job = asyncio.run(run())
    assert alive
    assert job is None
