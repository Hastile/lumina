"""Text layers: Premiere text FlatBuffers, AE template text documents, and the
shared layout rules (render/text.py, mirrored by the app's GraphicView)."""

import struct

import numpy as np
import pytest

from lumina.io import aep
from lumina.io.prproj import _mogrt_layers, _text_doc
from lumina.render.text import layout, render_text_layer


class FB:
    """Minimal FlatBuffers writer: tables are laid out front to back so every
    uoffset points forward, as the reader expects."""

    def __init__(self):
        self.buf = bytearray()

    def here(self) -> int:
        return len(self.buf)

    def table(self, fields: dict[int, tuple[str, object]]) -> int:
        """fields: id -> (kind, value) with kind in u32/f32/u8/ref; returns the table offset.
        'ref' values are patched later via the returned slot list."""
        n = max(fields) + 1 if fields else 0
        layout_, size = {}, 4
        for fid, (kind, _) in sorted(fields.items()):
            w = 1 if kind == "u8" else 4
            size += (-size) % w
            layout_[fid] = size
            size += w
        size += (-size) % 4
        vt = self.here()
        self.buf += struct.pack("<HH", 4 + 2 * n, size) + b"".join(struct.pack("<H", layout_.get(i, 0)) for i in range(n))
        self.buf += b"\0" * ((-len(self.buf)) % 4)
        t = self.here()
        self.buf += struct.pack("<i", t - vt) + b"\0" * (size - 4)
        self.slots = {}
        for fid, (kind, v) in fields.items():
            o = t + layout_[fid]
            if kind == "u32":
                struct.pack_into("<I", self.buf, o, v)
            elif kind == "f32":
                struct.pack_into("<f", self.buf, o, v)
            elif kind == "u8":
                self.buf[o] = v
            else:
                self.slots[fid] = o
        return t

    def point(self, slot: int, target: int) -> None:
        struct.pack_into("<I", self.buf, slot, target - slot)

    def string(self, s: str) -> int:
        b = s.encode("utf-8")
        at = self.here()
        self.buf += struct.pack("<I", len(b)) + b + b"\0" * ((-len(b)) % 4 or 4)
        return at

    def vector(self, targets: list[int]) -> int:
        at = self.here()
        self.buf += struct.pack("<I", len(targets))
        for i, t in enumerate(targets):
            slot = self.here()
            self.buf += struct.pack("<I", t - slot)
        return at

    def vector_slots(self, n: int) -> tuple[int, list[int]]:
        at = self.here()
        self.buf += struct.pack("<I", n)
        slots = []
        for _ in range(n):
            slots.append(self.here())
            self.buf += b"\0\0\0\0"
        return at, slots


def premiere_text_blob(runs, box=None, align=None, valign=None, fonts=("Pretendard-Bold",)) -> bytes:
    """A text document laid out like Premiere's (see prproj._text_doc)."""
    fb = FB()
    fb.buf += b"\0\0\0\0"  # root uoffset, patched below
    root = fb.table({0: ("ref", None)})
    root_slot = fb.slots[0]
    doc_fields: dict = {0: ("ref", None), 1: ("ref", None)}
    if box:
        doc_fields[2], doc_fields[3] = ("f32", box[0]), ("f32", box[1])
    if align is not None:
        doc_fields[4] = ("u32", align)
    if valign is not None:
        doc_fields[5] = ("u32", valign)
    doc = fb.table(doc_fields)
    doc_slots = dict(fb.slots)
    fb.point(root_slot, doc)
    fonts_at, font_slots = fb.vector_slots(len(fonts))
    fb.point(doc_slots[1], fonts_at)
    for slot, name in zip(font_slots, fonts):
        fb.point(slot, fb.string(name))
    runs_at, run_slots = fb.vector_slots(len(runs))
    fb.point(doc_slots[0], runs_at)
    for slot, (text, style) in zip(run_slots, runs):
        r = fb.table({0: ("ref", None), 1: ("ref", None)})
        rs = dict(fb.slots)
        fb.point(slot, r)
        fb.point(rs[0], fb.string(text))
        sf = {k: v for k, v in style.items() if k not in ("fill", "stroke")}
        if "fill" in style:
            sf[2] = ("ref", None)
        if "stroke" in style:
            sf[4] = ("ref", None)
        st = fb.table(sf)
        ss = dict(fb.slots)
        fb.point(rs[1], st)
        for key, fid in (("fill", 2), ("stroke", 4)):
            if key in style:
                c = fb.table({0: ("u8", style[key][0]), 1: ("u8", style[key][1]), 2: ("u8", style[key][2])})
                fb.point(ss[fid], c)
    struct.pack_into("<I", fb.buf, 0, root)
    return struct.pack("<II", len(fb.buf), 0) + struct.pack("<I", 0x11223344) + bytes(fb.buf)


def test_premiere_text_document_fields():
    blob = premiere_text_blob(
        [("첫 줄\r", {1: ("f32", 80.0), "fill": (26, 153, 225), 6: ("f32", 3.0), 8: ("f32", -20.0)}),
         ("둘째 줄", {0: ("u32", 1), "stroke": (12, 12, 12), 6: ("f32", 6.0)})],
        box=(810.0, 576.0), align=2, valign=1, fonts=("Pretendard-Bold", "Pretendard-Black"))
    doc = _text_doc(blob)
    assert doc["box"] == [810.0, 576.0] and doc["align"] == "center" and doc["vertical_align"] == "center"
    a, b = doc["runs"]
    assert (a["text"], a["font_family"], a["font_size"], a["color"], a["tracking"]) == ("첫 줄\r", "Pretendard-Bold", 80.0, "#1a99e1", -20.0)
    assert a["stroke_color"] == "#ffffff" and a["stroke_width"] == 3.0, "stroke color defaults to white"
    assert (b["font_family"], b["font_size"], b["color"], b["stroke_color"]) == ("Pretendard-Black", 100.0, "#ffffff", "#0c0c0c"), \
        "size defaults to 100, fill to white"
    point = _text_doc(premiere_text_blob([("x", {})]))
    assert point["box"] is None and point["align"] == "left" and point["vertical_align"] == "top"


