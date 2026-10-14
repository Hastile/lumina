"""Frame compositor for export and frame grabs.

Mirrors the Program monitor (panels/Monitors.tsx + util/motion.ts): each
enabled clip on a visible video track, bottom (V1) to top, is drawn at its
source's natural pixel size and transformed by Motion

    dst = position + R(rotation) * S(scale) * (src - anchor)

with crop (percent of the source edges) and opacity, all keyframe-evaluated
by ``lumina.motion`` (the twin of the GUI's evaluator). Graphic clips are
rasterised once per clip at sequence size and then treated like a still.

Video sources are decoded by one ffmpeg process per clip that streams RGBA
frames already resampled to the sequence frame rate (and pre-scaled when the
clip is only ever shown smaller than its native size).
"""

from __future__ import annotations

import functools
import json
import math
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .. import timebase
from ..adb.quiet_process import NO_WINDOW
from ..media.probe import ffmpeg_bin, ffprobe_bin
from ..motion import transform_at
from . import fonts

TPS = timebase.TICKS_PER_SECOND


class RenderError(Exception):
    pass


# ---------------------------------------------------------------- geometry


def place(canvas: Image.Image, img: Image.Image, origin: tuple[float, float], tr: dict, prescale: tuple[float, float] = (1.0, 1.0)) -> None:
    """Composite ``img`` onto ``canvas``.

    ``img`` holds the source's pixels starting at source-pixel ``origin``,
    sampled at ``prescale`` decoded pixels per source pixel. ``tr`` is a
    resolved transform (``lumina.motion.transform_at``)."""
    fx, fy = prescale
    sx, sy = tr["scale_x"] / 100.0, tr["scale_y"] / 100.0
    opacity = max(0.0, min(1.0, tr["opacity"] / 100.0))
    if sx <= 0 or sy <= 0 or opacity <= 0:
        return
    # crop rect in source pixels, intersected with the provided pixels
    w, h = tr["src_w"], tr["src_h"]
    cl = max(tr["crop_left"], 0) / 100 * w
    ct = max(tr["crop_top"], 0) / 100 * h
    cr = w - max(tr["crop_right"], 0) / 100 * w
    cb = h - max(tr["crop_bottom"], 0) / 100 * h
    ox, oy = origin
    x0, y0 = max(cl, ox), max(ct, oy)
    x1, y1 = min(cr, ox + img.width / fx), min(cb, oy + img.height / fy)
    if x1 <= x0 or y1 <= y0:
        return
    # same region in img pixels
    ix0, iy0 = round((x0 - ox) * fx), round((y0 - oy) * fy)
    ix1, iy1 = round((x1 - ox) * fx), round((y1 - oy) * fy)
    if (ix0, iy0, ix1, iy1) != (0, 0, img.width, img.height):
        img = img.crop((ix0, iy0, ix1, iy1))
    if img.width <= 0 or img.height <= 0:
        return
    sox, soy = ox + ix0 / fx, oy + iy0 / fy  # source pixel of img (0, 0)
    (px, py), (ax, ay) = tr["position"], tr["anchor"]
    rad = math.radians(tr["rotation"])
    cos, sin = math.cos(rad), math.sin(rad)
    # img pixel -> sequence pixel: dst = P + R S ((sox, soy) + q / f - A)
    ex, ey = sx / fx, sy / fy  # effective scale per img pixel
    if opacity < 1 or img.mode != "RGBA":
        img = img.convert("RGBA") if img.mode != "RGBA" else img.copy()
        if opacity < 1:
            img.putalpha(img.getchannel("A").point(lambda v: round(v * opacity)))
    tx = px + cos * sx * (sox - ax) - sin * sy * (soy - ay)
    ty = py + sin * sx * (sox - ax) + cos * sy * (soy - ay)
    if abs(rad) < 1e-9 and abs(ex - 1) * img.width < 1.0 and abs(ey - 1) * img.height < 1.0:
        # pure translation: snap to whole pixels (sub-pixel resampling would only blur)
        _blit(canvas, img, round(tx), round(ty))
        return
    # bounding box in sequence space
    corners = [(0, 0), (img.width, 0), (0, img.height), (img.width, img.height)]
    pts = [(tx + cos * ex * qx - sin * ey * qy, ty + sin * ex * qx + cos * ey * qy) for qx, qy in corners]
    bx0 = max(0, math.floor(min(p[0] for p in pts)))
    by0 = max(0, math.floor(min(p[1] for p in pts)))
    bx1 = min(canvas.width, math.ceil(max(p[0] for p in pts)))
    by1 = min(canvas.height, math.ceil(max(p[1] for p in pts)))
    if bx1 <= bx0 or by1 <= by0:
        return
    # inverse map (output pixel centre -> img coords): q = S^-1 R^-1 (dst - T)
    a, b = cos / ex, sin / ex
    d, e = -sin / ey, cos / ey
    c = -(a * tx + b * ty)
    f = -(d * tx + e * ty)
    c += a * bx0 + b * by0
    f += d * bx0 + e * by0
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    resample = Image.Resampling.BILINEAR if (ex > 0.6 and ey > 0.6) else Image.Resampling.BICUBIC
    out = img.transform((bx1 - bx0, by1 - by0), Image.Transform.AFFINE, (a, b, c, d, e, f), resample=resample)
    canvas.alpha_composite(out, (bx0, by0))


