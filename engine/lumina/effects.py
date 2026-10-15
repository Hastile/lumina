"""Standard effects and transitions: definitions, defaults and evaluation.

Premiere's render order is: standard effects in list order, then the
intrinsic Motion (with its crop) and Opacity. Effects work in the clip's
source pixel space (a graphic's source is the sequence frame), so Transform's
default position/anchor is the source centre, exactly like Premiere.

The GUI has a TypeScript twin (app/src/renderer/src/util/effects.ts).
"""

from __future__ import annotations

import math

from .motion import interpolate, source_size, source_time

# param name -> (kind, default, label). Point defaults of None mean "source centre".
EFFECTS: dict[str, dict] = {
    "transform": {
        "label": "Transform",
        "category": "Distort",
        "params": {
            "anchor": ("point", None, "Anchor Point"),
            "position": ("point", None, "Position"),
            "uniform": ("bool", True, "Uniform Scale"),
            "scale": ("number", 100.0, "Scale Height"),
            "scale_width": ("number", 100.0, "Scale Width"),
            "skew": ("number", 0.0, "Skew"),
            "skew_axis": ("number", 0.0, "Skew Axis"),
            "rotation": ("number", 0.0, "Rotation"),
            "opacity": ("number", 100.0, "Opacity"),
        },
    },
    "gaussian_blur": {
        "label": "Gaussian Blur",
        "category": "Blur & Sharpen",
        "params": {
            "blurriness": ("number", 0.0, "Blurriness"),
            "dimensions": ("choice", 0.0, "Blur Dimensions"),  # 0 both, 1 horizontal, 2 vertical
            "repeat_edge": ("bool", False, "Repeat Edge Pixels"),
        },
    },
    # audio effects ("level": a number that is not keyframed); DSP in audio_fx.py
    "parametric_eq": {
        "label": "Parametric Equalizer",
        "category": "Filter and EQ",
        "media": "audio",
        "params": {
            "low_freq": ("level", 100.0, "Low Frequency"),
            "low_gain": ("level", 0.0, "Low Gain"),
            "mid_freq": ("level", 1000.0, "Mid Frequency"),
            "mid_gain": ("level", 0.0, "Mid Gain"),
            "mid_q": ("level", 1.0, "Mid Q"),
            "high_freq": ("level", 8000.0, "High Frequency"),
            "high_gain": ("level", 0.0, "High Gain"),
        },
    },
    "compressor": {
        "label": "Single-band Compressor",
        "category": "Amplitude and Compression",
        "media": "audio",
        "params": {
            "threshold": ("level", -20.0, "Threshold"),
            "ratio": ("level", 4.0, "Ratio"),
            "attack": ("level", 10.0, "Attack"),
            "release": ("level", 100.0, "Release"),
            "output_gain": ("level", 0.0, "Output Gain"),
        },
    },
    "hard_limiter": {
        "label": "Hard Limiter",
        "category": "Amplitude and Compression",
        "media": "audio",
        "params": {
            "max_amplitude": ("level", -0.1, "Maximum Amplitude"),
            "input_boost": ("level", 0.0, "Input Boost"),
        },
    },
}

# ranges of "level" parameters (Premiere's)
LEVEL_RANGES: dict[str, tuple[float, float]] = {
    "low_freq": (20, 2000), "mid_freq": (40, 16000), "high_freq": (1000, 20000),
    "low_gain": (-30, 30), "mid_gain": (-30, 30), "high_gain": (-30, 30), "mid_q": (0.1, 30),
    "threshold": (-60, 0), "ratio": (1, 20), "attack": (0, 1000), "release": (0, 3000), "output_gain": (-30, 30),
    "max_amplitude": (-30, 0), "input_boost": (0, 30),
}


def media_of(kind: str) -> str:
    return EFFECTS[kind].get("media", "video")

TRANSITIONS: dict[str, dict] = {
    "cross_dissolve": {"label": "Cross Dissolve", "kind": "video"},
    "dip_to_black": {"label": "Dip to Black", "kind": "video"},
    "dip_to_white": {"label": "Dip to White", "kind": "video"},
    "constant_power": {"label": "Constant Power", "kind": "audio"},
}

ANIMATABLE = {"point", "number"}
BLUR_SIGMA_PER_UNIT = 1 / 3  # Premiere "Blurriness" ~ 3 sigma (approximation shared with the GUI)


def default_params(kind: str, src_w: float, src_h: float) -> dict:
    out = {}
    for name, (ptype, default, _) in EFFECTS[kind]["params"].items():
        out[name] = [src_w / 2, src_h / 2] if ptype == "point" and default is None else default
    return out


