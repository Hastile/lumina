"""Export (Premiere: File > Export > Media) and Export Frame.

Pipeline: audio is mixed first (numpy, sample-accurate volume keyframes) into
a temporary float32 file, then video frames from :class:`Compositor` are piped
as raw RGB into one ffmpeg process that encodes and muxes both. The output is
then verified with ffprobe (duration, dimensions, streams) before the job is
reported done, so "done" means a playable file of the expected length.
"""

from __future__ import annotations

import copy
import functools
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import numpy as np

from .. import timebase
from ..adb.premiere_export import validate_media_probe
from ..adb.quiet_process import NO_WINDOW
from ..commands.registry import CommandError, resolve_time
from ..media.probe import ffmpeg_bin, run_ffprobe
from .. import effects as fx_
from ..motion import value_at
from ..audio_fx import ffmpeg_filters
from .compositor import Compositor, RenderError, media_path

TPS = timebase.TICKS_PER_SECOND

# Premiere's export dialog formats, reduced to the presets people actually use.
PRESETS: dict[str, dict] = {
    "h264": {"label": "H.264 (Match Source - High Bitrate)", "ext": ".mp4", "video": "h264", "audio": ["-c:a", "aac", "-b:a", "320k"]},
    "hevc": {"label": "HEVC (H.265)", "ext": ".mp4", "video": "hevc", "audio": ["-c:a", "aac", "-b:a", "320k"]},
    "prores": {"label": "Apple ProRes 422 HQ", "ext": ".mov", "video": "prores", "audio": ["-c:a", "pcm_s16le"]},
    "wav": {"label": "Waveform Audio (WAV)", "ext": ".wav", "video": None, "audio": ["-c:a", "pcm_s16le"]},
}

