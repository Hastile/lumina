"""Nested sequences (Premiere's Nest): a clip that shows another sequence.

``sequence.nest`` moves the selected clips into a new sequence and puts one nested clip
(video and audio, linked) in their place; ``clip.place_sequence`` drops an existing
sequence onto the timeline. A nested clip's ``source_in`` is a time in its sequence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..model import new_id
from .project import build_sequence, seq_of
from .registry import CommandError, Context, Params, TimeValue, command, resolve_time
from .timeline import (DEFAULT_MOTION, clear_range, clip_path, default_track, find_clip, linked_ids, razor_track,
                       require_unlocked, sequence_end, shift_from, sync_tracks, write_clip)


def contains(doc: dict, seq_id: str, target: str, seen: frozenset = frozenset()) -> bool:
    """Does sequence ``seq_id`` show ``target`` (itself, or through nested clips at any depth)?"""
    if seq_id == target:
        return True
    if seq_id in seen:
        return False
    seq = doc["sequences"].get(seq_id)
    if seq is None:
        return False
    for t in seq["tracks"].values():
        for c in t["clips"].values():
            if c.get("sequence_id") and contains(doc, c["sequence_id"], target, seen | {seq_id}):
                return True
    return False


def nested_clip(inner: dict, start: int, end: int, source_in: int = 0, link_id: str | None = None) -> dict:
    return {
        "id": new_id(), "media_id": None, "sequence_id": inner["id"], "name": inner["name"], "start": start, "end": end,
        "source_in": source_in, "speed": 1.0, "enabled": True, "link_id": link_id, "label": "rose", "opacity": 100.0,
        "volume_db": 0.0, "gain_db": 0.0, "audio_tag": None, "motion": dict(DEFAULT_MOTION), "keyframes": {}, "graphic": None,
        "effects": [],
    }


def _has_audio(doc: dict, seq: dict) -> bool:
    return any(seq["tracks"][t]["clips"] for t in seq["audio_tracks"])


def _has_video(doc: dict, seq: dict) -> bool:
    return any(seq["tracks"][t]["clips"] for t in seq["video_tracks"])


class Nest(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    name: str | None = Field(default=None, description="default 'Nested Sequence N'")
    include_linked: bool = True


@command("sequence.nest", Nest)
def sequence_nest(ctx: Context, p: Nest):
    """Nest (Clip > Nest): move the clips into a new sequence with the same settings, keeping their tracks
    and spacing, and replace them with one nested clip (linked video and audio) spanning them."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    ids = linked_ids(seq, p.clip_ids) if p.include_linked else list(dict.fromkeys(p.clip_ids))
    found = []
    for cid in ids:
        tid, c = find_clip(seq, cid)
        require_unlocked(seq, tid)
        found.append((tid, c))
    s = min(c["start"] for _, c in found)
    e = max(c["end"] for _, c in found)
    vt = [seq["video_tracks"].index(t) for t, _ in found if t in seq["video_tracks"]]
    at = [seq["audio_tracks"].index(t) for t, _ in found if t in seq["audio_tracks"]]
    n = 1 + sum(1 for q in doc["sequences"].values() if q["name"].startswith("Nested Sequence"))
    name = p.name or f"Nested Sequence {n:02d}"
    inner = build_sequence(name, seq["bin_id"], dict(seq["settings"]), max([2, *[i + 1 for i in vt]]), max([2, *[i + 1 for i in at]]))
    links: dict[str, str] = {}
    for tid, c in found:
        kind = seq["tracks"][tid]["kind"]
        order = seq["video_tracks"] if kind == "video" else seq["audio_tracks"]
        dest = (inner["video_tracks"] if kind == "video" else inner["audio_tracks"])[order.index(tid)]
        moved = dict(c, start=c["start"] - s, end=c["end"] - s,
                     link_id=links.setdefault(c["link_id"], new_id()) if c.get("link_id") else None)
        inner["tracks"][dest]["clips"][moved["id"]] = moved
        ctx.m.delete(clip_path(seq, tid, c["id"]))
    # transitions whose clips all move go along (the rest are pruned with their clips)
    moved_ids = {c["id"] for _, c in found}
    for tid in {t for t, _ in found}:
        kind = seq["tracks"][tid]["kind"]
        order = seq["video_tracks"] if kind == "video" else seq["audio_tracks"]
        dest = (inner["video_tracks"] if kind == "video" else inner["audio_tracks"])[order.index(tid)]
        for trid, tr in (seq["tracks"][tid].get("transitions") or {}).items():
            ends = [x for x in (tr.get("clip_a"), tr.get("clip_b")) if x]
            if ends and all(x in moved_ids for x in ends):
                inner["tracks"][dest]["transitions"][trid] = dict(tr)
    ctx.m.set(["sequences", inner["id"]], inner)
    link = new_id() if vt and at else None
    out = {"nested_sequence_id": inner["id"]}
    if vt:
        v = nested_clip(inner, s, e, link_id=link)
        write_clip(ctx, seq, seq["video_tracks"][min(vt)], v)
        out["video_clip_id"] = v["id"]
    if at:
        a = nested_clip(inner, s, e, link_id=link)
        write_clip(ctx, seq, seq["audio_tracks"][min(at)], a)
        out["audio_clip_id"] = a["id"]
    return out


class PlaceSequence(Params):
    sequence_id: str | None = Field(default=None, description="the sequence to place into (default active)")
    nested_id: str = Field(description="the sequence to place as a nested clip")
    at: TimeValue | None = Field(default=None, description="default playhead")
    mode: Literal["overwrite", "insert"] = "overwrite"
    video_track_id: str | None = None
    audio_track_id: str | None = None


@command("clip.place_sequence", PlaceSequence)
def clip_place_sequence(ctx: Context, p: PlaceSequence):
    """Drop a sequence onto the timeline as a nested clip (drag a sequence from the Project panel)."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    inner = doc["sequences"].get(p.nested_id)
    if inner is None:
        raise CommandError("NOT_FOUND", f"sequence {p.nested_id} not found")
    if contains(doc, inner["id"], seq["id"]):
        raise CommandError("NEST_CYCLE", "a sequence cannot contain itself")
    dur = sequence_end(inner)
    if dur <= 0:
        raise CommandError("EMPTY_SEQUENCE", f"{inner['name']} is empty")
    at = seq["playhead"] if p.at is None else resolve_time(p.at, seq["settings"])
    targets = []
    if _has_video(doc, inner):
        targets.append(p.video_track_id or default_track(seq, "video"))
    if _has_audio(doc, inner):
        targets.append(p.audio_track_id or default_track(seq, "audio"))
    for tid in targets:
        require_unlocked(seq, tid)
    if p.mode == "insert":
        for tid in sync_tracks(seq, set(targets)):
            razor_track(ctx, seq, tid, at, {})
        for tid in sync_tracks(seq, set(targets)):
            shift_from(ctx, seq, tid, at, dur)
    else:
        for tid in targets:
            clear_range(ctx, seq, tid, at, at + dur)
    link = new_id() if len(targets) > 1 else None
    ids = []
    for tid in targets:
        c = nested_clip(inner, at, at + dur, link_id=link)
        write_clip(ctx, seq, tid, c)
        ids.append(c["id"])
    ctx.m.set(["sequences", seq["id"], "playhead"], at + dur)
    return {"clip_ids": ids, "end": at + dur}
