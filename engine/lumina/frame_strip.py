"""get_frame_strip: one image of rendered frames at several times.

Checking a laid cut plan used to take one get_frame_image per shot (100-175
per Short). A strip shows every shot's frame at once, numbered, at a size
that still shows who is on screen and whether the caption covers a face.
"""

from __future__ import annotations

import base64
import io
import os

from PIL import Image, ImageDraw

TOOL = (
    "Look at many moments of the edit in one image: rendered sequence frames at the given times "
    "(e.g. each shot's start + a few frames, after clip_place_shots), tiled in order and numbered. "
    "Stills only: no rhythm or motion verdict from them.",
    {"type": "object", "properties": {
        "sequence_id": {"type": "string"},
        "times": {"type": "array", "minItems": 1, "maxItems": 16, "items": {"type": ["string", "integer"]},
                  "description": "sequence times ('128f', '4.2s' or ticks)"},
        "height": {"type": "integer", "default": 480, "minimum": 160, "maximum": 960,
                   "description": "tile height in pixels"}},
     "required": ["times"]})
PER_ROW = 8


def tile(frames: list[Image.Image], labels: list[str], height: int) -> Image.Image:
    tiles = []
    for im in frames:
        w = max(1, round(im.width * height / im.height))
        tiles.append(im.convert("RGB").resize((w, height), Image.Resampling.LANCZOS))
    rows = [tiles[i:i + PER_ROW] for i in range(0, len(tiles), PER_ROW)]
    gap = 6
    width = max(sum(t.width for t in r) + gap * (len(r) - 1) for r in rows)
    sheet = Image.new("RGB", (width, len(rows) * height + gap * (len(rows) - 1)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    n = 0
    for ri, r in enumerate(rows):
        x, y = 0, ri * (height + gap)
        for t in r:
            sheet.paste(t, (x, y))
            text = f"{n + 1} {labels[n]}"
            draw.rectangle((x, y, x + 8 + 7 * len(text), y + 18), fill=(255, 214, 0))
            draw.text((x + 4, y + 3), text, fill=(0, 0, 0))
            x += t.width + gap
            n += 1
    return sheet


def strip(engine, args: dict) -> tuple[bytes, str]:
    """JPEG bytes and a legend '1=00:00:01:15 ...'."""
    frames, labels = [], []
    for t in args["times"]:
        r = engine.dispatch("export.frame", {"sequence_id": args.get("sequence_id"), "time": t, "max_size": 960})
        try:
            with Image.open(r["path"]) as im:
                frames.append(im.copy())
        finally:
            try:
                os.unlink(r["path"])
            except OSError:
                pass
        labels.append(r.get("timecode") or str(t))
    sheet = tile(frames, labels, int(args.get("height", 480)))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=82)
    return buf.getvalue(), " ".join(f"{i + 1}={lab}" for i, lab in enumerate(labels))


def result(engine, args: dict):
    from mcp import types

    data, legend = strip(engine, args)
    return types.CallToolResult(content=[
        types.ImageContent(type="image", data=base64.b64encode(data).decode("ascii"), mime_type="image/jpeg"),
        types.TextContent(type="text", text=legend)])
