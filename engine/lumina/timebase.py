"""Time representation.

All document times are integer ticks at Premiere's tick rate. Every common
frame rate (23.976, 24, 25, 29.97, 30, 50, 59.94, 60) divides it exactly, so
frame <-> tick conversions are lossless and the values interoperate with the
ported adb-mcp planners (``lumina.adb.premiere_track_items.TICKS_PER_SECOND``).
"""

from __future__ import annotations

from fractions import Fraction

TICKS_PER_SECOND = 254_016_000_000


def fps(num: int, den: int) -> Fraction:
    return Fraction(num, den)


def ticks_per_frame(fps_num: int, fps_den: int) -> int:
    value = Fraction(TICKS_PER_SECOND * fps_den, fps_num)
    if value.denominator != 1:
        raise ValueError(f"frame rate {fps_num}/{fps_den} is not tick-exact")
    return value.numerator


def frames_to_ticks(frames: int, fps_num: int, fps_den: int) -> int:
    return frames * ticks_per_frame(fps_num, fps_den)


def ticks_to_frames(ticks: int, fps_num: int, fps_den: int) -> int:
    """Floor to the frame containing ``ticks``."""
    return ticks // ticks_per_frame(fps_num, fps_den)


def snap_to_frame(ticks: int, fps_num: int, fps_den: int) -> int:
    tpf = ticks_per_frame(fps_num, fps_den)
    return ((ticks + tpf // 2) // tpf) * tpf


def seconds_to_ticks(seconds: float | Fraction) -> int:
    return round(Fraction(seconds) * TICKS_PER_SECOND)


def ticks_to_seconds(ticks: int) -> float:
    return ticks / TICKS_PER_SECOND


def parse_rate(rate: str) -> tuple[int, int]:
    """Parse an ffprobe rate like ``30000/1001`` or ``25``."""
    if "/" in rate:
        num, den = rate.split("/", 1)
        n, d = int(num), int(den)
    else:
        f = Fraction(rate)
        n, d = f.numerator, f.denominator
    if d == 0 or n == 0:
        raise ValueError(f"invalid rate {rate!r}")
    f = Fraction(n, d)
    return f.numerator, f.denominator


def timecode(ticks: int, fps_num: int, fps_den: int) -> str:
    """Non-drop-frame HH:MM:SS:FF for display."""
    nominal = round(fps_num / fps_den)
    frames = ticks_to_frames(ticks, fps_num, fps_den)
    ff = frames % nominal
    total_seconds = frames // nominal
    return f"{total_seconds // 3600:02d}:{total_seconds // 60 % 60:02d}:{total_seconds % 60:02d}:{ff:02d}"