def _blit(canvas: Image.Image, img: Image.Image, x: int, y: int) -> None:
    # clip to canvas (alpha_composite rejects negative destinations)
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(img.width, canvas.width - x), min(img.height, canvas.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    if (sx0, sy0, sx1, sy1) != (0, 0, img.width, img.height):
        img = img.crop((sx0, sy0, sx1, sy1))
    if img.mode == "RGB" or img.getchannel("A").getextrema() == (255, 255):
        canvas.paste(img.convert("RGBA") if img.mode != "RGBA" else img, (x + sx0, y + sy0))
    else:
        canvas.alpha_composite(img, (x + sx0, y + sy0))


# ---------------------------------------------------------------- text graphics


def _color(value: str | None, default=(255, 255, 255, 255)):
    from PIL import ImageColor

    if not value:
        return default
    try:
        return ImageColor.getrgb(value)
    except ValueError:
        return default


_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}


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


def render_text_layer(layer: dict) -> tuple[Image.Image, float, float]:
    """Rasterise one text layer (unrotated, unscaled) -> (image, align_x, align_y)
    where (align_x, align_y) is the layer's alignment point inside the image.
    Matches GraphicView.textStyle: line-height 1.2, background padding
    0.08em/0.3em, outside stroke of ``stroke_width`` px."""
    size = max(1.0, float(layer["font_size"]))
    font, _, has_bold = load_font(layer["font_family"], size, layer["bold"])
    lines = layer["text"].split("\n") or [""]
    lh = 1.2 * size
    widths = [font.getlength(line) for line in lines]
    block_w = max(widths) if widths else 0
    block_h = lh * len(lines)
    pad_x, pad_y = (0.3 * size, 0.08 * size) if layer.get("background") else (0.0, 0.0)
    stroke = round(layer["stroke_width"]) if layer.get("stroke_color") and layer["stroke_width"] > 0 else 0
    faux_bold = layer["bold"] and not has_bold
    margin = stroke + math.ceil(size * 0.3) + 2  # room for ascenders/descenders overflowing the line box
    box_w, box_h = block_w + 2 * pad_x, block_h + 2 * pad_y
    img = Image.new("RGBA", (math.ceil(box_w + 2 * margin), math.ceil(box_h + 2 * margin)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if layer.get("background"):
        draw.rectangle((margin, margin, margin + box_w, margin + box_h), fill=_color(layer["background"], (0, 0, 0, 255)))
    fill = _color(layer["color"])
    anchor = {"left": "lm", "right": "rm"}.get(layer["align"], "mm")
    x = margin + pad_x + {"left": 0, "right": block_w}.get(layer["align"], block_w / 2)
    for i, line in enumerate(lines):
        y = margin + pad_y + lh * i + lh / 2
        kw: dict = {}
        if stroke:
            kw = {"stroke_width": stroke, "stroke_fill": _color(layer["stroke_color"], (0, 0, 0, 255))}
        if faux_bold:
            draw.text((x, y), line, font=font, fill=fill, anchor=anchor, stroke_width=max(1, round(size / 36)), stroke_fill=fill)
        draw.text((x, y), line, font=font, fill=fill, anchor=anchor, **kw)
    if layer.get("italic"):
        shear = 0.2  # synthetic oblique, like the browser does without an italic face
        img = img.transform(img.size, Image.Transform.AFFINE, (1, shear, -shear * img.height / 2, 0, 1, 0), resample=Image.Resampling.BICUBIC)
    align_x = margin + {"left": 0, "right": box_w}.get(layer["align"], box_w / 2)
    return img, align_x, margin + box_h / 2


def render_graphic(graphic: dict, width: int, height: int) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in graphic.get("layers", []):
        if not layer.get("text"):
            continue
        img, ax, ay = render_text_layer(layer)
        s = layer.get("scale", 100.0)
        place(canvas, img, (0, 0), {
            "src_w": img.width, "src_h": img.height, "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0,
            "position": layer["position"], "anchor": [ax, ay], "scale_x": s, "scale_y": s,
            "rotation": layer.get("rotation", 0.0), "opacity": layer.get("opacity", 100.0),
        })
    return canvas


# ---------------------------------------------------------------- sources


def media_path(media: dict) -> str | None:
    if Path(media["path"]).exists():
        return media["path"]
    if media.get("proxy_path") and Path(media["proxy_path"]).exists():
        return media["proxy_path"]
    return None


@functools.cache
def color_matrix(path: str, height: int) -> tuple[str, str]:
    """(swscale matrix, range) a source should be decoded with. Untagged HD is
    Rec.709, as Premiere and browsers assume (ffmpeg would default to 601)."""
    try:
        proc = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=color_space,color_range", "-of", "json", path],
                              capture_output=True, text=True, timeout=30, **NO_WINDOW)
        st = (json.loads(proc.stdout or "{}").get("streams") or [{}])[0]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        st = {}
    space = st.get("color_space") or ""
    matrix = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "bt601", "bt2020nc": "bt2020", "bt2020c": "bt2020",
              "fcc": "fcc", "smpte240m": "smpte240m"}.get(space) or ("bt709" if height >= 720 else "bt601")
    rng = "full" if st.get("color_range") in ("pc", "jpeg") else "tv"
    return matrix, rng


