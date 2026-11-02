"""clip.place_shots: a whole cut plan onto one video track as one edit.

A Short's V1 used to be laid shot by shot: place, set the speed, set scale,
set position, look, and often place again (60-110 placements per Short).
The rules each step followed are fixed, so here they are code:

  * a shot from the source DB (cut_id) starts edge_margin inside its scene
    cut, so no frame of the neighbouring shot slips in;
  * 100% speed when the cut is long enough; otherwise it is slowed to fit,
    never below min_speed (a shot that needs more is refused: re-cut or pick
    another source, do not stretch it);
  * clips end exactly at the shot's end, so the plan's cut points hold;
  * landscape footage in a portrait sequence is framed the channel's way:
    115% (of a 1080-line picture) at y 1060, and 130% with the subject moved
    to the centre when the DB says it sits off to a side;
  * nothing outside the plan is covered unless allow_cover is set.

Everything runs in one command, so a refused shot changes nothing and one
undo removes the whole plan.
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import Field

from .. import timebase
from .project import seq_of
from .registry import REGISTRY, CommandError, Context, Params, TimeValue, command, resolve_time
from .timeline import clear_range, default_track, find_clip, require_unlocked, source_problem, write_clip

SOURCE_DB = None          # tests point this at their own DB; None = the user's
BASE_SCALE = 115.0        # docs/shorts-editing: 16:9 footage at 115%, y 1060
SIDE_SCALE = 130.0        # a subject off to a side: push in and centre it
BASE_Y_OFFSET = 100       # y 1060 in a 1920-high frame: 100 px below the centre
SIDE = 0.2                # |subject_x - 0.5| beyond this is "to a side"
REFERENCE_LINES = 1080    # the percentages above are for 1080-line footage


class Shot(Params):
    at: TimeValue = Field(description="timeline start of the shot")
    end: TimeValue = Field(description="timeline end of the shot (exclusive): the next cut point")
    cut_id: Optional[int] = Field(default=None, description="a source DB cut (source_plan / source_search)")
    media_id: Optional[str] = Field(default=None, description="a project media item, instead of cut_id")
    path: Optional[str] = Field(default=None, description="a file, imported when the project lacks it")
    source_in: Optional[TimeValue] = Field(default=None, description="source start; default: the cut's start + edge_margin")
    source_out: Optional[TimeValue] = Field(default=None, description="source end for media_id/path video; sets the speed")
    scale: Optional[float] = Field(default=None, description="override the framing's scale (%)")
    position: Optional[list[float]] = Field(default=None, description="override the framing's position [x, y]")


class PlaceShots(Params):
    sequence_id: Optional[str] = None
    track_id: Optional[str] = Field(default=None, description="video track; default the targeted one (V1)")
    shots: list[Shot] = Field(min_length=1)
    edge_margin: float = Field(default=0.3, ge=0, description="seconds kept clear of each scene edge of a DB cut")
    min_speed: float = Field(default=0.87, gt=0, description="slowest a shot may play to fill its length")
    max_speed: float = Field(default=1.05, gt=0, description="fastest, for an explicit source_in..source_out")
    allow_cover: bool = Field(default=False, description="let the plan overwrite clips already on the track")


def _cut(cut_id: int) -> dict:
    from ..sourcedb import db, query

    con = db.connect(SOURCE_DB or db.DEFAULT_DB_PATH)
    try:
        hits = query._hydrate(con, [cut_id])
    finally:
        con.close()
    h = hits.get(cut_id)
    if h is None:
        raise CommandError("NO_CUT", f"no cut #{cut_id} in the source DB")
    if not h.path:
        raise CommandError("OFFLINE", f"cut #{cut_id}: its file is not on disk")
    return {"path": h.path, "kind": h.kind, "start": h.start_ticks, "end": h.end_ticks,
            "subject_x": h.subject_x, "luma": h.luma, "text_overlay": h.text_overlay}


def _media_for_path(ctx: Context, path: str) -> str:
    want = os.path.normcase(os.path.abspath(path))
    for m in ctx.doc["media"].values():
        if m.get("path") and os.path.normcase(os.path.abspath(m["path"])) == want:
            return m["id"]
    spec = REGISTRY.get("media.import")
    res = spec.handler(ctx, spec.params.model_validate({"paths": [path]}))
    return res["media_ids"][0]


def framing(seq_settings: dict, item: dict, subject_x: Optional[float]) -> Optional[dict]:
    """Scale and position for landscape footage in a portrait frame, else None."""
    W, H = seq_settings.get("width") or 1080, seq_settings.get("height") or 1920
    v = item.get("video") or {}
    w, h = v.get("width") or 0, v.get("height") or 0
    if not (w and h and H > W and w / h > W / H):
        return None
    side = subject_x is not None and abs(subject_x - 0.5) > SIDE
    scale = (SIDE_SCALE if side else BASE_SCALE) * REFERENCE_LINES / h
    x = W / 2
    if side:
        shown = w * scale / 100
        room = max(0.0, (shown - W) / 2)
        x += max(-room, min(room, (0.5 - subject_x) * shown))
    return {"scale": round(scale, 2), "position": [round(x, 1), H / 2 + BASE_Y_OFFSET * H / 1920]}


@command("clip.place_shots", PlaceShots)
def clip_place_shots(ctx: Context, p: PlaceShots):
    """Place a whole cut plan on one video track (video only) as one undoable edit.
    Each shot gets its source range, speed and framing by the Shorts rules; a shot that
    cannot be filled within min_speed, or that would cover a clip outside the plan,
    refuses the whole plan with the reasons."""
    doc = ctx.doc
    seq = seq_of(doc, p.sequence_id)
    st = seq["settings"]
    tid = p.track_id or default_track(seq, "video")
    if tid not in seq["tracks"] or seq["tracks"][tid]["kind"] != "video" or seq["tracks"][tid].get("caption_style"):
        raise CommandError("BAD_TRACK", f"{tid} is not a video track")
    require_unlocked(seq, tid)
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    margin = timebase.seconds_to_ticks(p.edge_margin)

    # 1. every shot's range, speed and framing, before anything changes
    plans, problems = [], []
    for i, s in enumerate(p.shots):
        n = f"shot {i + 1}"
        at, end = resolve_time(s.at, st), resolve_time(s.end, st)
        if end - at < tpf:
            problems.append(f"{n}: end must be after at")
            continue
        info = _cut(s.cut_id) if s.cut_id is not None else None
        if info is not None:
            mid = _media_for_path(ctx, info["path"])
        elif s.media_id:
            mid = s.media_id
            if mid not in doc["media"]:
                problems.append(f"{n}: no media {mid}")
                continue
        elif s.path:
            mid = _media_for_path(ctx, s.path)
        else:
            problems.append(f"{n}: give cut_id, media_id or path")
            continue
        item = doc["media"][mid]
        length = end - at
        speed, src_in, notes = 1.0, 0, []
        if item["kind"] == "video":
            if s.source_in is not None and s.source_out is not None:
                src_in, src_out = resolve_time(s.source_in, st, snap=False), resolve_time(s.source_out, st, snap=False)
                speed = (src_out - src_in) / length
                if not p.min_speed <= speed <= p.max_speed:
                    problems.append(f"{n}: {src_out - src_in} ticks over {length} plays at {speed:.0%}, "
                                    f"outside {p.min_speed:.0%}-{p.max_speed:.0%}")
                    continue
            else:
                lo, hi = (info["start"] + margin, info["end"] - margin) if info else (0, item["duration"])
                src_in = resolve_time(s.source_in, st, snap=False) if s.source_in is not None else lo
                usable = hi - src_in
                if usable >= length:
                    speed = 1.0
                elif usable >= length * p.min_speed:
                    speed = usable / length
                else:
                    have = usable / timebase.seconds_to_ticks(1)
                    problems.append(f"{n}: the source has {have:.2f}s from its in point"
                                    + (" inside the cut's edge margins" if info else "")
                                    + f"; the shot needs {length / timebase.seconds_to_ticks(1):.2f}s "
                                      f"(at least {length * p.min_speed / timebase.seconds_to_ticks(1):.2f}s "
                                      f"at {p.min_speed:.0%})")
                    continue
        elif item["kind"] != "image":
            problems.append(f"{n}: {item['name']} is {item['kind']}, not a picture")
            continue
        if info:
            if info["luma"] is not None and info["luma"] < 0.15:
                notes.append("dark")
            if info["text_overlay"] is not None and info["text_overlay"] > 0.2:
                notes.append("text over the picture")
        frame = framing(st, item, info["subject_x"] if info else None) if item["kind"] == "video" else None
        if s.scale is not None or s.position is not None:
            frame = dict(frame or {})
            if s.scale is not None:
                frame["scale"] = s.scale
            if s.position is not None:
                frame["position"] = list(s.position)
        plans.append({"n": n, "at": at, "end": end, "media_id": mid, "src_in": src_in, "speed": speed,
                      "frame": frame, "notes": notes, "cut_id": s.cut_id})
    order = sorted(plans, key=lambda x: x["at"])
    for a, b in zip(order, order[1:]):
        if b["at"] < a["end"]:
            problems.append(f"{a['n']} and {b['n']} overlap on the timeline")
    if problems:
        raise CommandError("PLAN_REFUSED", "; ".join(problems), problems=problems)

    # 2. clear each shot's range and lay the clip
    place = REGISTRY.get("clip.place")
    placed, covered = [], []
    ours: set[str] = set()
    for x in order:
        covered += [c for c in clear_range(ctx, seq, tid, x["at"], x["end"], exclude=ours)]
        span = x["end"] - x["at"]
        # Placed at 100% over exactly the shot's range, then given its speed:
        # a faster clip never reaches past its end, so nothing beyond is touched.
        r = place.handler(ctx, place.params.model_validate({
            "sequence_id": seq["id"], "media_id": x["media_id"], "at": x["at"], "mode": "overwrite",
            "source_in": x["src_in"], "source_out": x["src_in"] + span,
            "video_track_id": tid, "take_audio": False}))
        covered += r["covered"]
        cid = r["video_clip_id"]
        _, clip = find_clip(seq, cid)
        new = dict(clip, end=x["end"], speed=x["speed"])
        if x["frame"]:
            new["motion"] = dict(clip["motion"], **x["frame"])
        problem = source_problem(doc, new)
        if problem:
            raise CommandError("PLAN_REFUSED", f"{x['n']}: {problem}")
        write_clip(ctx, seq, tid, new)
        ours.add(cid)
        placed.append({"shot": x["n"], "clip_id": cid, "cut_id": x["cut_id"], "name": clip["name"],
                       "start": x["at"], "end": x["end"],
                       "source_seconds": [round(timebase.ticks_to_seconds(x["src_in"]), 3),
                                          round(timebase.ticks_to_seconds(x["src_in"] + round(span * x["speed"])), 3)]
                       if doc["media"][x["media_id"]]["kind"] == "video" else None,
                       "speed": round(x["speed"], 4), **(x["frame"] or {}), "notes": x["notes"]})
    if covered and not p.allow_cover:
        names = ", ".join(sorted({c["name"] for c in covered}))
        raise CommandError("COVERED", f"the plan would overwrite clips already on the track ({names}); "
                                      "remove them or pass allow_cover", covered=covered)
    return {"track_id": tid, "placed": placed, "covered": covered}
