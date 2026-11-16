import asyncio

import httpx
import pytest
from backend.llm import ConnectionConfig, LLMAdapter
from backend.runs import RunError, RunManager


def test_purge_waits_for_writer_and_preserves_other_runs(tmp_path):
    async def scenario():
        async with httpx.AsyncClient() as client:
            manager = RunManager(LLMAdapter(client), data_root=tmp_path)
            config = ConnectionConfig(base_url="http://ollama.test", model="synthetic")
            first = manager.create(config, [])['run_id']
            second = manager.create(config, [])['run_id']
            directory = manager.store.directory(first)
            directory.mkdir()
            other = manager.store.directory(second)
            other.mkdir()
            (other / 'result.json').write_text('other user')

            async def late_writer():
                await asyncio.sleep(0)
                (directory / 'summary.txt').write_text('private synthetic summary')

            manager.get(first).lifecycle = asyncio.create_task(late_writer())
            assert await manager.purge(first) == {'deleted': True}
            assert not directory.exists()
            assert first not in manager.runs
            assert (other / 'result.json').read_text() == 'other user'
            assert await manager.purge(first) == {'deleted': True}

    asyncio.run(scenario())


def test_purge_rejects_symlink_outside_evidence_root(tmp_path):
    async def scenario():
        root = tmp_path / 'runs'
        outside = tmp_path / 'outside'
        outside.mkdir()
        marker = outside / 'keep.txt'
        marker.write_text('preserve')
        async with httpx.AsyncClient() as client:
            manager = RunManager(LLMAdapter(client), data_root=root)
            manager.store.directory('a' * 32).symlink_to(outside, target_is_directory=True)
            with pytest.raises(RunError):
                await manager.purge('a' * 32)
            assert marker.read_text() == 'preserve'

    asyncio.run(scenario())
