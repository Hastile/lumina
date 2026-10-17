"""Media analysis for automated edits: silence, black, freeze, scene changes and
loudness, using the ffmpeg passes and parsers ported from adb-mcp
(``lumina.adb.premiere_*_analysis``), mapped onto Lumina clips and sequences.

``detect`` reports what a media item (or a clip's source range) contains;
``plan_cuts`` turns silence/black on chosen tracks into sequence ranges ready for
``sequence.remove_ranges``; ``plan_scene_cuts`` turns scene changes into razor
points. Planning never edits; the RPC wrappers apply the plan as one undo step
when asked.
"""

from __future__ import annotations

import subprocess

from . import timebase
from .adb import premiere_audio_analysis as aa
from .adb import premiere_video_analysis as va
from .adb.quiet_process import NO_WINDOW
from .commands.project import seq_of
from .commands.registry import CommandError
from .media.probe import ffmpeg_bin
from .render.compositor import media_path

TPS = timebase.TICKS_PER_SECOND
KINDS = ("silence", "black", "freeze", "scene", "loudness", "peak_rms")


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600, **NO_WINDOW)
    return proc.stderr or ""


def _source_range(clip: dict) -> tuple[int, int]:
    a = clip["source_in"]
    b = a + round((clip["end"] - clip["start"]) * clip["speed"])
    return min(a, b), max(a, b)


def _to_seq(clip: dict, src: float) -> int:
    return round(clip["start"] + (src - clip["source_in"]) / clip["speed"])


def detect_path(path: str, kind: str, start: float | None = None, duration: float | None = None, *, silence_db: float = -40.0,
                min_silence: float = 0.5, scene_threshold: float = 0.4, black_min: float = 0.1,
                black_pixel: float | None = None, black_ratio: float | None = None) -> dict:
    """One analysis pass over a file (times in file seconds)."""
    if kind not in KINDS:
        raise CommandError("BAD_PARAMS", f"kind is one of {KINDS}")
    if kind in ("silence", "loudness", "peak_rms"):
        cmd = aa.build_analysis_command(ffmpeg_bin(), path, "ebur128" if kind == "loudness" else kind, start_seconds=start,
                                        duration_seconds=duration, silence_threshold_db=silence_db,
                                        silence_min_duration_seconds=min_silence)
        err = _run(cmd)
        if kind == "silence":
            out = aa.parse_silencedetect_output(err)
            end = (start or 0) + (duration or 0) if duration else None
            for iv in out:  # silence running to the end of the range
                if iv["endSeconds"] is None and end is not None:
                    iv["endSeconds"], iv["durationSeconds"] = end, end - iv["startSeconds"]
            return {"intervals": [iv for iv in out if iv["endSeconds"] is not None]}
        parsed = aa.parse_ebur128_output(err) if kind == "loudness" else aa.parse_astats_output(err)
        return aa.sanitize_for_json(parsed)[0]
    cmd = va.build_video_analysis_command(ffmpeg_bin(), path, kind, start_seconds=start, duration_seconds=duration,
                                          scene_threshold=scene_threshold, black_min_duration_seconds=black_min,
                                          black_pixel_threshold=black_pixel, black_picture_ratio=black_ratio)
    err = _run(cmd)
    if kind == "scene":
        events = va.parse_scene_metadata_output(err)
        for ev in events:  # -ss resets timestamps to 0
            ev["ptsSeconds"] += start or 0
        return {"events": events}
    parse = va.parse_blackdetect_output if kind == "black" else va.parse_freezedetect_output
    out = parse(err)
    for iv in out:
        for k in ("startSeconds", "endSeconds"):
            if iv.get(k) is not None:
                iv[k] += start or 0
    return {"intervals": out}


def _media(doc: dict, media_id: str) -> tuple[dict, str]:
    item = doc["media"].get(media_id)
    if item is None:
        raise CommandError("NO_MEDIA", f"media not found: {media_id}")
    path = media_path(item)
    if path is None:
        raise CommandError("MEDIA_OFFLINE", f"media offline: {item['name']}")
    return item, path


def detect(doc: dict, kind: str, *, media_id: str | None = None, clip_id: str | None = None, sequence_id: str | None = None,
           **opts) -> dict:
    """Analyse a whole media item, or the source range a clip uses (then events also carry sequence ticks)."""
    if clip_id:
        from .commands.timeline import find_clip

        seq = seq_of(doc, sequence_id)
        _tid, clip = find_clip(seq, clip_id)
        if not clip.get("media_id"):
            raise CommandError("NO_MEDIA", "that clip has no media to analyse")
        _item, path = _media(doc, clip["media_id"])
        lo, hi = _source_range(clip)
        res = detect_path(path, kind, lo / TPS, (hi - lo) / TPS, **opts)
        for iv in res.get("intervals", []):
            s, e = _to_seq(clip, iv["startSeconds"] * TPS), _to_seq(clip, iv["endSeconds"] * TPS)
            iv["sequenceStart"], iv["sequenceEnd"] = max(clip["start"], min(s, e)), min(clip["end"], max(s, e))
        for ev in res.get("events", []):
            ev["sequenceTime"] = _to_seq(clip, ev["ptsSeconds"] * TPS)
        return {"kind": kind, "clip_id": clip_id, **res}
    if not media_id:
        raise CommandError("BAD_PARAMS", "give media_id or clip_id")
    _item, path = _media(doc, media_id)
    return {"kind": kind, "media_id": media_id, **detect_path(path, kind, **opts)}


