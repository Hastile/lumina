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
import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .. import timebase
from ..adb.quiet_process import NO_WINDOW
from ..media.probe import ffmpeg_bin, ffprobe_bin
from .. import effects as fx_
from .. import shapes
from ..motion import transform_at
from .. import tracks as tracks_
from . import fonts
from . import text as text_

TPS = timebase.TICKS_PER_SECOND


class RenderError(Exception):
    pass


# ---------------------------------------------------------------- geometry


def warp(canvas: Image.Image, img: Image.Image, m: tuple, opacity: float = 1.0) -> None:
    """Composite ``img`` onto ``canvas`` through the affine ``m`` (img pixel -> canvas pixel)."""
    if opacity <= 0 or img.width <= 0 or img.height <= 0:
        return
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    if opacity < 1:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda v: round(v * opacity)))
    a, b, c, d, e, f = m
    if abs(b) < 1e-9 and abs(d) < 1e-9 and abs(a - 1) * img.width < 1.0 and abs(e - 1) * img.height < 1.0:
        # pure translation: snap to whole pixels (sub-pixel resampling would only blur)
        _blit(canvas, img, round(c), round(f))
        return
    pts = [fx_.apply(m, x, y) for x, y in ((0, 0), (img.width, 0), (0, img.height), (img.width, img.height))]
    bx0 = max(0, math.floor(min(p[0] for p in pts)))
    by0 = max(0, math.floor(min(p[1] for p in pts)))
    bx1 = min(canvas.width, math.ceil(max(p[0] for p in pts)))
    by1 = min(canvas.height, math.ceil(max(p[1] for p in pts)))
    if bx1 <= bx0 or by1 <= by0:
        return
    inv = fx_.invert(m)
    if inv is None:
        return
    inv = fx_.mul(inv, (1.0, 0.0, bx0, 0.0, 1.0, by0))
    scale = math.sqrt(abs(a * e - b * d))
    resample = Image.Resampling.BILINEAR if scale > 0.6 else Image.Resampling.BICUBIC
    out = img.transform((bx1 - bx0, by1 - by0), Image.Transform.AFFINE, inv, resample=resample)
    canvas.alpha_composite(out, (bx0, by0))


def _materialize(img: Image.Image, origin, prescale, pre: tuple, src_w: float, src_h: float) -> Image.Image:
    """Render ``img`` through a source-space affine into a source-frame-sized
    image (at ``prescale``) -- standard effects are bounded by the clip frame."""
    fx, fy = prescale
    out = Image.new("RGBA", (max(1, math.ceil(src_w * fx)), max(1, math.ceil(src_h * fy))), (0, 0, 0, 0))
    img2src = (1 / fx, 0.0, origin[0], 0.0, 1 / fy, origin[1])
    warp(out, img, fx_.mul((fx, 0.0, 0.0, 0.0, fy, 0.0), fx_.mul(pre, img2src)))
    return out


