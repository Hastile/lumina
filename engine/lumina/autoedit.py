"""Automatic edits: a whole cut from one JSON edit plan, and a planner that writes that plan.

The expensive part of agent editing is the round trips: one tool call per
clip, per caption, per transition, and frames to look at in between. Here the
decisions come in one document and the engine does the rest:

``build(engine, plan)`` imports what the plan uses, creates the sequence,
lays the clips end to end, adds transitions, captions, transcript subtitles
and music, as one undo step, then saves and exports if asked. Any agent (MCP
``build_edit``), a script, or ``lumina build plan.json`` can send a plan.

``plan_with_claude(root, brief)`` makes the plan from a library index
(``lumina.library``): an optional vision pass describes undescribed shots
from contact sheets (cached in the index), then one Claude call picks and
orders shots for the brief. ``lumina auto FOLDER "brief"`` runs both.

Plan format (times in seconds, in/out are positions inside the source file)::

    {
      "title": "Seoul vlog",
      "library": "/footage/seoul",            # resolves shot ids and relative file paths
      "format": {"aspect": "9:16"},           # or {"width": 1080, "height": 1920, "fps": 30}; default the first clip's
      "fit": "fill",                          # fill (crop) or fit (letterbox) when sizes differ
      "clips": [
        {"shot": "a1b2c3d4-3", "in": 12.0, "out": 15.5, "transition": "dissolve", "caption": "Day 1"},
        {"file": "drone/DJI_0042.MP4", "in": 0, "out": 4, "mute": true}
      ],
      "music": {"file": "music/track.mp3", "volume_db": -18},
      "subtitles": "transcript",              # or "none"
      "project": "/out/seoul.lumina",         # optional: save here
      "output": "/out/seoul.mp4"              # optional: export here (h264)
    }
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from . import library, timebase
from .commands.registry import CommandError

TPS = timebase.TICKS_PER_SECOND
TRANSITIONS = {"cut": None, "dissolve": "cross_dissolve", "cross_dissolve": "cross_dissolve", "dip_to_black": "dip_to_black",
               "dip_to_white": "dip_to_white"}
ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "4:3": (4, 3), "21:9": (21, 9)}


# ---------------------------------------------------------------- resolving the plan

def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _resolve_path(p: str, root: Path | None) -> str:
    path = Path(p).expanduser()
    if not path.is_absolute() and root is not None:
        path = root / path
    return str(path.resolve())


def resolve(plan: dict) -> tuple[list[dict], dict | None, list[str]]:
    """Plan clips as [{path, in, out, ...}] (shot ids looked up, ranges clamped), music, warnings."""
    root = Path(plan["library"]).resolve() if plan.get("library") else None
    data = library.load(root) if root else None
    warnings: list[str] = []
    out = []
    for i, c in enumerate(plan.get("clips") or []):
        entry = shot = None
        if c.get("shot"):
            if data is None:
                warnings.append(f"clip {i}: shot {c['shot']} needs plan.library; skipped")
                continue
            try:
                entry, shot = library.find_shot(data, c["shot"])
            except CommandError:
                warnings.append(f"clip {i}: unknown shot {c['shot']}; skipped")
                continue
            path = str(root / entry["rel"])
        elif c.get("file"):
            path = _resolve_path(c["file"], root)
            if data is not None:
                try:
                    rel = Path(path).relative_to(root).as_posix()
                    entry = data["files"].get(rel)
                except ValueError:
                    entry = None
        else:
            warnings.append(f"clip {i}: needs shot or file; skipped")
            continue
        if not Path(path).is_file():
            warnings.append(f"clip {i}: file not found {path}; skipped")
            continue
        kind = entry["kind"] if entry else library.media_kind(Path(path))
        duration = entry["duration"] if entry else None
        src_in = c.get("in")
        src_out = c.get("out")
        if src_in is None:
            src_in = shot["start"] if shot else 0.0
        if src_out is None:
            src_out = (shot["end"] if shot and kind != "image" else None)
        if kind == "image":
            length = float(c.get("duration") or ((src_out - src_in) if src_out is not None and src_out > src_in else 3.0))
            src_in, src_out = 0.0, length
        else:
            if duration is not None:
                src_in = min(max(0.0, float(src_in)), duration)
                src_out = duration if src_out is None else min(float(src_out), duration)
            if src_out is not None and src_out - src_in < 0.1:
                warnings.append(f"clip {i}: empty range {src_in}-{src_out}s; skipped")
                continue
        out.append({"index": i, "path": path, "kind": kind, "in": float(src_in), "out": None if src_out is None else float(src_out),
                    "entry": entry, "transition": c.get("transition") or "cut", "caption": (c.get("caption") or "").strip(),
                    "mute": bool(c.get("mute")), "volume_db": c.get("volume_db")})
    music = None
    m = plan.get("music")
    if isinstance(m, str):
        m = {"file": m}
    if m and (m.get("file") or m.get("shot")):
        if m.get("shot") and data is not None:
            try:
                entry, _s = library.find_shot(data, m["shot"])
                m = {**m, "file": entry["rel"]}
            except CommandError:
                warnings.append(f"music: unknown shot {m['shot']}; no music")
                m = None
        if m and m.get("file"):
            path = _resolve_path(m["file"], root)
            if Path(path).is_file():
                music = {"path": path, "volume_db": float(m.get("volume_db", -18)), "in": float(m.get("in", 0))}
            else:
                warnings.append(f"music: file not found {path}; no music")
    return out, music, warnings


def _settings(plan: dict, first_video: dict | None) -> dict:
    v = first_video or {}
    w, h = v.get("width") or 1920, v.get("height") or 1080
    fps_num, fps_den = v.get("fps_num") or 30, v.get("fps_den") or 1
    fmt = plan.get("format") or {}
    if fmt.get("fps"):
        fps_num, fps_den = timebase.parse_rate(str(fmt["fps"]))
    if fmt.get("width") and fmt.get("height"):
        w, h = int(fmt["width"]), int(fmt["height"])
    elif fmt.get("aspect"):
        if fmt["aspect"] not in ASPECTS:
            raise CommandError("BAD_PLAN", f"format.aspect is one of {sorted(ASPECTS)}")
        aw, ah = ASPECTS[fmt["aspect"]]
        short = min(w, h)  # keep the source's resolution class: 1080p stays 1080 on the short side
        w, h = (_even(short * aw / ah), short) if aw >= ah else (short, _even(short * ah / aw))
    return {"width": w, "height": h, "fps_num": fps_num, "fps_den": fps_den, "sample_rate": 48000}


# ---------------------------------------------------------------- building

def _ex(engine, name: str, params: dict) -> dict:
    return engine.dispatch("execute", {"name": name, "params": params, "label": f"Auto Edit: {name}"})["result"]


def build(engine, plan: dict) -> dict:
    """Build the plan's sequence in the engine's current project (one undo step); save/export when asked."""
    if not plan.get("clips"):
        raise CommandError("BAD_PLAN", "plan has no clips")
    clips, music, warnings = resolve(plan)
    if not any(c["kind"] in ("video", "image") for c in clips):
        raise CommandError("BAD_PLAN", "no usable video or image clips in the plan", warnings=warnings)

    summary = engine.dispatch("project.summary", {})
    have = {m["path"]: m["id"] for m in summary.get("media", []) if m.get("path")}
    need = sorted({c["path"] for c in clips} | ({music["path"]} if music else set()))
    engine.dispatch("transaction.begin", {"label": f"Auto Edit: {plan.get('title') or 'Sequence'}"})
    try:
        new = [p for p in need if p not in have]
        if new:
            res = _ex(engine, "media.import", {"paths": new})
            for err in res.get("errors", []):
                warnings.append(f"import failed: {err['path']}: {err['error']}")
            summary = engine.dispatch("project.summary", {})
            have = {m["path"]: m["id"] for m in summary.get("media", []) if m.get("path")}
        media = {m["id"]: m for m in summary.get("media", [])}
        clips = [c for c in clips if c["path"] in have]
        first_video = next(((media[have[c["path"]]].get("video")) for c in clips if c["kind"] == "video"), None)
        settings = _settings(plan, first_video)
        seq = _ex(engine, "sequence.create", {"name": plan.get("title") or "Auto Edit", "settings": settings,
                                              "video_tracks": 3, "audio_tracks": 3})
        sid, vt, at = seq["sequence_id"], seq["video_tracks"], seq["audio_tracks"]
        fit = plan.get("fit", "fill")
        t, placed = 0, []
        for c in clips:
            mid = have[c["path"]]
            item = media[mid]
            params: dict[str, Any] = {"sequence_id": sid, "media_id": mid, "at": t, "mode": "overwrite",
                                      "video_track_id": vt[0], "audio_track_id": at[0], "source_in": round(c["in"] * TPS)}
            if c["out"] is not None:
                params["source_out"] = round(c["out"] * TPS)
            if c["kind"] == "image":
                params.pop("source_in")
            try:
                r = _ex(engine, "clip.place", params)
            except CommandError as exc:
                warnings.append(f"clip {c['index']}: {exc.message}")
                continue
            vcid, acid = r.get("video_clip_id"), r.get("audio_clip_id")
            iv = item.get("video") or {}
            if vcid and iv and (iv.get("width"), iv.get("height")) != (settings["width"], settings["height"]):
                _ex(engine, "clip.fit_to_frame", {"sequence_id": sid, "clip_ids": [vcid], "mode": fit})
            if acid and (c["mute"] or c["volume_db"] is not None):
                params = {"sequence_id": sid, "clip_ids": [acid]}
                params.update({"muted": True} if c["mute"] else {"volume_db": max(-96.0, min(15.0, float(c["volume_db"])))})
                _ex(engine, "clip.set", params)
            placed.append({**c, "video_clip_id": vcid, "audio_clip_id": acid, "start": r["start"], "end": r["end"]})
            t = r["end"]
        if not placed:
            raise CommandError("BAD_PLAN", "no clip could be placed", warnings=warnings)

        for n, c in enumerate(placed):
            ttype = TRANSITIONS.get(str(c["transition"]).lower())
            if not ttype or not c["video_clip_id"] or (n == 0 and ttype == "cross_dissolve"):
                continue
            try:
                _ex(engine, "transition.add", {"sequence_id": sid, "clip_id": c["video_clip_id"], "edge": "start",
                                               "type": ttype, "duration": round(0.5 * TPS)})
            except CommandError as exc:
                warnings.append(f"clip {c['index']}: transition skipped ({exc.message})")

        captions = 0
        subs = plan.get("subtitles", "none") == "transcript"
        if subs or any(c["caption"] for c in placed):
            track = _ex(engine, "caption.add_track", {"sequence_id": sid, "name": "Subtitles"})
            ctid = track.get("track_id") or track.get("id")
            for c in placed:
                if c["caption"]:
                    _ex(engine, "caption.add", {"sequence_id": sid, "track_id": ctid, "text": c["caption"], "at": c["start"],
                                                "duration": c["end"] - c["start"]})
                    captions += 1
                    continue
                if not subs or not c["entry"] or not c["entry"].get("transcript") or c["mute"]:
                    continue
                for seg in c["entry"]["transcript"]["segments"]:
                    s = c["start"] + round((seg["start"] - c["in"]) * TPS)
                    e = c["start"] + round((seg["end"] - c["in"]) * TPS)
                    s, e = max(s, c["start"]), min(e, c["end"])
                    if e - s < 0.3 * TPS:
                        continue
                    _ex(engine, "caption.add", {"sequence_id": sid, "track_id": ctid, "text": seg["text"], "at": s, "duration": e - s})
                    captions += 1

        music_clips = 0
        if music and music["path"] in have:
            mid = have[music["path"]]
            mdur = round(media[mid]["duration_seconds"] * TPS)
            pos, src = 0, round(music["in"] * TPS)
            while pos < t and mdur - src > TPS // 10:
                take = min(mdur - src, t - pos)
                r = _ex(engine, "clip.place", {"sequence_id": sid, "media_id": mid, "at": pos, "audio_track_id": at[1],
                                                "take_video": False, "source_in": src, "source_out": src + take})
                _ex(engine, "clip.set", {"sequence_id": sid, "clip_ids": [r["audio_clip_id"]],
                                         "volume_db": max(-96.0, min(15.0, music["volume_db"]))})
                music_clips += 1
                pos, src = r["end"], 0
        engine.dispatch("transaction.commit", {})
    except Exception:
        engine.dispatch("transaction.rollback", {})
        raise

    result: dict[str, Any] = {"sequence_id": sid, "title": plan.get("title") or "Auto Edit", "settings": settings,
                              "clips": len(placed), "duration": round(t / TPS, 3), "captions": captions,
                              "music_clips": music_clips, "warnings": warnings}
    if plan.get("project"):
        engine.dispatch("project.save", {"path": plan["project"]})
        result["project"] = plan["project"]
    if plan.get("output"):
        job = engine.dispatch("export.start", {"sequence_id": sid, "path": plan["output"], "preset": plan.get("preset", "h264"),
                                               "wait": True})
        result["export"] = job.get("result") if job.get("status") == "done" else {"status": job.get("status"), "error": job.get("error")}
    return result


# ---------------------------------------------------------------- planning with Claude

DEFAULT_MODEL = "claude-opus-5-5"

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "clips": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "shot": {"type": "string", "description": "shot id from the library"},
                "in": {"type": "number", "description": "seconds into the source file"},
                "out": {"type": "number", "description": "seconds into the source file"},
                "transition": {"type": "string", "enum": ["cut", "dissolve", "dip_to_black"]},
                "caption": {"type": "string", "description": "on-screen text for this clip, or empty"},
                "mute": {"type": "boolean"},
            },
            "required": ["shot", "in", "out", "transition", "caption", "mute"],
            "additionalProperties": False,
        }},
        "music": {"type": "string", "description": "library-relative path of an audio file for music, or empty"},
        "music_volume_db": {"type": "number"},
        "subtitles": {"type": "string", "enum": ["none", "transcript"]},
        "notes": {"type": "string", "description": "one or two sentences on the edit's structure, for the user"},
    },
    "required": ["title", "clips", "music", "music_volume_db", "subtitles", "notes"],
    "additionalProperties": False,
}

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {"shots": {"type": "array", "items": {
        "type": "object",
        "properties": {"shot": {"type": "string"}, "desc": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}},
        "required": ["shot", "desc", "tags"], "additionalProperties": False}}},
    "required": ["shots"], "additionalProperties": False,
}

PLANNER_SYSTEM = """You are the editor for an automated video pipeline. You receive a creative brief and a log of
the available footage: every file, its shots (id, source time range, flags, description, tags) and, when
available, a timestamped transcript. You return an edit decision list that a program assembles exactly.

