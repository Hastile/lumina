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


class EngineGone(Exception):
    """The GUI's engine closed the connection; ``sent``: the request may already have run."""

    def __init__(self, sent: bool):
        super().__init__("engine connection closed")
        self.sent = sent


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
            try:
                self.ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}))
            except Exception as exc:  # noqa: BLE001 - closed socket: the engine is gone
                raise EngineGone(sent=False) from exc
            while True:
                try:
                    raw = self.ws.recv()
                except Exception as exc:  # noqa: BLE001
                    raise EngineGone(sent=True) from exc
                msg = json.loads(raw)
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


def _remote() -> RemoteEngine | None:
    """The running GUI's engine, from its discovery file (None when there is none or it is stale)."""
    from .server import discovery_path

    path = discovery_path()
    if path.exists():
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            return RemoteEngine(info["host"], info["port"], info["token"])
        except Exception:  # noqa: BLE001 - stale discovery file
            pass
    return None


def connect(prefer_remote: bool = True):
    return (_remote() if prefer_remote else None) or LocalEngine()


class FollowingEngine:
    """The MCP server's engine: the GUI's while one runs, else a headless one, and it follows the GUI.

    A GUI started after the agent is picked up on the next call while the headless engine still
    holds only a blank project (nothing to lose), and a GUI that restarts is reconnected to. A
    request lost on the way is resent; one that may already have run is reported, not repeated."""

    def __init__(self, engine=None):
        self.current = engine if engine is not None else connect()

    def _headless_is_blank(self) -> bool:
        try:
            return all(p.get("blank") and not p.get("dirty") for p in self.current.dispatch("project.list"))
        except Exception:  # noqa: BLE001
            return False

    def dispatch(self, method: str, params: dict | None = None) -> Any:
        from .server import discovery_path

        if isinstance(self.current, LocalEngine) and discovery_path().exists() and self._headless_is_blank():
            remote = _remote()
            if remote is not None:
                self.current.close()
                self.current = remote
        try:
            return self.current.dispatch(method, params)
        except EngineGone as gone:
            remote = _remote()
            if remote is None:
                raise CommandError("NO_ENGINE", "the Lumina editor closed; start it again and retry") from gone
            self.current = remote
            if gone.sent:
                raise CommandError("ENGINE_RESTARTED", "the editor restarted during this call (reconnected): check the state, then retry") from gone
            return self.current.dispatch(method, params)

    def close(self) -> None:
        self.current.close()
