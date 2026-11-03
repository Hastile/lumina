"""shorts_check / edit_check: an edit checked against the grammar as text, with no frames to look at.

Reviewing by eye was the largest single cost of a Short (get_frame_image
100-175 times). Most of what the eye checked is measurable on the timeline
and in the source DB, so this lists only what is wrong, each with a frame:

  picture   V1 shots under 1.1 s, black gaps, cuts off a caption start, speeds
            outside 87-105 %, a source scene change within 3 frames of a clip
            edge (a neighbouring shot flashes), a scene change inside a shot,
            dark shots (DB luma)
  ending    the video ends 3-9 frames after the narration; V5 band, V6 title,
            the last caption and V1 end on that frame; no audio runs past it
  captions  cues at the same time on two caption tracks; an enlarged slam wider
            than one line (810 px)
  sounds    sounds on A2-A4 not tagged SFX; sounds under 0.5 s apart

both formats (docs/editing-grammar: common-cuts, caption-quality)
  reuse     the same source span (or a near-duplicate media) used twice: reuse
            needs a reason in the plan, so every repeat is listed with its first use
  captions  first caption not at frame 0, gaps and overlaps between captions,
            trailing , or . on a caption, protected phrases split across two
            captions (해결하지 / 못했던, 들고 / 온); long-form also: over 3.5 s to
            review, over 5 s or two sentences in one caption to split
  cuts      (long-form) a cut that separates a modifier or object from its head

profile="longform" drops the Shorts-only rules (1.1 s shots, the 87-105 % band,
the V5/V6 ending, the 810 px slam, the A2-A4 sound tracks).

Then one get_frame_strip is enough to see the shots themselves.
"""

from __future__ import annotations

import re

from typing import Optional

from . import timebase
from .tracks import is_caption_track

TPS = timebase.TICKS_PER_SECOND
MIN_SHOT = 1.1
EDGE_FRAMES = 3
END_PAD = (3, 9)
LINE_WIDTH = 810.0
DARK = 0.15


def sp_bound(text: str) -> bool:
    from .shorts_plan import is_bound

    return is_bound(text)


def _dup_root(path: str, db_path=None):
    """The DB identity of a file: its media, or the media it is a near-duplicate of (other size, other format)."""
    try:
        from pathlib import Path

        from .sourcedb import db
        p = db_path or db.DEFAULT_DB_PATH
        if not Path(p).exists():
            return None
        con = db.connect(p)
        try:
            mid = db.media_id_for_path(con, path)
            if mid is None:
                return None
            row = con.execute("SELECT COALESCE(dup_of, media_id) FROM media WHERE media_id = ?", (mid,)).fetchone()
            return f"db:{row[0]}"
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return None


def _scene_info(path: str, db_path=None) -> Optional[dict]:
    """Scene edges and dark spans of a file from the source DB, or None when the DB or the file is not there."""
    try:
        from .sourcedb import db
    except Exception:  # noqa: BLE001
        return None
    p = db_path or db.DEFAULT_DB_PATH
    from pathlib import Path

    if not Path(p).exists():
        return None
    con = db.connect(p)
    try:
        mid = db.media_id_for_path(con, path)
        if mid is None:
            return None
        edges = sorted({t for r in con.execute("SELECT start_ticks, end_ticks FROM cut WHERE media_id = ?", (mid,))
                        for t in r})
        dark = [(r[0], r[1]) for r in con.execute(
            "SELECT t0_ticks, t1_ticks FROM fact WHERE media_id = ? AND kind = 'luma' AND num < ? AND status != 'rejected' "
            "AND t0_ticks IS NOT NULL", (mid, DARK))]
        return {"edges": edges, "dark": dark}
    finally:
        con.close()


