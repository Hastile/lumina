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
from . import presets as presets_mod
from .render import export as export_mod
from .session import Session


class MediaServices:
    def probe(self, path: str) -> dict:
        return probe_mod.probe(path)

    def audio_peak(self, path: str, src_start: int, src_dur: int, speed: float) -> float:
        """Sample peak (dBFS) of a source range, as Premiere's Audio Gain dialog measures it."""
        import numpy as np

        pcm = export_mod.decode_audio(path, src_start, src_dur, speed, 48000)
        peak = float(np.abs(pcm).max()) if pcm.size else 0.0
        return 20 * np.log10(peak) if peak > 0 else -float("inf")

    def audio_features(self, path: str, src_start: int, src_dur: int, channels: int = 2) -> dict:
        from . import audio_analysis

        rate = 16000
        return audio_analysis.features(export_mod.decode_audio(path, src_start, src_dur, 1.0, rate, None, channels), rate)

    def audio_measure(self, path: str, src_start: int, src_dur: int, speed: float = 1.0, channels: int = 2,
                      filters: list[str] | tuple = ()) -> dict:
        """Loudness, loudness range, noise floor and true peak of a clip as it plays (Auto-Match)."""
        from . import audio_analysis

        return audio_analysis.measure(path, src_start, src_dur, speed, channels, filters)


class Engine:
    def __init__(self, services: Any = None, run_jobs: bool = True):
        self.session = Session(services=services or MediaServices())
        self.run_jobs = run_jobs
        self._queued: set[str] = set()
        self.jobs = JobRunner(record=self._record_derived, emit=self.session._emit)
        self.exports = export_mod.ExportQueue(emit=self.session._emit)
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
            "jobs.list": lambda p: self.jobs.list() + self.exports.list(),
            "export.presets": lambda p: export_mod.presets(),
            "render.graphic": self._render_graphic,
            "presets.list": lambda p: presets_mod.load(),
            "presets.import_premiere": lambda p: presets_mod.import_premiere(p.get("path")),
            "presets.remove": lambda p: {"removed": presets_mod.remove(p["preset_id"])},
            "export.start": self._export_start,
            "export.cancel": lambda p: self.exports.cancel(p["job_id"]),
            "export.frame": self._export_frame,
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
        if name in ("media.import", "project.import_premiere"):
            self._submit_missing_jobs()
        return out

    def _submit_missing_jobs(self) -> None:
        """Queue thumbnail/waveform/proxy generation for media that has none yet."""
        if not self.run_jobs:
            return
        for item in list(self.session.doc["media"].values()):
            if item["id"] in self._queued:
                continue
            derived = [item.get(k) for k in ("thumbnail_path", "waveform_path", "proxy_path", "filmstrip_path")]
            if any(d and not Path(d).exists() for d in derived) or not (item.get("thumbnail_path") or item.get("waveform_path")):
                if Path(item["path"]).exists():
                    self._queued.add(item["id"])
                    self.jobs.submit_media(item)
            elif item["kind"] == "video" and item.get("video") and not item.get("filmstrip_path") and Path(item["path"]).exists():
                # projects from before timeline thumbnails: only the filmstrip is missing
                self._queued.add(item["id"])
                self.jobs.submit_media(item, {"filmstrip"})

    def _open(self, p: dict) -> dict:
        snap = self.session.open(p["path"])
        self._queued.clear()
        self._submit_missing_jobs()
        return snap

    def _record_derived(self, media_id: str, fields: dict) -> None:
        if media_id in self.session.doc["media"]:
            self.session.execute("media.set_derived", {"media_id": media_id, **fields})

    def _export_start(self, p: dict) -> dict:
        from .commands.project import seq_of

        seq = seq_of(self.session.doc, p.get("sequence_id"))
        if not p.get("path"):
            raise CommandError("BAD_PARAMS", "export.start needs 'path'")
        params = {**p, "sequence_id": seq["id"]}
        export_mod.resolve_range(seq, p.get("range"), p.get("start"), p.get("end"))  # fail fast on an empty range
        return self.exports.submit(self.session.doc, params, wait=bool(p.get("wait")))

    def _export_frame(self, p: dict) -> dict:
        """Premiere's Export Frame (Ctrl+Shift+E). ``time`` defaults to the playhead."""
        import tempfile

        from .commands.project import seq_of
        from .commands.registry import resolve_time

        seq = seq_of(self.session.doc, p.get("sequence_id"))
        st = seq["settings"]
        t = resolve_time(p["time"], st) if p.get("time") is not None else seq["playhead"]
        t = timebase.ticks_to_frames(t, st["fps_num"], st["fps_den"]) * timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
        path = p.get("path") or str(Path(tempfile.gettempdir()) / f"lumina-frame-{seq['id'][:8]}-{t}.png")
        img = export_mod.export_frame(self.session.doc, seq["id"], t, path, p.get("max_size"))
        return {"path": path, "time": t, "seconds": timebase.ticks_to_seconds(t),
                "timecode": timebase.timecode(t, st["fps_num"], st["fps_den"]), "width": img.width, "height": img.height}

    def _render_graphic(self, p: dict) -> dict:
        """A graphic clip's layers rasterised by the export renderer, for the Program
        monitor's GPU compositor: [{blend, x, y, width, height, png (base64)}] in
        sequence pixels (each run of layers sharing a blend mode, cropped to its pixels)."""
        import base64
        import io

        from .render.compositor import render_graphic_groups

        out = []
        for blend, img in render_graphic_groups(p["graphic"], int(p["width"]), int(p["height"])):
            box = img.getbbox()
            if not box:
                continue
            buf = io.BytesIO()
            img.crop(box).save(buf, "PNG", compress_level=1)
            out.append({"blend": blend, "x": box[0], "y": box[1], "width": box[2] - box[0], "height": box[3] - box[1],
                        "png": base64.b64encode(buf.getvalue()).decode("ascii")})
        return {"groups": out}

    def _convert_time(self, p: dict) -> dict:
        from .commands.project import seq_of
        from .commands.registry import resolve_time

        seq = seq_of(self.session.doc, p.get("sequence_id"))
        st = seq["settings"]
        ticks = resolve_time(p["time"], st)
        return {"ticks": ticks, "seconds": timebase.ticks_to_seconds(ticks),
                "frame": timebase.ticks_to_frames(ticks, st["fps_num"], st["fps_den"]),
                "timecode": timebase.timecode(ticks, st["fps_num"], st["fps_den"])}
