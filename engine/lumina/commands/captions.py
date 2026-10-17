"""Caption tracks: cues are plain text drawn in the track's style (Premiere's
captions with a track style), so they never need converting to graphics; track
effects (``effect.add`` / ``effect.apply_preset`` with ``track_ids``) animate
every cue. SRT import here, SRT export is the ``caption.export_srt`` RPC."""

from __future__ import annotations

import copy
from pathlib import Path

from pydantic import Field

from .. import timebase
from ..model import new_id
from ..tracks import CAPTION_LAYER_ID, STYLE_FIELDS, caption_layer, default_caption_style, is_caption_track

from .project import _make_track, seq_of
from .registry import CommandError, Context, Params, TimeValue, command, resolve_time
from .timeline import DEFAULT_MOTION, clear_range, clip_path, clips_sorted, find_clip, require_unlocked, write_clip

Style = dict[str, str | float | bool | list[float] | dict | None]
# the track's transform: never overridden per cue (Premiere)
TRANSFORM_FIELDS = {"position", "scale", "rotation", "opacity", "box", "vertical_align"}


def _check_style(style: Style) -> dict:
    out = {}
    for k, v in style.items():
        if k not in STYLE_FIELDS:
            raise CommandError("BAD_PARAM", f"unknown caption style field {k!r}; one of {list(STYLE_FIELDS)}")
        out[k] = v
    if "font_size" in out and not (isinstance(out["font_size"], (int, float)) and 0 < out["font_size"] <= 2000):
        raise CommandError("BAD_VALUE", "font_size is 0-2000")
    if "position" in out and not (isinstance(out["position"], list) and len(out["position"]) == 2):
        raise CommandError("BAD_VALUE", "position needs [x, y]")
    return out


def caption_clip(text: str, start: int, end: int) -> dict:
    return {
        "id": new_id(), "media_id": None, "sequence_id": None, "name": first_line(text), "start": start, "end": end,
        "source_in": 0, "speed": 1.0, "enabled": True, "link_id": None, "label": "yellow", "opacity": 100.0,
        "volume_db": 0.0, "gain_db": 0.0, "audio_tag": None, "muted": False, "auto_match": None, "motion": dict(DEFAULT_MOTION),
        "keyframes": {}, "graphic": None, "caption": {"text": text, "style": None}, "effects": [], "freeze": None, "adjustment": False,
    }


def first_line(text: str) -> str:
    return (text.strip().splitlines() or [""])[0][:80] or "Caption"


def _caption_track(seq: dict, track_id: str | None) -> str | None:
    if track_id is not None:
        track = seq["tracks"].get(track_id)
        if track is None:
            raise CommandError("NO_TRACK", f"track not found: {track_id}")
        if not is_caption_track(track):
            raise CommandError("NOT_CAPTION_TRACK", f"{track['name']} is not a caption track")
        return track_id
    for tid in reversed(seq["video_tracks"]):
        if is_caption_track(seq["tracks"][tid]):
            return tid
    return None


def new_caption_track(ctx: Context, seq: dict, name: str | None = None, style: dict | None = None) -> str:
    """A caption track above every video track (captions draw on top)."""
    track = _make_track("video", len(seq["video_tracks"]) + 1)
    n = sum(1 for t in seq["tracks"].values() if is_caption_track(t)) + 1
    track.update(name=name or (f"Subtitles {n}" if n > 1 else "Subtitles"), targeted=False,
                 caption_style={**default_caption_style(seq["settings"]), **(style or {})})
    ctx.m.set(["sequences", seq["id"], "tracks", track["id"]], track)
    ctx.m.set(["sequences", seq["id"], "video_tracks"], list(seq["video_tracks"]) + [track["id"]])
    return track["id"]


class AddCaptionTrack(Params):
    sequence_id: str | None = None
    name: str | None = None
    style: Style = Field(default_factory=dict, description="CaptionStyle fields to set (font_family, font_size, color, "
                                                           "stroke_color, stroke_width, background, align, position, ...)")


@command("caption.add_track", AddCaptionTrack)
def caption_add_track(ctx: Context, p: AddCaptionTrack):
    """New caption (subtitle) track: a video track whose cues are text in one track style."""
    seq = seq_of(ctx.doc, p.sequence_id)
    return {"track_id": new_caption_track(ctx, seq, p.name, _check_style(p.style))}


class SetCaptionStyle(Params):
    sequence_id: str | None = None
    track_id: str
    style: Style = Field(description="CaptionStyle fields to change; every cue on the track follows")


@command("caption.set_style", SetCaptionStyle)
def caption_set_style(ctx: Context, p: SetCaptionStyle):
    """Change a caption track's style (Premiere's track style): restyles every cue at once."""
    seq = seq_of(ctx.doc, p.sequence_id)
    tid = _caption_track(seq, p.track_id)
    require_unlocked(seq, tid)
    style = {**seq["tracks"][tid]["caption_style"], **_check_style(p.style)}
    ctx.m.set(["sequences", seq["id"], "tracks", tid, "caption_style"], style)
    return {"style": style}


