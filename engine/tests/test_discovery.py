"""Discovery ownership survives an old engine shutting down after a replacement."""
import asyncio
import contextlib
import json

from lumina.engine import Engine
from lumina.server import run_server


def test_discovery_is_published_complete_and_old_cleanup_preserves_replacement(tmp_path, monkeypatch):
    path = tmp_path / 'engine.json'
    monkeypatch.setattr('lumina.server.discovery_path', lambda: path)

    async def wait_token(token):
        async with asyncio.timeout(5):
            while not path.exists() or json.loads(path.read_text(encoding='utf-8'))['token'] != token:
                await asyncio.sleep(0.01)
        info = json.loads(path.read_text(encoding='utf-8'))
        assert info['host'] == '127.0.0.1' and info['port'] > 0 and info['pid'] > 0

    async def cancel(task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def check():
        # Both tasks have the same PID: ownership must include the token.
        first = asyncio.create_task(run_server(Engine(run_jobs=False), token='old'))
        second = None
        try:
            await wait_token('old')
            second = asyncio.create_task(run_server(Engine(run_jobs=False), token='new'))
            await wait_token('new')
            await cancel(first)
            assert json.loads(path.read_text(encoding='utf-8'))['token'] == 'new'
            await cancel(second)
            assert not path.exists()
            assert not list(tmp_path.glob('*.tmp'))
        finally:
            await cancel(first)
            if second:
                await cancel(second)

    asyncio.run(check())