def place(canvas: Image.Image, img: Image.Image, origin: tuple[float, float], tr: dict, prescale: tuple[float, float] = (1.0, 1.0),
          pre: tuple = fx_.IDENTITY, pre_opacity: float = 1.0) -> None:
    """Composite one clip's frame onto ``canvas``.

    ``img`` holds the source's pixels starting at source-pixel ``origin``,
    sampled at ``prescale`` decoded pixels per source pixel. ``pre`` is the
    source->source affine of the standard effects (Transform), ``tr`` the
    resolved Motion (``lumina.motion.transform_at``) applied after it."""
    fx, fy = prescale
    sx, sy = tr["scale_x"] / 100.0, tr["scale_y"] / 100.0
    opacity = max(0.0, min(1.0, tr["opacity"] / 100.0)) * pre_opacity
    if sx <= 0 or sy <= 0 or opacity <= 0:
        return
    w, h = tr["src_w"], tr["src_h"]
    crops = [max(tr[k], 0) for k in ("crop_left", "crop_top", "crop_right", "crop_bottom")]
    if pre != fx_.IDENTITY and any(crops):
        img, origin, pre = _materialize(img, origin, prescale, pre, w, h), (0.0, 0.0), fx_.IDENTITY
    if pre == fx_.IDENTITY:
        # crop rect in source pixels, intersected with the provided pixels
        cl, ct = crops[0] / 100 * w, crops[1] / 100 * h
        cr, cb = w - crops[2] / 100 * w, h - crops[3] / 100 * h
        ox, oy = origin
        x0, y0 = max(cl, ox), max(ct, oy)
        x1, y1 = min(cr, ox + img.width / fx), min(cb, oy + img.height / fy)
        if x1 <= x0 or y1 <= y0:
            return
        ix0, iy0 = round((x0 - ox) * fx), round((y0 - oy) * fy)
        ix1, iy1 = round((x1 - ox) * fx), round((y1 - oy) * fy)
        if (ix0, iy0, ix1, iy1) != (0, 0, img.width, img.height):
            img = img.crop((ix0, iy0, ix1, iy1))
        origin = (ox + ix0 / fx, oy + iy0 / fy)
    img2src = (1 / fx, 0.0, origin[0], 0.0, 1 / fy, origin[1])
    motion = fx_.trs(tr["position"], tr["anchor"], sx, sy, tr["rotation"])
    warp(canvas, img, fx_.mul(motion, fx_.mul(pre, img2src)), opacity)


def apply_effects(clip: dict, img: Image.Image, origin, prescale, t: int, src_w: float, src_h: float, chain=None):
    """Run the clip's standard effects -> (img, origin, pre affine, pre opacity).
    ``chain``: explicit [(effect, timing clip)] instead (track effects)."""
    pre, pre_op = fx_.IDENTITY, 1.0
    for effect, clip in chain if chain is not None else [(e, clip) for e in clip.get("effects") or []]:
        if not effect.get("enabled", True):
            continue
        if effect["type"] == "transform":
            m, op = fx_.transform_effect_at(effect, clip, t, src_w, src_h)
            pre, pre_op = fx_.mul(m, pre), pre_op * op
        elif effect["type"] == "lumetri":
            from . import color

            vals = {k: fx_.param(effect, k, clip, t, src_w, src_h) for k in fx_.EFFECTS["lumetri"]["params"]}
            if not color.is_identity(vals):
                img = color.grade_image(img, vals)  # per pixel: commutes with the pending affine
        elif effect["type"] == "gaussian_blur":
            sx, sy = fx_.blur_sigma(effect, clip, t)
            sx, sy = sx * prescale[0], sy * prescale[1]
            if sx < 0.05 and sy < 0.05:
                continue
            if pre != fx_.IDENTITY or pre_op < 1:
                img = _materialize(img, origin, prescale, pre, src_w, src_h)
                if pre_op < 1:
                    img.putalpha(img.getchannel("A").point(lambda v, o=pre_op: round(v * o)))
                origin, pre, pre_op = (0.0, 0.0), fx_.IDENTITY, 1.0
            if param_bool(effect, "repeat_edge"):
                img = img.convert("RGBA").filter(ImageFilter.GaussianBlur((sx, sy)))
            else:
                px, py = math.ceil(3 * sx) + 1, math.ceil(3 * sy) + 1
                padded = Image.new("RGBA", (img.width + 2 * px, img.height + 2 * py), (0, 0, 0, 0))
                padded.paste(img.convert("RGBA"), (px, py))
                img = _premultiplied_blur(padded, (sx, sy))
                origin = (origin[0] - px / prescale[0], origin[1] - py / prescale[1])
    return img, origin, pre, pre_op


IDENTITY_TR = {"position": [0.0, 0.0], "anchor": [0.0, 0.0], "scale_x": 100.0, "scale_y": 100.0, "rotation": 0.0, "opacity": 100.0,
               "crop_left": 0.0, "crop_top": 0.0, "crop_right": 0.0, "crop_bottom": 0.0}


