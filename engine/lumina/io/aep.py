"""Read the text layers of an After Effects Motion Graphics Template.

A Premiere ``.aegraphic`` / ``.mogrt`` built in After Effects is a zip holding
an ``.aep`` project: big-endian RIFX chunks. Each layer is a ``LIST Layr``
with its name (``Utf8``), property groups (``tdmn`` match names followed by
``cdat`` values, big-endian doubles) and, for text layers, a ``btdk`` text
document: AE's "CoS" dictionary syntax (``<< /0 ... >>``, ``[ ]``, ``(...)``
strings, UTF-16 when they start with FE FF) with numeric keys.

Only what Premiere shows for a template's text fields is extracted: the text,
font, size, fill/stroke, tracking, leading, justification and the layer
transform (anchor/position/scale/rotation/opacity) in comp pixels, plus a
Drop Shadow effect. Keys were mapped from AE 2025 files; this is
undocumented, and anything unrecognised falls back to defaults. The file is
only read.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

# ---------------------------------------------------------------- RIFX


def _chunks(d: bytes, off: int, end: int):
    while off + 8 <= end:
        tag = d[off:off + 4]
        size = struct.unpack(">I", d[off + 4:off + 8])[0]
        body = off + 8
        if tag == b"LIST":
            yield "LIST:" + d[body:body + 4].decode("latin1"), body + 4, min(body + size, end)
        else:
            yield tag.decode("latin1"), body, min(body + size, end)
        off = body + size + (size & 1)


def _walk(d: bytes, off: int, end: int):
    for tag, b, e in _chunks(d, off, end):
        yield tag, b, e
        if tag.startswith("LIST:"):
            yield from _walk(d, b, e)


# ---------------------------------------------------------------- CoS text document

class _CoS:
    def __init__(self, data: bytes):
        self.d, self.i = data, 0

    def ws(self) -> None:
        d = self.d
        while self.i < len(d) and d[self.i] in b" \t\r\n\x00":
            self.i += 1

    def value(self):
        self.ws()
        d, i = self.d, self.i
        if d.startswith(b"<<", i):
            self.i += 2
            out = {}
            while True:
                self.ws()
                if self.d.startswith(b">>", self.i):
                    self.i += 2
                    return out
                key = self.value()
                out[key] = self.value()
        if d[i:i + 1] == b"[":
            self.i += 1
            out = []
            while True:
                self.ws()
                if self.d[self.i:self.i + 1] == b"]":
                    self.i += 1
                    return out
                out.append(self.value())
        if d[i:i + 1] == b"(":
            return self.string()
        if d[i:i + 1] == b"/":
            j = i + 1
            while j < len(d) and d[j] not in b" \t\r\n/[]<>()":
                j += 1
            self.i = j
            name = d[i + 1:j].decode("latin1")
            return int(name) if name.isdigit() else "/" + name
        j = i
        while j < len(d) and d[j] not in b" \t\r\n/[]<>()":
            j += 1
        self.i = j
        tok = d[i:j].decode("latin1")
        if tok == "true":
            return True
        if tok == "false":
            return False
        try:
            return float(tok) if any(c in tok for c in ".eE") else int(tok)
        except ValueError:
            return tok

    def string(self) -> str:
        d = self.d
        self.i += 1
        buf = bytearray()
        while self.i < len(d):
            c = d[self.i]
            if c == 0x5C:  # backslash escapes the next byte
                buf.append(d[self.i + 1])
                self.i += 2
                continue
            if c == 0x29:  # ')'
                self.i += 1
                break
            buf.append(c)
            self.i += 1
        if buf[:2] == b"\xfe\xff":
            return bytes(buf[2:]).decode("utf-16-be", "replace")
        return bytes(buf).decode("latin1")


def parse_text_document(data: bytes) -> dict:
    p = _CoS(data)
    out = {}
    while True:
        p.ws()
        if p.i >= len(data):
            return out
        key = p.value()
        if p.i >= len(data):
            return out
        out[key] = p.value()


def _paint(v) -> str | None:
    """<< /99 /SimplePaint /0 << /0 1 /1 [ a r g b ] >> >> -> #rrggbb (None if transparent)."""
    try:
        a, r, g, b = v[0][1]
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if a <= 0:
        return None
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in (r, g, b))


JUSTIFY = {0: "left", 1: "right", 2: "center"}