How to edit:
- Serve the brief: structure (hook, body, ending), pacing and target length come from it.
- Every clip references a shot id. Its in/out are seconds in that shot's source file and should stay inside
  that shot's range; trim to the strongest part rather than using whole shots.
- When speech matters, cut on transcript segment boundaries so sentences are never cut off, and leave the
  audio on. Mute b-roll whose sound would clash with speech or music.
- Avoid shots flagged dark or soft unless nothing else covers the moment.
- Do not reuse the same source range twice.
- Use "cut" by default; "dissolve" for time passing or mood changes; "dip_to_black" sparingly, for openings,
  endings or chapter breaks.
- Captions are short on-screen text (titles, chapter names, key words), in the brief's language; leave them
  empty for most clips. Choose subtitles "transcript" when the edit is speech-driven.
- Pick music only from audio files in the log, and only when the brief calls for music or the edit has little
  speech; keep it under speech (-20 dB to -14 dB).
- The sum of (out - in) over all clips is the runtime; land within 10% of the target length when one is given."""


def _client(client=None):
    if client is not None:
        return client
    try:
        import anthropic
    except ImportError:
        raise CommandError("NO_ANTHROPIC", "the planner needs the anthropic package: uv sync --extra auto") from None
    return anthropic.Anthropic()


def _ask(client, model: str, effort: str, system: str, content: list[dict], schema: dict, max_tokens: int = 16000) -> dict:
    """One structured-output request; refusals fall back to another model server-side."""
    response = client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        raise CommandError("PLANNER_REFUSED", f"the model declined: {getattr(detail, 'explanation', None) or 'no reason given'}")
    if response.stop_reason == "max_tokens":
        raise CommandError("PLANNER_TRUNCATED", "the plan did not fit in max_tokens; shorten the brief or the library")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise CommandError("PLANNER_EMPTY", "the model returned no plan")
    return json.loads(text)


def describe_shots(root: str | Path, *, model: str = DEFAULT_MODEL, client=None, progress=None, force: bool = False) -> dict:
    """Vision pass: one contact sheet per file whose shots have no description; writes desc/tags to the index."""
    client = _client(client)
    data = library.load(root)
    described = 0
    for f in data["files"].values():
        if f.get("error") or f["kind"] not in ("video", "image"):
            continue
        todo = [s for s in f["shots"] if s.get("keyframe") and (force or not s.get("desc"))]
        for start in range(0, len(todo), 60):
            batch = todo[start:start + 60]
            if progress:
                progress(f"describing {f['rel']} ({len(batch)} shots)")
            sheet = library.contact_sheet(root, shot_ids=[s["id"] for s in batch])
            img = base64.standard_b64encode(Path(sheet["path"]).read_bytes()).decode("ascii")
            tr = " ".join(seg["text"] for seg in (f.get("transcript") or {}).get("segments", []))[:4000]
            prompt = (f"Contact sheet of '{f['rel']}': one keyframe per shot, each labelled with its shot id. "
                      "For every labelled shot, write a one-line factual description (subject, action, setting, shot size, "
                      "camera movement if obvious) and 3-6 lowercase tags useful for search, in English. "
                      + (f"Transcript of the file, for context: {tr}" if tr else ""))
            out = _ask(client, model, "low", "You log footage for a video editor. Be concrete and brief.",
                       [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
                        {"type": "text", "text": prompt}], DESCRIBE_SCHEMA, 8000)
            wanted = {s["id"] for s in batch}
            res = library.annotate(root, [n for n in out["shots"] if n["shot"] in wanted])
            described += res["updated"]
    return {"described": described}


def plan_with_claude(root: str | Path, brief: str, *, duration: float | None = None, aspect: str | None = None,
                     model: str = DEFAULT_MODEL, effort: str = "high", client=None) -> dict:
    """One planning call over the library log; returns a plan ready for ``build``."""
    client = _client(client)
    log = library.overview_text(root)
    ask = [f"Brief: {brief}"]
    if duration:
        ask.append(f"Target length: {duration:g} seconds.")
    if aspect:
        ask.append(f"Output aspect ratio: {aspect} (the program crops to fill).")
    content = [{"type": "text", "text": f"<footage_log>\n{log}\n</footage_log>", "cache_control": {"type": "ephemeral"}},
               {"type": "text", "text": "\n".join(ask)}]
    out = _ask(client, model, effort, PLANNER_SYSTEM, content, PLAN_SCHEMA)
    plan: dict[str, Any] = {
        "title": out["title"], "library": str(Path(root).resolve()),
        "clips": [{k: c[k] for k in ("shot", "in", "out", "transition", "caption", "mute")} for c in out["clips"]],
        "subtitles": out["subtitles"], "notes": out.get("notes", ""),
    }
    if out.get("music"):
        plan["music"] = {"file": out["music"], "volume_db": out.get("music_volume_db", -18)}
    if aspect:
        plan["format"] = {"aspect": aspect}
    return plan
