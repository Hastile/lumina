"""Timeline editing commands: place, insert/overwrite, razor, lift/extract,
move, trim (incl. ripple), speed, link, markers.

Invariants every command preserves (checked by ``check_sequence``):
  * clips on one track never overlap and have ``start < end``
  * ``source_in >= 0`` and the source range stays inside the media
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .. import timebase
from ..model import LabelColor, new_id
from .project import media_of, seq_of
from .registry import CommandError, Context, Params, TimeValue, command, resolve_time

STILL_DEFAULT_SECONDS = 5
DEFAULT_MOTION = {"position": None, "scale": 100.0, "scale_width": 100.0, "uniform": True, "rotation": 0.0, "anchor": None,
                  "crop_left": 0.0, "crop_top": 0.0, "crop_right": 0.0, "crop_bottom": 0.0}


# ---------------------------------------------------------------- helpers

def clips_sorted(track: dict) -> list[dict]:
    return sorted(track["clips"].values(), key=lambda c: c["start"])


def clip_path(seq: dict, track_id: str, clip_id: str) -> list[str]:
    return ["sequences", seq["id"], "tracks", track_id, "clips", clip_id]


def source_at(clip: dict, t: int) -> int:
    return clip["source_in"] + round((t - clip["start"]) * clip["speed"])


def sequence_end(seq: dict) -> int:
    return max((c["end"] for t in seq["tracks"].values() for c in t["clips"].values()), default=0)


def media_limit(doc: dict, clip: dict) -> int | None:
    if clip.get("sequence_id"):
        inner = doc["sequences"].get(clip["sequence_id"])
        return sequence_end(inner) if inner else None
    item = doc["media"].get(clip["media_id"])
    if item is None or item["kind"] == "image":
        return None
    return item["duration"]


def source_problem(doc: dict, clip: dict) -> str | None:
    """Why a clip's source range is invalid (None if fine). Works for reversed clips
    (negative speed: source time runs down from source_in) and Frame Hold clips
    (only the held frame must exist)."""
    limit = media_limit(doc, clip)
    if clip.get("freeze") is not None:
        if clip["freeze"] < 0 or (limit is not None and clip["freeze"] > limit):
            return "the held frame is outside the media"
        return None
    # forward covers [source_in, source_at(end)); reversed covers [source_at(end), source_in)
    a, b = clip["source_in"], source_at(clip, clip["end"])
    lo, hi = min(a, b), max(a, b)
    if lo < 0:
        return "no more source media before the In point"
    if limit is not None and hi > limit + 1:
        return "no more source media after the Out point"
    return None


def find_clip(seq: dict, clip_id: str) -> tuple[str, dict]:
    for tid, t in seq["tracks"].items():
        if clip_id in t["clips"]:
            return tid, t["clips"][clip_id]
    raise CommandError("NO_CLIP", f"clip not found: {clip_id}")


def linked_ids(seq: dict, clip_ids: list[str]) -> list[str]:
    links = set()
    for cid in clip_ids:
        _, c = find_clip(seq, cid)
        if c.get("link_id"):
            links.add(c["link_id"])
    out = list(dict.fromkeys(clip_ids))
    if links:
        for t in seq["tracks"].values():
            for c in t["clips"].values():
                if c.get("link_id") in links and c["id"] not in out:
                    out.append(c["id"])
    return out


def ordered_tracks(seq: dict) -> list[str]:
    return list(seq["video_tracks"]) + list(seq["audio_tracks"])


def require_unlocked(seq: dict, track_id: str) -> None:
    if seq["tracks"][track_id]["locked"]:
        raise CommandError("TRACK_LOCKED", f"track {seq['tracks'][track_id]['name']} is locked")


def is_free(track: dict, start: int, end: int, exclude: set[str] = frozenset()) -> bool:
    return not any(
        c["start"] < end and c["end"] > start for c in track["clips"].values() if c["id"] not in exclude
    )


def write_clip(ctx: Context, seq: dict, track_id: str, clip: dict) -> None:
    ctx.m.set(clip_path(seq, track_id, clip["id"]), clip)


def split_clip(ctx: Context, seq: dict, track_id: str, clip: dict, at: int, right_link: str | None = None) -> str:
    """Split ``clip`` at ``at``; returns the id of the new right-hand clip."""
    if not clip["start"] < at < clip["end"]:
        raise CommandError("BAD_SPLIT", "split point outside clip")
    right = dict(clip, id=new_id(), start=at, source_in=source_at(clip, at), link_id=right_link)
    left = dict(clip, end=at)
    write_clip(ctx, seq, track_id, left)
    write_clip(ctx, seq, track_id, right)
    return right["id"]


def clear_range(ctx: Context, seq: dict, track_id: str, start: int, end: int, exclude: set[str] = frozenset()) -> list[dict]:
    """Overwrite-clear [start, end) on one track. Returns what it covered: each clip's range before
    (``was``) and the parts of it left (``now``, empty when it was removed)."""
    track = seq["tracks"][track_id]
    covered = []
    for c in clips_sorted(track):
        if c["id"] in exclude or c["end"] <= start or c["start"] >= end:
            continue
        if c["start"] >= start and c["end"] <= end:
            ctx.m.delete(clip_path(seq, track_id, c["id"]))
            now = []
        elif c["start"] < start and c["end"] > end:
            split_clip(ctx, seq, track_id, c, end)
            write_clip(ctx, seq, track_id, dict(track["clips"][c["id"]], end=start))
            now = [[c["start"], start], [end, c["end"]]]
        elif c["start"] < start:
            write_clip(ctx, seq, track_id, dict(c, end=start))
            now = [[c["start"], start]]
        else:
            write_clip(ctx, seq, track_id, dict(c, start=end, source_in=source_at(c, end)))
            now = [[end, c["end"]]]
        covered.append({"clip_id": c["id"], "name": c["name"], "track_id": track_id, "was": [c["start"], c["end"]], "now": now})
    return covered


def shift_from(ctx: Context, seq: dict, track_id: str, at: int, delta: int, exclude: set[str] = frozenset()) -> None:
    """Move every clip starting at/after ``at`` by ``delta`` (ripple)."""
    if delta == 0:
        return
    track = seq["tracks"][track_id]
    movers = [c for c in track["clips"].values() if c["start"] >= at and c["id"] not in exclude]
    if delta < 0 and movers:
        first = min(c["start"] for c in movers)
        if first + delta < 0:
            raise CommandError("RIPPLE_BLOCKED", "ripple would move clips before 0")
        staying = [c for c in track["clips"].values() if c not in movers and c["id"] not in exclude]
        if any(s["end"] > first + delta for s in staying if s["start"] < first):
            raise CommandError("RIPPLE_BLOCKED", f"ripple collides with a clip on {track['name']}")
    for c in movers:
        write_clip(ctx, seq, track_id, dict(c, start=c["start"] + delta, end=c["end"] + delta))


def razor_track(ctx: Context, seq: dict, track_id: str, at: int, link_map: dict[str, str]) -> str | None:
    track = seq["tracks"][track_id]
    for c in list(track["clips"].values()):
        if c["start"] < at < c["end"]:
            right_link = None
            if c.get("link_id"):
                right_link = link_map.setdefault(c["link_id"], new_id())
            return split_clip(ctx, seq, track_id, c, at, right_link)
    return None


def default_track(seq: dict, kind: str) -> str:
    key = "video_tracks" if kind == "video" else "audio_tracks"
    usable = [t for t in seq[key] if not seq["tracks"][t]["locked"] and seq["tracks"][t].get("caption_style") is None]
    for tid in usable:
        if seq["tracks"][tid]["targeted"]:
            return tid
    for tid in usable:
        return tid
    raise CommandError("NO_TRACK", f"no unlocked {kind} track")


def sync_tracks(seq: dict, base: set[str]) -> list[str]:
    """Tracks a ripple applies to: the edited ones plus sync-locked unlocked ones."""
    return [
        tid for tid in ordered_tracks(seq)
        if tid in base or (seq["tracks"][tid]["sync_lock"] and not seq["tracks"][tid]["locked"])
    ]


def check_sequence(doc: dict, seq: dict) -> None:
    for t in seq["tracks"].values():
        prev_end = None
        for c in clips_sorted(t):
            assert c["start"] < c["end"], f"empty clip {c['id']}"
            assert c["source_in"] >= 0, f"negative source_in {c['id']}"
            assert prev_end is None or c["start"] >= prev_end, f"overlap on {t['name']} at {c['start']}"
            assert source_problem(doc, c) is None, f"clip {c['id']}: {source_problem(doc, c)}"
            prev_end = c["end"]


# ---------------------------------------------------------------- place / insert / overwrite

class PlaceMedia(Params):
    sequence_id: str | None = None
    media_id: str
    at: TimeValue | None = Field(default=None, description="timeline time; default playhead")
    mode: Literal["overwrite", "insert"] = "overwrite"
    source_in: TimeValue | None = Field(default=None, description="source time; default media In mark or 0")
    source_out: TimeValue | None = Field(default=None, description="source time; default media Out mark or end")
    video_track_id: str | None = None
    audio_track_id: str | None = None
    take_video: bool = True
    take_audio: bool = True


@command("clip.place", PlaceMedia)
def clip_place(ctx: Context, p: PlaceMedia):
    """Place media on the timeline (Insert ',' / Overwrite '.'), video and audio linked.
    An overwrite reports the clips it covered (``covered``: each one's range before and the parts left)."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    item = media_of(doc, p.media_id)
    st = seq["settings"]
    at = seq["playhead"] if p.at is None else resolve_time(p.at, st)
    if at < 0:
        raise CommandError("BAD_TIME", "cannot place before 0")

    if item["kind"] == "image":
        src_in = 0
        default_len = timebase.seconds_to_ticks(STILL_DEFAULT_SECONDS)
        src_out = resolve_time(p.source_out, st) if p.source_out is not None else default_len
    else:
        src_in = resolve_time(p.source_in, st) if p.source_in is not None else (item.get("in_point") or 0)
        src_out = resolve_time(p.source_out, st) if p.source_out is not None else (item.get("out_point") or item["duration"])
        src_out = min(src_out, item["duration"])
    duration = timebase.snap_to_frame(src_out - src_in, st["fps_num"], st["fps_den"])
    if src_out - src_in > 0 and duration == 0:
        duration = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    if duration <= 0:
        raise CommandError("BAD_RANGE", "source range is empty")
    if item["kind"] != "image" and src_in + duration > item["duration"]:
        duration -= timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
        if duration <= 0:
            raise CommandError("BAD_RANGE", "source range is shorter than one frame")

    targets: list[tuple[str, str]] = []
    for kind, take, explicit in (("video", p.take_video, p.video_track_id), ("audio", p.take_audio, p.audio_track_id)):
        if not take or item.get(kind) is None:
            continue
        patched = seq.get(f"source_{kind}_track")
        if explicit:
            targets.append((kind, explicit))
        elif patched == "":
            continue  # source patching turned this kind off
        elif patched and patched in seq["tracks"]:
            targets.append((kind, patched))
        else:
            targets.append((kind, default_track(seq, kind)))
    if not targets:
        raise CommandError("NOTHING_TO_PLACE", "media has no selected video/audio")
    for kind, tid in targets:
        if tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != kind:
            raise CommandError("BAD_TRACK", f"{tid} is not a {kind} track")
        if seq["tracks"][tid].get("caption_style"):
            raise CommandError("BAD_TRACK", "media cannot go on a caption track")
        require_unlocked(seq, tid)

    end = at + duration
    covered: list[dict] = []
    if p.mode == "insert":
        link_map: dict[str, str] = {}
        affected = sync_tracks(seq, {tid for _, tid in targets})
        for tid in affected:
            razor_track(ctx, seq, tid, at, link_map)
        for tid in affected:
            shift_from(ctx, seq, tid, at, duration)
    else:
        for _, tid in targets:
            covered += clear_range(ctx, seq, tid, at, end)

    link_id = new_id() if len(targets) > 1 else None
    base_name = item["name"]
    created = {}
    for kind, tid in targets:
        clip = {
            "id": new_id(),
            "media_id": item["id"],
            "name": base_name,
            "start": at,
            "end": end,
            "source_in": src_in,
            "speed": 1.0,
            "enabled": True,
            "link_id": link_id,
            "label": item.get("label", "iris"),
            "opacity": 100.0,
            "volume_db": 0.0,
            "gain_db": 0.0,
            "audio_tag": None,
            "sequence_id": None,
            "muted": False,
            "auto_match": None,
            "motion": dict(DEFAULT_MOTION),
            "keyframes": {},
            "graphic": None,
            "caption": None,
            "effects": [],
            "freeze": None,
            "adjustment": False,
        }
        write_clip(ctx, seq, tid, clip)
        created[f"{kind}_clip_id"] = clip["id"]
    ctx.m.set(["sequences", seq["id"], "playhead"], end)
    return {**created, "start": at, "end": end, "covered": covered}


# ---------------------------------------------------------------- razor / lift / extract

class Razor(Params):
    sequence_id: str | None = None
    at: TimeValue | None = Field(default=None, description="default playhead")
    track_ids: list[str] | None = Field(default=None, description="default: all unlocked tracks (Add Edit to All Tracks)")


@command("clip.razor", Razor)
def clip_razor(ctx: Context, p: Razor):
    """Razor / Add Edit (Ctrl+K) at a time."""
    seq = seq_of(ctx.doc, p.sequence_id)
    at = seq["playhead"] if p.at is None else resolve_time(p.at, seq["settings"])
    tids = p.track_ids or [t for t in ordered_tracks(seq) if not seq["tracks"][t]["locked"]]
    link_map: dict[str, str] = {}
    created = []
    for tid in tids:
        if tid not in seq["tracks"]:
            raise CommandError("NO_TRACK", f"track not found: {tid}")
        require_unlocked(seq, tid)
        rid = razor_track(ctx, seq, tid, at, link_map)
        if rid:
            created.append(rid)
    return {"created_clip_ids": created}


class RangeEdit(Params):
    sequence_id: str | None = None
    ranges: list[tuple[TimeValue, TimeValue]] = Field(min_length=1, description="[start, end) pairs")
    track_ids: list[str] | None = Field(default=None, description="default: all unlocked tracks")
    ripple: bool = Field(default=True, description="true = Extract (close gap), false = Lift (leave gap)")


@command("sequence.remove_ranges", RangeEdit)
def sequence_remove_ranges(ctx: Context, p: RangeEdit):
    """Lift or Extract one or more time ranges across tracks (e.g. silence cuts)."""
    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    ranges = []
    for a, b in p.ranges:
        s, e = resolve_time(a, st), resolve_time(b, st)
        if e <= s:
            continue
        ranges.append((s, e))
    ranges.sort()
    merged: list[list[int]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    tids = p.track_ids or [t for t in ordered_tracks(seq) if not seq["tracks"][t]["locked"]]
    for tid in tids:
        require_unlocked(seq, tid)
    removed = 0
    for s, e in reversed(merged):
        link_map: dict[str, str] = {}
        for tid in tids:
            razor_track(ctx, seq, tid, s, link_map)
        link_map = {}
        for tid in tids:
            razor_track(ctx, seq, tid, e, link_map)
        for tid in tids:
            for c in list(seq["tracks"][tid]["clips"].values()):
                if c["start"] >= s and c["end"] <= e:
                    ctx.m.delete(clip_path(seq, tid, c["id"]))
            if p.ripple:
                shift_from(ctx, seq, tid, e, -(e - s))
        removed += e - s
    return {"ranges": merged, "removed_ticks": removed if p.ripple else 0}


class RemoveClips(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    ripple: bool = Field(default=False, description="Ripple Delete (Shift+Delete) instead of Clear")
    include_linked: bool = True


@command("clip.remove", RemoveClips)
def clip_remove(ctx: Context, p: RemoveClips):
    """Delete clips (Clear) or Ripple Delete them."""
    seq = seq_of(ctx.doc, p.sequence_id)
    ids = linked_ids(seq, p.clip_ids) if p.include_linked else list(dict.fromkeys(p.clip_ids))
    removed: list[tuple[str, int, int]] = []
    for cid in ids:
        tid, c = find_clip(seq, cid)
        require_unlocked(seq, tid)
        removed.append((tid, c["start"], c["end"]))
        ctx.m.delete(clip_path(seq, tid, cid))
    unsynced: list[str] = []
    if p.ripple:
        spans: list[list[int]] = []
        for _, s, e in sorted(removed, key=lambda r: r[1]):
            if spans and s <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], e)
            else:
                spans.append([s, e])
        base = {tid for tid, _, _ in removed}
        for s, e in reversed(spans):
            for tid in sync_tracks(seq, base):
                if is_free(seq["tracks"][tid], s, e):
                    shift_from(ctx, seq, tid, e, -(e - s))
                elif tid not in unsynced:
                    unsynced.append(tid)
    return {"removed_clip_ids": ids, "unsynced_track_ids": unsynced}


# ---------------------------------------------------------------- move

class MoveClips(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    delta: TimeValue = Field(description="time offset, may be negative (e.g. '-1.5s')")
    track_delta: int = Field(default=0, description="move up (+) / down (-) within the same track kind")
    include_linked: bool = True
    mode: Literal["overwrite", "insert"] = "overwrite"
    duplicate: bool = Field(default=False, description="Alt-drag: leave the clips where they are and drop copies")


@command("clip.move", MoveClips)
def clip_move(ctx: Context, p: MoveClips):
    """Drag clips in time and/or across tracks (overwrite, or Ctrl-drag insert); Alt-drag
    duplicates them (duplicate). An overwrite reports the clips it covered (``covered``)."""
    seq = seq_of(ctx.doc, p.sequence_id)
    st = seq["settings"]
    if isinstance(p.delta, str) and p.delta.strip().startswith("-"):
        delta = -resolve_time(p.delta.strip()[1:], st)
    else:
        delta = resolve_time(p.delta, st)
    ids = linked_ids(seq, p.clip_ids) if p.include_linked else list(dict.fromkeys(p.clip_ids))
    moves = []
    for cid in ids:
        tid, c = find_clip(seq, cid)
        require_unlocked(seq, tid)
        kind = seq["tracks"][tid]["kind"]
        order = seq["video_tracks"] if kind == "video" else seq["audio_tracks"]
        # video stacks upward, audio downward: "+1" moves away from the center line
        new_idx = order.index(tid) + p.track_delta
        if not 0 <= new_idx < len(order):
            raise CommandError("NO_TRACK", "destination track does not exist")
        dest = order[new_idx]
        require_unlocked(seq, dest)
        if bool(seq["tracks"][dest].get("caption_style")) != bool(c.get("caption")) and dest != tid:
            raise CommandError("BAD_TRACK", "caption cues stay on caption tracks, and other clips off them")
        if c["start"] + delta < 0:
            raise CommandError("BAD_TIME", "cannot move before 0")
        moves.append((tid, dest, c))
    if p.duplicate:
        # the copies keep their links among themselves, not with the originals
        links: dict[str, str] = {}
        moves = [(tid, dest, dict(c, id=new_id(), link_id=links.setdefault(c["link_id"], new_id()) if c.get("link_id") else None,
                                  effects=[dict(e, id=new_id()) for e in c.get("effects") or []]))
                 for tid, dest, c in moves]
    else:
        for tid, _, c in moves:
            ctx.m.delete(clip_path(seq, tid, c["id"]))
    covered: list[dict] = []
    for _, dest, c in moves:
        s, e = c["start"] + delta, c["end"] + delta
        if p.mode == "insert":
            razor_track(ctx, seq, dest, s, {})
            shift_from(ctx, seq, dest, s, e - s)
        else:
            covered += clear_range(ctx, seq, dest, s, e)
        write_clip(ctx, seq, dest, dict(c, start=s, end=e))
    out = {"moved_clip_ids": ids, "delta": delta, "covered": covered}
    if p.duplicate:
        out = {"copied_from": ids, "clip_ids": [c["id"] for _, _, c in moves], "delta": delta, "covered": covered}
    return out


# ---------------------------------------------------------------- trim / speed

class TrimClip(Params):
    sequence_id: str | None = None
    clip_id: str
    edge: Literal["start", "end"]
    delta: TimeValue = Field(description="positive moves the edge later; may be negative")
    ripple: bool = Field(default=False, description="Ripple Edit (B / Q / W): shift later clips")
    include_linked: bool = True


@command("clip.trim", TrimClip)
def clip_trim(ctx: Context, p: TrimClip):
    """Trim a clip's In (start) or Out (end) edge, optionally rippling."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    st = seq["settings"]
    raw = p.delta.strip() if isinstance(p.delta, str) else p.delta
    if isinstance(raw, str) and raw.startswith("-"):
        delta = -resolve_time(raw[1:], st)
    else:
        delta = resolve_time(raw, st)
    if delta == 0:
        return {"delta": 0}
    ids = linked_ids(seq, [p.clip_id]) if p.include_linked else [p.clip_id]
    edits = []
    for cid in ids:
        tid, c = find_clip(seq, cid)
        require_unlocked(seq, tid)
        limit = media_limit(doc, c)
        if p.edge == "end":
            new = dict(c, end=c["end"] + delta)
            if p.ripple:
                ripple_at, ripple_delta = c["end"], delta
            else:
                ripple_at = ripple_delta = None
        else:
            new_src = c["source_in"] + round(delta * c["speed"])
            if p.ripple:
                # head stays put; content slides; later clips follow
                new = dict(c, source_in=new_src, end=c["end"] - delta)
                ripple_at, ripple_delta = c["end"], -delta
            else:
                new = dict(c, start=c["start"] + delta, source_in=new_src)
                ripple_at = ripple_delta = None
        if new["end"] <= new["start"]:
            raise CommandError("TRIM_TOO_FAR", "trim would make the clip empty")
        if new["start"] < 0:
            raise CommandError("TRIM_PAST_MEDIA", "no more source media before the In point")
        problem = source_problem(doc, new)
        if problem or (new.get("freeze") is None and limit is not None and max(new["source_in"], source_at(new, new["end"])) > limit):
            raise CommandError("TRIM_PAST_MEDIA", problem or "no more source media after the Out point")
        edits.append((tid, c, new, ripple_at, ripple_delta))

    trimmed = {c["id"] for _, c, _, _, _ in edits}
    if not p.ripple:
        for tid, c, new, _, _ in edits:
            if not is_free(seq["tracks"][tid], new["start"], new["end"], exclude=trimmed):
                raise CommandError("TRIM_COLLISION", "trim runs into an adjacent clip; use ripple or Ripple Edit")
        for tid, _, new, _, _ in edits:
            write_clip(ctx, seq, tid, new)
        return {"delta": delta, "clip_ids": ids}

    ripple_delta = edits[0][4]
    # each edited track ripples from its own clip's edge (linked clips can end at
    # different times); other sync-locked tracks follow the primary clip's edge
    ripple_points = {tid: at for tid, _, _, at, _ in edits}
    base = set(ripple_points)
    for tid in sync_tracks(seq, base):
        if ripple_delta > 0:
            shift_from(ctx, seq, tid, ripple_points.get(tid, edits[0][3]), ripple_delta, exclude=trimmed)
    for tid, _, new, _, _ in edits:
        write_clip(ctx, seq, tid, new)
    for tid in sync_tracks(seq, base):
        if ripple_delta < 0:
            shift_from(ctx, seq, tid, ripple_points.get(tid, edits[0][3]), ripple_delta, exclude=trimmed)
    return {"delta": delta, "clip_ids": ids}


class SetSpeed(Params):
    sequence_id: str | None = None
    clip_id: str
    speed: float = Field(gt=0, le=100, description="1.0 = 100% (the magnitude; see reverse)")
    reverse: bool | None = Field(default=None, description="Reverse Speed: play the same frames back to front; default keeps the direction")
    ripple: bool = True
    include_linked: bool = True


@command("clip.set_speed", SetSpeed)
def clip_set_speed(ctx: Context, p: SetSpeed):
    """Clip Speed/Duration (Ctrl+R)."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    st = seq["settings"]
    ids = linked_ids(seq, [p.clip_id]) if p.include_linked else [p.clip_id]
    edits = []
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    for cid in ids:
        tid, c = find_clip(seq, cid)
        require_unlocked(seq, tid)
        reverse = (c["speed"] < 0) if p.reverse is None else p.reverse
        if reverse and c.get("sequence_id"):
            raise CommandError("NOT_SUPPORTED", "a nested sequence clip cannot be reversed")
        # the source range the clip covers, whichever way it plays
        a, b = c["source_in"], source_at(c, c["end"])
        lo, hi = min(a, b), max(a, b)
        new_len = timebase.snap_to_frame(round((hi - lo) / p.speed), st["fps_num"], st["fps_den"])
        new_len = max(new_len, tpf)
        speed = -p.speed if reverse else p.speed
        # the same [lo, hi) range either way: a reversed clip starts at its exclusive end and
        # shows the frame just before each source time (tracks.media_time)
        new = dict(c, speed=speed, source_in=lo if not reverse else lo + round(new_len * p.speed), end=c["start"] + new_len)
        limit = media_limit(doc, c)
        while source_problem(doc, new) and new["end"] - new["start"] > tpf:
            new["end"] -= tpf
        edits.append((tid, c, new))
    changed = {c["id"] for _, c, _ in edits}
    delta = edits[0][2]["end"] - edits[0][1]["end"]
    if p.ripple and any(new["end"] != old["end"] for _, old, new in edits):
        # linked clips of different lengths change by different amounts: each edited
        # track ripples by its own clip's change, other sync-locked tracks by the primary's
        per_track = {tid: (old["end"], new["end"] - old["end"]) for tid, old, new in edits}
        primary = (edits[0][1]["end"], delta)
        tracks = sync_tracks(seq, set(per_track))
        for tid in tracks:
            at, d = per_track.get(tid, primary)
            if d > 0:
                shift_from(ctx, seq, tid, at, d, exclude=changed)
        for tid, _, new in edits:
            write_clip(ctx, seq, tid, new)
        for tid in tracks:
            at, d = per_track.get(tid, primary)
            if d < 0:
                shift_from(ctx, seq, tid, at, d, exclude=changed)
    else:
        for tid, _, new in edits:
            if not is_free(seq["tracks"][tid], new["start"], new["end"], exclude=changed):
                raise CommandError("TRIM_COLLISION", "new duration overlaps the next clip; enable ripple")
            write_clip(ctx, seq, tid, new)
    return {"clip_ids": ids, "duration": edits[0][2]["end"] - edits[0][2]["start"]}


class FrameHold(Params):
    sequence_id: str | None = None
    clip_id: str
    mode: Literal["hold", "add", "insert", "clear"] = Field(
        default="hold", description="hold: Frame Hold Options (the whole clip shows one frame); add: Add Frame Hold (split at "
                                    "the playhead, hold from there); insert: Insert Frame Hold Segment (a held segment of "
                                    "`duration` at the playhead, later clips ripple); clear: remove the hold")
    hold_on: Literal["playhead", "in", "out", "source"] = Field(default="playhead", description="mode hold: which frame")
    source_time: TimeValue | None = Field(default=None, description="hold_on source: the source time to hold")
    at: TimeValue | None = Field(default=None, description="default playhead")
    duration: TimeValue = "2s"


@command("clip.frame_hold", FrameHold)
def clip_frame_hold(ctx: Context, p: FrameHold):
    """Frame Hold (Premiere's Frame Hold Options / Add Frame Hold / Insert Frame Hold Segment) on a video clip."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    st = seq["settings"]
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    tid, c = find_clip(seq, p.clip_id)
    require_unlocked(seq, tid)
    item = doc["media"].get(c.get("media_id") or "")
    if seq["tracks"][tid]["kind"] != "video" or item is None or item["kind"] != "video":
        raise CommandError("NOT_VIDEO", "Frame Hold works on video clips")
    from ..tracks import media_time

    at = seq["playhead"] if p.at is None else resolve_time(p.at, st)
    at = min(max(timebase.snap_to_frame(at, st["fps_num"], st["fps_den"]), c["start"]), c["end"] - tpf)
    path = clip_path(seq, tid, c["id"])
    if p.mode == "clear":
        if c.get("freeze") is not None:
            ctx.m.set(path + ["freeze"], None)
        return {"freeze": None}
    held = media_time(c, source_at(c, at))
    if p.mode == "hold":
        if p.hold_on == "in":
            held = media_time(c, c["source_in"])
        elif p.hold_on == "out":
            held = media_time(c, source_at(c, c["end"] - tpf))
        elif p.hold_on == "source":
            if p.source_time is None:
                raise CommandError("BAD_PARAMS", "hold_on source needs source_time")
            held = resolve_time(p.source_time, st)
        if not 0 <= held <= item["duration"]:
            raise CommandError("BAD_TIME", "that frame is outside the media")
        ctx.m.set(path + ["freeze"], held)
        return {"freeze": held}
    if p.mode == "add":
        if at <= c["start"]:
            ctx.m.set(path + ["freeze"], held)
            return {"freeze": held, "clip_id": c["id"]}
        right = split_clip(ctx, seq, tid, c, at, None)  # the audio keeps playing: only the video is split
        ctx.m.set(clip_path(seq, tid, right) + ["freeze"], held)
        return {"freeze": held, "clip_id": right}
    # insert: split everything at the playhead, open a gap and fill it with the held frame
    dur = max(tpf, timebase.snap_to_frame(resolve_time(p.duration, st), st["fps_num"], st["fps_den"]))
    link_map: dict[str, str] = {}
    affected = sync_tracks(seq, {tid})
    for t_ in affected:
        razor_track(ctx, seq, t_, at, link_map)
    for t_ in affected:
        shift_from(ctx, seq, t_, at, dur)
    hold = dict(c, id=new_id(), start=at, end=at + dur, source_in=source_at(c, at), link_id=None, freeze=held, keyframes={},
                effects=list(c.get("effects") or []))
    write_clip(ctx, seq, tid, hold)
    return {"freeze": held, "clip_id": hold["id"]}


# ---------------------------------------------------------------- properties / links

class SetClips(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)
    name: str | None = None
    enabled: bool | None = None
    label: LabelColor | None = None
    opacity: float | None = Field(default=None, ge=0, le=100)
    volume_db: float | None = Field(default=None, ge=-96, le=15)
    gain_db: float | None = Field(default=None, ge=-96, le=96, description="Audio Gain (dB)")
    muted: bool | None = Field(default=None, description="Essential Sound Mute")


@command("clip.set", SetClips)
def clip_set(ctx: Context, p: SetClips):
    """Set clip name, Enable (Shift+E), label color, opacity, volume or audio gain."""
    seq = seq_of(ctx.doc, p.sequence_id)
    for cid in p.clip_ids:
        tid, c = find_clip(seq, cid)
        require_unlocked(seq, tid)
        if p.opacity is not None and c.get("caption") is not None and c["opacity"] != p.opacity:
            raise CommandError("CAPTION_TRANSFORM", "a caption cue uses its track's opacity (caption.set_style)")
        for key in ("name", "enabled", "label", "opacity", "volume_db", "gain_db", "muted"):
            value = getattr(p, key)
            if value is not None and c.get(key) != value:
                ctx.m.set(clip_path(seq, tid, cid) + [key], value)


class LinkClips(Params):
    sequence_id: str | None = None
    clip_ids: list[str] = Field(min_length=1)


@command("clip.link", LinkClips)
def clip_link(ctx: Context, p: LinkClips):
    """Link clips so they select, move and trim together."""
    seq = seq_of(ctx.doc, p.sequence_id)
    lid = new_id()
    for cid in p.clip_ids:
        tid, _ = find_clip(seq, cid)
        ctx.m.set(clip_path(seq, tid, cid) + ["link_id"], lid)
    return {"link_id": lid}


@command("clip.unlink", LinkClips)
def clip_unlink(ctx: Context, p: LinkClips):
    """Unlink clips."""
    seq = seq_of(ctx.doc, p.sequence_id)
    for cid in linked_ids(seq, p.clip_ids):
        tid, _ = find_clip(seq, cid)
        ctx.m.set(clip_path(seq, tid, cid) + ["link_id"], None)


# ---------------------------------------------------------------- markers

MarkerColor = Literal["green", "red", "purple", "orange", "yellow", "white", "blue", "cyan"]


class AddMarker(Params):
    sequence_id: str | None = None
    at: TimeValue | None = Field(default=None, description="sequence time (default playhead); with media_id: source time")
    duration: TimeValue = 0
    name: str = ""
    comment: str = ""
    color: MarkerColor = "green"
    clip_id: str | None = Field(default=None, description="a clip marker on this clip (stored on its media at the source time "
                                                          "under `at`), like M with a clip selected in Premiere's Timeline")
    media_id: str | None = Field(default=None, description="a clip marker on this media at source time `at` (Source monitor M)")


def _marker_owner(ctx: Context, p) -> tuple[list[str], dict, dict | None]:
    """(path to the markers dict, sequence, media or None) for a sequence or clip marker."""
    seq = seq_of(ctx.doc, p.sequence_id)
    media_id = p.media_id
    if p.clip_id:
        _, clip = find_clip(seq, p.clip_id)
        media_id = clip.get("media_id")
        if not media_id:
            raise CommandError("NO_MEDIA", "clip markers go on clips of media (not graphics or nested sequences)")
    if media_id:
        media = ctx.doc["media"].get(media_id)
        if media is None:
            raise CommandError("NO_MEDIA", f"media not found: {media_id}")
        return ["media", media_id, "markers"], seq, media
    return ["sequences", seq["id"], "markers"], seq, None


@command("marker.add", AddMarker)
def marker_add(ctx: Context, p: AddMarker):
    """Add a marker (M): a sequence marker, or a clip marker on a clip's media (clip_id: at the source
    time under the playhead) or on a media item (media_id: at source time `at`)."""
    path, seq, media = _marker_owner(ctx, p)
    st = seq["settings"]
    at = seq["playhead"] if p.at is None else resolve_time(p.at, st)
    if p.clip_id:
        _, clip = find_clip(seq, p.clip_id)
        if not clip["start"] <= at < clip["end"]:
            raise CommandError("OUTSIDE_CLIP", "the time is not inside the clip")
        at = source_at(clip, at)
    mid = new_id()
    ctx.m.set(path + [mid], {"id": mid, "start": at, "duration": resolve_time(p.duration, st), "name": p.name, "comment": p.comment, "color": p.color})
    return {"marker_id": mid, **({"media_id": media["id"], "source_time": at} if media else {})}


class UpdateMarker(Params):
    sequence_id: str | None = None
    media_id: str | None = Field(default=None, description="a clip marker of this media (at is then source time)")
    clip_id: str | None = Field(default=None, description="a clip marker of this clip's media")
    marker_id: str
    at: TimeValue | None = None
    duration: TimeValue | None = None
    name: str | None = None
    comment: str | None = None
    color: MarkerColor | None = None


@command("marker.update", UpdateMarker)
def marker_update(ctx: Context, p: UpdateMarker):
    """Edit a marker (sequence marker, or a clip marker with media_id / clip_id)."""
    owner, seq, media = _marker_owner(ctx, p)
    st = seq["settings"]
    if p.marker_id not in (media or seq)["markers"]:
        raise CommandError("NO_MARKER", f"marker not found: {p.marker_id}")
    path = owner + [p.marker_id]
    if p.at is not None:
        ctx.m.set(path + ["start"], resolve_time(p.at, st))
    if p.duration is not None:
        ctx.m.set(path + ["duration"], resolve_time(p.duration, st))
    for key in ("name", "comment", "color"):
        if getattr(p, key) is not None:
            ctx.m.set(path + [key], getattr(p, key))


class RemoveMarkers(Params):
    sequence_id: str | None = None
    media_id: str | None = Field(default=None, description="clip markers of this media")
    clip_id: str | None = Field(default=None, description="clip markers of this clip's media")
    marker_ids: list[str] = Field(min_length=1)


@command("marker.remove", RemoveMarkers)
def marker_remove(ctx: Context, p: RemoveMarkers):
    """Delete markers (sequence markers, or clip markers with media_id / clip_id)."""
    owner, seq, media = _marker_owner(ctx, p)
    for mid in p.marker_ids:
        if mid not in (media or seq)["markers"]:
            raise CommandError("NO_MARKER", f"marker not found: {mid}")
        ctx.m.delete(owner + [mid])


def clip_markers(doc: dict, clip: dict) -> list[tuple[dict, int]]:
    """The clip markers a clip shows: (marker, sequence time) for each of its media's markers inside it."""
    media = doc["media"].get(clip.get("media_id") or "")
    if not media or not clip["speed"]:
        return []
    out = []
    for m in media.get("markers", {}).values():
        t = clip["start"] + round((m["start"] - clip["source_in"]) / clip["speed"])
        if clip["start"] <= t < clip["end"]:
            out.append((m, t))
    return sorted(out, key=lambda mt: mt[1])


class AlignMarker(Params):
    sequence_id: str | None = None
    clip_id: str
    marker_id: str | None = Field(default=None, description="a clip marker of the clip's media; default the first one inside the clip")
    at: TimeValue | None = Field(default=None, description="where the marker should land (sequence time); default the playhead")
    mode: Literal["speed", "move"] = Field(default="speed", description="speed: change the clip's speed, keeping where it starts "
                                                                       "and its first frame; move: slide the clip in time")
    include_linked: bool = True


@command("clip.align_marker", AlignMarker)
def clip_align_marker(ctx: Context, p: AlignMarker):
    """Put a clip marker on a time (a caption start, a cut, the playhead): mode speed changes the
    clip's speed so the marked moment lands there while the clip keeps its start, first frame and
    end (it ends earlier only if the media runs out); mode move slides the clip instead."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    st = seq["settings"]
    tid, clip = find_clip(seq, p.clip_id)
    require_unlocked(seq, tid)
    media = doc["media"].get(clip.get("media_id") or "")
    if not media:
        raise CommandError("NO_MEDIA", "the clip has no media markers")
    if p.marker_id:
        m = media.get("markers", {}).get(p.marker_id)
        if m is None:
            raise CommandError("NO_MARKER", f"marker not found: {p.marker_id}")
    else:
        inside = clip_markers(doc, clip)
        if not inside:
            raise CommandError("NO_MARKER", "no clip marker inside the clip")
        m = inside[0][0]
    target = seq["playhead"] if p.at is None else resolve_time(p.at, st)
    if p.mode == "move":
        landed = clip["start"] + round((m["start"] - clip["source_in"]) / clip["speed"])
        return {**clip_move(ctx, MoveClips(sequence_id=seq["id"], clip_ids=[clip["id"]], delta=target - landed,
                                           include_linked=p.include_linked)), "mode": "move"}
    if clip["speed"] < 0:
        raise CommandError("NOT_SUPPORTED", "a reversed clip cannot be sped to a marker")
    if not (target > clip["start"] and m["start"] > clip["source_in"]):
        raise CommandError("BAD_TARGET", "the marker and the target must both come after the clip's first frame")
    speed = (m["start"] - clip["source_in"]) / (target - clip["start"])
    if not 0.01 <= speed <= 100:
        raise CommandError("BAD_TARGET", f"that needs {speed * 100:.0f}% speed")
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    ids = linked_ids(seq, [clip["id"]]) if p.include_linked else [clip["id"]]
    ends = []
    for cid in ids:
        ltid, c = find_clip(seq, cid)
        require_unlocked(seq, ltid)
        new = dict(c, speed=speed)
        while source_problem(doc, new) and new["end"] - new["start"] > tpf:
            new["end"] -= tpf
        write_clip(ctx, seq, ltid, new)
        ends.append(new["end"])
    return {"speed": round(speed, 4), "end": ends[0], "shortened": ends[0] < clip["end"], "mode": "speed"}
