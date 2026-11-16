import asyncio

import httpx
from test_user_isolation import BASE, Providers, application

from prodog_gmail.app import COOKIE


def test_disconnect_waits_for_inflight_sync_then_removes_its_rows(tmp_path):
    async def scenario():
        provider = Providers()
        app = application(tmp_path, provider)
        started, release = asyncio.Event(), asyncio.Event()

        class Sink:
            saved = False
            deleted = False

            async def save_page(self, user_id, email, messages):
                started.set()
                await release.wait()
                self.saved = True
                return 0

            async def delete_connection(self, user_id, email):
                self.saved = False
                self.deleted = True

        async with app.router.lifespan_context(app):
            sink = Sink()
            app.state.supabase = sink
            store = app.state.store
            secret = store.new_session()
            secret = store.complete_oauth(
                secret, "a@example.invalid", {}, lambda *_: {}, google_sub="a", user_id="owner-a")

            async def empty_page(*args):
                return {}

            async def revoke(*args):
                return None

            app.state.google.list_messages = empty_page
            app.state.google.revoke = revoke
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE,
                                         cookies={COOKIE: secret}) as client:
                headers = {"Origin": BASE, "X-CSRF-Token": store.csrf(secret)}
                saving = asyncio.create_task(client.post('/api/sync', headers=headers))
                await asyncio.wait_for(started.wait(), 5)
                deleting = asyncio.create_task(client.post('/api/disconnect', headers=headers))
                await asyncio.sleep(0)
                assert sink.deleted is False
                release.set()
                assert (await saving).status_code == 200
                assert (await deleting).status_code == 200
                assert sink.deleted is True and sink.saved is False
                assert store.session(secret) is None

    asyncio.run(scenario())