def _text_style(doc: dict) -> dict:
    """Text, font and the first run's style from a parsed text document."""
    out: dict = {}
    try:
        fonts = [f[0][0][0] for f in doc[0][1][0]]
    except (KeyError, TypeError, IndexError):
        fonts = []
    # the text and its run lists: /1 /1 [ << /0 << /0 text /5 paragraph runs /6 style runs >> ... ]
    try:
        entry = doc[1][1][0][0]
        out["text"] = entry[0]
        para = entry[5][0][0][0][0][5]
        style = entry[6][0][0][0][0][6]
    except (KeyError, TypeError, IndexError):
        return out
    out["align"] = JUSTIFY.get(para.get(0), "left")
    idx = style.get(0, 0)
    if isinstance(idx, int) and idx < len(fonts):
        out["font_family"] = fonts[idx]
    if isinstance(style.get(1), (int, float)):
        out["font_size"] = float(style[1])
    if isinstance(style.get(5), (int, float)):
        out["leading"] = float(style[5])
    if isinstance(style.get(8), (int, float)):
        out["tracking"] = float(style[8])
    fill = _paint(style.get(53)) if style.get(56, True) else None
    stroke = _paint(style.get(54)) if style.get(57, False) else None
    out["color"] = fill or "#ffffff"
    width = float(style.get(63, 0) or 0)
    # AE strokes are centered on the outline; Lumina's are outside strokes
    out["stroke_color"], out["stroke_width"] = (stroke, width / 2) if stroke and width > 0 else (None, 0.0)
    out["bold"] = bool(style.get(2, False))
    out["italic"] = bool(style.get(3, False))
    return out


# ---------------------------------------------------------------- layers

def _doubles(d: bytes, b: int, e: int, n: int) -> list[float]:
    n = min(n, (e - b) // 8)
    return list(struct.unpack(">%dd" % n, d[b:b + 8 * n]))


def read_aep_text_layers(data: bytes) -> dict:
    """-> {"width", "height", "layers": [{name, text, ..., anchor, position, scale, rotation, opacity}]}
    in comp pixels, top-most layer first (AE order)."""
    if data[:4] != b"RIFX":
        raise ValueError("not an After Effects project")
    width = height = None
    layers = []
    for tag, b, e in _walk(data, 12, len(data)):
        if tag == "cdta" and width is None and e - b >= 144:
            width, height = struct.unpack(">HH", data[b + 140:b + 144])
        if tag != "LIST:Layr":
            continue
        layer: dict = {"name": None, "anchor": [0.0, 0.0], "position": None, "scale": 100.0, "rotation": 0.0, "opacity": 100.0}
        group = None
        shadow: dict = {}
        for t2, b2, e2 in _walk(data, b, e):
            if t2 == "Utf8" and layer["name"] is None:
                layer["name"] = data[b2:e2].decode("utf-8", "replace")
            elif t2 == "tdmn":
                group = data[b2:b2 + 40].split(b"\x00")[0].decode("latin1")
            elif t2 == "LIST:btdk":
                try:
                    layer.update(_text_style(parse_text_document(data[b2:e2])))
                except (IndexError, ValueError):
                    pass
            elif t2 == "cdat" and group:
                if group == "ADBE Anchor Point":
                    layer["anchor"] = _doubles(data, b2, e2, 2)
                elif group == "ADBE Position":
                    layer["position"] = _doubles(data, b2, e2, 2)
                elif group == "ADBE Scale":
                    layer["scale"] = _doubles(data, b2, e2, 1)[0] * 100
                elif group == "ADBE Rotate Z":
                    layer["rotation"] = _doubles(data, b2, e2, 1)[0]
                elif group == "ADBE Opacity":
                    layer["opacity"] = _doubles(data, b2, e2, 1)[0]
                elif group.startswith("ADBE Drop Shadow-"):
                    shadow[group[-4:]] = _doubles(data, b2, e2, 4)
                group = None
        if "text" not in layer:
            continue
        if shadow:
            # Drop Shadow params: 0001 color (ARGB 0-1 doubles), 0002 opacity (0-255),
            # 0003 direction, 0004 distance, 0005 softness; absent = AE defaults
            color = shadow.get("0001")
            layer["shadow"] = {
                "color": "#%02x%02x%02x" % tuple(round(c * 255) for c in color[1:4]) if color and len(color) >= 4 else "#000000",
                "opacity": shadow["0002"][0] / 255 * 100 if "0002" in shadow else 50.0,
                "angle": shadow["0003"][0] if "0003" in shadow else 135.0,
                "distance": shadow["0004"][0] if "0004" in shadow else 5.0,
                "blur": shadow["0005"][0] if "0005" in shadow else 0.0,
            }
        layer["text"] = layer["text"].rstrip("\r\n")
        layers.append(layer)
    return {"width": width, "height": height, "layers": layers}


def read_mogrt(path: str) -> dict | None:
    """Text layers of an AE-made template (.aegraphic / .mogrt zip), or None."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".aep")]
            if not names:
                inner = [n for n in z.namelist() if n.lower().endswith(".aegraphic")]
                if not inner:
                    return None
                with zipfile.ZipFile(io.BytesIO(z.read(inner[0]))) as z2:
                    names2 = [n for n in z2.namelist() if n.lower().endswith(".aep")]
                    return read_aep_text_layers(z2.read(names2[0])) if names2 else None
            return read_aep_text_layers(z.read(names[0]))
    except (zipfile.BadZipFile, OSError, ValueError, struct.error):
        return None
