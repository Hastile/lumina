"""WebSocket JSON-RPC 2.0 server.

Requests:      {"jsonrpc": "2.0", "id": 1, "method": "execute", "params": {...}}
Notifications: {"jsonrpc": "2.0", "method": "event", "params": {type: patch|reset|job|...}}

The first message on a connection must be ``hello`` carrying the token from
the discovery file (``~/.lumina/engine.json``), which is how the GUI, the MCP
server and CLI tools find and attach to the same running engine.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve

from .commands.registry import CommandError
from .engine import Engine


def discovery_path() -> Path:
    return Path(os.environ.get("LUMINA_HOME") or Path.home() / ".lumina") / "engine.json"


def _error(code: str, message: str, details: dict | None = None) -> dict:
    return {"code": -32000, "message": message, "data": {"code": code, **({"details": details} if details else {})}}


async def run_server(engine: Engine, host: str = "127.0.0.1", port: int = 0, token: str | None = None) -> None:
    token = token or secrets.token_hex(16)
    loop = asyncio.get_running_loop()
    clients: set[ServerConnection] = set()

    def on_event(event: dict) -> None:
        msg = json.dumps({"jsonrpc": "2.0", "method": "event", "params": event}, ensure_ascii=False)
        for ws in list(clients):
            asyncio.run_coroutine_threadsafe(_safe_send(ws, msg), loop)

    engine.subscribe(on_event)

    async def handler(ws: ServerConnection) -> None:
        authed = False
        try:
            async for raw in ws:
                try:
                    req = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}))
                    continue
                rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
                if not authed:
                    if method != "hello" or params.get("token") != token:
                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "error": _error("UNAUTHORIZED", "hello with token required")}))
                        await ws.close()
                        return
                    authed = True
                    clients.add(ws)
                try:
                    # engine calls may run ffprobe etc.; keep the event loop responsive
                    result = await asyncio.to_thread(engine.dispatch, method, params)
                    reply = {"jsonrpc": "2.0", "id": rid, "result": result}
                except CommandError as exc:
                    reply = {"jsonrpc": "2.0", "id": rid, "error": _error(exc.code, exc.message, exc.details or None)}
                except Exception as exc:  # noqa: BLE001
                    reply = {"jsonrpc": "2.0", "id": rid, "error": _error("INTERNAL", f"{type(exc).__name__}: {exc}")}
                if rid is not None:
                    await ws.send(json.dumps(reply, ensure_ascii=False))
        finally:
            clients.discard(ws)

    async with serve(handler, host, port, max_size=64 * 1024 * 1024) as server:
        actual_port = next(iter(server.sockets)).getsockname()[1]
        info = {"host": host, "port": actual_port, "token": token, "pid": os.getpid()}
        path = discovery_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info), encoding="utf-8")
        # the Electron main process reads this line to connect
        print(json.dumps({"ready": True, **info}), flush=True)
        try:
            await asyncio.Future()
        finally:
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
                    path.unlink()
            except (OSError, ValueError):
                pass


async def _safe_send(ws: ServerConnection, msg: str) -> None:
    try:
        await ws.send(msg)
    except Exception:  # noqa: BLE001 - client went away
        pass


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="lumina serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--token")
    ap.add_argument("--project", help="open this project on start")
    args = ap.parse_args(argv)
    engine = Engine()
    if args.project:
        engine.dispatch("project.open", {"path": args.project})
    asyncio.run(run_server(engine, args.host, args.port, args.token))
