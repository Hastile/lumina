"""Synchronous engine clients used by the MCP server and CLI.

``connect()`` attaches to a running engine (the GUI's) through the discovery
file so agent edits show up live in the open editor; with no engine running it
falls back to an in-process headless engine.
"""

from __future__ import annotations

import copy
import itertools
import json
import os
import re
import threading
import time
import uuid
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
        self.endpoint = (host, port, token)
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
        self.engine._stop.set()
        # The GUI queues its own missing derived media. Do not delay attachment
        # behind minutes of headless proxy jobs; already running jobs may finish.
        self.engine.jobs.pool.shutdown(wait=False, cancel_futures=True)


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


def _endpoint() -> tuple | None:
    from .server import discovery_path

    try:
        info = json.loads(discovery_path().read_text(encoding="utf-8"))
        return info["host"], info["port"], info["token"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def connect(prefer_remote: bool = True):
    return (_remote() if prefer_remote else None) or LocalEngine()


def _launch_editor() -> None:
    """Launch the interactive editor, detached from MCP's stdio lifetime."""
    import subprocess
    import sys
    from pathlib import Path

    configured = os.environ.get('LUMINA_EDITOR_EXE')
    candidates = [Path(configured)] if configured else []
    if not configured and os.name == 'nt':
        candidates.append(Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) /
                          'Programs/Lumina/Lumina.exe')
    if not configured:
        if getattr(sys, 'frozen', False):
            candidates.append(Path(sys.executable).resolve().parents[2] / 'Lumina.exe')
        candidates.append(Path(__file__).resolve().parents[2] / 'app/release/win-unpacked/Lumina.exe')
    exe = next((p for p in candidates if p.is_file()), None)
    if exe is None:
        raise CommandError('NO_EDITOR', 'Lumina GUI executable not found; install Lumina or set LUMINA_EDITOR_EXE')
    try:
        if os.name == 'nt':
            # Let Explorer own the GUI. MCP hosts can put their server and all
            # descendants in a kill-on-close Job Object, including detached children.
            import pythoncom
            import win32com.client

            home = os.environ.get('LUMINA_HOME')
            arguments = subprocess.list2cmdline([f'--lumina-home={home}']) if home else ''
            pythoncom.CoInitialize()
            try:
                # Obtain IShellDispatch from the existing Explorer desktop, not
                # a new in-process Shell.Application object inside MCP's job.
                desktop = win32com.client.Dispatch('Shell.Application').Windows().FindWindowSW(0, 0, 8, 0, 1)
                desktop.Document.Application.ShellExecute(str(exe), arguments, str(exe.parent), 'open', 1)
            finally:
                pythoncom.CoUninitialize()
            return
        from .server import discovery_path

        log = discovery_path().parent / 'gui-launch.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('ab') as stderr:
            subprocess.Popen([str(exe)], cwd=str(exe.parent), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=stderr,
                             **({'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                                if os.name == 'nt' else {'start_new_session': True}))
    except Exception as exc:
        raise CommandError('GUI_LAUNCH_FAILED', f'Could not start Lumina GUI: {exc}') from exc


class GuiEngine:
    """Idle MCP transport until an engine tool is used; edits always run in the GUI."""

    def __init__(self, startup_timeout: float = 30):
        self.current = None
        self.lock = threading.RLock()
        self.startup_timeout = startup_timeout
        self.target_key: str | None = None
        self.target_path: str | None = None

    def _attach(self) -> bool:
        endpoint = _endpoint()
        if self.current is not None and endpoint == self.current.endpoint:
            try:
                self.current.dispatch('project.list')  # Read-only liveness check before any edit.
                return True
            except EngineGone:
                self.current.close()
                self.current = None
        remote = _remote() if endpoint else None
        if remote is None:
            return False
        old, self.current = self.current, remote
        if old:
            old.close()
        return True

    def _ensure(self) -> bool:
        if self._attach():
            return False
        _launch_editor()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._attach():
                return True
            time.sleep(0.1)
        raise CommandError('GUI_START_TIMEOUT', 'Lumina GUI did not become ready; no headless edits were made')

    def _status(self) -> dict:
        attached = self._attach()
        try:
            projects = self.current.dispatch('project.list') if attached else []
        except EngineGone:
            self.current.close()
            self.current = None
            attached, projects = False, []
        return {'mode': 'gui', 'connected': attached, 'headless': False, 'projects': projects}

    def dispatch(self, method: str, params: dict | None = None) -> Any:
        with self.lock:
            try:
                return self._dispatch(method, params)
            except EngineGone as gone:
                if self.current:
                    self.current.close()
                self.current = None
                raise CommandError('ENGINE_DISCONNECTED', 'GUI disconnected during the call; reconnect with ensure_editor(project_path), inspect state, then retry',
                                   may_have_run=gone.sent) from gone

    def _dispatch(self, method: str, params: dict | None = None) -> Any:
        with self.lock:
            params = params or {}
            if method == 'editor.status':
                return self._status()  # Status/tool discovery never opens a window.
            started = self._ensure()
            if method == 'editor.ensure':
                from pathlib import Path

                projects = self.current.dispatch('project.list')
                path = params.get('project_path')
                case = 'gui_started' if started else 'gui_connected'
                if path:
                    target = os.path.normcase(str(Path(path).resolve()))
                    existing = next((p for p in projects if p.get('path') and
                                     os.path.normcase(str(Path(p['path']).resolve())) == target), None)
                    if existing:
                        self.current.dispatch('project.activate', {'key': existing['key']})
                        case = 'project_already_open' if not started else case
                    else:
                        self.current.dispatch('project.open', {'path': path})
                        case = 'project_opened' if not started else case
                status = self._status()
                if not status['connected']:
                    raise CommandError('ENGINE_DISCONNECTED', 'GUI closed while preparing the project; retry ensure_editor(project_path)')
                active = next(p for p in status['projects'] if p['active'])
                self.target_key, self.target_path = active['key'], active.get('path')
                return {**status, 'case': case}
            try:
                explicit_target = method in {'project.open', 'project.new', 'project.activate'}
                if self.target_key and not explicit_target and method not in {'project.list', 'hello', 'commands.list'}:
                    active = next(p for p in self.current.dispatch('project.list') if p['active'])
                    if active['key'] != self.target_key:
                        raise CommandError('TARGET_CHANGED', 'The GUI active project changed; call ensure_editor(project_path) before continuing',
                                           expected_path=self.target_path, active_path=active.get('path'))
                result = self.current.dispatch(method, params)
                if explicit_target or method == 'project.save':
                    active = next(p for p in self.current.dispatch('project.list') if p['active'])
                    self.target_key, self.target_path = active['key'], active.get('path')
                return result
            except EngineGone as gone:
                self.current.close()
                self.current = None
                # Never launch a blank GUI and replay a lost edit into it.
                raise CommandError('ENGINE_DISCONNECTED', 'GUI disconnected during the call; reconnect with ensure_editor(project_path), inspect state, then retry',
                                   may_have_run=gone.sent) from gone

    def close(self) -> None:
        with self.lock:
            if self.current:
                self.current.close()
                self.current = None


class FollowingEngine:
    """The MCP server's engine: the GUI's while one runs, else a headless one, and it follows the GUI.

    A GUI started after the agent is picked up on the next call. Headless work is preserved
    before switching; it must never keep agent writes invisible in a separate engine. A
    request lost on the way is resent; one that may already have run is reported, not repeated."""

    def __init__(self, engine=None):
        self.current = engine if engine is not None else connect()
        self.lock = threading.RLock()
        self._handoff_files: list[str] | None = None
        self._handoff_active_path: str | None = None

    def _headless_is_blank(self) -> bool:
        try:
            return all(p.get("blank") and not p.get("dirty") for p in self.current.dispatch("project.list"))
        except Exception:  # noqa: BLE001
            return False

    def _handoff(self, remote) -> None:
        """Open saved work, recover unsaved work as separate files, never overwrite GUI edits."""
        from pathlib import Path
        from .server import discovery_path
        from .session import Session

        projects = remote.dispatch("project.list")
        active = next((p for p in projects if p.get("active") and not p.get("blank")), None)
        if self._handoff_files is None:
            files = []
            for key, session in self.current.engine.projects.items():
                with session.lock:
                    if self.current.engine._pristine(session):
                        continue
                    same_gui_path = session.path and any(p.get('path') and
                        Path(p['path']).resolve() == Path(session.path).resolve() for p in projects)
                    disk_matches = False
                    if session.path and not same_gui_path and session.version == session.saved_version:
                        from .session import _read_project
                        try:
                            disk_doc, disk_version = _read_project(session.path)
                            disk_matches = disk_version == session.version and disk_doc == session.doc
                        except (OSError, ValueError, CommandError):
                            pass
                    if disk_matches:
                        path = Path(session.path)
                    else:
                        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', session.doc['name'])[:80] or 'Untitled'
                        path = discovery_path().parent / 'handoff' / f'{name}-MCP-recovered-{uuid.uuid4().hex}.lumina'
                        path.parent.mkdir(parents=True, exist_ok=True)
                        recovery = Session(services=self.current.engine.services)
                        recovery._reset(copy.deepcopy(session.doc), None, version=session.version)
                        recovery.save(str(path))
                    files.append(str(path))
                    if key == self.current.engine.active:
                        self._handoff_active_path = str(path)
            # Publish only a complete recovery set, so a disk failure cannot make
            # a retry silently forget the remaining projects.
            self._handoff_files = files
        try:
            for path in self._handoff_files:
                if not any(p.get('path') and Path(p['path']).resolve() == Path(path).resolve() for p in projects):
                    remote.dispatch('project.open', {'path': path})
        finally:
            if active:
                remote.dispatch('project.activate', {'key': active['key']})
            elif self._handoff_active_path:
                transferred = next((p for p in remote.dispatch('project.list') if p.get('path') and
                                    Path(p['path']).resolve() == Path(self._handoff_active_path).resolve()), None)
                if transferred:
                    remote.dispatch('project.activate', {'key': transferred['key']})

    def dispatch(self, method: str, params: dict | None = None) -> Any:
        with self.lock:
            return self._dispatch(method, params)

    def _dispatch(self, method: str, params: dict | None = None) -> Any:
        endpoint = _endpoint()
        changed = endpoint is not None and endpoint != getattr(self.current, 'endpoint', None)
        if changed:
            # Finish an existing headless transaction before moving its complete state.
            busy = isinstance(self.current, LocalEngine) and not self._headless_is_blank() and any(
                s.txn is not None for s in self.current.engine.projects.values())
            if not busy:
                remote = _remote()
                if remote is not None:
                    try:
                        if isinstance(self.current, LocalEngine) and not self._headless_is_blank():
                            self._handoff(remote)
                    except Exception:
                        remote.close()
                        raise
                    old, self.current = self.current, remote
                    old.close()
                    self._handoff_files = None
                    self._handoff_active_path = None
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