TRAILING = re.compile(r"(?<![0-9.])[,.，。．]\s*$")
AUX_AFTER = {"지": ("못", "않", "말"), "고": ("있", "싶", "온", "간", "왔", "갔", "계", "나서"),
             "아": ("주", "보", "버", "놓", "두", "있"), "어": ("주", "보", "버", "놓", "두", "있"), "해": ("주", "보", "버", "놓", "두")}


def protected_split(prev: str, nxt: str) -> bool:
    """A verb and its auxiliary/negation split across captions: 해결하지 / 못했던, 들고 / 온, 먹어 / 버린."""
    a, b = prev.strip().rstrip(",").strip(), nxt.strip()
    if not a or not b:
        return False
    for end, starts in AUX_AFTER.items():
        if a.endswith(end) and b.startswith(starts):
            return True
    return False


def _caption_checks(cues: list[dict], tpf: int, add, longform: bool) -> None:
    from . import shorts_plan as sp

    if not cues:
        return
    if cues[0]["start"] >= tpf:
        add("caption start", cues[0]["start"], f"the first caption starts at frame {cues[0]['start'] // tpf}, not 0 "
                                               "(unless the intro is meant to have none)", cues[0]["id"])
    for a, b in zip(cues, cues[1:]):
        gap = b["start"] - a["end"]
        if gap >= tpf:
            add("caption gap", a["end"], f"{gap // tpf} frames without a caption after '{a['caption']['text']}'", b["id"])
        ta, tb = a["caption"]["text"], b["caption"]["text"]
        if protected_split(ta, tb):
            add("caption split", b["start"], f"'{ta} / {tb}' splits a verb from its auxiliary", b["id"])
    for c in cues:
        text = c["caption"]["text"]
        if TRAILING.search(text) and not text.rstrip().endswith(("...", "…")):
            add("caption punctuation", c["start"], f"'{text}' ends with a comma or full stop", c["id"])
        if longform:
            secs = (c["end"] - c["start"]) / TPS
            inner = re.search(r"(다|요|죠|까)[.?!]\s+\S", text)
            if secs > 5 or inner:
                add("caption long", c["start"], f"'{text[:30]}…' {secs:.1f}s" + (" holds two sentences" if inner else "")
                    + ": split at the speech's clause boundary", c["id"])
            elif secs > 3.5:
                add("caption review", c["start"], f"'{text[:30]}…' is on screen {secs:.1f}s (review)", c["id"])


def _reuse_checks(doc: dict, seq: dict, tpf: int, add, dup_root) -> None:
    pics = []
    for tid in seq["video_tracks"][:3]:
        for c in seq["tracks"][tid]["clips"].values():
            item = doc["media"].get(c.get("media_id") or "")
            if not item or item["kind"] not in ("video", "image"):
                continue
            s0 = c["source_in"]
            s1 = s0 + round((c["end"] - c["start"]) * abs(c["speed"])) if item["kind"] == "video" else s0
            pics.append((c["start"], dup_root(item), s0, s1, c, item["kind"]))
    pics.sort(key=lambda x: x[0])
    for j, (t, root, s0, s1, c, kind) in enumerate(pics):
        for t_prev, root_prev, p0, p1, cp, _k in pics[:j]:
            if root != root_prev:
                continue
            if kind == "image":
                same = True
            else:
                ov = min(s1, p1) - max(s0, p0)
                same = ov > 0 and (ov >= 0.5 * TPS or ov >= 0.5 * min(s1 - s0, p1 - p0))
            if same:
                add("reuse", t, f"'{c['name']}' repeats the source used at frame {t_prev // tpf}: give the reuse a reason "
                                "(new evidence, recall, comparison) or replace it", c["id"])
                break