SOFTWARE = {
    "h264": ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-profile:v", "high"],
    "hevc": ["-c:v", "libx265", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"],
    "prores": ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0"],
}
# hardware encoders, tried in order (NVIDIA, AMD, Intel)
HARDWARE = {
    "h264": [
        ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "18", "-b:v", "0", "-pix_fmt", "yuv420p", "-profile:v", "high"],
        ["-c:v", "h264_amf", "-quality", "quality", "-rc", "qvbr", "-qvbr_quality_level", "20", "-pix_fmt", "yuv420p", "-profile:v", "high"],
        ["-c:v", "h264_qsv", "-preset", "slower", "-global_quality", "20", "-pix_fmt", "nv12", "-profile:v", "high"],
    ],
    "hevc": [
        ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"],
        ["-c:v", "hevc_amf", "-quality", "quality", "-rc", "qvbr", "-qvbr_quality_level", "22", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"],
        ["-c:v", "hevc_qsv", "-preset", "slower", "-global_quality", "22", "-pix_fmt", "nv12", "-tag:v", "hvc1"],
    ],
}


@functools.cache
def hardware_encoder(codec: str) -> tuple[str, ...] | None:
    """ffmpeg builds list hardware encoders even without the GPU; try a real encode."""
    for args in HARDWARE.get(codec, []):
        cmd = [ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=640x360:d=0.2", *args, "-f", "null", "-"]
        try:
            if subprocess.run(cmd, capture_output=True, timeout=30, **NO_WINDOW).returncode == 0:
                return tuple(args)
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def presets() -> list[dict]:
    return [{"id": k, "label": v["label"], "extension": v["ext"], "has_video": v["video"] is not None,
             "hardware": bool(v["video"] and v["video"] in HARDWARE)} for k, v in PRESETS.items()]


# ---------------------------------------------------------------- ranges


def sequence_end(seq: dict) -> int:
    return max((c["end"] for t in seq["tracks"].values() for c in t["clips"].values()), default=0)


def resolve_range(seq: dict, rng: str | None, start=None, end=None) -> tuple[int, int]:
    st = seq["settings"]
    if start is not None or end is not None:
        a = resolve_time(start, st) if start is not None else 0
        b = resolve_time(end, st) if end is not None else sequence_end(seq)
    elif rng in ("in_out", "inout"):
        a = seq["in_point"] if seq["in_point"] is not None else 0
        b = seq["out_point"] if seq["out_point"] is not None else sequence_end(seq)
    else:
        a, b = 0, sequence_end(seq)
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    a = a // tpf * tpf
    b = -(-b // tpf) * tpf
    if b <= a:
        raise CommandError("EMPTY_RANGE", "nothing to export: the sequence (or the In/Out range) is empty")
    return a, b


# ---------------------------------------------------------------- audio


def _audible_tracks(seq: dict) -> list[dict]:
    tracks = [seq["tracks"][t] for t in seq["audio_tracks"]]
    solo = [t for t in tracks if t["solo"]]
    return [t for t in (solo or tracks) if not t["muted"]]


def _atempo(speed: float) -> list[str]:
    out = []
    while speed > 2.0:
        out.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        out.append("atempo=0.5")
        speed /= 0.5
    if abs(speed - 1.0) > 1e-9:
        out.append(f"atempo={speed!r}")
    return out


def decode_audio(path: str, src_start: int, src_dur: int, speed: float, rate: int, filters: list[str] | None = None,
                 channels: int = 2) -> np.ndarray:
    # mono plays at full level on both sides, as in the preview (ffmpeg's -ac 2 would mix it 3 dB down)
    up = ["pan=stereo|c0=c0|c1=c0"] if channels == 1 else []
    af = ",".join(up + _atempo(speed) + [f"aresample={rate}"] + list(filters or []))
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{max(0, src_start) / TPS:.6f}", "-t", f"{src_dur / TPS:.6f}",
           "-i", path, "-vn", "-af", af, "-ac", "2", "-ar", str(rate), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=3600, **NO_WINDOW)
    if proc.returncode != 0:
        raise RenderError(proc.stderr.decode("utf-8", "replace").strip()[-300:] or "audio decode failed")
    return np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2)


def stereo_pan(pcm: np.ndarray, pan: float) -> np.ndarray:
    """Track pan on a stereo signal, exactly as Web Audio's StereoPannerNode (the preview) does it:
    equal-power balance, pan -100 (left) .. 100 (right)."""
    x = max(-1.0, min(1.0, pan / 100))
    if x == 0:
        return pcm
    left, right = pcm[:, 0], pcm[:, 1]
    if x < 0:
        g = (x + 1) * np.pi / 2
        return np.stack([left + right * np.float32(np.cos(g)), right * np.float32(np.sin(g))], axis=1)
    g = x * np.pi / 2
    return np.stack([left * np.float32(np.cos(g)), right + left * np.float32(np.sin(g))], axis=1)


def filter_pcm(pcm: np.ndarray, rate: int, filters: list[str]) -> np.ndarray:
    """Run stereo float PCM through ffmpeg audio filters (a nested clip's Audio Gain and effects)."""
    if not filters or not len(pcm):
        return pcm
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", "-",
           "-af", ",".join(filters), "-f", "f32le", "-ar", str(rate), "-ac", "2", "-"]
    proc = subprocess.run(cmd, input=np.ascontiguousarray(pcm, dtype=np.float32).tobytes(), capture_output=True, timeout=3600, **NO_WINDOW)
    if proc.returncode != 0:
        raise RenderError(proc.stderr.decode("utf-8", "replace").strip()[-300:] or "audio filter failed")
    out = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2)
    if len(out) < len(pcm):
        out = np.concatenate([out, np.zeros((len(pcm) - len(out), 2), np.float32)])
    return out[: len(pcm)]


def _clip_pcm(doc: dict, c: dict, s: int, e: int, rate: int, warn: Callable[[str], None], depth: int) -> np.ndarray | None:
    """A clip's audio for the sequence range [s, e), after its Audio Gain and audio effects."""
    pre = ([f"volume={c['gain_db']}dB"] if c.get("gain_db") else []) + ffmpeg_filters(c.get("effects") or [], rate)
    src = c["source_in"] + round((s - c["start"]) * c["speed"])
    if c.get("sequence_id"):
        inner = doc["sequences"].get(c["sequence_id"])
        if inner is None or depth >= 8:
            return None
        n_out = round((e - s) * rate / TPS)
        span = round((e - s) * c["speed"])
        n_in = round(span * rate / TPS)
        pcm = np.zeros((max(0, n_in), 2), dtype=np.float32)
        _mix_into(doc, inner, src, src + span, pcm, rate, warn=warn, depth=depth + 1)
        if n_in != n_out and n_in > 1 and n_out > 0:
            # a sped-up / slowed nested clip: resampled (pitch follows the speed)
            x = np.linspace(0, n_in - 1, n_out)
            pcm = np.stack([np.interp(x, np.arange(n_in), pcm[:, k]) for k in (0, 1)], axis=1).astype(np.float32)
        return filter_pcm(pcm, rate, pre)
    media = doc["media"].get(c["media_id"] or "")
    if not media or not media.get("audio"):
        return None
    path = media_path(media)
    if path is None:
        warn(f"media offline, audio silent: {media['name']}")
        return None
    return decode_audio(path, src, round((e - s) * c["speed"]), c["speed"], rate, pre, (media["audio"] or {}).get("channels", 2))


