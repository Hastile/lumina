"""Text layer layout and rasterisation.

The same rules drive the Program monitor (``app/.../panels/GraphicView.tsx``),
so preview and export agree:

* A layer is a list of runs (its ``runs``, or one run of ``text``); runs split
  into lines at ``\\n`` / ``\\r``. Each run may override the layer's font,
  size, colors, stroke and tracking.
* Line ``i`` is ``1.2 x`` its largest font size tall; its segments share a
  baseline at ``max_k((L - (asc_k + desc_k)) / 2 + asc_k)`` from the line top
  (what CSS flex ``align-items: baseline`` gives with ``line-height: L``).
* Tracking is in 1/1000 em, added after every character (CSS letter-spacing).
* Point text (``box`` null): ``position`` is the alignment point (left edge /
  center / right edge) at the block's vertical center.
* Area text (``box`` [w, h]): ``position`` is the box's top-left; lines align
  horizontally per ``align`` and the block vertically per ``vertical_align``.
* Strokes are outside strokes of ``stroke_width`` px; the shadow is the text's
  silhouette offset by ``distance`` at ``angle`` (Premiere: 135 = down-right)
  and Gaussian-blurred with sigma ``blur / 2`` (CSS text-shadow).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from . import fonts

LINE_HEIGHT = 1.2
_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}
RUN_KEYS = ("font_family", "font_size", "bold", "italic", "color", "stroke_color", "stroke_width", "tracking")


def color(value: str | None, default=(255, 255, 255, 255)) -> tuple:
    if not value:
        return default
    try:
        c = ImageColor.getrgb(value)
    except ValueError:
        return default
    return c if len(c) == 4 else (*c, 255)


def load_font(family: str, size: float, bold: bool) -> tuple[ImageFont.FreeTypeFont, bool, bool]:
    """-> (font, exact, has_bold_face)"""
    face, exact = fonts.find(family, bold)
    key = (face["path"], face["index"], round(size * 4)) if face else ("default", 0, round(size * 4))
    font = _font_cache.get(key)
    if font is None:
        font = ImageFont.truetype(face["path"], max(1, round(size)), index=face["index"]) if face else ImageFont.load_default(max(1, round(size)))
        if len(_font_cache) > 64:
            _font_cache.clear()
        _font_cache[key] = font
    is_bold = bool(face and "bold" in fonts.norm(face["style"]) + fonts.norm(face["ps"]) or face and "black" in fonts.norm(face["ps"]))
    return font, exact, is_bold


def resolved_runs(layer: dict) -> list[dict]:
    base = {k: layer.get(k) for k in RUN_KEYS}
    base["tracking"] = base["tracking"] or 0.0
    if layer.get("runs"):
        return [{**base, **{k: v for k, v in r.items() if v is not None and k != "text"}, "text": r.get("text", "")} for r in layer["runs"]]
    return [{**base, "text": layer.get("text", "")}]


@dataclass
class Seg:
    run: dict
    text: str
    font: ImageFont.FreeTypeFont
    size: float
    faux_bold: bool
    width: float
    x: float = 0.0


@dataclass
class Line:
    segs: list[Seg]
    height: float
    baseline: float  # from the line top
    width: float
    top: float = 0.0
    x: float = 0.0  # left edge, relative to the block


def _seg_width(font: ImageFont.FreeTypeFont, text: str, track: float) -> float:
    if not track:
        return font.getlength(text)
    return sum(font.getlength(ch) + track for ch in text)


def layout(layer: dict) -> tuple[list[Line], float, float]:
    """-> (lines, block width, block height); lines carry their own x/top."""
    lines: list[list[tuple[dict, str]]] = [[]]
    for run in resolved_runs(layer):
        parts = run["text"].replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for i, part in enumerate(parts):
            if i:
                lines.append([])
            if part:
                lines[-1].append((run, part))
    out: list[Line] = []
    for items in lines:
        segs = []
        for run, text in items:
            size = max(1.0, float(run["font_size"]))
            font, _, has_bold = load_font(run["font_family"], size, bool(run.get("bold")))
            track = float(run.get("tracking") or 0) / 1000 * size
            segs.append(Seg(run, text, font, size, bool(run.get("bold")) and not has_bold, _seg_width(font, text, track)))
        sizes = [s.size for s in segs] or [max(1.0, float(layer["font_size"]))]
        height = LINE_HEIGHT * max(sizes)
        if segs:
            baseline = max((height - sum(s.font.getmetrics())) / 2 + s.font.getmetrics()[0] for s in segs)
        else:
            font, _, _ = load_font(layer["font_family"], sizes[0], bool(layer.get("bold")))
            asc, desc = font.getmetrics()
            baseline = (height - asc - desc) / 2 + asc
        x = 0.0
        for s in segs:
            s.x = x
            x += s.width
        out.append(Line(segs, height, baseline, x))
    bw = max((ln.width for ln in out), default=0.0)
    top = 0.0
    for ln in out:
        ln.top = top
        top += ln.height
        ln.x = {"left": 0.0, "right": bw - ln.width}.get(layer.get("align", "center"), (bw - ln.width) / 2)
    return out, bw, top


def _draw_lines(img: Image.Image, lines: list[Line], ox: float, oy: float, layer: dict, part: str) -> None:
    """part: 'stroke' draws outlines (and fills under them), 'fill' fills only."""
    draw = ImageDraw.Draw(img)
    for ln in lines:
        y = oy + ln.top + ln.baseline
        for s in ln.segs:
            r = s.run
            stroke = round(float(r.get("stroke_width") or 0)) if r.get("stroke_color") else 0
            fill = color(r.get("color"))
            kw: dict = {}
            if part == "stroke":
                if not stroke:
                    continue
                kw = {"stroke_width": stroke, "stroke_fill": color(r.get("stroke_color"), (0, 0, 0, 255))}
            track = float(r.get("tracking") or 0) / 1000 * s.size
            x = ox + ln.x + s.x
            chunks = [s.text] if not track else list(s.text)
            for ch in chunks:
                if s.faux_bold and part == "fill":
                    draw.text((x, y), ch, font=s.font, fill=fill, anchor="ls", stroke_width=max(1, round(s.size / 36)), stroke_fill=fill)
                draw.text((x, y), ch, font=s.font, fill=fill, anchor="ls", **kw)
                x += s.font.getlength(ch) + track if track else 0


def render_text_layer(layer: dict) -> tuple[Image.Image, float, float]:
    """Rasterise one text layer (unrotated, unscaled) -> (image, anchor_x,
    anchor_y): the point inside the image that lands on ``position``."""
    lines, bw, bh = layout(layer)
    box = layer.get("box")
    if box:
        bx, by = float(box[0]), float(box[1])
        x0 = {"left": 0.0, "right": bx - bw}.get(layer.get("align", "center"), (bx - bw) / 2)
        y0 = {"top": 0.0, "bottom": by - bh}.get(layer.get("vertical_align", "top"), (by - bh) / 2)
        anchor = (0.0, 0.0)  # box top-left, in block-relative coordinates
    else:
        x0 = y0 = 0.0
        anchor = ({"left": 0.0, "right": bw}.get(layer.get("align", "center"), bw / 2), bh / 2)
    runs = [s.run for ln in lines for s in ln.segs]
    max_size = max((s.size for ln in lines for s in ln.segs), default=float(layer["font_size"]))
    max_stroke = max((float(r.get("stroke_width") or 0) for r in runs if r.get("stroke_color")), default=0.0)
    shadow = layer.get("shadow")
    sd = (float(shadow.get("distance", 0)) + 2 * float(shadow.get("blur", 0))) if shadow else 0.0
    m = math.ceil(max_stroke + max_size * 0.3 + sd) + 2
    # canvas spans the block and (for area text) the box, plus margins
    left, top = min(0.0, x0), min(0.0, y0)
    right, bottom = max(bx if box else 0.0, x0 + bw), max(by if box else 0.0, y0 + bh)
    w, h = math.ceil(right - left + 2 * m), math.ceil(bottom - top + 2 * m)
    ox, oy = m - left + x0, m - top + y0  # block origin in the image
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    if layer.get("background"):
        pad_x, pad_y = 0.3 * max_size, 0.08 * max_size
        ImageDraw.Draw(img).rectangle((ox - pad_x, oy - pad_y, ox + bw + pad_x, oy + bh + pad_y), fill=color(layer["background"], (0, 0, 0, 255)))
    text = Image.new("RGBA", img.size, (0, 0, 0, 0))
    _draw_lines(text, lines, ox, oy, layer, "stroke")
    _draw_lines(text, lines, ox, oy, layer, "fill")
    if layer.get("italic"):
        shear = 0.2  # synthetic oblique, like the browser does without an italic face
        text = text.transform(text.size, Image.Transform.AFFINE, (1, shear, -shear * (oy + bh / 2), 0, 1, 0), resample=Image.Resampling.BICUBIC)
    if shadow:
        a = math.radians(float(shadow.get("angle", 135)))
        d = float(shadow.get("distance", 0))
        dx, dy = d * math.sin(a), -d * math.cos(a)
        sil = Image.new("L", img.size, 0)
        sil.paste(text.getchannel("A"), (round(dx), round(dy)))
        blur = float(shadow.get("blur", 0))
        if blur > 0:
            sil = sil.filter(ImageFilter.GaussianBlur(blur / 2))
        sc = color(shadow.get("color"), (0, 0, 0, 255))
        op = max(0.0, min(1.0, float(shadow.get("opacity", 100)) / 100))
        sil = sil.point(lambda v: round(v * op * sc[3] / 255))
        layer_shadow = Image.new("RGBA", img.size, sc[:3] + (0,))
        layer_shadow.putalpha(sil)
        img.alpha_composite(layer_shadow)
    img.alpha_composite(text)
    return img, m - left + anchor[0], m - top + anchor[1]


def baseline_offset(layer: dict) -> float:
    """Distance from the block's vertical center up to the first baseline is
    ``bh / 2 - offset``; returns the first line's baseline below the block top."""
    lines, _, _ = layout(layer)
    return lines[0].baseline if lines else 0.0


def block_height(layer: dict) -> float:
    return layout(layer)[2]
