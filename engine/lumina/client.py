"""Synchronous engine clients used by the MCP server and CLI.

``connect()`` attaches to a running engine (the GUI's) through the discovery
file so agent edits show up live in the open editor; with no engine running it
falls back to an in-process headless engine.
"""

from __future__ import annotations

import itertools
import json
import threading
from typing import Any

from .commands.registry import CommandError


class RemoteEngine:
    def __init__(self, host: str, port: int, token: str):
        from websockets.sync.client import connect

        self.ws = connect(f"ws://{host}:{port}", max_size=64 * 1024 * 1024, open_timeout=3)
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
        self.hello = self.dispatch("hello", {"token": token})

    def dispatch(self, method: str, params: dict | None = None) -> Any:
        with self.lock:
            rid = next(self.ids)
            self.ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get("id") != rid:
                    continue  # event notification or stale reply
                if "error" in msg:
                    data = msg["error"].get("data") or {}
                    raise CommandError(data.get("code", "RPC_ERROR"), msg["error"].get("message", "error"), **(data.get("details") or {}))
                return msg["result"]

    def close(self) -> None:
        self.ws.close()


class LocalEngine:
    def __init__(self, run_jobs: bool = True):
        from .engine import Engine

        self.engine = Engine(run_jobs=run_jobs)

    def dispatch(self, method: str, params: dict | None = None) -> Any:
        return self.engine.dispatch(method, params or {})

    def close(self) -> None:
        self.engine.jobs.pool.shutdown(wait=True)


def connect(prefer_remote: bool = True):
    if prefer_remote:
        from .server import discovery_path

        path = discovery_path()
        if path.exists():
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
                return RemoteEngine(info["host"], info["port"], info["token"])
            except Exception:  # noqa: BLE001 - stale discovery file
                pass
    return LocalEngine()