def _mix_into(doc: dict, seq: dict, a: int, b: int, buf: np.ndarray, rate: int, cancelled: Callable[[], bool] = lambda: False,
              warn: Callable[[str], None] = lambda m: None, progress: Callable[[float], None] = lambda f: None, depth: int = 0) -> bool:
    """Add the mix of [a, b) of ``seq`` into ``buf`` (stereo float, ``rate``). False when cancelled."""
    total = len(buf)
    # clips with their audio transitions: (clip, shown start, shown end, [(fade start, fade end, "in"|"out")])
    jobs = []
    for track in _audible_tracks(seq):
        fades: dict[str, list] = {}
        ext: dict[str, list[int]] = {}
        for ws, we, _tr, ca, cb in fx_.track_transitions(track):
            for c, kind in ((ca, "out"), (cb, "in")):
                if c is None:
                    continue
                fades.setdefault(c["id"], []).append((ws, we, kind))
                r = ext.setdefault(c["id"], [c["start"], c["end"]])
                r[0], r[1] = min(r[0], ws), max(r[1], we)
        for c in track["clips"].values():
            s0, e0 = ext.get(c["id"], (c["start"], c["end"]))
            if c["enabled"] and e0 > a and s0 < b:
                jobs.append((c, s0, e0, fades.get(c["id"], []), track))
    master = 10 ** (seq.get("master_volume_db", 0.0) / 20)
    for i, (c, s0, e0, fades, track) in enumerate(jobs):
        if cancelled():
            return False
        progress(i / max(1, len(jobs)))
        s, e = max(s0, a), min(e0, b)
        src = c["source_in"] + round((s - c["start"]) * c["speed"])
        if src < 0:  # transition reaches before the media starts: silence until it does
            s -= round(src / c["speed"])
            if s >= e:
                continue
        pcm = _clip_pcm(doc, c, s, e, rate, warn, depth)
        if pcm is None:
            continue
        off = round((s - a) * rate / TPS)
        n = min(len(pcm), round((e - a) * rate / TPS) - off, total - off)
        if n <= 0:
            continue
        pcm = pcm[:n]
        if fades:
            t_ = s + (np.arange(n, dtype=np.float64) * TPS) / rate
            gain = np.ones(n, dtype=np.float64)
            for ws, we, kind in fades:
                u = np.clip((t_ - ws) / max(1, we - ws), 0.0, 1.0)
                # Constant Power: equal-power crossfade
                gain *= np.sin(u * np.pi / 2) if kind == "in" else np.cos(u * np.pi / 2)
            pcm = pcm * gain.astype(np.float32)[:, None]
        if c.get("keyframes", {}).get("volume_db"):
            # gain envelope sampled every 10 ms, interpolated per sample
            step = rate // 100
            idx = np.arange(0, n + step, step)
            ticks = s + (idx * TPS) // rate
            db = np.array([value_at(c, "volume_db", int(t_), seq) for t_ in ticks], dtype=np.float64)
            gain = np.interp(np.arange(n), idx, np.power(10.0, db / 20.0)).astype(np.float32)
            pcm = pcm * gain[:, None]
        elif c["volume_db"] != 0:
            pcm = pcm * np.float32(10 ** (c["volume_db"] / 20))
        # the track fader and the Mix fader are plain gains; then the track's pan
        g = 10 ** (track.get("volume_db", 0.0) / 20) * master
        if g != 1:
            pcm = pcm * np.float32(g)
        pcm = stereo_pan(pcm, track.get("pan", 0.0))
        buf[off:off + n] += pcm
    return True


def mix_audio(doc: dict, seq: dict, a: int, b: int, out_path: Path, cancelled: Callable[[], bool] = lambda: False,
              warn: Callable[[str], None] = lambda m: None, progress: Callable[[float], None] = lambda f: None) -> None:
    """Mix [a, b) of the sequence to interleaved stereo float32 at the sequence rate."""
    rate = seq["settings"]["sample_rate"]
    total = round((b - a) * rate / TPS)
    buf = np.lib.format.open_memmap(out_path.with_suffix(".npy"), mode="w+", dtype=np.float32, shape=(total, 2)) if total > 0 else None
    if buf is not None:
        if not _mix_into(doc, seq, a, b, buf, rate, cancelled, warn, progress):
            return
        np.clip(buf, -1.0, 1.0, out=buf)
        buf.flush()
        with open(out_path, "wb") as f:
            for i in range(0, total, rate * 10):
                f.write(np.ascontiguousarray(buf[i:i + rate * 10]).tobytes())
        del buf
        try:
            os.unlink(out_path.with_suffix(".npy"))
        except OSError:
            pass
    else:
        out_path.write_bytes(b"")