class VideoDecoder:
    """Streams RGBA frames of one clip at the sequence frame rate, starting at
    source time ``start_ticks``."""

    def __init__(self, path: str, start_ticks: int, speed: float, fps: tuple[int, int], size: tuple[int, int], max_frames: int, src_height: int = 1080):
        self.size = size
        self.frame_bytes = size[0] * size[1] * 4
        matrix, rng = color_matrix(path, src_height)
        # fps round=up keeps, for each output frame, the last source frame at or
        # before it -- what <video>.currentTime shows in the Program monitor
        vf = (f"setpts=(PTS-STARTPTS)/{speed!r},fps={fps[0]}/{fps[1]}:round=up,"
              f"scale={size[0]}:{size[1]}:flags=bicubic:in_color_matrix={matrix}:in_range={rng},format=rgba")
        cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{max(0, start_ticks) / TPS:.6f}", "-i", path,
               "-an", "-sn", "-dn", "-vf", vf, "-frames:v", str(max_frames), "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **NO_WINDOW)
        self.q: queue.Queue = queue.Queue(maxsize=6)
        self.last: Image.Image | None = None
        self.done = False
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        out = self.proc.stdout
        try:
            while True:
                buf = out.read(self.frame_bytes)
                if not buf or len(buf) < self.frame_bytes:
                    break
                self.q.put(buf)
        except (OSError, ValueError):
            pass
        finally:
            self.q.put(None)

    def next(self) -> Image.Image | None:
        if not self.done:
            buf = self.q.get()
            if buf is None:
                self.done = True
            else:
                self.last = Image.frombuffer("RGBA", self.size, buf, "raw", "RGBA", 0, 1)
        return self.last

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass
        try:
            while self.q.get_nowait() is not None:
                pass
        except queue.Empty:
            pass


def decode_still(path: str) -> Image.Image:
    try:
        with Image.open(path) as im:
            return im.convert("RGBA")
    except (OSError, ValueError):
        proc = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", path, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                              capture_output=True, timeout=120, **NO_WINDOW)
        if proc.returncode != 0 or not proc.stdout:
            raise RenderError(f"cannot decode image {path}")
        import io

        return Image.open(io.BytesIO(proc.stdout)).convert("RGBA")


def _max_scale(clip: dict, prop: str) -> float:
    vals = [kf["v"] for kf in clip.get("keyframes", {}).get(prop) or []]
    static = clip["motion"][prop]
    return max([static, *vals]) if vals else static


@dataclass
class ActiveClip:
    clip: dict
    media: dict | None
    src_w: float
    src_h: float
    prescale: tuple[float, float] = (1.0, 1.0)
    decoder: VideoDecoder | None = None
    still: Image.Image | None = None
    origin: tuple[float, float] = (0.0, 0.0)


