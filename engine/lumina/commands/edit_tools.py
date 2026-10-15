"""Premiere's trimming tools beyond Selection / Ripple: Rolling Edit (N), Rate Stretch (R),
Slip (Y) and Slide (U). Each edits the clip and its linked partners (Alt-drag: this clip only).

Times are sequence ticks, snapped to frames by the caller.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .. import timebase
from .project import seq_of
from .registry import CommandError, Context, Params, command
from .timeline import clips_sorted, find_clip, is_free, linked_ids, media_limit, require_unlocked, source_at, write_clip


class ToolEdit(Params):
    sequence_id: str | None = None
    clip_id: str
    delta: int = Field(description="sequence ticks the mouse moved (positive = later)")
    include_linked: bool = True


class EdgeEdit(ToolEdit):
    edge: Literal["start", "end"]


def _targets(seq: dict, p: ToolEdit) -> list[tuple[str, dict]]:
    ids = linked_ids(seq, [p.clip_id]) if p.include_linked else [p.clip_id]
    out = []
    for cid in ids:
        tid, c = find_clip(seq, cid)
        require_unlocked(seq, tid)
        out.append((tid, c))
    return out


def _check_source(doc: dict, c: dict) -> None:
    if c["end"] <= c["start"]:
        raise CommandError("TRIM_TOO_FAR", f"the edit would make {c['name']} empty")
    if c["source_in"] < 0:
        raise CommandError("TRIM_PAST_MEDIA", f"no more source media before the In point of {c['name']}")
    limit = media_limit(doc, c)
    if limit is not None and source_at(c, c["end"]) > limit + 1:
        raise CommandError("TRIM_PAST_MEDIA", f"no more source media after the Out point of {c['name']}")


def _neighbor(track: dict, c: dict, side: Literal["before", "after"]) -> dict | None:
    """The clip touching ``c`` on that side (an edit point), if any."""
    for o in clips_sorted(track):
        if o["id"] == c["id"]:
            continue
        if side == "before" and o["end"] == c["start"]:
            return o
        if side == "after" and o["start"] == c["end"]:
            return o
    return None


@command("clip.roll", EdgeEdit)
def clip_roll(ctx: Context, p: EdgeEdit):
    """Rolling Edit (N): move an edit point, trimming the outgoing clip's Out and the incoming clip's In
    together so nothing else moves. Without a neighbor it trims that edge alone."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    d = p.delta
    if d == 0:
        return {"delta": 0}
    writes: dict[str, tuple[str, dict]] = {}
    for tid, c in _targets(seq, p):
        track = seq["tracks"][tid]
        if p.edge == "end":
            left, right = c, _neighbor(track, c, "after")
        else:
            left, right = _neighbor(track, c, "before"), c
        if left is not None:
            writes[left["id"]] = (tid, dict(left, end=left["end"] + d))
        if right is not None:
            writes[right["id"]] = (tid, dict(right, start=right["start"] + d, source_in=right["source_in"] + round(d * right["speed"])))
    edited = set(writes)
    for tid, n in writes.values():
        _check_source(doc, n)
        if n["start"] < 0 or not is_free(seq["tracks"][tid], n["start"], n["end"], exclude=edited):
            raise CommandError("TRIM_COLLISION", "the edit point cannot roll past the next clip")
    for tid, n in writes.values():
        write_clip(ctx, seq, tid, n)
    return {"delta": d, "clip_ids": list(writes)}


@command("clip.slip", ToolEdit)
def clip_slip(ctx: Context, p: ToolEdit):
    """Slip (Y): keep the clip where it is and shift its In and Out in the source. Dragging right
    (positive delta) shows earlier source frames, as in Premiere."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    out = []
    for tid, c in _targets(seq, p):
        n = dict(c, source_in=c["source_in"] - round(p.delta * c["speed"]))
        _check_source(doc, n)
        out.append((tid, n))
    for tid, n in out:
        write_clip(ctx, seq, tid, n)
    return {"clip_ids": [n["id"] for _, n in out], "source_in": out[0][1]["source_in"]}


@command("clip.slide", ToolEdit)
def clip_slide(ctx: Context, p: ToolEdit):
    """Slide (U): move the clip along the timeline keeping its content, while the clips around it
    give and take: the previous clip's Out and the next clip's In follow, so the sequence length stays.
    A gap next to the clip is used up first."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    d = p.delta
    if d == 0:
        return {"delta": 0}
    writes: dict[str, tuple[str, dict]] = {}
    for tid, c in _targets(seq, p):
        track = seq["tracks"][tid]
        prev, nxt = _neighbor(track, c, "before"), _neighbor(track, c, "after")
        writes[c["id"]] = (tid, dict(c, start=c["start"] + d, end=c["end"] + d))
        if prev is not None:
            writes[prev["id"]] = (tid, dict(prev, end=prev["end"] + d))
        if nxt is not None:
            writes[nxt["id"]] = (tid, dict(nxt, start=nxt["start"] + d, source_in=nxt["source_in"] + round(d * nxt["speed"])))
    edited = set(writes)
    for tid, n in writes.values():
        _check_source(doc, n)
        if n["start"] < 0 or not is_free(seq["tracks"][tid], n["start"], n["end"], exclude=edited):
            raise CommandError("SLIDE_BLOCKED", "the neighbouring clips have no more media (or room) to slide into")
    for tid, n in writes.values():
        write_clip(ctx, seq, tid, n)
    return {"delta": d, "clip_ids": list(writes)}


