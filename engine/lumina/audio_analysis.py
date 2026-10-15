"""Essential Sound analysis: audio type tagging and loudness (LUFS).

Auto-tag looks at the file name first (editors name their SFX / BGM files),
then at the sound itself:

- dialogue: the envelope is modulated at syllable rate (2-8 Hz) with pauses
  between phrases, energy centred in the voice band;
- ambience: steady and noise-like (high spectral flatness, little dynamics);
- sfx: short clips (under 5 s) or a single transient;
- music: everything sustained and tonal otherwise.

Loudness is integrated loudness (ITU-R BS.1770 / EBU R128) measured by
ffmpeg's ebur128 filter on the clip's source range.
"""

from __future__ import annotations

import re
import subprocess

import numpy as np

from .adb.quiet_process import NO_WINDOW
from .media.probe import ffmpeg_bin
from .timebase import TICKS_PER_SECOND as TPS

TAGS = ("dialogue", "music", "sfx", "ambience")

# Premiere's Essential Sound Auto-Match targets differ per type; dialogue at broadcast loudness
DEFAULT_TARGETS = {"dialogue": -23.0, "music": -25.0, "sfx": -21.0, "ambience": -30.0}

_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("sfx", ("sfx", "sound fx", "soundfx", "whoosh", "swoosh", "swish", "impact", "hit", "punch", "boom", "riser",
             "bell", "ding", "click", "pop", "transition", "glitch", "효과음", "효과", "뚜훅", "띠링", "효과 음")),
    ("music", ("bgm", "music", "song", "ost", "beat", "instrumental", " inst", "음악", "브금", "배경음악", "노래")),
    ("ambience", ("ambience", "ambient", "ambiance", "room tone", "roomtone", "atmos", "앰비언스", "환경음", "현장음")),
    ("dialogue", ("voice", "narration", "dialog", "interview", " vo", "_vo", "나레이션", "내레이션", "대사", "목소리", "인터뷰")),
]


def tag_from_name(name: str) -> str | None:
    n = " " + name.lower()
    for tag, words in _KEYWORDS:
        if any(w in n for w in words):
            return tag
    return None


def features(pcm: np.ndarray, rate: int) -> dict:
    """Mono float signal -> the measurements the classifier uses."""
    x = pcm.mean(axis=1) if pcm.ndim == 2 else pcm
    hop = rate // 50  # 20 ms frames
    n = len(x) // hop
    if n < 10:
        return {"duration": len(x) / rate, "silent": True}
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    loud = np.percentile(db, 95)
    if loud < -60:
        return {"duration": len(x) / rate, "silent": True}
    active = db > loud - 30
    pause_ratio = float(np.mean(db < loud - 25))
    # envelope modulation spectrum (50 Hz frame rate): share of 2-8 Hz energy among 0.5-20 Hz
    env = db - db.mean()
    spec = np.abs(np.fft.rfft(env * np.hanning(len(env)))) ** 2
    fr = np.fft.rfftfreq(len(env), 1 / 50)
    band = spec[(fr >= 2) & (fr <= 8)].sum()
    total = spec[(fr >= 0.5) & (fr <= 20)].sum() + 1e-12
    # spectral flatness and centroid on 2048-sample windows of the active part
    win = 2048
    flats, cents = [], []
    idx = np.flatnonzero(active)
    for i in idx[:: max(1, len(idx) // 60)]:
        seg = x[i * hop: i * hop + win]
        if len(seg) < win:
            continue
        p = np.abs(np.fft.rfft(seg * np.hanning(win))) ** 2 + 1e-12
        f = np.fft.rfftfreq(win, 1 / rate)
        keep = (f > 60) & (f < 8000)
        p, f = p[keep], f[keep]
        flats.append(float(np.exp(np.mean(np.log(p))) / np.mean(p)))
        cents.append(float((p * f).sum() / p.sum()))
    return {
        "duration": len(x) / rate,
        "silent": False,
        "pause_ratio": pause_ratio,
        "syllabic": float(band / total),
        "flatness": float(np.median(flats)) if flats else 0.0,
        "centroid": float(np.median(cents)) if cents else 0.0,
        "dynamics": float(np.std(db[active])) if active.any() else 0.0,
    }


def classify(f: dict) -> tuple[str | None, str]:
    """(tag, reason) from ``features``."""
    if f.get("silent"):
        return None, "silent"
    if f["duration"] < 5:
        return "sfx", f"short ({f['duration']:.1f} s)"
    if f["syllabic"] > 0.3 and f["pause_ratio"] > 0.12 and 250 < f["centroid"] < 3500:
        return "dialogue", f"syllable-rate modulation {f['syllabic']:.2f}, pauses {f['pause_ratio']:.0%}"
    if f["flatness"] > 0.3 and f["dynamics"] < 4:
        return "ambience", f"steady noise (flatness {f['flatness']:.2f})"
    return "music", f"sustained (pauses {f['pause_ratio']:.0%}, flatness {f['flatness']:.2f})"


def _input_args(path: str, src_start: int, src_dur: int) -> list[str]:
    return ["-ss", f"{max(0, src_start) / TPS:.6f}", "-t", f"{src_dur / TPS:.6f}", "-i", path, "-vn"]


def loudness(path: str, src_start: int, src_dur: int, channels: int = 2) -> float | None:
    """Integrated loudness (LUFS) of a source range; None for silence."""
    up = "pan=stereo|c0=c0|c1=c0," if channels == 1 else ""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", *_input_args(path, src_start, src_dur),
           "-af", f"{up}ebur128=framelog=quiet", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=3600, **NO_WINDOW)
    text = proc.stderr.decode("utf-8", "replace")
    m = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", text)
    if not m or m[-1] == "-inf":
        return None
    v = float(m[-1])
    return None if v <= -70 else v
