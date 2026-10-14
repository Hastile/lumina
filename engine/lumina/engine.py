"""Engine facade: one session + media services + background jobs, exposed
through a transport-neutral ``dispatch(method, params)``.

The WebSocket server (GUI), the MCP server (agents) and the CLI (recipes) all
call ``dispatch``, so they share exactly the same behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import inspect as views
from . import timebase
from .commands.registry import REGISTRY, CommandError
from .media import probe as probe_mod
from .media.jobs import JobRunner
from .session import Session


class MediaServices:
    def probe(self, path: str) -> dict:
        return probe_mod.probe(path)


class Engine:
    def __init__(self, services: Any = None, run_jobs: bool = True):
        self.session = Session(services=services or MediaServices())
        self.run_jobs = run_jobs
        self.jobs = JobRunner(record=self._record_derived, emit=self.session._emit)
        self.methods: dict[str, Callable[[dict], Any]] = {
            "hello": lambda p: {"engine": "lumina", "api": 1, "commands": REGISTRY.describe(), **self.session.snapshot()},
            "commands.list": lambda p: REGISTRY.describe(),
            "execute": self._execute,
            "undo": lambda p: self.session.undo(),
            "redo": lambda p: self.session.redo(),
            "transaction.begin": lambda p: self.session.begin(p.get("label", "Edit")),
            "transaction.commit": lambda p: self.session.commit(),
            "transaction.rollback": lambda p: self.session.rollback(),
            "snapshot": lambda p: self.session.snapshot(),
            "project.new": lambda p: self.session.new(p.get("name", "Untitled")),
            "project.open": self._open,
            "project.save": lambda p: self.session.save(p.get("path")),
            "project.summary": lambda p: views.project_summary(self.session.doc),
            "timeline.state": lambda p: views.timeline_state(self.session.doc, p.get("sequence_id")),
            "jobs.list": lambda p: self.jobs.list(),
            "time.convert": self._convert_time,
        }

    # ------------------------------------------------------------ dispatch

    def dispatch(self, method: str, params: dict | None = None) -> Any:
        fn = self.methods.get(method)
        if fn is None:
            raise CommandError("UNKNOWN_METHOD", f"unknown method {method!r}")
        return fn(params or {})

    def subscribe(self, fn):
        return self.session.subscribe(fn)

    # ------------------------------------------------------------ methods

    def _execute(self, p: dict) -> dict:
        name = p.get("name")
        if not name:
            raise CommandError("BAD_PARAMS", "execute needs 'name'")
        out = self.session.execute(name, p.get("params") or {}, p.get("label"))
        if name == "media.import" and self.run_jobs:
            for mid in out["result"]["media_ids"]:
                self.jobs.submit_media(self.session.doc["media"][mid])
        return out

    def _open(self, p: dict) -> dict:
        snap = self.session.open(p["path"])
        if self.run_jobs:
            for item in self.session.doc["media"].values():
                derived = [item.get(k) for k in ("thumbnail_path", "waveform_path", "proxy_path")]
                if any(d and not Path(d).exists() for d in derived) or not (item.get("thumbnail_path") or item.get("waveform_path")):
                    self.jobs.submit_media(item)
        return snap

    def _record_derived(self, media_id: str, fields: dict) -> None:
        if media_id in self.session.doc["media"]:
            self.session.execute("media.set_derived", {"media_id": media_id, **fields})

    def _convert_time(self, p: dict) -> dict:
        from .commands.project import seq_of
        from .commands.registry import resolve_time

        seq = seq_of(self.session.doc, p.get("sequence_id"))
        st = seq["settings"]
        ticks = resolve_time(p["time"], st)
        return {"ticks": ticks, "seconds": timebase.ticks_to_seconds(ticks),
                "frame": timebase.ticks_to_frames(ticks, st["fps_num"], st["fps_den"]),
                "timecode": timebase.timecode(ticks, st["fps_num"], st["fps_den"])}