# ---------------------------------------------------------------- interval helpers (sequence ticks)

def _merge(iv: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted(iv):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _intersect(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if s < e:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _subtract(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = []
    for s, e in a:
        cur = s
        for bs, be in b:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
        if cur < e:
            out.append((cur, e))
    return out


def plan_cuts(doc: dict, kind: str, *, sequence_id: str | None = None, track_ids: list[str] | None = None,
              clip_ids: list[str] | None = None, padding_before: float = 0.1, padding_after: float = 0.1, min_duration: float = 0.5,
              protect_markers: bool = True, marker_padding: float = 0.25, **opts) -> dict:
    """Sequence ranges [start, end) that are silent (audio tracks) or black (video tracks) on every chosen
    track that has a clip there, shrunk by the paddings (some pause is kept), frame-snapped, at least
    ``min_duration`` long, and clear of markers."""
    if kind not in ("silence", "black"):
        raise CommandError("BAD_PARAMS", "plan_cuts kind is silence or black")
    seq = seq_of(doc, sequence_id)
    st = seq["settings"]
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    kind_tracks = seq["audio_tracks"] if kind == "silence" else seq["video_tracks"]
    tids = [t for t in (track_ids or kind_tracks) if t in kind_tracks]
    if kind == "silence":
        opts.setdefault("min_silence", min_duration)
    else:
        opts.setdefault("black_min", min_duration)
    per_track, analysed = [], 0
    for tid in tids:
        covered, hits = [], []
        for c in seq["tracks"][tid]["clips"].values():
            if not c["enabled"] or not c.get("media_id") or (clip_ids and c["id"] not in clip_ids):
                continue
            covered.append((c["start"], c["end"]))
            res = detect(doc, kind, clip_id=c["id"], sequence_id=seq["id"], **opts)
            analysed += 1
            hits += [(iv["sequenceStart"], iv["sequenceEnd"]) for iv in res["intervals"]]
        if covered:
            covered = _merge(covered)
            # where this track has no clip, it does not veto a cut
            per_track.append((covered, _merge(hits)))
    if not per_track:
        return {"kind": kind, "ranges": [], "analysed_clips": 0, "removed_seconds": 0.0}
    union_cov = _merge([iv for cov, _ in per_track for iv in cov])
    result = union_cov
    for cov, hits in per_track:
        allowed = _merge(hits + _subtract(union_cov, cov))
        result = _intersect(result, allowed)
    pb, pa = round(padding_before * TPS), round(padding_after * TPS)
    ranges = []
    for s, e in result:
        s, e = s + pb, e - pa
        s = -(-s // tpf) * tpf  # inside the silence: round the start up, the end down
        e = e // tpf * tpf
        if e - s >= max(tpf, round(min_duration * TPS) - pb - pa):
            ranges.append((s, e))
    if protect_markers and seq.get("markers"):
        mp = round(marker_padding * TPS)
        guard = _merge([(m["start"] - mp, m["start"] + m.get("duration", 0) + mp) for m in seq["markers"].values()])
        ranges = [r for r in _subtract(ranges, guard) if r[1] - r[0] >= tpf]
    return {"kind": kind, "ranges": [list(r) for r in ranges], "analysed_clips": analysed,
            "removed_seconds": round(sum(e - s for s, e in ranges) / TPS, 3),
            "ranges_seconds": [[round(s / TPS, 3), round(e / TPS, 3)] for s, e in ranges]}


def plan_scene_cuts(doc: dict, *, sequence_id: str | None = None, track_ids: list[str] | None = None,
                    clip_ids: list[str] | None = None, scene_threshold: float = 0.4, min_shot: float = 0.5) -> dict:
    """Sequence times where the picture cuts to a new shot inside a clip (razor points), at least
    ``min_shot`` seconds from each other and from the clip's edges."""
    seq = seq_of(doc, sequence_id)
    st = seq["settings"]
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    gap = max(tpf, round(min_shot * TPS))
    points = []
    for tid in track_ids or seq["video_tracks"]:
        for c in seq["tracks"][tid]["clips"].values():
            if not c.get("media_id") or (clip_ids and c["id"] not in clip_ids):
                continue
            item = doc["media"].get(c["media_id"])
            if item is None or item["kind"] != "video":
                continue
            res = detect(doc, "scene", clip_id=c["id"], sequence_id=seq["id"], scene_threshold=scene_threshold)
            last = c["start"]
            for ev in sorted(res["events"], key=lambda x: x["sequenceTime"]):
                t = round(ev["sequenceTime"] / tpf) * tpf
                if t - last >= gap and c["end"] - t >= gap:
                    points.append({"time": t, "seconds": round(t / TPS, 3), "clip_id": c["id"], "score": ev.get("sceneScore")})
                    last = t
    points.sort(key=lambda x: x["time"])
    return {"points": points, "count": len(points)}