class AddCaption(Params):
    sequence_id: str | None = None
    track_id: str | None = Field(default=None, description="caption track; default the topmost one (created if there is none)")
    text: str = Field(min_length=1)
    at: TimeValue | None = Field(default=None, description="default playhead")
    duration: TimeValue = "3s"


@command("caption.add", AddCaption)
def caption_add(ctx: Context, p: AddCaption):
    """Add a caption cue (it overwrites whatever cue it lands on)."""
    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    tid = _caption_track(seq, p.track_id) or new_caption_track(ctx, seq)
    require_unlocked(seq, tid)
    at = timebase.snap_to_frame(seq["playhead"] if p.at is None else resolve_time(p.at, st), st["fps_num"], st["fps_den"])
    dur = max(timebase.ticks_per_frame(st["fps_num"], st["fps_den"]),
              timebase.snap_to_frame(resolve_time(p.duration, st), st["fps_num"], st["fps_den"]))
    clear_range(ctx, seq, tid, at, at + dur)
    clip = caption_clip(p.text, at, at + dur)
    write_clip(ctx, seq, tid, clip)
    return {"clip_id": clip["id"], "track_id": tid}


class SetCaption(Params):
    sequence_id: str | None = None
    clip_id: str
    text: str | None = None
    style: Style | None = Field(default=None, description="per-cue overrides to merge (null values remove an override)")
    clear_style: bool = Field(default=False, description="drop every per-cue override (back to the track style)")


@command("caption.set", SetCaption)
def caption_set(ctx: Context, p: SetCaption):
    """Edit a cue's text and/or its per-cue style overrides."""
    seq = seq_of(ctx.doc, p.sequence_id)
    tid, clip = find_clip(seq, p.clip_id)
    require_unlocked(seq, tid)
    if clip.get("caption") is None:
        raise CommandError("NOT_CAPTION", "that clip is not a caption cue")
    cap = copy.deepcopy(clip["caption"])
    if p.text is not None:
        cap["text"] = p.text
    if p.clear_style:
        cap["style"] = None
    if p.style:
        moved = sorted(set(p.style) & TRANSFORM_FIELDS)
        if moved:
            raise CommandError("CAPTION_TRANSFORM", f"{', '.join(moved)}: caption cues share their track's transform (caption.set_style)")
        merged = {**(cap.get("style") or {}), **_check_style(p.style)}
        cap["style"] = {k: v for k, v in merged.items() if v is not None} or None
    path = clip_path(seq, tid, clip["id"])
    ctx.m.set(path + ["caption"], cap)
    if p.text is not None and clip["name"] == first_line(clip["caption"]["text"]):
        ctx.m.set(path + ["name"], first_line(p.text))


class ImportSrt(Params):
    sequence_id: str | None = None
    path: str
    track_id: str | None = Field(default=None, description="caption track to fill; default a new one named after the file")
    offset: TimeValue = Field(default=0, description="added to every cue time")
    replace: bool = Field(default=False, description="with track_id: delete the track's cues first")
    style: Style = Field(default_factory=dict, description="new track: CaptionStyle fields")


@command("caption.import_srt", ImportSrt)
def caption_import_srt(ctx: Context, p: ImportSrt):
    """Import an .srt file as caption cues (frame-snapped; overlapping cues are trimmed to follow each other)."""
    from ..adb.srt_parser import parse_srt_file

    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    parsed = parse_srt_file(p.path)
    if parsed.get("status") not in ("ok", "success", "parsed") and not parsed.get("cues"):
        raise CommandError("BAD_SRT", (parsed.get("error") or {}).get("message") or f"cannot read {p.path}")
    if p.track_id is None:
        tid = new_caption_track(ctx, seq, Path(p.path).stem, _check_style(p.style))
    else:
        tid = _caption_track(seq, p.track_id)
        require_unlocked(seq, tid)
        if p.replace:
            for c in list(seq["tracks"][tid]["clips"].values()):
                ctx.m.delete(clip_path(seq, tid, c["id"]))
    off = resolve_time(p.offset, st)
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    made, prev_end = [], 0
    for cue in parsed.get("cues") or []:
        text = (cue.get("text") or "").strip()
        if not text:
            continue
        s = timebase.snap_to_frame(round(cue["startMilliseconds"] * timebase.TICKS_PER_SECOND / 1000) + off, st["fps_num"], st["fps_den"])
        e = timebase.snap_to_frame(round(cue["endMilliseconds"] * timebase.TICKS_PER_SECOND / 1000) + off, st["fps_num"], st["fps_den"])
        s = max(s, prev_end, 0)
        e = max(e, s + tpf)
        clear_range(ctx, seq, tid, s, e)
        clip = caption_clip(text, s, e)
        write_clip(ctx, seq, tid, clip)
        made.append(clip["id"])
        prev_end = e
    return {"track_id": tid, "clip_ids": made, "count": len(made), "skipped": parsed.get("skippedCueCount", 0)}


