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
    """Open projects (Premiere keeps several open): one Session each; one is active and every
    command, query and agent works on it. ``session`` is the active one."""

    def __init__(self, services: Any = None, run_jobs: bool = True):
        self.services = services or MediaServices()
        self.run_jobs = run_jobs
        self._listeners: list[Callable[[dict], None]] = []
        self.projects: dict[str, Session] = {}
        self.active = ""
        self._projects_sig = None
        self._add(self._blank())
        self._queued: set[str] = set()
        self.jobs = JobRunner(record=self._record_derived, emit=self._broadcast)
        self.exports = export_mod.ExportQueue(emit=self._broadcast)
        from . import media_cache

        media_cache.auto_clean(self._media_in_use)
        self.methods: dict[str, Callable[[dict], Any]] = {
            "hello": lambda p: {"engine": "lumina", "api": 1, "commands": REGISTRY.describe(), **self.session.snapshot(),
                                "projects": self._project_list()},
            "project.list": lambda p: self._project_list(),
            "project.activate": self._activate,
            "project.close": self._close,
            "project.close_all": self._close_all,
            "cache.info": lambda p: self._cache().info(self._media_in_use()),
            "cache.clean": self._cache_clean,
            "cache.settings": lambda p: self._cache().update_settings(p),
            "commands.list": lambda p: REGISTRY.describe(),
            "execute": self._execute,
            "undo": lambda p: self.session.undo(),
            "redo": lambda p: self.session.redo(),
            "transaction.begin": lambda p: self.session.begin(p.get("label", "Edit")),
            "transaction.commit": lambda p: self.session.commit(),
            "transaction.rollback": lambda p: self.session.rollback(),
            "snapshot": lambda p: self.session.snapshot(),
            "project.new": self._new,
            "project.open": self._open,
            "project.save": lambda p: self._session_of(p).save(p.get("path")),
            "project.summary": lambda p: views.project_summary(self.session.doc),
            "timeline.state": lambda p: views.timeline_state(self.session.doc, p.get("sequence_id")),
            "clip.get": lambda p: views.clip_details(self.session.doc, p.get("sequence_id"), p.get("clip_ids"), p.get("track_id")),
            "jobs.list": lambda p: self.jobs.list() + self.exports.list(),
            "export.presets": lambda p: export_mod.presets(),
            "render.graphic": self._render_graphic,
            "presets.list": lambda p: presets_mod.load(),
            "presets.import_premiere": lambda p: presets_mod.import_premiere(p.get("path")),
            "presets.remove": lambda p: {"removed": presets_mod.remove(p["preset_id"])},
            "styles.list": lambda p: self._styles().load(),
            "styles.import_premiere": lambda p: self._styles().import_premiere(p.get("path")),
            "styles.remove": lambda p: {"removed": self._styles().remove(p["style_id"])},
            "styles.save": self._styles_save,
            "styles.set_default": lambda p: {"default": self._styles().set_default(p.get("style_id"))},
            "export.start": self._export_start,
            "export.cancel": lambda p: self.exports.cancel(p["job_id"]),
            "export.frame": self._export_frame,
            "time.convert": self._convert_time,
            "caption.export_srt": self._export_srt,
            "media.scan": lambda p: self._media_scan(),
            "analysis.detect": self._analysis_detect,
            "analysis.plan_cuts": self._analysis_plan_cuts,
            "analysis.plan_scene_cuts": self._analysis_plan_scene,
            "shorts.plan": self._shorts_plan,
            "shorts.check": self._shorts_check,
        }

    # ------------------------------------------------------------ dispatch

    def dispatch(self, method: str, params: dict | None = None) -> Any:
        fn = self.methods.get(method)
        if fn is None:
            raise CommandError("UNKNOWN_METHOD", f"unknown method {method!r}")
        return fn(params or {})

    def subscribe(self, fn):
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    # ------------------------------------------------------------ open projects

    @property
    def session(self) -> Session:
        return self.projects[self.active]

    def _session_of(self, p: dict) -> Session:
        key = p.get("key") or self.active
        if key not in self.projects:
            raise CommandError("NO_PROJECT", f"project not open: {key}")
        return self.projects[key]

    def _broadcast(self, event: dict) -> None:
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - a broken client must not break editing
                pass

    def _add(self, s: Session) -> str:
        from .model import new_id

        key = new_id()
        self.projects[key] = s
        s.subscribe(lambda ev, k=key: self._from_session(k, ev))
        if not self.active:
            self.active = key
        return key

    def _from_session(self, key: str, ev: dict) -> None:
        if key == self.active:
            self._broadcast(ev)
        if ev.get("type") in ("patch", "saved", "reset", "history"):
            self._announce_projects()

    def _project_list(self) -> list[dict]:
        # named like Premiere's project tabs: the file name once saved
        return [{"key": k, "name": Path(s.path).stem if s.path else s.doc["name"], "path": s.path, "dirty": s.version != s.saved_version, "active": k == self.active,
                 "blank": self._pristine(s)} for k, s in self.projects.items()]

    def _announce_projects(self) -> None:
        lst = self._project_list()
        sig = [tuple(x.values()) for x in lst]
        if sig != self._projects_sig:
            self._projects_sig = sig
            self._broadcast({"type": "projects", "projects": lst})

    def _blank(self) -> Session:
        s = Session(services=self.services)
        s.placeholder = True  # the blank project shown when none is open
        return s

    @staticmethod
    def _pristine(s: Session) -> bool:
        """The untouched blank project (at start, after closing the last one): New / Open take its place."""
        return getattr(s, "placeholder", False) and s.path is None and s.version == 0 and not s.undo_stack

    def _switch(self, key: str) -> dict:
        self.active = key
        snap = self.session.snapshot()
        self._broadcast({"type": "reset", "version": snap["version"], "document": snap["document"], "path": snap["path"],
                         **self.session.history_state()})
        self._announce_projects()
        self._queued.clear()
        self._submit_missing_jobs()
        return snap

    def _new(self, p: dict) -> dict:
        if self._pristine(self.session):
            snap = self.session.new(p.get("name", "Untitled"), p.get("template"))
            self.session.placeholder = False
            self._submit_missing_jobs()
            self._announce_projects()
            return snap
        s = Session(services=self.services)
        s.new(p.get("name", "Untitled"), p.get("template"))
        return self._switch(self._add(s))

    def _activate(self, p: dict) -> dict:
        key = p.get("key")
        if key not in self.projects:
            raise CommandError("NO_PROJECT", f"project not open: {key}")
        return self._switch(key) if key != self.active else self.session.snapshot()

    def _close(self, p: dict) -> dict:
        """Close a project (default: the active one). Unsaved changes refuse unless discard=true (the GUI
        asks Save / Don't Save / Cancel first). Closing the last one leaves a blank project."""
        key = p.get("key") or self.active
        s = self.projects.get(key)
        if s is None:
            raise CommandError("NO_PROJECT", f"project not open: {key}")
        if s.version != s.saved_version and not p.get("discard"):
            raise CommandError("UNSAVED", f"{s.doc['name']} has unsaved changes", key=key, name=s.doc["name"])
        s.discard_recovery()
        del self.projects[key]
        if not self.projects:
            self.active = ""
            self._add(self._blank())
            return self._switch(self.active)
        if key == self.active:
            return self._switch(list(self.projects)[-1])
        self._announce_projects()
        return self.session.snapshot()

    def _close_all(self, p: dict) -> dict:
        dirty = [k for k, s in self.projects.items() if s.version != s.saved_version]
        if dirty and not p.get("discard"):
            raise CommandError("UNSAVED", "projects have unsaved changes", keys=dirty,
                               names=[self.projects[k].doc["name"] for k in dirty])
        for s in self.projects.values():
            s.discard_recovery()
        self.projects.clear()
        self.active = ""
        self._add(self._blank())
        return self._switch(self.active)

    def _media_in_use(self) -> set[str]:
        return {mid for s in self.projects.values() for mid in s.doc["media"]}

    @staticmethod
    def _cache():
        from . import media_cache

        return media_cache

    def _cache_clean(self, p: dict) -> dict:
        """Delete media cache: unused (no open project's media), all, or older than N days. Open projects'
        derived files that were deleted are forgotten and regenerated."""
        mc = self._cache()
        try:
            res = mc.clean(p.get("mode", "unused"), self._media_in_use(), p.get("days"))
        except ValueError as exc:
            raise CommandError("BAD_PARAMS", str(exc)) from exc
        gone = set(res["removed_media"])
        for s in self.projects.values():
            for mid, item in s.doc["media"].items():
                if mid in gone or any(item.get(k) and not Path(item[k]).exists()
                                      for k in ("thumbnail_path", "waveform_path", "proxy_path", "filmstrip_path")):
                    s.execute("media.set_derived", {"media_id": mid, "clear": True})
        self._queued.clear()
        self._submit_missing_jobs()
        return res

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
        """Open a project next to the open ones (Premiere); one already open is brought to front."""
        target = str(Path(p["path"]).resolve())
        for k, s in self.projects.items():
            if s.path and str(Path(s.path).resolve()).lower() == target.lower():
                return self._switch(k) if k != self.active else s.snapshot()
        if self._pristine(self.session):
            snap = self.session.open(p["path"])
            self.session.placeholder = False
            self._queued.clear()
            self._submit_missing_jobs()
            self._announce_projects()
            return snap
        s = Session(services=self.services)
        snap = s.open(p["path"])
        self._switch(self._add(s))
        return snap

    def _record_derived(self, media_id: str, fields: dict) -> None:
        for s in self.projects.values():  # the project the media belongs to, active or not
            if media_id in s.doc["media"]:
                s.execute("media.set_derived", {"media_id": media_id, **fields})

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

    # ------------------------------------------------------------ text styles

    @staticmethod
    def _styles():
        from . import text_styles

        return text_styles

    def _styles_save(self, p: dict) -> dict:
        """Save a caption track's formatting as a text style (Premiere's Create Style)."""
        from .commands.project import seq_of

        seq = seq_of(self.session.doc, p.get("sequence_id"))
        track = seq["tracks"].get(p.get("track_id") or "")
        if not track or not track.get("caption_style"):
            raise CommandError("NOT_CAPTION_TRACK", "styles.save needs a caption track")
        ts = self._styles()
        return ts.save_style(p.get("name") or track["name"], track["caption_style"], ts.transform_of(track["caption_style"], seq["settings"]))

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

    def _shorts_check(self, p: dict) -> dict:
        """The Short's edit checked against the grammar as text (read-only)."""
        from . import shorts_check
        from .commands.project import seq_of

        rep = shorts_check.check(self.session.doc, seq_of(self.session.doc, p.get("sequence_id")),
                                 use_db=p.get("use_db", True) is not False, profile=p.get("profile", "shorts"))
        return rep if p.get("format") == "json" else {"ok": rep["ok"], "report": shorts_check.render(rep)}

    def _shorts_plan(self, p: dict) -> dict:
        """Draft a Short's shots, caption effects and sounds from its captions (read-only)."""
        from pathlib import Path

        from . import shorts_plan as sp
        from .commands.project import seq_of

        seq = seq_of(self.session.doc, p.get("sequence_id"))
        st = seq["settings"]
        if p.get("srt"):
            cues = sp.parse_srt(Path(p["srt"]).read_text(encoding="utf-8-sig"))
        else:
            cues = sp.cues_from_sequence(seq)
        if not cues:
            raise CommandError("NO_CAPTIONS", "no caption cues on the sequence's caption tracks (or give srt)")
        reveal = tuple(int(i) for i in p.get("reveal") or ())
        plan = sp.make_plan(cues, reveal=reveal, min_shot=float(p.get("min_shot", 1.1)))
        if p.get("format", "text") == "json":
            return sp.to_dict(plan, st["fps_num"], st["fps_den"])
        return {"plan": sp.render(plan, st["fps_num"], st["fps_den"]),
                "shots": sp.to_dict(plan, st["fps_num"], st["fps_den"])["shots"]}

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
