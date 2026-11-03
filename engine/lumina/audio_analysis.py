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


# ---------------------------------------------------------------- Auto-Match measurement

# ITU-R BS.1770 K-weighting at 48 kHz: high shelf (head) then RLB high-pass
_K_SHELF = (1.53512485958697, -2.69169618940638, 1.19839281085285, 1.0, -1.69065929318241, 0.73248077421585)
_K_HPF = (1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621)
_RATE = 48000


def _lufs(power):
    return -0.691 + 10 * np.log10(np.maximum(power, 1e-20))


def _power(lufs: float) -> float:
    return 10 ** ((lufs + 0.691) / 10)


def _true_peak(x: np.ndarray) -> float:
    """Inter-sample peak (4x oversampled around the loudest samples), linear."""
    a = np.abs(x).max(axis=1)
    if not a.size:
        return 0.0
    peak = float(a.max())
    n = 64
    for i in np.argsort(a)[-16:]:
        lo, hi = max(0, i - n), min(len(a), i + n)
        if hi - lo < 8:
            continue
        for ch in range(x.shape[1]):
            seg = x[lo:hi, ch].astype(np.float64)
            up = np.fft.irfft(np.fft.rfft(seg), len(seg) * 4) * 4
            # the window's edges ring; only the middle half is trusted
            peak = max(peak, float(np.abs(up[len(up) // 4: 3 * len(up) // 4]).max()))
    return peak


def measure_pcm(raw: np.ndarray, weighted: np.ndarray, rate: int = _RATE) -> dict:
    """Loudness measurements of a stereo clip (BS.1770 / EBU R128) from its raw and K-weighted signals."""
    dur = len(raw) / rate
    hop = rate // 10  # 100 ms
    nh = len(weighted) // hop
    out = {"duration": dur, "integrated": None, "momentary_max": None, "short_max": None, "lra": None,
           "noise_floor": None, "active": 0.0, "sample_peak": None, "true_peak": None}
    if raw.size:
        sp = float(np.abs(raw).max())
        out["sample_peak"] = float(20 * np.log10(sp)) if sp > 0 else None
        tp = _true_peak(raw)
        out["true_peak"] = float(20 * np.log10(tp)) if tp > 0 else None
    if nh == 0:
        return out
    e = (weighted[: nh * hop].astype(np.float64) ** 2).sum(axis=1).reshape(nh, hop).mean(axis=1)
    m = np.convolve(e, np.ones(4) / 4, "valid") if nh >= 4 else np.array([e.mean()])
    out["momentary_max"] = float(_lufs(m.max()))
    if nh >= 30:
        s = np.convolve(e, np.ones(30) / 30, "valid")
        out["short_max"] = float(_lufs(s.max()))
        s = s[s > _power(-70)]
        if len(s):
            s = s[s > _power(float(_lufs(s.mean())) - 20)]
            ls = _lufs(s)
            out["lra"] = float(np.percentile(ls, 95) - np.percentile(ls, 10))
    gated = m[m > _power(-70)]
    if len(gated):
        gated = gated[gated > _power(float(_lufs(gated.mean())) - 10)]
        out["integrated"] = float(_lufs(gated.mean()))
        out["active"] = len(gated) * 0.1 if nh >= 4 else dur
    out["noise_floor"] = float(_lufs(max(float(np.percentile(m, 10)), _power(-90))))
    return out


def measure(path: str, src_start: int, src_dur: int, speed: float = 1.0, channels: int = 2,
            filters: list[str] | tuple = ()) -> dict:
    """Decode a clip's source range as it plays (speed, then Audio Gain and effects in ``filters``) and measure it."""
    from .render.export import _atempo

    up = ["pan=stereo|c0=c0|c1=c0"] if channels == 1 else []
    pre = ",".join(up + _atempo(speed) + [f"aresample={_RATE}", "aformat=channel_layouts=stereo"] + list(filters))
    kw = ",".join(f"biquad=b0={c[0]!r}:b1={c[1]!r}:b2={c[2]!r}:a0={c[3]!r}:a1={c[4]!r}:a2={c[5]!r}" for c in (_K_SHELF, _K_HPF))
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", *_input_args(path, src_start, src_dur),
           "-af", f"{pre},asplit=2[r][k];[k]{kw}[w];[r][w]amerge=inputs=2", "-ar", str(_RATE), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=3600, **NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[-300:] or "audio decode failed")
    pcm = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 4)
    return measure_pcm(pcm[:, :2], pcm[:, 2:])


# ---------------------------------------------------------------- Auto-Match decisions

MATCH_DEFAULTS = {
    "music_bed_db": 12.0,  # music under dialogue sits this many LU below the dialogue target
    "peak_ceiling": -1.0,  # dBTP no clip may exceed after its gain
    "max_boost": 20.0,  # beyond this the clip is near-silent or the wrong source
    "outlier_lu": 6.0,  # a cut this far from its source's level is pulled halfway back
    "keep_within": 0.5,  # changes smaller than this are not worth touching
}

NOTES = ("silent", "bed", "short", "outlier", "peak_limited", "boost_capped", "noisy", "kept", "compressed")


def level_of(tag: str, m: dict) -> tuple[float | None, str]:
    """The loudness a clip is matched on: integrated (gated) loudness, as Premiere's Auto-Match measures
    it, for every type; a clip with under a second of gated sound (a click, a hit) has no stable integrated
    value and is matched on its loudest moment instead."""
    if m.get("integrated") is None or m.get("active", 0) < 1.0:
        if m.get("duration", 0) >= 3 and m.get("short_max") is not None:
            return m["short_max"], "short_max"
        return m.get("momentary_max"), "momentary_max"
    return m["integrated"], "integrated"


def plan_match(items: list[dict], targets: dict[str, float], opts: dict | None = None) -> dict[str, dict]:
    """Decide each clip's Audio Gain.

    ``items``: {id, tag, group, gain_db (current), bed (music under dialogue), m (measurement at 0 dB gain,
    effects included)}. Clips of one ``group`` (same source, type and processing) share one gain, so a
    recording cut into pieces keeps its natural dynamics; a piece far off its group's level is pulled
    halfway back. The gain is then held under the true-peak ceiling (group-wide when that costs at most
    3 dB, else only the offending clips) and under ``max_boost``; tiny changes keep the current gain."""
    o = {**MATCH_DEFAULTS, **(opts or {})}
    out: dict[str, dict] = {}
    groups: dict[object, list[dict]] = {}
    for it in items:
        lvl, metric = level_of(it["tag"], it["m"])
        if lvl is None or lvl < -69:
            out[it["id"]] = {"tag": it["tag"], "lufs": None, "gain_db": it["gain_db"], "notes": ["silent"]}
            continue
        groups.setdefault(it["group"], []).append(dict(it, level=lvl, metric=metric))
    for members in groups.values():
        tag = members[0]["tag"]
        bed = bool(members[0].get("bed")) and o["music_bed_db"] is not None
        target = min(targets[tag], targets["dialogue"] - o["music_bed_db"]) if bed else targets[tag]
        w = [max(0.1, x["m"].get("active") or x["m"].get("duration") or 0.1) for x in members]
        level = float(_lufs(sum(wi * _power(x["level"]) for wi, x in zip(w, members)) / sum(w)))
        notes = {x["id"]: (["bed"] if bed else []) + (["short"] if x["metric"] != "integrated" else [])
                 for x in members}
        gains = {}
        for x in members:
            dev = x["level"] - level
            g = target - level
            if abs(dev) > o["outlier_lu"]:
                g -= float(np.sign(dev)) * (abs(dev) - o["outlier_lu"]) * 0.5
                notes[x["id"]].append("outlier")
            gains[x["id"]] = g
        room = {x["id"]: o["peak_ceiling"] - x["m"]["true_peak"] - gains[x["id"]] for x in members if x["m"].get("true_peak") is not None}
        worst = min(room.values(), default=0.0)
        if worst < 0:
            for i, r in room.items():
                cut = worst if -worst <= 3 else min(0.0, r)  # cheap: the whole group, so it stays consistent
                if cut < 0:
                    gains[i] += cut
                    notes[i].append("peak_limited")
        for x in members:
            i, m = x["id"], x["m"]
            g = gains[i]
            if g > o["max_boost"]:
                g = o["max_boost"]
                notes[i].append("boost_capped")
            if g > 6 and tag in ("dialogue", "music") and m.get("integrated") is not None and m.get("noise_floor") is not None \
                    and m["integrated"] - m["noise_floor"] < 15:
                notes[i].append("noisy")
            g = round(max(-96.0, min(96.0, g)), 1)
            if abs(g - x["gain_db"]) < o["keep_within"]:
                g = x["gain_db"]
                notes[i].append("kept")
            out[i] = {"tag": tag, "lufs": round(x["level"], 1), "metric": x["metric"], "target": round(target, 1),
                      "result": round(x["level"] + g, 1), "gain_db": g, "group": len(members), "notes": notes[i]}
    return out