def param(effect: dict, name: str, clip: dict, seq_time: int, src_w: float = 1920, src_h: float = 1080):
    kfs = effect.get("keyframes", {}).get(name)
    if kfs:
        return interpolate(kfs, source_time(clip, seq_time))
    if name in effect["params"]:
        return effect["params"][name]
    ptype, default, _ = EFFECTS[effect["type"]]["params"][name]
    return [src_w / 2, src_h / 2] if ptype == "point" and default is None else default


# ---------------------------------------------------------------- affine helpers (2x3, row-major a b c / d e f)

IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def mul(m: tuple, n: tuple) -> tuple:
    """m after n."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * D, a * B + b * E, a * C + b * F + c,
            d * A + e * D, d * B + e * E, d * C + e * F + f)


def invert(m: tuple) -> tuple | None:
    a, b, c, d, e, f = m
    det = a * e - b * d
    if abs(det) < 1e-12:
        return None
    ia, ib, id_, ie = e / det, -b / det, -d / det, a / det
    return (ia, ib, -(ia * c + ib * f), id_, ie, -(id_ * c + ie * f))


def apply(m: tuple, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]


def trs(position, anchor, sx: float, sy: float, rotation_deg: float, skew_deg: float = 0.0, skew_axis_deg: float = 0.0) -> tuple:
    """dst = position + R(rot) K(skew) S (src - anchor), y down, clockwise degrees."""
    m = (1.0, 0.0, -anchor[0], 0.0, 1.0, -anchor[1])
    m = mul((sx, 0.0, 0.0, 0.0, sy, 0.0), m)
    if skew_deg:
        ax = math.radians(skew_axis_deg)
        k = math.tan(math.radians(max(-85.0, min(85.0, skew_deg))))
        ca, sa = math.cos(ax), math.sin(ax)
        rot_neg = (ca, sa, 0.0, -sa, ca, 0.0)
        rot_pos = (ca, -sa, 0.0, sa, ca, 0.0)
        m = mul(rot_pos, mul((1.0, -k, 0.0, 0.0, 1.0, 0.0), mul(rot_neg, m)))
    r = math.radians(rotation_deg)
    cr, sr = math.cos(r), math.sin(r)
    m = mul((cr, -sr, 0.0, sr, cr, 0.0), m)
    return mul((1.0, 0.0, position[0], 0.0, 1.0, position[1]), m)


def transform_effect_at(effect: dict, clip: dict, seq_time: int, src_w: float, src_h: float) -> tuple[tuple, float]:
    """-> (source->source affine, opacity 0-1)"""
    v = lambda n: param(effect, n, clip, seq_time, src_w, src_h)  # noqa: E731
    sy = v("scale") / 100
    sx = sy if v("uniform") else v("scale_width") / 100
    m = trs(v("position"), v("anchor"), sx, sy, v("rotation"), v("skew"), v("skew_axis"))
    return m, max(0.0, min(1.0, v("opacity") / 100))


def blur_sigma(effect: dict, clip: dict, seq_time: int) -> tuple[float, float]:
    s = max(0.0, float(param(effect, "blurriness", clip, seq_time))) * BLUR_SIGMA_PER_UNIT
    dims = round(float(param(effect, "dimensions", clip, seq_time)))
    return (s, 0.0) if dims == 1 else (0.0, s) if dims == 2 else (s, s)


def clip_source_size(clip: dict, seq: dict, doc: dict) -> tuple[float, float]:
    media = doc["media"].get(clip["media_id"] or "") if clip.get("media_id") else None
    return source_size(clip, seq, media)


# ---------------------------------------------------------------- transitions


def transition_window(track: dict, tr: dict) -> tuple[int, int, dict | None, dict | None] | None:
    """(start, end, clip_a, clip_b) in sequence ticks, or None if the
    transition no longer sits on a valid edit point."""
    a = track["clips"].get(tr["clip_a"]) if tr.get("clip_a") else None
    b = track["clips"].get(tr["clip_b"]) if tr.get("clip_b") else None
    if (tr.get("clip_a") and a is None) or (tr.get("clip_b") and b is None) or (a is None and b is None):
        return None
    if a is not None and b is not None and a["end"] != b["start"]:
        return None
    cut = a["end"] if a is not None else b["start"]
    d = max(1, tr["duration"])
    align = tr.get("alignment", "center")
    if a is None:
        align = "start"
    elif b is None:
        align = "end"
    if align == "center":
        s = cut - d // 2
        return s, s + d, a, b
    if align == "start":
        return cut, cut + d, a, b
    return cut - d, cut, a, b


def track_transitions(track: dict) -> list[tuple[int, int, dict, dict | None, dict | None]]:
    out = []
    for tr in track.get("transitions", {}).values():
        w = transition_window(track, tr)
        if w is not None:
            out.append((w[0], w[1], tr, w[2], w[3]))
    return sorted(out, key=lambda x: x[0])


def dissolve_progress(t: int, start: int, end: int) -> float:
    return max(0.0, min(1.0, (t - start) / max(1, end - start)))
