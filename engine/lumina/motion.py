"""Motion / keyframe evaluation.

Keyframe times are *source* ticks (Premiere's convention), so a keyframe stays
on the same frame of content when the clip is trimmed or slipped. A clip at
sequence time ``t`` shows source time ``source_in + (t - start) * speed``.

The GUI has a TypeScript twin (app/src/renderer/src/util/motion.ts); both are
checked against shared/test-vectors/keyframes.json.
"""

from __future__ import annotations

import math

KEYFRAME_PROPS = (
    "position", "scale", "scale_width", "rotation", "anchor",
    "crop_left", "crop_top", "crop_right", "crop_bottom", "opacity", "volume_db",
)
POINT_PROPS = {"position", "anchor"}


def source_time(clip: dict, seq_time: int) -> int:
    return clip["source_in"] + round((seq_time - clip["start"]) * clip["speed"])


def _ease(u: float) -> float:
    # Premiere's default temporal ease (Bezier, 33% influence) is close to smoothstep
    return u * u * (3 - 2 * u)


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
            if a["interp"] == "ease" or b["interp"] == "ease":
                u = _ease(u)
            if isinstance(a["v"], list):
                return [x + (y - x) * u for x, y in zip(a["v"], b["v"])]
            return a["v"] + (b["v"] - a["v"]) * u
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
