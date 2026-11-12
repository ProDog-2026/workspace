import asyncio
from types import SimpleNamespace

from backend import main


def test_restart_waits_for_both_listeners_before_recovery_and_probe():
    async def scenario():
        checked = []
        servers = [SimpleNamespace(started=False), SimpleNamespace(started=False)]

        async def check_environment():
            checked.append(True)

        manager = SimpleNamespace(check_environment=check_environment)
        task = asyncio.create_task(main.prepare_when_started(servers, manager))
        try:
            await asyncio.sleep(0)
            assert not checked
            servers[0].started = True
            await asyncio.sleep(0.12)
            assert not checked
            servers[1].started = True
            await asyncio.wait_for(task, timeout=1)
            assert checked == [True]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