# ---------------------------------------------------------------- export


def _encoder_args(preset: dict, hardware: str) -> tuple[list[str], str]:
    codec = preset["video"]
    hw = hardware_encoder(codec) if hardware in ("auto", "on") else None
    if hardware == "on" and hw is None:
        raise CommandError("NO_HARDWARE_ENCODER", f"no working hardware {codec} encoder on this machine")
    args = list(hw) if hw else SOFTWARE[codec]
    return args, args[1]


def export(doc: dict, sequence_id: str, path: str, preset_id: str = "h264", rng: str | None = None, start=None, end=None,
           hardware: str = "auto", on_progress: Callable[[dict], None] = lambda p: None,
           cancelled: Callable[[], bool] = lambda: False) -> dict:
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise CommandError("BAD_PRESET", f"unknown preset {preset_id!r}; one of {list(PRESETS)}")
    seq = doc["sequences"].get(sequence_id)
    if seq is None:
        raise CommandError("NOT_FOUND", f"sequence {sequence_id} not found")
    a, b = resolve_range(seq, rng, start, end)
    st = seq["settings"]
    tpf = timebase.ticks_per_frame(st["fps_num"], st["fps_den"])
    n_frames = (b - a) // tpf
    out = Path(path)
    if not out.suffix:
        out = out.with_suffix(preset["ext"])
    out.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    warn = lambda m: warnings.append(m) if m not in warnings else None  # noqa: E731
    t0 = time.monotonic()
    tmp_dir = Path(tempfile.mkdtemp(prefix="lumina-export-"))
    partial = out.with_name(out.stem + ".partial" + out.suffix)
    try:
        audio_raw = tmp_dir / "mix.f32"
        on_progress({"phase": "audio", "progress": 0.0})
        mix_audio(doc, seq, a, b, audio_raw, cancelled, warn, lambda f: on_progress({"phase": "audio", "progress": f * 0.05}))
        if cancelled():
            raise CommandError("CANCELLED", "export cancelled")
        audio_in = ["-f", "f32le", "-ar", str(st["sample_rate"]), "-ac", "2", "-i", str(audio_raw)]
        if preset["video"] is None:
            cmd = [ffmpeg_bin(), "-y", "-v", "error", *audio_in, *preset["audio"], str(partial)]
            proc = subprocess.run(cmd, capture_output=True, timeout=3600, **NO_WINDOW)
            if proc.returncode != 0:
                raise RenderError(proc.stderr.decode("utf-8", "replace")[-500:])
            encoder = None
        else:
            vargs, encoder = _encoder_args(preset, hardware)
            rate = f"{st['fps_num']}/{st['fps_den']}"
            cmd = [ffmpeg_bin(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{st['width']}x{st['height']}",
                   "-r", rate, "-i", "-", *audio_in, "-map", "0:v", "-map", "1:a",
                   "-vf", "scale=out_color_matrix=bt709:out_range=tv", *vargs,
                   "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                   *preset["audio"], "-r", rate]
            if out.suffix.lower() in (".mp4", ".mov", ".m4v"):
                cmd += ["-movflags", "+faststart"]
            cmd.append(str(partial))
            log = open(tmp_dir / "ffmpeg.log", "wb")
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log, **NO_WINDOW)
            comp = Compositor(doc, sequence_id, warn)
            last_emit = 0.0
            tv = time.monotonic()  # fps/ETA from the video phase only (audio mixing came first)
            try:
                for i in range(n_frames):
                    if cancelled():
                        raise CommandError("CANCELLED", "export cancelled")
                    frame = comp.frame(a + i * tpf).convert("RGB")
                    try:
                        proc.stdin.write(frame.tobytes())
                    except (BrokenPipeError, OSError):
                        break
                    now = time.monotonic()
                    if now - last_emit > 0.25 or i == n_frames - 1:
                        last_emit = now
                        el = now - tv
                        fps = (i + 1) / el if el > 0 else 0
                        on_progress({"phase": "video", "progress": 0.05 + 0.93 * (i + 1) / n_frames, "frame": i + 1,
                                     "frames": n_frames, "fps": round(fps, 1),
                                     "eta_seconds": round((n_frames - i - 1) / fps, 1) if fps else None})
            finally:
                comp.close()
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                code = proc.wait()
                log.close()
            if code != 0:
                tail = (tmp_dir / "ffmpeg.log").read_text(encoding="utf-8", errors="replace").strip()[-500:]
                raise RenderError(f"encoder failed ({encoder}): {tail}")
        on_progress({"phase": "verify", "progress": 0.99})
        duration = (b - a) / TPS
        expected = {"duration_seconds": duration, "frame_rate": st["fps_num"] / st["fps_den"], "audio_stream_required": True,
                    "audio_sample_rate": st["sample_rate"], "minimum_file_size_bytes": 1}
        if preset["video"]:
            expected.update(video_stream_required=True, width=st["width"], height=st["height"])
        check = validate_media_probe(run_ffprobe(str(partial)), expected, file_size_bytes=partial.stat().st_size)
        if check["errors"]:
            raise RenderError("output failed verification: " + "; ".join(e["message"] for e in check["errors"]))
        os.replace(partial, out)
        return {"path": str(out), "preset": preset_id, "encoder": encoder, "range": {"start": a, "end": b, "seconds": duration},
                "frames": n_frames if preset["video"] else 0, "elapsed_seconds": round(time.monotonic() - t0, 2),
                "size_bytes": out.stat().st_size, "verification": check["summary"], "warnings": warnings + check["warnings"]}
    finally:
        for p in tmp_dir.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass


def export_frame(doc: dict, sequence_id: str, t: int, path: str | None = None, max_size: int | None = None):
    """Render one frame. Returns the PIL image (and writes it when ``path``)."""
    comp = Compositor(doc, sequence_id)
    try:
        img = comp.frame(t).convert("RGB")
    finally:
        comp.close()
    if max_size and max(img.size) > max_size:
        img.thumbnail((max_size, max_size))
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
    return img


# ---------------------------------------------------------------- render queue


class ExportQueue:
    """Premiere's render queue: one export at a time, progress as job events."""

    def __init__(self, emit: Callable[[dict], None]):
        self.emit = emit
        self.jobs: dict[str, dict] = {}
        self.cancel_flags: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.queue_lock = threading.Lock()

    def _update(self, job: dict, **changes) -> None:
        with self.lock:
            job.update(changes)
            snap = dict(job)
        self.emit({"type": "job", "job": snap})

    def submit(self, doc: dict, params: dict, wait: bool = False) -> dict:
        doc = copy.deepcopy(doc)  # render what the user saw when they hit Export
        job = {"id": uuid.uuid4().hex, "kind": "export", "media_id": None, "status": "queued", "error": None,
               "progress": 0.0, "phase": "queued", "path": params["path"], "sequence_id": params["sequence_id"],
               "preset": params.get("preset", "h264"), "result": None}
        self.jobs[job["id"]] = job
        flag = threading.Event()
        self.cancel_flags[job["id"]] = flag
        self.emit({"type": "job", "job": dict(job)})

        def run() -> None:
            with self.queue_lock:
                if flag.is_set():
                    self._update(job, status="cancelled", phase="cancelled")
                    return
                self._update(job, status="running")
                try:
                    result = export(doc, params["sequence_id"], params["path"], params.get("preset", "h264"), params.get("range"),
                                    params.get("start"), params.get("end"), params.get("hardware", "auto"),
                                    on_progress=lambda p: self._update(job, **p), cancelled=flag.is_set)
                    self._update(job, status="done", progress=1.0, phase="done", result=result, path=result["path"])
                except CommandError as exc:
                    if exc.code == "CANCELLED":
                        self._update(job, status="cancelled", phase="cancelled")
                    else:
                        self._update(job, status="failed", error=exc.message)
                except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                    self._update(job, status="failed", error=f"{type(exc).__name__}: {exc}")

        if wait:
            run()
        else:
            threading.Thread(target=run, name=f"lumina-export-{job['id'][:6]}", daemon=True).start()
        return dict(job)

    def cancel(self, job_id: str) -> dict:
        flag = self.cancel_flags.get(job_id)
        if flag is None:
            raise CommandError("NOT_FOUND", f"no export job {job_id}")
        flag.set()
        return dict(self.jobs[job_id])

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(j) for j in self.jobs.values()]