def test_ae_text_document_and_template_layout():
    doc = aep.parse_text_document(
        b" /0 << /1 << /0 [ << /0 << /99 /CoolTypeFont /0 << /0 (\xfe\xff\x00A\x00B) >> >> >> ] >> >>"
        b" /1 << /1 [ << /0 << /0 (\xfe\xff\xba\x54\xc7\x78\x00\r) /5 << /0 [ << /0 << /0 << /5 << /0 2 >> >> >> >> ] >>"
        b" /6 << /0 [ << /0 << /0 << /6 << /0 0 /1 115.0 /8 -18 /53 << /99 /SimplePaint /0 << /0 1 /1 [ 1.0 .22745 .43529 .70196 ] >> >>"
        b" /54 << /99 /SimplePaint /0 << /0 1 /1 [ 1.0 1.0 1.0 1.0 ] >> >> /56 true /57 true /63 12.0 >> >> >> >> ] >> >> >> ] >>")
    st = aep._text_style(doc)
    assert st["text"] == "메인\r" and st["font_family"] == "AB" and st["align"] == "center"
    assert (st["font_size"], st["tracking"], st["color"]) == (115.0, -18.0, "#3a6fb3")
    assert (st["stroke_color"], st["stroke_width"]) == ("#ffffff", 6.0), "AE's centered 12 px stroke -> 6 px outside"
    ae = {"width": 1080, "height": 1920, "layers": [
        {"name": "Main", "text": "기본", "anchor": [0.0, -40.0], "position": [540.0, 300.0], "scale": 100.0, **{k: v for k, v in st.items() if k != "text"}},
    ]}
    (layer,) = _mogrt_layers(ae, {"Main": {"textEditValue": "바뀐 제목", "fontSizeEditValue": [100]}}, 1080, 1920)
    assert layer["text"] == "바뀐 제목" and layer["font_size"] == 100.0 and layer["align"] == "center"
    lines, _, bh = layout(layer)
    first_baseline = layer["position"][1] - bh / 2 + lines[0].baseline
    assert first_baseline == pytest.approx(340.0), "AE puts the first baseline at position - anchor"


def _alpha_bbox(img):
    a = np.asarray(img.getchannel("A")) > 128
    ys, xs = np.nonzero(a)
    return xs.min(), xs.max(), ys.min(), ys.max()


def base_layer(**kw):
    layer = {"type": "text", "text": "가나다 ABC", "font_family": "Pretendard", "font_size": 80.0, "bold": False,
             "italic": False, "color": "#ffffff", "stroke_color": None, "stroke_width": 0.0, "background": None,
             "align": "center", "position": [0, 0], "box": None, "vertical_align": "top", "tracking": 0.0,
             "shadow": None, "runs": None}
    layer.update(kw)
    return layer


def test_area_text_aligns_inside_its_box():
    img, ax, ay = render_text_layer(base_layer(box=[800.0, 400.0], vertical_align="center"))
    x0, x1, y0, y1 = _alpha_bbox(img)
    # the anchor is the box's top-left; the ink is centered in the 800x400 box
    assert abs((x0 + x1) / 2 - (ax + 400)) < 6 and abs((y0 + y1) / 2 - (ay + 200)) < 12
    img, ax, ay = render_text_layer(base_layer(box=[800.0, 400.0], align="right", vertical_align="bottom"))
    x0, x1, y0, y1 = _alpha_bbox(img)
    assert ax + 800 - 8 < x1 <= ax + 800 and ay + 400 - 40 < y1 <= ay + 400


def test_tracking_runs_stroke_and_shadow():
    plain = layout(base_layer())[1]
    tight = layout(base_layer(tracking=-50.0))[1]
    assert tight == pytest.approx(plain - 0.05 * 80 * len("가나다 ABC"), abs=1.0), "tracking is 1/1000 em per character"
    layer = base_layer(text="AB\nCD", runs=[{"text": "AB\r", "color": "#ff0000"}, {"text": "CD", "font_size": 160.0, "color": "#0000ff"}])
    lines, _, bh = layout(layer)
    assert [round(ln.height) for ln in lines] == [96, 192] and bh == pytest.approx(288)
    img, _, _ = render_text_layer(layer)
    a = np.asarray(img)
    red = (a[..., 0] > 200) & (a[..., 2] < 60) & (a[..., 3] > 200)
    blue = (a[..., 2] > 200) & (a[..., 0] < 60) & (a[..., 3] > 200)
    assert red.any() and blue.any() and np.nonzero(red)[0].max() < np.nonzero(blue)[0].min(), "runs keep their own styles, line by line"
    shadowed, _, _ = render_text_layer(base_layer(shadow={"color": "#000000", "opacity": 100, "angle": 135, "distance": 10, "blur": 0}))
    sx0, sx1, sy0, sy1 = _alpha_bbox(shadowed)
    px0, px1, py0, py1 = _alpha_bbox(render_text_layer(base_layer(shadow={"color": "#000000", "opacity": 0, "angle": 135, "distance": 10, "blur": 0}))[0])
    assert sx1 - px1 == pytest.approx(7, abs=1.5) and sy1 - py1 == pytest.approx(7, abs=1.5), "135 deg = down-right"
