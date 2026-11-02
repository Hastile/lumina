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
            "caption.export_srt": self._export_srt,
            "media.scan": lambda p: self._media_scan(),
            "analysis.detect": self._analysis_detect,
            "analysis.plan_cuts": self._analysis_plan_cuts,
            "analysis.plan_scene_cuts": self._analysis_plan_scene,
            "library.index": self._library_index,
            "library.search": self._library_search,
            "library.overview": self._library_overview,
            "library.annotate": lambda p: self._lib().annotate(p["root"], p.get("notes") or []),
            "library.contact_sheet": lambda p: self._lib().contact_sheet(p["root"], p.get("file"), p.get("shot_ids"),
                                                                          int(p.get("columns", 6)), p.get("path")),
            "edit.build": self._edit_build,
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
        out = self.session.execute(name, p.get("params") or {}, p.get("label"), record=p.get("record", True) is not False)
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

    # ------------------------------------------------------------ captions / media / analysis

    def _export_srt(self, p: dict) -> dict:
        from .commands.captions import export_srt

        if not p.get("path"):
            raise CommandError("BAD_PARAMS", "caption.export_srt needs 'path'")
        return export_srt(self.session.doc, p.get("sequence_id"), p.get("track_id"), p["path"])

    def _media_scan(self) -> dict:
        from .commands.media import scan

        return scan(self.session.doc)

    _ANALYSIS_OPTS = ("silence_db", "min_silence", "scene_threshold", "black_min", "black_pixel", "black_ratio")

    def _analysis_detect(self, p: dict) -> dict:
        from . import analysis

        opts = {k: p[k] for k in self._ANALYSIS_OPTS if p.get(k) is not None}
        return analysis.detect(self.session.doc, p.get("kind", "silence"), media_id=p.get("media_id"), clip_id=p.get("clip_id"),
                               sequence_id=p.get("sequence_id"), **opts)

    def _analysis_plan_cuts(self, p: dict) -> dict:
        """Plan silence/black removal; apply: true executes it (Extract, or Lift with ripple false) as one undo step."""
        from . import analysis

        keys = ("track_ids", "clip_ids", "padding_before", "padding_after", "min_duration", "protect_markers", "marker_padding")
        opts = {k: p[k] for k in keys + self._ANALYSIS_OPTS if p.get(k) is not None}
        plan = analysis.plan_cuts(self.session.doc, p.get("kind", "silence"), sequence_id=p.get("sequence_id"), **opts)
        if p.get("apply") and plan["ranges"]:
            label = "Remove Silence" if plan["kind"] == "silence" else "Remove Black"
            plan["applied"] = self.session.execute("sequence.remove_ranges", {"sequence_id": p.get("sequence_id"), "ranges": plan["ranges"],
                                                                               "ripple": p.get("ripple", True)}, label)["result"]
        return plan

    # ------------------------------------------------------------ source library / automatic edits

    @staticmethod
    def _lib():
        from . import library

        return library

    def _library_index(self, p: dict) -> dict:
        keys = ("force", "whisper_model", "language", "scene_threshold", "min_shot", "silence_db")
        opts = {k: p[k] for k in keys if p.get(k) is not None}
        return self._lib().index(p["root"], transcribe_mode=p.get("transcribe", "auto"), **opts)

    def _library_search(self, p: dict) -> dict:
        return self._lib().search(p["root"], p.get("query", ""), kind=p.get("kind"), min_duration=float(p.get("min_duration", 0)),
                                  limit=int(p.get("limit", 20)), include_flagged=p.get("include_flagged", True) is not False)

    def _library_overview(self, p: dict) -> dict:
        if p.get("format", "text") == "text":
            return {"root": p["root"], "log": self._lib().overview_text(p["root"])}
        return self._lib().overview(p["root"], p.get("transcript", True) is not False)

    def _edit_build(self, p: dict) -> dict:
        from .autoedit import build

        plan = p.get("plan") if isinstance(p.get("plan"), dict) else p
        return build(self, plan)

    def _analysis_plan_scene(self, p: dict) -> dict:
        """Plan razor points at shot changes; apply: true razors there (one undo step)."""
        from . import analysis

        opts = {k: p[k] for k in ("track_ids", "clip_ids", "scene_threshold", "min_shot") if p.get(k) is not None}
        plan = analysis.plan_scene_cuts(self.session.doc, sequence_id=p.get("sequence_id"), **opts)
        if p.get("apply") and plan["points"]:
            self.session.begin("Scene Edit Detection")
            try:
                from .commands.project import seq_of
                from .commands.timeline import find_clip, linked_ids

                for pt in plan["points"]:
                    # the shot's clip and its linked audio, at the time the clip is cut (Premiere's Scene Edit Detection)
                    seq = seq_of(self.session.doc, p.get("sequence_id"))
                    track = seq["tracks"][find_clip(seq, pt["clip_id"])[0]]  # the left part keeps the clip's id
                    at_clip = next((c for c in track["clips"].values() if c["start"] < pt["time"] < c["end"]), None)
                    if at_clip is None:
                        continue
                    tids = sorted({find_clip(seq, cid)[0] for cid in linked_ids(seq, [at_clip["id"]])})
                    self.session.execute("clip.razor", {"sequence_id": seq["id"], "at": pt["time"], "track_ids": tids})
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise
            plan["applied"] = True
        return plan