@command("clip.rate_stretch", EdgeEdit)
def clip_rate_stretch(ctx: Context, p: EdgeEdit):
    """Rate Stretch (R): drag an edge to change the clip's duration by changing its speed; the same
    source frames play, faster or slower."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    st = seq["settings"]
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    out = []
    for tid, c in _targets(seq, p):
        src_len = (c["end"] - c["start"]) * c["speed"]
        start, end = (c["start"], c["end"] + p.delta) if p.edge == "end" else (c["start"] + p.delta, c["end"])
        if end - start < tpf:
            raise CommandError("TRIM_TOO_FAR", "a clip needs at least one frame")
        speed = src_len / (end - start)
        if not 0.01 <= speed <= 100:
            raise CommandError("BAD_SPEED", "speed must stay between 1% and 10000%")
        n = dict(c, start=start, end=end, speed=round(speed, 6))
        if n["start"] < 0 or not is_free(seq["tracks"][tid], start, end, exclude={c["id"]}):
            raise CommandError("TRIM_COLLISION", "the stretched clip would overlap its neighbour")
        _check_source(doc, n)
        out.append((tid, n))
    for tid, n in out:
        write_clip(ctx, seq, tid, n)
    return {"clip_ids": [n["id"] for _, n in out], "speed": out[0][1]["speed"]}


# ---------------------------------------------------------------- copy / paste

class PastedClip(Params):
    track_id: str = Field(description="the track it was copied from (pasted onto the same track, or shifted by track_offset)")
    clip: dict = Field(description="the copied clip as it appears in the document")


class PasteClips(Params):
    sequence_id: str | None = None
    clips: list[PastedClip] = Field(min_length=1)
    at: int | None = Field(default=None, description="where the earliest clip lands; default the playhead")
    mode: Literal["overwrite", "insert"] = Field(default="overwrite", description="Ctrl+V overwrites, Ctrl+Shift+V inserts")


@command("clip.paste", PasteClips)
def clip_paste(ctx: Context, p: PasteClips):
    """Paste (Ctrl+V) / Paste Insert (Ctrl+Shift+V): copies of clips at the playhead, keeping their
    spacing, tracks, links, effects and keyframes. The playhead moves to the end of the pasted clips."""
    from ..model import Clip, new_id
    from .timeline import clear_range, razor_track, shift_from, sync_tracks

    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    at = seq["playhead"] if p.at is None else p.at
    first = min(x.clip["start"] for x in p.clips)
    last = max(x.clip["end"] for x in p.clips)
    links: dict[str, str] = {}
    made = []
    for x in p.clips:
        tid = x.track_id
        if tid not in seq["tracks"]:
            raise CommandError("NO_TRACK", "the copied clip's track is not in this sequence")
        require_unlocked(seq, tid)
        c = Clip.model_validate(x.clip).model_dump(mode="json")
        if c["media_id"] and c["media_id"] not in doc["media"]:
            raise CommandError("NO_MEDIA", f"media of {c['name']} is not in the project")
        off = at - first
        c.update(id=new_id(), start=c["start"] + off, end=c["end"] + off,
                 link_id=links.setdefault(c["link_id"], new_id()) if c.get("link_id") else None,
                 effects=[dict(e, id=new_id()) for e in c["effects"]])
        made.append((tid, c))
    tracks = {tid for tid, _ in made}
    if p.mode == "insert":
        span = last - first
        for tid in sync_tracks(seq, tracks):
            razor_track(ctx, seq, tid, at, {})
        for tid in sync_tracks(seq, tracks):
            shift_from(ctx, seq, tid, at, span)
    else:
        for tid, c in made:
            clear_range(ctx, seq, tid, c["start"], c["end"])
    for tid, c in made:
        write_clip(ctx, seq, tid, c)
    end = max(c["end"] for _, c in made)
    ctx.m.set(["sequences", seq["id"], "playhead"], end)
    return {"clip_ids": [c["id"] for _, c in made], "end": end}


ATTRIBUTES = ("motion", "opacity", "time_remapping", "volume", "effects")


class PasteAttributes(Params):
    sequence_id: str | None = None
    source: dict = Field(description="the copied clip")
    clip_ids: list[str] = Field(min_length=1)
    attributes: list[Literal["motion", "opacity", "time_remapping", "volume", "effects"]] = Field(
        default=list(ATTRIBUTES), description="Paste Attributes dialog check boxes")
    scale_keyframes: bool = Field(default=False, description="Scale Attribute Times: stretch keyframes to each target's length")


_MOTION_KF = ("position", "scale", "scale_width", "rotation", "anchor", "crop_left", "crop_top", "crop_right", "crop_bottom")


@command("clip.paste_attributes", PasteAttributes)
def clip_paste_attributes(ctx: Context, p: PasteAttributes):
    """Paste Attributes (Ctrl+Alt+V): copy Motion, Opacity, Volume, speed (Time Remapping) and effects
    — with their keyframes — from a copied clip onto the selected clips. Video attributes go to video
    clips and Volume to audio clips; keyframes keep their offset from the clip start."""
    from ..effects import media_of
    from ..model import new_id
    from .timeline import clip_path

    seq = seq_of(ctx.doc, p.sequence_id)
    src = p.source
    want = set(p.attributes)
    done = []
    for cid in p.clip_ids:
        tid, c = find_clip(seq, cid)
        require_unlocked(seq, tid)
        kind = seq["tracks"][tid]["kind"]
        path = clip_path(seq, tid, cid)
        src_len = max(1, src["end"] - src["start"])
        dst_len = c["end"] - c["start"]

        def retime(kfs: list[dict]) -> list[dict]:
            # stored in source ticks: re-anchor from the copied clip's In to this clip's In
            out = []
            for k in kfs:
                rel = (k["t"] - src["source_in"]) / src.get("speed", 1.0)
                if p.scale_keyframes:
                    rel *= dst_len / src_len
                out.append(dict(k, t=c["source_in"] + round(rel * c["speed"])))
            return out

        kf = dict(c["keyframes"])
        props: list[str] = []
        if kind == "video":
            if "motion" in want:
                ctx.m.set(path + ["motion"], dict(src["motion"]))
                props += _MOTION_KF
            if "opacity" in want:
                ctx.m.set(path + ["opacity"], src["opacity"])
                props.append("opacity")
        else:
            if "volume" in want:
                ctx.m.set(path + ["volume_db"], src.get("volume_db", 0.0))
                props.append("volume_db")
        for prop in props:
            if prop in src.get("keyframes", {}):
                kf[prop] = retime(src["keyframes"][prop])
            else:
                kf.pop(prop, None)
        if kf != c["keyframes"]:
            ctx.m.set(path + ["keyframes"], kf)
        if "effects" in want:
            fx = [dict(e, id=new_id(), keyframes={k: retime(v) for k, v in (e.get("keyframes") or {}).items()})
                  for e in src.get("effects", []) if media_of(e["type"]) == kind]
            if fx:
                ctx.m.set(path + ["effects"], list(c["effects"]) + fx)
        done.append(cid)
    if "time_remapping" in want and src.get("speed", 1.0) != 1.0:
        from .timeline import clip_set_speed, SetSpeed

        for cid in done:
            if find_clip(seq, cid)[1]["speed"] != src["speed"]:
                clip_set_speed(ctx, SetSpeed(sequence_id=seq["id"], clip_id=cid, speed=src["speed"], include_linked=False))
    return {"clip_ids": done}


# ---------------------------------------------------------------- out of sync

def sync_anchor(c: dict) -> float:
    """The timeline time at which the clip's source time 0 would play: linked clips of one
    source are in sync when their anchors agree."""
    return c["start"] - c["source_in"] / c["speed"]


def sync_partner(seq: dict, c: dict) -> dict | None:
    """The linked clip of the same source on the other kind of track (video <-> audio)."""
    if not c.get("link_id"):
        return None
    src = c.get("media_id") or c.get("sequence_id")
    kind = next(t["kind"] for t in seq["tracks"].values() if c["id"] in t["clips"])
    for t in seq["tracks"].values():
        if t["kind"] == kind:
            continue
        for o in t["clips"].values():
            if o["link_id"] == c["link_id"] and (o.get("media_id") or o.get("sequence_id")) == src and o["speed"] == c["speed"]:
                return o
    return None


class Resync(Params):
    sequence_id: str | None = None
    clip_id: str
    mode: Literal["move", "slip"] = Field(description="Move into Sync (shift the clip) or Slip into Sync (shift its content)")


@command("clip.resync", Resync)
def clip_resync(ctx: Context, p: Resync):
    """Out-of-sync linked clips (the red offset on the clip): Move into Sync or Slip into Sync
    against its linked partner."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    tid, c = find_clip(seq, p.clip_id)
    require_unlocked(seq, tid)
    o = sync_partner(seq, c)
    if o is None:
        raise CommandError("NO_PARTNER", "the clip has no linked clip of the same source")
    off = round(sync_anchor(c) - sync_anchor(o))
    if off == 0:
        return {"offset": 0}
    if p.mode == "move":
        n = dict(c, start=c["start"] - off, end=c["end"] - off)
        if n["start"] < 0 or not is_free(seq["tracks"][tid], n["start"], n["end"], exclude={c["id"]}):
            raise CommandError("MOVE_BLOCKED", "there is no room to move the clip into sync; try Slip into Sync")
    else:
        n = dict(c, source_in=c["source_in"] + round(off * c["speed"]))
    _check_source(doc, n)
    write_clip(ctx, seq, tid, n)
    return {"offset": off}
