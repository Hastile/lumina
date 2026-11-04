"""Filling a Short as its sources arrive: the shot plan lives on the sequence as markers, and each new
source is matched to the shots still empty.

The usual way of working is to set the Short up first (narration, captions, title, effects, sounds)
before the footage exists, then fill the picture as sources are handed over one by one. Without a
stored plan, every hand-over meant re-deriving the plan and re-reading the timeline. Here:

  shorts.mark_shots   writes the plan as sequence markers S1, S2 ... spanning each shot, the comment
                      holding "look | narration" (visible and draggable in the GUI; it survives the
                      session, so the next session does not plan again)
  shorts_gaps         reads those markers and V1: which shots are empty or only partly covered; given
                      new files (or DB cut ids), searches only them for the empty shots and returns a
                      ready clip_place_shots list (each cut used once)
"""

from __future__ import annotations

import re
from typing import Optional

from . import timebase

TPS = timebase.TICKS_PER_SECOND
SHOT_NAME = re.compile(r"^S(\d+)$")


def comment(look: str, text: str) -> str:
    return f"{look.strip()} | {text.strip()}" if look.strip() else text.strip()


def parse_comment(c: str) -> tuple[str, str]:
    if " | " in c:
        look, text = c.split(" | ", 1)
        return look.strip(), text.strip()
    return "", c.strip()


def shot_markers(seq: dict) -> list[dict]:
    out = []
    for m in seq.get("markers", {}).values():
        g = SHOT_NAME.match(m.get("name") or "")
        if g:
            look, text = parse_comment(m.get("comment") or "")
            out.append({"n": int(g.group(1)), "marker_id": m["id"], "at": m["start"], "end": m["start"] + (m.get("duration") or 0),
                        "look": look, "text": text})
    out.sort(key=lambda x: x["at"])
    return out


def coverage(seq: dict, at: int, end: int) -> list[tuple[int, int]]:
    """The parts of [at, end) that no V1 clip covers."""
    v1 = seq["tracks"][seq["video_tracks"][0]]["clips"].values() if seq["video_tracks"] else []
    spans = sorted((max(at, c["start"]), min(end, c["end"])) for c in v1 if c["start"] < end and c["end"] > at)
    holes, cur = [], at
    for s, e in spans:
        if s > cur:
            holes.append((cur, s))
        cur = max(cur, e)
    if cur < end:
        holes.append((cur, end))
    return holes


def gaps(doc: dict, seq: dict, *, min_frames: int = 2) -> dict:
    st = seq["settings"]
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    shots = shot_markers(seq)
    rows = []
    for s in shots:
        holes = [(a, b) for a, b in coverage(seq, s["at"], s["end"]) if b - a >= min_frames * tpf]
        length = s["end"] - s["at"]
        status = "filled" if not holes else ("empty" if sum(b - a for a, b in holes) >= length - tpf else "partial")
        rows.append({**s, "status": status, "seconds": round(length / TPS, 2), "frames": [s["at"] // tpf, s["end"] // tpf],
                     "holes": [[a // tpf, b // tpf] for a, b in holes]})
    return {"shots": rows, "empty": [r["n"] for r in rows if r["status"] == "empty"],
            "partial": [r["n"] for r in rows if r["status"] == "partial"], "marked": bool(shots)}


def render(rep: dict) -> str:
    if not rep["marked"]:
        return "no shot markers (S1, S2 ...) on the sequence: run shorts_mark_shots (or shorts_build) first"
    head = (f"{len(rep['shots'])} shots: {len(rep['empty'])} empty, {len(rep['partial'])} partial, "
            f"{len(rep['shots']) - len(rep['empty']) - len(rep['partial'])} filled")
    lines = [head]
    for r in rep["shots"]:
        if r["status"] == "filled":
            continue
        holes = "" if r["status"] == "empty" else " holes " + ", ".join(f"{a}-{b}f" for a, b in r["holes"])
        lines.append(f"S{r['n']} {r['frames'][0]}-{r['frames'][1]}f {r['seconds']}s {r['status']}{holes} | "
                     f"{r['look'] or '(no look)'} | \"{r['text']}\"")
    return "\n".join(lines)


class _NoPictures:
    """Words only: no picture encoder (no GPU environment needed)."""

    def encode(self, texts):
        return None


def suggest(con, rep: dict, media_ids: list[int], *, per_shot: int = 3, use_pictures: bool = True,
            include_partial: bool = False) -> dict:
    """Candidates from the given media only, for the empty shots; the best unused cut per shot as a
    clip_place_shots list."""
    from .sourcedb import plan as splan, query

    want = [r for r in rep["shots"] if r["status"] == "empty" or (include_partial and r["status"] == "partial")]
    if not want or not media_ids:
        return {"candidates": "", "place": []}
    shots = [splan.Shot(text=r["text"], look=r["look"], seconds=r["seconds"]) for r in want]
    f = query.Filters(media_ids=tuple(media_ids), include_staged=True)
    if not use_pictures:
        shots = [splan.Shot(text=f"{s.text} {s.look}".strip(), seconds=s.seconds) for s in shots]
    results = splan.plan(con, shots, f, per_shot=per_shot, encoder=None if use_pictures else _NoPictures())
    used, place = set(), []
    for r, hits in zip(want, results):
        pick = next((h for h in hits if h.cut_id not in used), None)
        if pick is None:
            continue
        used.add(pick.cut_id)
        place.append({"at": f"{r['frames'][0]}f", "end": f"{r['frames'][1]}f", "cut_id": pick.cut_id, "shot": f"S{r['n']}"})
    lines = []
    for r, hits in zip(want, results):
        lines.append(f"S{r['n']} {r['seconds']}s \"{r['text']}\"" + (f" / look: {r['look']}" if r["look"] else ""))
        lines += ["  " + h.line() for h in hits] or ["  (nothing in these sources fits)"]
    return {"candidates": "\n".join(lines), "place": place}
