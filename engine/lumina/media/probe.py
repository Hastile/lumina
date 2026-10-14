"""ffprobe wrapper producing MediaItem fields."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .. import timebase
from ..adb.quiet_process import NO_WINDOW

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif", ".psd"}


class ProbeError(Exception):
    pass


def ffprobe_bin() -> str:
    return os.environ.get("LUMINA_FFPROBE") or shutil.which("ffprobe") or "ffprobe"


def ffmpeg_bin() -> str:
    return os.environ.get("LUMINA_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def run_ffprobe(path: str) -> dict:
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60, **NO_WINDOW)
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe not found; install FFmpeg or set LUMINA_FFPROBE") from exc
    if proc.returncode != 0:
        raise ProbeError(proc.stderr.strip() or f"ffprobe failed for {path}")
    return json.loads(proc.stdout or "{}")


def probe(path: str) -> dict:
    """Return ``{name, kind, duration, video, audio}`` for a media file."""
    p = Path(path)
    if not p.is_file():
        raise ProbeError(f"file not found: {path}")
    info = run_ffprobe(str(p))
    streams = info.get("streams", [])
    vstream = next(
        (s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if vstream is None and astream is None:
        raise ProbeError(f"no audio or video streams in {path}")

    is_image = vstream is not None and astream is None and (
        p.suffix.lower() in IMAGE_EXTS or vstream.get("codec_name") in {"png", "mjpeg", "bmp", "tiff", "webp"}
        and float(info.get("format", {}).get("duration") or 0) <= 0.05
    )

    video = None
    if vstream is not None:
        rate = vstream.get("avg_frame_rate") or vstream.get("r_frame_rate") or "30/1"
        try:
            fn, fd = timebase.parse_rate(rate)
            timebase.ticks_per_frame(fn, fd)
        except (ValueError, ZeroDivisionError):
            fn, fd = 30, 1
        video = {
            "width": int(vstream.get("width") or 0),
            "height": int(vstream.get("height") or 0),
            "fps_num": fn,
            "fps_den": fd,
            "codec": vstream.get("codec_name", ""),
            "pix_fmt": vstream.get("pix_fmt", ""),
        }
    audio = None
    if astream is not None:
        audio = {
            "channels": int(astream.get("channels") or 2),
            "sample_rate": int(astream.get("sample_rate") or 48000),
            "codec": astream.get("codec_name", ""),
        }

    duration_s = float(info.get("format", {}).get("duration") or 0)
    if not duration_s:
        for s in (vstream, astream):
            if s and s.get("duration"):
                duration_s = float(s["duration"])
                break
    kind = "image" if is_image else ("video" if video else "audio")
    return {
        "name": p.name,
        "kind": kind,
        "duration": 0 if is_image else timebase.seconds_to_ticks(duration_s),
        "video": video,
        "audio": audio,
    }
