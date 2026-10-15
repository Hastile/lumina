"""Audio effects DSP, shared definition for the export and the preview.

The preview (app/src/renderer/src/playback/AudioEngine.ts) builds Web Audio
nodes; the export runs ffmpeg filters with the same maths:

- Parametric Equalizer: Web Audio's BiquadFilterNode (low shelf, peaking,
  high shelf; Audio EQ Cookbook with S = 1). The export feeds the exact
  coefficients to ffmpeg's ``biquad`` filter, so both are the same filter.
- Single-band Compressor / Hard Limiter: Web Audio's DynamicsCompressorNode
  applies an automatic makeup gain; the preview cancels it (makeup_db), the
  export uses ``acompressor`` / ``alimiter`` with the same thresholds, so the
  two match in level (the envelope detectors differ slightly).
"""

from __future__ import annotations

import math


def _shelf_or_peak(kind: str, f: float, gain_db: float, q: float, fs: float) -> tuple[float, ...]:
    f = min(f, fs / 2 * 0.999)
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f / fs
    c, s = math.cos(w0), math.sin(w0)
    if kind == "peaking":
        al = s / (2 * q)
        return (1 + al * a, -2 * c, 1 - al * a, 1 + al / a, -2 * c, 1 - al / a)
    al = s / 2 * math.sqrt(2)  # shelf slope S = 1 (Web Audio)
    ra = 2 * math.sqrt(a) * al
    if kind == "lowshelf":
        return (a * ((a + 1) - (a - 1) * c + ra), 2 * a * ((a - 1) - (a + 1) * c), a * ((a + 1) - (a - 1) * c - ra),
                (a + 1) + (a - 1) * c + ra, -2 * ((a - 1) + (a + 1) * c), (a + 1) + (a - 1) * c - ra)
    return (a * ((a + 1) + (a - 1) * c + ra), -2 * a * ((a - 1) + (a + 1) * c), a * ((a + 1) + (a - 1) * c - ra),
            (a + 1) - (a - 1) * c + ra, 2 * ((a - 1) - (a + 1) * c), (a + 1) - (a - 1) * c - ra)


def compressor_makeup_db(threshold: float, ratio: float) -> float:
    """Web Audio's automatic makeup gain (spec: (1 / fullRangeGain) ^ 0.6), with knee 0."""
    full_range_db = threshold * (1 - 1 / ratio)  # gain applied to a 0 dBFS signal
    return -0.6 * full_range_db


def _biquad(coef: tuple[float, ...]) -> str:
    b0, b1, b2, a0, a1, a2 = coef
    return f"biquad=b0={b0!r}:b1={b1!r}:b2={b2!r}:a0={a0!r}:a1={a1!r}:a2={a2!r}"


def ffmpeg_filters(effects: list[dict], fs: int) -> list[str]:
    """ffmpeg audio filters for a clip's enabled audio effects, in stack order."""
    out: list[str] = []
    for e in effects:
        if not e.get("enabled", True):
            continue
        p = e["params"]
        if e["type"] == "parametric_eq":
            for kind, f, g, q in (("lowshelf", p["low_freq"], p["low_gain"], 1), ("peaking", p["mid_freq"], p["mid_gain"], p["mid_q"]),
                                  ("highshelf", p["high_freq"], p["high_gain"], 1)):
                if abs(g) > 1e-9:
                    out.append(_biquad(_shelf_or_peak(kind, f, g, q, fs)))
        elif e["type"] == "compressor":
            out.append(f"acompressor=threshold={10 ** (p['threshold'] / 20)!r}:ratio={p['ratio']!r}:attack={max(0.01, p['attack'])!r}"
                       f":release={max(0.01, p['release'])!r}:knee=1:makeup=1")
            if p["output_gain"]:
                out.append(f"volume={p['output_gain']}dB")
        elif e["type"] == "hard_limiter":
            if p["input_boost"]:
                out.append(f"volume={p['input_boost']}dB")
            lim = 10 ** (p["max_amplitude"] / 20)
            out.append(f"alimiter=limit={max(0.0625, lim)!r}:attack=1:release=50:level=0:latency=1")
    return out