def apply_track_effects(target: Image.Image, layer: Image.Image, clip: dict, track: dict, t: int) -> None:
    """Composite a clip's finished layer (sequence pixels) through its track's effects."""
    chain = [(e, tracks_.timing_clip(e, clip)) for e in tracks_.active_track_effects(track)]
    box = layer.getbbox()
    if box is None:
        return
    w, h = layer.size
    img, origin, pre, pre_op = apply_effects(clip, layer, (0.0, 0.0), (1.0, 1.0), t, w, h, chain)
    place(target, img, origin, {**IDENTITY_TR, "src_w": w, "src_h": h}, (1.0, 1.0), pre, pre_op)


def param_bool(effect: dict, name: str) -> bool:
    return bool(effect.get("params", {}).get(name, fx_.EFFECTS[effect["type"]]["params"][name][1]))


def _premultiplied_blur(img: Image.Image, sigma) -> Image.Image:
    # blur in premultiplied alpha so transparent pixels don't darken the edges
    return img.convert("RGBa").filter(ImageFilter.GaussianBlur(sigma)).convert("RGBA")


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


_color = text_.color
load_font = text_.load_font
render_text_layer = text_.render_text_layer


def _coverage(poly: list[tuple[float, float]], w: int, h: int, ss: int) -> np.ndarray:
    """Exact nonzero-winding coverage of a polygon, sampled at ss x ss points
    per pixel -> float32 (h, w) in 0..1. Pillow's polygon fill is inclusive of
    its edges (an N-pixel box grows to N+1), which shows as a faint halo."""
    n = len(poly)
    p = np.asarray(poly, dtype=np.float64) * ss
    q = np.roll(p, -1, axis=0)
    x0, y0, x1, y1 = p[:, 0], p[:, 1], q[:, 0], q[:, 1]
    keep = y0 != y1
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    mask = np.zeros((h * ss, w * ss), dtype=bool)
    if not len(x0) or n < 3:
        return np.zeros((h, w), dtype=np.float32)
    ys = np.arange(h * ss) + 0.5
    lo, hi = np.minimum(y0, y1), np.maximum(y0, y1)
    hit = (ys[:, None] >= lo) & (ys[:, None] < hi)  # half-open: shared vertices count once
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = x0 + (ys[:, None] - y0) * (x1 - x0) / (y1 - y0)
    wind = np.where(y1 > y0, 1, -1)
    cols = np.arange(w * ss) + 0.5
    for r in np.nonzero(hit.any(axis=1))[0]:
        e = np.nonzero(hit[r])[0]
        order = np.argsort(xs[r, e])
        acc = 0
        for k in range(len(order) - 1):
            acc += wind[e[order[k]]]
            if acc:
                a, b = xs[r, e[order[k]]], xs[r, e[order[k + 1]]]
                i0, i1 = np.searchsorted(cols, a), np.searchsorted(cols, b)
                mask[r, i0:i1] = True
    return mask.reshape(h, ss, w, ss).mean(axis=(1, 3), dtype=np.float32)


