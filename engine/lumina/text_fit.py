"""Where a graphic's text lands in the frame, so automated copy never runs off screen.

Bounds are the rendered ink (glyphs, stroke and shadow) of a text layer,
placed the way the compositor places it: the layer at
``position + scale * (src - anchor)`` inside the graphic, then the clip's
Motion on top. Rotation is honoured by bounding the transformed corners.
"""

from __future__ import annotations

import functools

from . import effects as fx_
from .motion import transform_at
from .render import text as text_


def layer_bounds(seq: dict, clip: dict, layer: dict, t: int | None = None) -> list[float] | None:
    """Frame-space [x0, y0, x1, y1] of a text layer's ink at sequence time t
    (default: the clip's start), or None when nothing is drawn."""
    img, ax, ay = text_.render_text_layer(layer)
    box = img.getchannel("A").getbbox()
    if box is None:
        return None
    s = float(layer.get("scale", 100.0)) / 100
    inner = fx_.trs(layer["position"], [ax, ay], s, s, float(layer.get("rotation", 0.0)))
    tr = transform_at(clip, clip["start"] if t is None else t, seq, None)
    outer = fx_.trs(tr["position"], tr["anchor"], tr["scale_x"] / 100, tr["scale_y"] / 100, tr["rotation"])
    m = fx_.mul(outer, inner)
    l, t_, r, b = box
    pts = [fx_.apply(m, x, y) for x, y in ((l, t_), (r, t_), (l, b), (r, b))]
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


@functools.lru_cache(maxsize=64)
def _notdef(path: str, index: int, size: int) -> bytes:
    font = text_.ImageFont.truetype(path, size, index=index)
    return bytes(font.getmask("\U0010fffd"))


def missing_glyphs(layer: dict) -> list[str]:
    """Characters of the layer's text its font draws as the missing-glyph box (or nothing)."""
    from .render import fonts

    face, _ = fonts.find(layer.get("font_family") or "", bool(layer.get("bold")))
    if not face:
        return []
    size = 64
    font = text_.ImageFont.truetype(face["path"], size, index=face["index"])
    notdef = _notdef(face["path"], face["index"], size)
    text = layer.get("text") or "".join(r.get("text", "") for r in layer.get("runs") or [])
    return sorted({ch for ch in text if not ch.isspace() and bytes(font.getmask(ch)) == notdef})


def fits(bounds: list[float] | None, width: float, margin: float = 0.0) -> bool:
    return bounds is not None and bounds[0] >= margin and bounds[2] <= width - margin


def max_repeat(seq: dict, clip: dict, layer: dict, char: str = "가", margin: float = 0.0, limit: int = 60) -> int:
    """How many of ``char`` fit on one line in this layer's style and place."""
    width = seq["settings"]["width"]
    lo, hi = 0, limit
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(layer_bounds(seq, clip, dict(layer, text=char * mid, runs=None)), width, margin):
            lo = mid
        else:
            hi = mid - 1
    return lo