class TrackRef(Params):
    sequence_id: str | None = None
    track_id: str


def _text_of(graphic: dict) -> tuple[str, dict] | None:
    texts = [l for l in graphic.get("layers", []) if l.get("type") == "text" and l.get("visible", True)]
    if len(texts) != 1 or any(l.get("type") == "shape" and l.get("visible", True) for l in graphic.get("layers", [])):
        return None
    return texts[0]["text"], texts[0]


@command("caption.from_graphics", TrackRef)
def caption_from_graphics(ctx: Context, p: TrackRef):
    """Turn a track of one-line text graphics (e.g. imported subtitles) into a caption track: the first
    graphic's text style becomes the track style, each graphic becomes a cue (differences from the track
    style are kept as per-cue overrides). Timing, Motion, keyframes and effects stay as they are."""
    seq = seq_of(ctx.doc, p.sequence_id)
    track = seq["tracks"].get(p.track_id)
    if track is None or track["kind"] != "video":
        raise CommandError("NO_TRACK", "not a video track")
    require_unlocked(seq, p.track_id)
    candidates = [(c, _text_of(c["graphic"])) for c in clips_sorted(track) if c.get("graphic")]
    candidates = [(c, t) for c, t in candidates if t is not None]
    if not candidates:
        raise CommandError("NOTHING_TO_CONVERT", "the track has no single-text graphics")
    was_caption = bool(track.get("caption_style"))
    style = track.get("caption_style") or {k: candidates[0][1][1].get(k) for k in STYLE_FIELDS if k in candidates[0][1][1]}
    ctx.m.set(["sequences", seq["id"], "tracks", p.track_id, "caption_style"], style)
    if not was_caption:
        # caption tracks sit above the video tracks
        ctx.m.set(["sequences", seq["id"], "video_tracks"], [t for t in seq["video_tracks"] if t != p.track_id] + [p.track_id])
    for c, (text, layer) in candidates:
        over = {k: layer[k] for k in STYLE_FIELDS if k in layer and layer[k] != style.get(k)}
        path = clip_path(seq, p.track_id, c["id"])
        ctx.m.set(path + ["caption"], {"text": text, "style": over or None})
        ctx.m.set(path + ["graphic"], None)
    return {"converted": len(candidates), "style": style}


class ToGraphics(Params):
    sequence_id: str | None = None
    track_id: str | None = Field(default=None, description="every cue of this caption track ...")
    clip_ids: list[str] = Field(default_factory=list, description="... or just these cues")


@command("caption.to_graphics", ToGraphics)
def caption_to_graphics(ctx: Context, p: ToGraphics):
    """Upgrade Caption to Graphic: cues become ordinary text graphic clips (editable layer by layer)."""
    seq = seq_of(ctx.doc, p.sequence_id)
    targets: list[tuple[str, dict]] = []
    if p.track_id:
        tid = _caption_track(seq, p.track_id)
        targets = [(tid, c) for c in clips_sorted(seq["tracks"][tid]) if c.get("caption")]
    for cid in p.clip_ids:
        targets.append(find_clip(seq, cid))
    out = []
    for tid, c in targets:
        require_unlocked(seq, tid)
        if not c.get("caption"):
            continue
        layer = caption_layer(seq["tracks"][tid].get("caption_style") or {}, c["caption"])
        layer["id"] = new_id() if layer["id"] == CAPTION_LAYER_ID else layer["id"]
        path = clip_path(seq, tid, c["id"])
        ctx.m.set(path + ["graphic"], {"layers": [layer], "source": "caption"})
        ctx.m.set(path + ["caption"], None)
        out.append(c["id"])
    return {"clip_ids": out}


def export_srt(doc: dict, sequence_id: str | None, track_id: str | None, path: str) -> dict:
    """Write a caption track's cues as an .srt file (not an edit)."""
    seq = seq_of(doc, sequence_id)
    tid = _caption_track(seq, track_id)
    if tid is None:
        raise CommandError("NO_CAPTION_TRACK", "the sequence has no caption track")

    def ts(t: int) -> str:
        ms = round(t * 1000 / timebase.TICKS_PER_SECOND)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    cues = [c for c in clips_sorted(seq["tracks"][tid]) if c.get("caption") and c["enabled"]]
    body = "\n".join(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{c['caption']['text'].strip()}\n" for i, c in enumerate(cues, 1))
    Path(path).write_text(body, encoding="utf-8")
    return {"path": path, "count": len(cues), "track_id": tid}