def render_shape_layer(layer: dict) -> tuple[Image.Image, float, float] | None:
    """Rasterise one shape layer (untransformed) -> (image, anchor_x, anchor_y)
    with the layer's anchor located inside the image. Antialiased by
    supersampling; the stroke is centered on the path, as in the SVG preview."""
    closed = layer.get("closed", True)
    poly = shapes.flatten(layer["path"], closed)
    if len(poly) < 2:
        return None
    fill = layer.get("fill") if closed else None
    stroke = float(layer.get("stroke_width") or 0) if layer.get("stroke_color") else 0.0
    if not fill and stroke <= 0:
        return None
    xs, ys = [x for x, _ in poly], [y for _, y in poly]
    m = stroke / 2 + 2
    ox, oy = math.floor(min(xs) - m), math.floor(min(ys) - m)
    w, h = math.ceil(max(xs) + m) - ox, math.ceil(max(ys) + m) - oy
    ss = max(1, min(4, 4096 // max(w, h, 1)))
    local = [(x - ox, y - oy) for x, y in poly]
    rgba = np.zeros((h, w, 4), dtype=np.float32)  # premultiplied
    if fill:
        cov = _coverage(local, w, h, ss)
        c = np.asarray((*_color(fill), 255)[:4], dtype=np.float32) / 255
        rgba += cov[..., None] * np.array([c[0] * c[3], c[1] * c[3], c[2] * c[3], c[3]], dtype=np.float32)
    if stroke > 0:
        line = Image.new("L", (w * ss, h * ss), 0)
        pts = [(x * ss - 0.5, y * ss - 0.5) for x, y in local]
        ImageDraw.Draw(line).line(pts + ([pts[0]] if closed else []), fill=255, width=max(1, round(stroke * ss)), joint="curve")
        cov = np.asarray(line, dtype=np.float32).reshape(h, ss, w, ss).mean(axis=(1, 3)) / 255
        c = np.asarray((*_color(layer["stroke_color"], (0, 0, 0, 255)), 255)[:4], dtype=np.float32) / 255
        src = cov[..., None] * np.array([c[0] * c[3], c[1] * c[3], c[2] * c[3], c[3]], dtype=np.float32)
        rgba = src + rgba * (1 - src[..., 3:4])
    alpha = rgba[..., 3:4]
    rgb = np.where(alpha > 0, rgba[..., :3] / np.maximum(alpha, 1e-6), 0)
    out = np.concatenate([rgb, alpha], axis=2)
    img = Image.fromarray(np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA")
    ax, ay = layer.get("anchor") or [0.0, 0.0]
    return img, ax - ox, ay - oy


_IMAGES: dict[tuple[str, float], Image.Image] = {}


def image_layer_pixels(path: str) -> Image.Image | None:
    """An image layer's picture, decoded once per file version (None when the file is missing)."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key not in _IMAGES:
        if len(_IMAGES) > 64:
            _IMAGES.clear()
        try:
            _IMAGES[key] = decode_still(path)
        except RenderError:
            return None
    return _IMAGES[key]


def _layer_image(layer: dict) -> tuple[Image.Image, dict] | None:
    if layer.get("type") == "image":
        img = image_layer_pixels(layer["path"])
        if img is None:
            return None
        ax, ay = layer.get("anchor") or [img.width / 2, img.height / 2]
        s = layer.get("scale", 100.0)
        sx = s if layer.get("uniform", True) else layer.get("scale_width", 100.0)
    elif layer.get("type") == "shape":
        out = render_shape_layer(layer)
        if out is None:
            return None
        img, ax, ay = out
        s = layer.get("scale", 100.0)
        sx = s if layer.get("uniform", True) else layer.get("scale_width", 100.0)
    else:
        if not layer.get("text") and not layer.get("runs"):
            return None
        img, ax, ay = render_text_layer(layer)
        s = sx = layer.get("scale", 100.0)
    crop = {k: layer.get(k, 0.0) for k in ("crop_left", "crop_top", "crop_right", "crop_bottom")} if layer.get("type") == "image" else {}
    return img, {
        "src_w": img.width, "src_h": img.height, "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0, **crop,
        "position": layer["position"], "anchor": [ax, ay], "scale_x": sx, "scale_y": s,
        "rotation": layer.get("rotation", 0.0), "opacity": layer.get("opacity", 100.0),
    }


def render_graphic_groups(graphic: dict, width: int, height: int) -> list[tuple[str, Image.Image]]:
    """Layers bottom to top, flattened into consecutive runs that share a blend
    mode: [(blend, image)]. Normal runs are plain alpha-over; a multiply run is
    blended onto whatever lies below it in the sequence (Premiere does not
    isolate a graphic's layers)."""
    groups: list[tuple[str, Image.Image]] = []
    for layer in graphic.get("layers", []):
        if not layer.get("visible", True):
            continue
        out = _layer_image(layer)
        if out is None:
            continue
        blend = layer.get("blend", "normal") if layer.get("type") in ("shape", "image") else "normal"
        if not groups or groups[-1][0] != blend:
            groups.append((blend, Image.new("RGBA", (width, height), (0, 0, 0, 0))))
        place(groups[-1][1], out[0], (0, 0), out[1])
    return groups


def render_graphic(graphic: dict, width: int, height: int) -> Image.Image:
    """All layers alpha-over (blend modes ignored): thumbnails and tests."""
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for _, img in render_graphic_groups(graphic, width, height):
        canvas.alpha_composite(img)
    return canvas


def blend_multiply(target: Image.Image, src: Image.Image) -> None:
    """W3C multiply with source-over compositing, in place:
    co = cs * as * (1 - ab) + cs * cb * as * ab + cb * ab * (1 - as)."""
    d = np.asarray(target, dtype=np.float32) / 255
    s_ = np.asarray(src, dtype=np.float32) / 255
    ab, as_ = d[..., 3:4], s_[..., 3:4]
    cb, cs = d[..., :3], s_[..., :3]
    co = cs * as_ * (1 - ab) + cs * cb * as_ * ab + cb * ab * (1 - as_)
    ao = as_ + ab * (1 - as_)
    rgb = np.where(ao > 0, co / np.maximum(ao, 1e-6), 0)
    out = np.concatenate([rgb, ao], axis=2)
    target.paste(Image.fromarray(np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA"))


# ---------------------------------------------------------------- sources


def media_path(media: dict) -> str | None:
    if media.get("offline"):
        return None
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
        # Each output frame shows the source frame active at its time (Premiere, and the
        # Program monitor's WebCodecs path). An input seek drops frames before the seek
        # point, which would lose the frame covering a start that falls between source
        # frames, so seek MARGIN earlier and put t=0 back on the requested start; fps then
        # repeats the last frame at or before each tick (round=up keeps a frame that starts
        # just after a tick from being shown at that tick).
        start = max(0, start_ticks) / TPS
        margin = min(start, 0.25)
        vf = (f"setpts=(PTS-{margin:.6f}/TB)/{speed!r},fps={fps[0]}/{fps[1]}:round=up:start_time=0,"
              f"scale={size[0]}:{size[1]}:flags=bicubic:in_color_matrix={matrix}:in_range={rng},format=rgba")
        cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{start - margin:.6f}", "-i", path,
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


class ReverseDecoder:
    """Frames of a reversed clip (negative speed): decoded forward in chunks and
    handed out back to front. Same interface as VideoDecoder."""

    CHUNK = 24

    def __init__(self, path: str, start_src: int, speed: float, fps: tuple[int, int], size: tuple[int, int], max_frames: int, src_height: int = 1080):
        self.path, self.speed, self.fps, self.size, self.src_height = path, abs(speed), fps, size, src_height
        self.tpf = timebase.ticks_per_frame(*fps)
        self.next_src = start_src  # source time of the next frame handed out
        self.left = max_frames
        self.pending: list[Image.Image] = []
        self.last: Image.Image | None = None
        self.done = False

    def _fill(self) -> None:
        n = min(self.CHUNK, self.left)
        if n <= 0:
            self.done = True
            return
        step = self.tpf * self.speed
        low = self.next_src - (n - 1) * step  # the chunk's earliest source time
        first = 0
        if low < 0:  # the clip runs into the media start: only what exists
            first = math.ceil(-low / step)
            low += first * step
        dec = VideoDecoder(self.path, round(low), self.speed, self.fps, self.size, n - first, self.src_height)
        frames = []
        try:
            for _ in range(n - first):
                img = dec.next()
                if img is None:
                    break
                frames.append(img.copy())
        finally:
            dec.close()
        self.pending = list(reversed(frames))
        self.next_src -= n * step
        self.left -= n

    def next(self) -> Image.Image | None:
        if not self.pending and not self.done:
            self._fill()
        if self.pending:
            self.last = self.pending.pop(0)
        return self.last

    def close(self) -> None:
        self.pending = []


def grab_frame(path: str, t: int, size: tuple[int, int] | None = None, src_height: int = 1080) -> Image.Image | None:
    """One frame of a video at source time ``t`` (Frame Hold)."""
    matrix, rng = color_matrix(path, src_height)
    # the same colour conversion as VideoDecoder (ffmpeg's default would be Rec.601)
    w, h = size if size else ("iw", "ih")
    vf = f"scale={w}:{h}:flags=bicubic:in_color_matrix={matrix}:in_range={rng},format=rgba"
    proc = subprocess.run([ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{max(0, t) / TPS:.6f}", "-i", path, "-frames:v", "1",
                           "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=120, **NO_WINDOW)
    if proc.returncode != 0 or not proc.stdout:
        return None
    import io

    return Image.open(io.BytesIO(proc.stdout)).convert("RGBA")


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


def mix(a: Image.Image | None, b: Image.Image | None, u: float) -> Image.Image | None:
    """Linear blend of two straight-alpha layers in premultiplied space
    (``None`` = transparent), like Premiere's Cross Dissolve."""
    u = max(0.0, min(1.0, u))
    if a is None and b is None:
        return None
    if b is None or u <= 0:
        return _fade(a, 1 - u) if a is not None else None
    if a is None or u >= 1:
        return _fade(b, u) if b is not None else None
    return Image.blend(a.convert("RGBa"), b.convert("RGBa"), u).convert("RGBA")


def _fade(img: Image.Image, k: float) -> Image.Image:
    if k >= 1:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: round(v * k)))
    return out


@dataclass
class ActiveClip:
    clip: dict
    media: dict | None
    src_w: float
    src_h: float
    prescale: tuple[float, float] = (1.0, 1.0)
    decoder: "VideoDecoder | ReverseDecoder | None" = None
    still: Image.Image | None = None
    origin: tuple[float, float] = (0.0, 0.0)
    groups: list | None = None  # graphics with blend modes: [(blend, image, origin)]
    sub: "Compositor | None" = None  # nested sequence


class Compositor:
    """Renders consecutive frames of a sequence. Create one per render pass and
    call :meth:`frame` with increasing times (random access works, but costs a
    decoder restart per jump)."""

    def __init__(self, doc: dict, sequence_id: str, warn=None, depth: int = 0):
        self.doc = doc
        self.depth = depth
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
        # transitions extend their clips into the handles: clip id -> (start, end) actually shown
        self.tracks: list[tuple[dict, list]] = []
        self.extent: dict[str, tuple[int, int]] = {}
        self.track_of: dict[str, dict] = {}
        for tid in self.seq["video_tracks"]:
            track = self.seq["tracks"][tid]
            trans = fx_.track_transitions(track)
            self.tracks.append((track, trans))
            for cid in track["clips"]:
                self.track_of[cid] = track
            for ws, we, _, a, b in trans:
                for c in (a, b):
                    if c is not None:
                        s0, e0 = self.extent.get(c["id"], (c["start"], c["end"]))
                        self.extent[c["id"]] = (min(s0, ws), max(e0, we))

    def _once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.warn(msg)

    def track_items(self, track: dict, trans: list, t: int):
        """What a track shows at ``t``: ("clip", clip) | ("transition", tr, u, a, b) | None."""
        for ws, we, tr, a, b in trans:
            if ws <= t < we:
                a = a if a is not None and a["enabled"] else None
                b = b if b is not None and b["enabled"] else None
                return ("transition", tr, fx_.dissolve_progress(t + self.tpf // 2, ws, we), a, b)
        for c in track["clips"].values():
            if c["enabled"] and c["start"] <= t < c["end"]:
                return ("clip", c)
        return None

    def visible_clips(self, t: int) -> list[dict]:
        out = []
        for track, trans in self.tracks:
            if track["hidden"]:
                continue
            item = self.track_items(track, trans, t)
            if item and item[0] == "clip":
                out.append(item[1])
            elif item:
                out += [c for c in item[3:] if c is not None]
        return out

    def _open(self, clip: dict, t: int) -> ActiveClip | None:
        graphic = tracks_.clip_graphic(clip, self.track_of.get(clip["id"]))
        if graphic is not None:
            key = json.dumps(graphic, sort_keys=True)
            cached = self._graphics.get(clip["id"])
            if not cached or cached[0] != key:
                groups = []
                for blend, img in render_graphic_groups(graphic, self.w, self.h):
                    box = img.getbbox()
                    if box:
                        groups.append((blend, img.crop(box), (float(box[0]), float(box[1]))))
                if not groups:
                    groups = [("normal", Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (0.0, 0.0))]
                cached = (key, groups)
                self._graphics[clip["id"]] = cached
            groups = cached[1]
            if len(groups) == 1 and groups[0][0] == "normal":
                return ActiveClip(clip, None, self.w, self.h, still=groups[0][1], origin=groups[0][2])
            return ActiveClip(clip, None, self.w, self.h, still=groups[0][1], origin=groups[0][2], groups=groups)
        if clip.get("sequence_id"):
            # nested sequence: rendered by its own compositor, shown at this sequence's frame size
            if clip["sequence_id"] not in self.doc["sequences"] or self.depth >= 8:
                return None
            sub = Compositor(self.doc, clip["sequence_id"], self.warn, self.depth + 1)
            if not sub.seq["video_tracks"]:
                return None
            return ActiveClip(clip, None, self.w, self.h, sub=sub)
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
        if clip.get("freeze") is not None:
            # Frame Hold: one frame for the whole clip
            img = grab_frame(path, clip["freeze"], None, h)
            return ActiveClip(clip, media, w, h, still=img) if img is not None else None
        # decode no larger than the clip is ever shown (saves 4K -> 1080p work)
        m = clip["motion"]
        sy = _max_scale(clip, "scale") / 100
        sx = sy if m["uniform"] else _max_scale(clip, "scale_width") / 100
        kfs = clip.get("keyframes", {})
        if (not any(kfs.get(k) for k in ("scale", "scale_width", "rotation")) and m["rotation"] % 360 == 0 and not clip.get("effects")
                and not tracks_.active_track_effects(self.track_of.get(clip["id"]))):
            # static, unrotated: let ffmpeg scale to the exact displayed size so compositing is a paste
            limit = 2 * max(self.w, self.h) / max(w, h)
            sx, sy = min(limit, max(sx, 0.01)), min(limit, max(sy, 0.01))
        else:
            sx, sy = min(1.0, max(sx, 0.01)), min(1.0, max(sy, 0.01))
        dw, dh = max(2, round(w * sx / 2) * 2), max(2, round(h * sy / 2) * 2)
        frames_left = -(-(self.extent.get(clip["id"], (0, clip["end"]))[1] - t) // self.tpf)
        src = clip["source_in"] + round((t - clip["start"]) * clip["speed"])
        if clip["speed"] < 0:
            dec = ReverseDecoder(path, tracks_.media_time(clip, src), clip["speed"], self.fps, (dw, dh), frames_left, h)
        else:
            dec = VideoDecoder(path, src, clip["speed"], self.fps, (dw, dh), frames_left, h)
        return ActiveClip(clip, media, w, h, prescale=(dw / w, dh / h), decoder=dec)

    def _draw(self, target: Image.Image, clip: dict, t: int) -> None:
        track = self.track_of.get(clip["id"])
        if tracks_.active_track_effects(track):
            # track effects work on the clip's finished picture, in sequence pixels
            layer = Image.new("RGBA", target.size, (0, 0, 0, 0))
            self._draw_clip(layer, clip, t)
            apply_track_effects(target, layer, clip, track, t)
            return
        self._draw_clip(target, clip, t)

    def _draw_clip(self, target: Image.Image, clip: dict, t: int) -> None:
        ac = self.active.get(clip["id"])
        if ac and ac.decoder and self.expected.get(clip["id"]) != t:
            ac.decoder.close()  # random access: restart at the new time
            ac = None
        if ac and ac.sub and ac.sub.seq is not self.doc["sequences"].get(clip["sequence_id"]):
            self._close(ac)
            ac = None
        if ac is None:
            ac = self._open(clip, t)
            if ac is None:
                return
            self.active[clip["id"]] = ac
        if ac.decoder:
            img = ac.decoder.next()
            self.expected[clip["id"]] = t + self.tpf
            if img is None:
                return
        elif ac.sub:
            img = ac.sub.frame_rgba(clip["source_in"] + round((t - clip["start"]) * clip["speed"]))
            if img.size != (self.w, self.h):
                img = img.resize((self.w, self.h), Image.Resampling.BILINEAR)
        else:
            img = ac.still
        tr = transform_at(clip, t, self.seq, ac.media)
        tr["src_w"], tr["src_h"] = ac.src_w, ac.src_h
        if ac.groups:
            for blend, gimg, gorigin in ac.groups:
                gimg, origin, pre, pre_op = apply_effects(clip, gimg, gorigin, ac.prescale, t, ac.src_w, ac.src_h)
                if blend == "normal":
                    place(target, gimg, origin, tr, ac.prescale, pre, pre_op)
                else:
                    layer = Image.new("RGBA", target.size, (0, 0, 0, 0))
                    place(layer, gimg, origin, tr, ac.prescale, pre, pre_op)
                    blend_multiply(target, layer)
            return
        img, origin, pre, pre_op = apply_effects(clip, img, ac.origin, ac.prescale, t, ac.src_w, ac.src_h)
        place(target, img, origin, tr, ac.prescale, pre, pre_op)

    def _layer(self, clip: dict | None, t: int) -> Image.Image | None:
        if clip is None:
            return None
        layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        self._draw(layer, clip, t)
        return layer

    def _transition(self, canvas: Image.Image, tr: dict, u: float, a: dict | None, b: dict | None, t: int) -> None:
        la, lb = self._layer(a, t), self._layer(b, t)
        kind = tr["type"]
        if kind in ("dip_to_black", "dip_to_white"):
            color = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 255) if kind == "dip_to_black" else (255, 255, 255, 255))
            out = mix(la, color, u * 2) if u < 0.5 else mix(color, lb, u * 2 - 1)
        else:
            out = mix(la, lb, u)
        if out is not None:
            canvas.alpha_composite(out)

    def frame(self, t: int) -> Image.Image:
        # transparent until the end so blend modes see "nothing below" as
        # transparent (as Premiere does); flattened onto black on return
        canvas = self.frame_rgba(t)
        out = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 255))
        out.alpha_composite(canvas)
        return out

    def frame_rgba(self, t: int) -> Image.Image:
        """The composited frame with transparency where nothing is (what a nested sequence shows)."""
        canvas = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        items = []
        for track, trans in self.tracks:
            if not track["hidden"]:
                item = self.track_items(track, trans, t)
                if item:
                    items.append(item)
        live = {c["id"] for it in items for c in ([it[1]] if it[0] == "clip" else it[3:]) if c is not None}
        for cid in [k for k in self.active if k not in live]:
            self._close(self.active.pop(cid))
        for item in items:
            if item[0] == "clip":
                if item[1].get("adjustment"):
                    canvas = self._adjust(canvas, item[1], t)
                else:
                    self._draw(canvas, item[1], t)
            else:
                self._transition(canvas, item[1], item[2], item[3], item[4], t)
        return canvas

    def _adjust(self, canvas: Image.Image, clip: dict, t: int) -> Image.Image:
        """An Adjustment Layer: its effects run on everything composited below it; its Opacity
        mixes the result back in (Premiere)."""
        img, origin, pre, pre_op = apply_effects(clip, canvas, (0.0, 0.0), (1.0, 1.0), t, self.w, self.h)
        out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        place(out, img, origin, {**IDENTITY_TR, "src_w": self.w, "src_h": self.h}, (1.0, 1.0), pre, pre_op)
        opacity = max(0.0, min(1.0, transform_at(clip, t, self.seq, None)["opacity"] / 100))
        return out if opacity >= 1 else mix(canvas, out, opacity)

    @staticmethod
    def _close(ac: ActiveClip) -> None:
        if ac.decoder:
            ac.decoder.close()
        if ac.sub:
            ac.sub.close()

    def close(self) -> None:
        for ac in self.active.values():
            self._close(ac)
        self.active.clear()