def check(doc: dict, seq: dict, *, db_path=None, use_db: bool = True, profile: str = "shorts") -> dict:
    longform = profile == "longform"
    st = seq["settings"]
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    problems: list[dict] = []

    def add(kind: str, t: int, msg: str, clip_id: Optional[str] = None) -> None:
        problems.append({"kind": kind, "frame": t // tpf, "msg": msg, **({"clip_id": clip_id} if clip_id else {})})

    vt, at = seq["video_tracks"], seq["audio_tracks"]
    tracks = seq["tracks"]
    caption_tids = [t for t in vt if is_caption_track(tracks[t])]
    cues = sorted((c for t in caption_tids for c in tracks[t]["clips"].values()), key=lambda c: c["start"])
    cue_starts = [c["start"] for c in cues]
    v1 = sorted(tracks[vt[0]]["clips"].values(), key=lambda c: c["start"]) if vt else []

    # ---- picture
    prev_end = 0
    scenes: dict[str, Optional[dict]] = {}
    for c in v1:
        if c["start"] > prev_end and c["start"] - prev_end >= tpf:
            add("gap", prev_end, f"black gap of {(c['start'] - prev_end) // tpf} frames before '{c['name']}'", c["id"])
        prev_end = max(prev_end, c["end"])
        length = (c["end"] - c["start"]) / TPS
        if length < MIN_SHOT and not longform:
            add("short shot", c["start"], f"'{c['name']}' is {length:.2f}s (< {MIN_SHOT}s)", c["id"])
        if c["start"] > 0 and cue_starts and not longform and min(abs(c["start"] - s) for s in cue_starts) > 2 * tpf:
            add("off caption", c["start"], f"cut to '{c['name']}' is not on a caption start", c["id"])
        if longform and c["start"] > 0:
            k = next((n for n, cue in enumerate(cues) if abs(cue["start"] - c["start"]) <= 2 * tpf), None)
            if k and sp_bound(cues[k - 1]["caption"]["text"]):
                add("cut splits phrase", c["start"], f"the cut to '{c['name']}' separates '{cues[k - 1]['caption']['text']}' "
                                                     "from what it modifies", c["id"])
        item = doc["media"].get(c.get("media_id") or "")
        if not item or item["kind"] != "video":
            continue
        if not longform and abs(c["speed"] - 1.0) > 1e-6 and not 0.87 <= abs(c["speed"]) <= 1.05:
            add("speed", c["start"], f"'{c['name']}' plays at {c['speed']:.0%} (keep 87-105%)", c["id"])
        if not use_db or not item.get("path"):
            continue
        if item["path"] not in scenes:
            scenes[item["path"]] = _scene_info(item["path"], db_path)
        info = scenes[item["path"]]
        if not info:
            continue
        s0 = c["source_in"]
        s1 = s0 + round((c["end"] - c["start"]) * c["speed"])
        for e in info["edges"]:
            if not s0 < e < s1:
                continue
            d0, d1 = (e - s0) / c["speed"], (s1 - e) / c["speed"]  # in timeline ticks
            if d0 < EDGE_FRAMES * tpf:
                add("flash", c["start"], f"'{c['name']}' starts {d0 / tpf:.1f} frames before a scene change: the previous "
                                         "shot shows; move the in point onto the change", c["id"])
            elif d1 < EDGE_FRAMES * tpf:
                add("flash", c["end"], f"'{c['name']}' runs {d1 / tpf:.1f} frames into the next shot of its source", c["id"])
            else:
                add("scene inside", c["start"] + round(d0), f"'{c['name']}' has a scene change inside it", c["id"])
        for a, b in info["dark"]:
            if a < s1 and b > s0 and (min(b, s1) - max(a, s0)) >= 0.5 * (s1 - s0):
                add("dark", c["start"], f"'{c['name']}' is dark for most of the shot", c["id"])
                break

    # ---- reuse (both formats)
    roots: dict[str, object] = {}

    def dup_root(item: dict):
        path = item.get("path")
        if path not in roots:
            roots[path] = _dup_root(path, db_path) if use_db and path else None
        return roots[path] or path or item["id"]

    _reuse_checks(doc, seq, tpf, add, dup_root)

    # ---- ending
    narration = None if longform else max((c["end"] for c in tracks[at[0]]["clips"].values()), default=None) if at else None
    video_end = max((c["end"] for c in v1), default=None)
    if narration is not None and video_end is not None:
        pad = (video_end - narration) // tpf
        if not END_PAD[0] <= pad <= END_PAD[1]:
            add("ending", video_end, f"the video ends {pad} frames after the narration (want {END_PAD[0]}-{END_PAD[1]}, "
                                     f"usually 6)")
        ends = {}
        for k, name in ((4, "V5 band"), (5, "V6 title")):
            if len(vt) > k and not is_caption_track(tracks[vt[k]]) and tracks[vt[k]]["clips"]:
                ends[name] = max(c["end"] for c in tracks[vt[k]]["clips"].values())
        if cues:
            ends["last caption"] = max(c["end"] for c in cues)
        for name, e in ends.items():
            if e != video_end:
                add("ending", min(e, video_end), f"{name} ends at frame {e // tpf}, the picture at {video_end // tpf}")
        for tid in at:
            for c in tracks[tid]["clips"].values():
                if c["end"] > video_end + tpf // 2:
                    add("ending", video_end, f"'{c['name']}' on A{at.index(tid) + 1} runs past the end", c["id"])

    # ---- captions
    _caption_checks(cues, tpf, add, longform)
    for a, b in zip(cues, cues[1:]):
        if b["start"] < a["end"] - tpf // 2:
            add("caption overlap", b["start"], f"'{a['caption']['text']}' and '{b['caption']['text']}' are on screen together",
                b["id"])
    try:
        from .commands.shorts_fx import line_width
    except Exception:  # noqa: BLE001
        line_width = None
    if line_width is not None and not longform:
        for t in caption_tids:
            style = tracks[t]["caption_style"]
            for c in tracks[t]["clips"].values():
                size = ((c.get("caption") or {}).get("style") or {}).get("font_size")
                if size and size > (style.get("font_size") or 100):
                    w = line_width(style, c["caption"], size)
                    if w and w > LINE_WIDTH:
                        add("caption wide", c["start"], f"'{c['caption']['text']}' at {size:.0f} is {w:.0f}px (> {LINE_WIDTH:.0f})",
                            c["id"])

    # ---- sounds
    sounds = [] if longform else sorted((c for tid in at[1:4] for c in tracks[tid]["clips"].values()), key=lambda c: c["start"])
    for c in sounds:
        if c.get("audio_tag") != "sfx":
            add("sfx tag", c["start"], f"'{c['name']}' is not tagged SFX (Auto-Match skips it)", c["id"])
    for a, b in zip(sounds, sounds[1:]):
        if b["start"] - a["start"] < 0.5 * TPS:
            add("sfx close", b["start"], f"'{a['name']}' and '{b['name']}' start under 0.5 s apart", b["id"])

    problems.sort(key=lambda p: (p["frame"], p["kind"]))
    shots = [(c["end"] - c["start"]) / TPS for c in v1]
    return {
        "ok": not problems,
        "problems": problems,
        "summary": {"shots": len(v1), "captions": len(cues), "sounds": len(sounds),
                    "seconds": round((video_end or 0) / TPS, 2),
                    "shot_seconds": [round(min(shots), 2), round(sum(shots) / len(shots), 2), round(max(shots), 2)] if shots else None,
                    "source_db": any(v is not None for v in scenes.values()), "profile": profile},
    }


def render(rep: dict) -> str:
    s = rep["summary"]
    head = (f"{s['shots']} shots, {s['captions']} captions, {s['sounds']} sounds, {s['seconds']}s"
            + (f"; shot length min/mean/max {s['shot_seconds']}" if s["shot_seconds"] else "")
            + ("" if s["source_db"] else "; source DB not consulted (no scene/dark checks)"))
    if rep["ok"]:
        return head + "\nno problems"
    return head + "\n" + "\n".join(f"{p['frame']:>5}f {p['kind']:<15} {p['msg']}" for p in rep["problems"])
