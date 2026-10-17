"""Project items that are not media files: captions (their cues), Motion Graphics
Templates and Adjustment Layers. ``asset.place`` puts one on the timeline the way
Premiere does when the item is dragged in."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from .. import timebase
from ..model import new_id
from .captions import caption_clip, new_caption_track, _caption_track
from .project import bin_id_or_root, seq_of
from .registry import CommandError, Context, Params, TimeValue, command, resolve_time
from .timeline import DEFAULT_MOTION, clear_range, require_unlocked, write_clip


def asset_of(doc: dict, asset_id: str) -> dict:
    a = doc.get("assets", {}).get(asset_id)
    if a is None:
        raise CommandError("NO_ASSET", f"project item not found: {asset_id}")
    return a


def srt_cues(path: str) -> list[dict]:
    from ..adb.srt_parser import parse_srt_file

    parsed = parse_srt_file(path)
    if not parsed.get("cues"):
        raise CommandError("BAD_SRT", (parsed.get("error") or {}).get("message") or f"no captions in {path}")
    tps = timebase.TICKS_PER_SECOND
    return [{"start": round(c["startMilliseconds"] * tps / 1000), "end": round(c["endMilliseconds"] * tps / 1000), "text": c["text"].strip()}
            for c in parsed["cues"] if (c.get("text") or "").strip()]


class AddAsset(Params):
    kind: Literal["captions", "template", "adjustment"]
    path: str | None = Field(default=None, description="captions: an .srt file; template: a .mogrt / .aegraphic")
    name: str | None = None
    bin_id: str | None = None


@command("asset.add", AddAsset)
def asset_add(ctx: Context, p: AddAsset):
    """New project item: import captions (.srt) or a Motion Graphics Template, or New Item > Adjustment Layer."""
    bid = bin_id_or_root(ctx.doc, p.bin_id)
    cues = None
    if p.kind in ("captions", "template"):
        if not p.path or not Path(p.path).is_file():
            raise CommandError("NO_FILE", f"file not found: {p.path}")
        if p.kind == "captions":
            cues = srt_cues(p.path)
    name = p.name or (Path(p.path).name if p.path else "Adjustment Layer")
    aid = new_id()
    ctx.m.set(["assets", aid], {"id": aid, "name": name, "bin_id": bid, "kind": p.kind, "path": p.path, "cues": cues,
                                "label": "lavender" if p.kind == "adjustment" else "rose"})
    return {"asset_id": aid}


class AssetRef(Params):
    asset_ids: list[str] = Field(min_length=1)


@command("asset.remove", AssetRef)
def asset_remove(ctx: Context, p: AssetRef):
    """Delete project items (clips already on timelines stay)."""
    for aid in p.asset_ids:
        asset_of(ctx.doc, aid)
        ctx.m.delete(["assets", aid])


class RenameAsset(Params):
    asset_id: str
    name: str = Field(min_length=1)


@command("asset.rename", RenameAsset)
def asset_rename(ctx: Context, p: RenameAsset):
    asset_of(ctx.doc, p.asset_id)
    ctx.m.set(["assets", p.asset_id, "name"], p.name)


class PlaceAsset(Params):
    sequence_id: str | None = None
    asset_id: str
    at: TimeValue | None = Field(default=None, description="default: the playhead (captions: the sequence start, where they were timed)")
    track_id: str | None = Field(default=None, description="adjustment/template: a video track; captions: a caption track (default a new one)")
    duration: TimeValue = Field(default="5s", description="adjustment layers and templates")


@command("asset.place", PlaceAsset)
def asset_place(ctx: Context, p: PlaceAsset):
    """Put a project item on the timeline: captions become cues on a caption track, a template a graphic
    clip, an adjustment layer an adjustment clip on the first free video track above what is there."""
    a = asset_of(ctx.doc, p.asset_id)
    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    if a["kind"] == "template":
        from .motion import PlaceMogrt, graphic_place_mogrt

        if not a.get("path"):
            raise CommandError("NO_FILE", "the template file is missing")
        return graphic_place_mogrt(ctx, PlaceMogrt(sequence_id=seq["id"], path=a["path"], at=p.at, duration=p.duration, track_id=p.track_id))
    if a["kind"] == "captions":
        off = 0 if p.at is None else resolve_time(p.at, st)
        tid = _caption_track(seq, p.track_id) if p.track_id else new_caption_track(ctx, seq, Path(a["name"]).stem)
        require_unlocked(seq, tid)
        made, prev = [], 0
        tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
        for c in a.get("cues") or []:
            s = max(timebase.snap_to_frame(c["start"] + off, st["fps_num"], st["fps_den"]), prev, 0)
            e = max(timebase.snap_to_frame(c["end"] + off, st["fps_num"], st["fps_den"]), s + tpf)
            clear_range(ctx, seq, tid, s, e)
            clip = caption_clip(c["text"], s, e)
            write_clip(ctx, seq, tid, clip)
            made.append(clip["id"])
            prev = e
        return {"track_id": tid, "clip_ids": made, "count": len(made)}
    # adjustment layer
    from .motion import _free_track_above

    start = seq["playhead"] if p.at is None else resolve_time(p.at, st)
    end = start + max(resolve_time(p.duration, st), timebase.ticks_per_frame(st["fps_num"], st["fps_den"]))
    tid = p.track_id or _free_track_above(ctx, seq, start, end)
    if tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != "video" or seq["tracks"][tid].get("caption_style"):
        raise CommandError("BAD_TRACK", "adjustment layers go on a video track")
    require_unlocked(seq, tid)
    clear_range(ctx, seq, tid, start, end)
    clip = {
        "id": new_id(), "media_id": None, "sequence_id": None, "name": a["name"], "start": start, "end": end, "source_in": 0,
        "speed": 1.0, "enabled": True, "link_id": None, "label": a.get("label") or "lavender", "opacity": 100.0, "volume_db": 0.0,
        "gain_db": 0.0, "audio_tag": None, "muted": False, "auto_match": None, "motion": dict(DEFAULT_MOTION), "keyframes": {},
        "graphic": None, "caption": None, "effects": [], "freeze": None, "adjustment": True,
    }
    write_clip(ctx, seq, tid, clip)
    return {"clip_id": clip["id"], "track_id": tid}
