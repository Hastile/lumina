"""Background media jobs: proxy, thumbnail and waveform generation.

Results are written under the cache dir and recorded on the media item with
the non-undoable ``media.set_derived`` command so the GUI (and agents) see
them through the normal patch stream.
"""

from __future__ import annotations

import array
import json
import os
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from ..adb.quiet_process import NO_WINDOW
from .probe import ffmpeg_bin

BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le", "pcm_f32le"}
BROWSER_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
PEAKS_PER_SECOND = 100
PEAK_SAMPLE_RATE = 8000


def cache_root() -> Path:
    from .. import media_cache

    root = media_cache.root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run(cmd: list[str], timeout: int = 3600) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"ffmpeg exited {proc.returncode}")


def make_thumbnail(item: dict, out: Path) -> Path:
    src = item["path"]
    seek = [] if item["kind"] == "image" else ["-ss", str(min(1.0, item["duration"] / 254_016_000_000 / 2))]
    _run([ffmpeg_bin(), "-y", "-v", "error", *seek, "-i", src, "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "4", str(out)], timeout=120)
    return out


FILMSTRIP_HEIGHT = 54
FILMSTRIP_MAX = 300


def make_filmstrip(item: dict, out_dir: Path) -> Path:
    """Frames across the media in one horizontal sprite, for the timeline's clip thumbnails:
    one per second, or fewer (evenly spaced, at most FILMSTRIP_MAX) for long media."""
    v = item["video"]
    secs = item["duration"] / 254_016_000_000
    interval = max(1.0, secs / FILMSTRIP_MAX)
    count = max(1, int(secs / interval))
    w = max(2, round(FILMSTRIP_HEIGHT * v["width"] / max(1, v["height"]) / 2) * 2)
    img = out_dir / "filmstrip.jpg"
    # frame i shows the media at (i + 0.5) * interval
    _run([ffmpeg_bin(), "-y", "-v", "error", "-skip_frame", "nokey" if interval >= 4 else "default", "-i", item["path"], "-an",
          "-vf", f"fps=1/{interval!r}:start_time={interval / 2!r},scale={w}:{FILMSTRIP_HEIGHT},tile={count}x1",
          "-frames:v", "1", "-q:v", "5", str(img)])
    meta = out_dir / "filmstrip.json"
    meta.write_text(json.dumps({"image": str(img), "interval": interval, "count": count, "width": w, "height": FILMSTRIP_HEIGHT}),
                    encoding="utf-8")
    return meta


def needs_proxy(item: dict) -> bool:
    if item["kind"] == "image":
        return Path(item["path"]).suffix.lower() not in BROWSER_IMAGE_EXT
    v, a = item.get("video"), item.get("audio")
    if v is not None:
        if v["codec"] not in BROWSER_VIDEO or v["pix_fmt"] not in ("yuv420p", "yuvj420p") or v["height"] > 1080:
            return True
        if Path(item["path"]).suffix.lower() not in (".mp4", ".m4v", ".mov", ".webm", ".mkv"):
            return True
    if a is not None and a["codec"] not in BROWSER_AUDIO:
        return True
    return False


def make_proxy(item: dict, out_dir: Path) -> Path:
    """Browser-playable, seek-friendly proxy (short GOP, no B-frames)."""
    src = item["path"]
    if item["kind"] == "image":
        out = out_dir / "proxy.png"
        _run([ffmpeg_bin(), "-y", "-v", "error", "-i", src, "-frames:v", "1", str(out)], timeout=120)
        return out
    if item.get("video") is None:
        out = out_dir / "proxy.m4a"
        _run([ffmpeg_bin(), "-y", "-v", "error", "-i", src, "-vn", "-c:a", "aac", "-b:a", "192k", str(out)])
        return out
    out = out_dir / "proxy.mp4"
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", src,
           "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           "-g", "15", "-bf", "0", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "160k"] if item.get("audio") else ["-an"]
    cmd += ["-movflags", "+faststart", str(out)]
    _run(cmd)
    return out


def make_waveform(item: dict, out: Path) -> Path:
    """Peak envelope: ``PEAKS_PER_SECOND`` uint8 values per second, mono."""
    proc = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", item["path"], "-vn", "-ac", "1", "-ar", str(PEAK_SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, timeout=3600, **NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[-300:])
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    step = PEAK_SAMPLE_RATE // PEAKS_PER_SECOND
    peaks = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        peak = max(max(chunk), -min(chunk)) if chunk else 0
        peaks.append(min(255, peak * 255 // 32767))
    out.write_text(json.dumps({"rate": PEAKS_PER_SECOND, "peaks": peaks}, separators=(",", ":")), encoding="utf-8")
    return out


class JobRunner:
    def __init__(self, record: Callable[[str, dict], None], emit: Callable[[dict], None], workers: int = 2):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lumina-job")
        self.record = record
        self.emit = emit
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def _update(self, job: dict, **changes) -> None:
        with self.lock:
            job.update(changes)
        self.emit({"type": "job", "job": dict(job)})

    def submit_media(self, item: dict, kinds: set[str] | None = None) -> list[str]:
        """Queue the derived files of a media item (``kinds``: only these of thumbnail / filmstrip / waveform / proxy)."""
        out_dir = cache_root() / item["id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        ids = []
        tasks: list[tuple[str, Callable[[], dict]]] = []
        if item.get("video") is not None:
            tasks.append(("thumbnail", lambda: {"thumbnail_path": str(make_thumbnail(item, out_dir / "thumb.jpg"))}))
            if item["kind"] == "video" and item["duration"] > 0:
                tasks.append(("filmstrip", lambda: {"filmstrip_path": str(make_filmstrip(item, out_dir))}))
        if item.get("audio") is not None:
            tasks.append(("waveform", lambda: {"waveform_path": str(make_waveform(item, out_dir / "waveform.json"))}))
        if needs_proxy(item):
            tasks.append(("proxy", lambda: {"proxy_path": str(make_proxy(item, out_dir))}))
        if kinds is not None:
            tasks = [t for t in tasks if t[0] in kinds]
        for kind, fn in tasks:
            job = {"id": uuid.uuid4().hex, "kind": kind, "media_id": item["id"], "status": "queued", "error": None}
            self.jobs[job["id"]] = job
            ids.append(job["id"])
            self.pool.submit(self._run, job, fn)
        return ids

    def _run(self, job: dict, fn: Callable[[], dict]) -> None:
        self._update(job, status="running")
        try:
            fields = fn()
            self.record(job["media_id"], fields)
            self._update(job, status="done")
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self._update(job, status="failed", error=str(exc))

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(j) for j in self.jobs.values()]
