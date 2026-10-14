"""Motion / keyframe evaluation.

Keyframe times are *source* ticks (Premiere's convention), so a keyframe stays
on the same frame of content when the clip is trimmed or slipped. A clip at
sequence time ``t`` shows source time ``source_in + (t - start) * speed``.

The GUI has a TypeScript twin (app/src/renderer/src/util/motion.ts); both are
checked against shared/test-vectors/keyframes.json.
"""

from __future__ import annotations

import math

from .timebase import TICKS_PER_SECOND

KEYFRAME_PROPS = (
    "position", "scale", "scale_width", "rotation", "anchor",
    "crop_left", "crop_top", "crop_right", "crop_bottom", "opacity", "volume_db",
)
POINT_PROPS = {"position", "anchor"}


def source_time(clip: dict, seq_time: int) -> int:
    return clip["source_in"] + round((seq_time - clip["start"]) * clip["speed"])


EASY_EASE = (0.0, 1 / 3)  # Premiere's Easy Ease handle: speed 0, influence 33.3 %


def _handle(k: dict, side: str):
    """(speed, influence) of a keyframe's Bezier handle on ``side`` ('in'/'out'), or None for linear."""
    if k.get("interp") not in ("bezier", "ease"):
        return None
    h = k.get("ease_" + side)
    if h:
        return float(h[0]), min(1.0, max(0.0, float(h[1])))
    return EASY_EASE


def _cubic(p0: float, p1: float, p2: float, p3: float, u: float) -> float:
    v = 1 - u
    return v * v * v * p0 + 3 * v * v * u * p1 + 3 * v * u * u * p2 + u * u * u * p3


def _progress(a: dict, b: dict, u: float, dist: float) -> float:
    """Temporal Bezier: distance covered (0..dist) at time fraction u of the segment.
    Control points in (time fraction, distance); handle heights are speed x
    influence x segment duration, as in After Effects."""
    ha, hb = _handle(a, "out"), _handle(b, "in")
    if ha is None and hb is None:
        return u * dist
    dt = (b["t"] - a["t"]) / TICKS_PER_SECOND
    x1, y1 = (ha[1], ha[0] * ha[1] * dt) if ha else (1 / 3, dist / 3)
    x2, y2 = (1 - hb[1], dist - hb[0] * hb[1] * dt) if hb else (2 / 3, 2 * dist / 3)
    lo, hi = 0.0, 1.0
    for _ in range(48):  # x(s) is monotonic for influences in [0, 1]
        mid = (lo + hi) / 2
        if _cubic(0.0, x1, x2, 1.0, mid) < u:
            lo = mid
        else:
            hi = mid
    return _cubic(0.0, y1, y2, dist, (lo + hi) / 2)


PATH_STEPS = 64


def _path_point(q: list[list[float]], s: float) -> list[float]:
    return [_cubic(q[0][i], q[1][i], q[2][i], q[3][i], s) for i in range(len(q[0]))]


def _point_at(a: dict, b: dict, u: float) -> list[float]:
    pa, pb = [float(x) for x in a["v"]], [float(x) for x in b["v"]]
    to, ti = a.get("tangent_out"), b.get("tangent_in")
    if not to and not ti:
        dist = math.dist(pa, pb)
        if dist == 0:
            return pa
        f = _progress(a, b, u, dist) / dist
        return [x + (y - x) * f for x, y in zip(pa, pb)]
    q = [pa, [x + d for x, d in zip(pa, to or [0.0] * len(pa))], [x + d for x, d in zip(pb, ti or [0.0] * len(pb))], pb]
    pts = [_path_point(q, i / PATH_STEPS) for i in range(PATH_STEPS + 1)]
    cum = [0.0]
    for p0, p1 in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.dist(p0, p1))
    total = cum[-1]
    if total == 0:
        return pa
    d = min(max(_progress(a, b, u, total), 0.0), total)
    for i in range(PATH_STEPS):
        if cum[i + 1] >= d:
            seg = cum[i + 1] - cum[i]
            f = (d - cum[i]) / seg if seg else 0.0
            return [x + (y - x) * f for x, y in zip(pts[i], pts[i + 1])]
    return pts[-1]


def interpolate(keyframes: list[dict], t: int):
    """Value of a keyframed property at source time ``t``."""
    if not keyframes:
        raise ValueError("no keyframes")
    if t <= keyframes[0]["t"]:
        return keyframes[0]["v"]
    if t >= keyframes[-1]["t"]:
        return keyframes[-1]["v"]
    for a, b in zip(keyframes, keyframes[1:]):
        if a["t"] <= t < b["t"]:
            if a["interp"] == "hold":
                return a["v"]
            u = (t - a["t"]) / (b["t"] - a["t"])
            if isinstance(a["v"], list):
                return _point_at(a, b, u)
            dv = b["v"] - a["v"]
            ha, hb = _handle(a, "out"), _handle(b, "in")
            if ha is None and hb is None:
                return a["v"] + dv * u
            # scalar: the "distance" is the value itself (speeds are signed)
            return a["v"] + _progress(a, b, u, dv)
    return keyframes[-1]["v"]


def static_value(clip: dict, prop: str, seq: dict | None = None, media: dict | None = None):
    m = clip["motion"]
    if prop == "opacity":
        return clip["opacity"]
    if prop == "volume_db":
        return clip["volume_db"]
    if prop == "position":
        if m["position"] is not None:
            return list(m["position"])
        st = (seq or {}).get("settings") or {"width": 1920, "height": 1080}
        return [st["width"] / 2, st["height"] / 2]
    if prop == "anchor":
        if m["anchor"] is not None:
            return list(m["anchor"])
        w, h = source_size(clip, seq, media)
        return [w / 2, h / 2]
    return m[prop]


def source_size(clip: dict, seq: dict | None, media: dict | None) -> tuple[float, float]:
    """Pixel size of what the clip shows: the media frame, or the sequence frame for graphics."""
    if media and media.get("video"):
        return media["video"]["width"], media["video"]["height"]
    st = (seq or {}).get("settings") or {"width": 1920, "height": 1080}
    return st["width"], st["height"]


def value_at(clip: dict, prop: str, seq_time: int, seq: dict | None = None, media: dict | None = None):
    kfs = clip.get("keyframes", {}).get(prop)
    if kfs:
        return interpolate(kfs, source_time(clip, seq_time))
    return static_value(clip, prop, seq, media)


def transform_at(clip: dict, seq_time: int, seq: dict, media: dict | None) -> dict:
    """Resolved Motion for rendering/inspection at a sequence time."""
    out = {p: value_at(clip, p, seq_time, seq, media) for p in KEYFRAME_PROPS}
    m = clip["motion"]
    if m["uniform"]:
        out["scale_x"] = out["scale_y"] = out["scale"]
    else:
        # Premiere: with Uniform Scale off, "Scale" is the height and "Scale Width" the width
        out["scale_x"], out["scale_y"] = out["scale_width"], out["scale"]
    out["rotation_rad"] = math.radians(out["rotation"])
    return out