class Compositor:
    """Renders consecutive frames of a sequence. Create one per render pass and
    call :meth:`frame` with increasing times (random access works, but costs a
    decoder restart per jump)."""

    def __init__(self, doc: dict, sequence_id: str, warn=None):
        self.doc = doc
        self.seq = doc["sequences"][sequence_id]
        st = self.seq["settings"]
        self.w, self.h = st["width"], st["height"]
        self.fps = (st["fps_num"], st["fps_den"])
        self.tpf = timebase.ticks_per_frame(*self.fps)
        self.active: dict[str, ActiveClip] = {}
        self.expected: dict[str, int] = {}  # clip id -> next frame time its decoder will produce
        self.warn = warn or (lambda msg: None)
        self._warned: set[str] = set()
        self._graphics: dict[str, tuple[str, Image.Image, tuple[float, float]]] = {}

    def _once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.warn(msg)

    def visible_clips(self, t: int) -> list[dict]:
        out = []
        for tid in self.seq["video_tracks"]:
            track = self.seq["tracks"][tid]
            if track["hidden"]:
                continue
            for c in track["clips"].values():
                if c["enabled"] and c["start"] <= t < c["end"]:
                    out.append(c)
                    break
        return out

    def _open(self, clip: dict, t: int) -> ActiveClip | None:
        if clip.get("graphic") is not None:
            key = json.dumps(clip["graphic"], sort_keys=True)
            cached = self._graphics.get(clip["id"])
            if not cached or cached[0] != key:
                img = render_graphic(clip["graphic"], self.w, self.h)
                box = img.getbbox()
                if box:
                    img = img.crop(box)
                    origin = (float(box[0]), float(box[1]))
                else:
                    img, origin = Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (0.0, 0.0)
                cached = (key, img, origin)
                self._graphics[clip["id"]] = cached
            return ActiveClip(clip, None, self.w, self.h, still=cached[1], origin=cached[2])
        media = self.doc["media"].get(clip["media_id"] or "")
        if not media or not media.get("video"):
            return None
        path = media_path(media)
        if path is None:
            self._once(media["id"], f"media offline, rendered black: {media['name']}")
            return None
        w, h = media["video"]["width"], media["video"]["height"]
        if media["kind"] == "image":
            return ActiveClip(clip, media, w, h, still=decode_still(path))
        # decode no larger than the clip is ever shown (saves 4K -> 1080p work)
        m = clip["motion"]
        sy = _max_scale(clip, "scale") / 100
        sx = sy if m["uniform"] else _max_scale(clip, "scale_width") / 100
        kfs = clip.get("keyframes", {})
        if not any(kfs.get(k) for k in ("scale", "scale_width", "rotation")) and m["rotation"] % 360 == 0:
            # static, unrotated: let ffmpeg scale to the exact displayed size so compositing is a paste
            limit = 2 * max(self.w, self.h) / max(w, h)
            sx, sy = min(limit, max(sx, 0.01)), min(limit, max(sy, 0.01))
        else:
            sx, sy = min(1.0, max(sx, 0.01)), min(1.0, max(sy, 0.01))
        dw, dh = max(2, round(w * sx / 2) * 2), max(2, round(h * sy / 2) * 2)
        frames_left = -(-(clip["end"] - t) // self.tpf)
        src = clip["source_in"] + round((t - clip["start"]) * clip["speed"])
        dec = VideoDecoder(path, src, clip["speed"], self.fps, (dw, dh), frames_left, h)
        return ActiveClip(clip, media, w, h, prescale=(dw / w, dh / h), decoder=dec)

    def frame(self, t: int) -> Image.Image:
        canvas = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 255))
        clips = self.visible_clips(t)
        live = {c["id"] for c in clips}
        for cid in [k for k in self.active if k not in live]:
            ac = self.active.pop(cid)
            if ac.decoder:
                ac.decoder.close()
        for clip in clips:
            ac = self.active.get(clip["id"])
            if ac and ac.decoder and self.expected.get(clip["id"]) != t:
                ac.decoder.close()  # random access: restart at the new time
                ac = None
            if ac is None:
                ac = self._open(clip, t)
                if ac is None:
                    continue
                self.active[clip["id"]] = ac
            if ac.decoder:
                img = ac.decoder.next()
                self.expected[clip["id"]] = t + self.tpf
                if img is None:
                    continue
            else:
                img = ac.still
            tr = transform_at(clip, t, self.seq, ac.media)
            tr["src_w"], tr["src_h"] = ac.src_w, ac.src_h
            place(canvas, img, ac.origin, tr, ac.prescale)
        return canvas

    def close(self) -> None:
        for ac in self.active.values():
            if ac.decoder:
                ac.decoder.close()
        self.active.clear()
