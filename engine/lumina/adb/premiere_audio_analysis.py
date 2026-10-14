"""Pure parsers for ffmpeg/ffprobe-based read-only audio analysis.

No subprocess calls here - only regex parsing of already-captured stdout/
stderr text, so parsing logic is unit-testable without ffmpeg installed.
Mirrors premiere_track_items.py/premiere_property_keyframes.py's convention
of keeping planning/parsing free of I/O.
"""

from __future__ import annotations

import math
import re
from typing import Any


class AudioAnalysisError(ValueError):
    """A fail-closed parsing error with a stable public error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _to_db_or_none(text: str) -> float | None:
    """ffmpeg prints "-inf" for true digital silence - never surfaced as a
    JSON -Infinity number, always null (matches the audio pack's existing
    silence convention: true silence is a well-defined null/0.0, not -Infinity)."""
    text = text.strip().lower()
    if text in ("-inf", "-infinity"):
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


_PEAK_RE = re.compile(r"Peak level dB:\s*(-inf|-?[\d.]+)", re.IGNORECASE)
_RMS_RE = re.compile(r"RMS level dB:\s*(-inf|-?[\d.]+)", re.IGNORECASE)


def parse_astats_output(stderr_text: str) -> dict[str, Any]:
    """Parses ffmpeg's `astats` filter stderr text. Uses the last (Overall)
    match for each metric, since per-channel blocks precede the Overall
    summary and Overall is the value callers actually want."""
    peaks = _PEAK_RE.findall(stderr_text)
    rms = _RMS_RE.findall(stderr_text)
    if not peaks and not rms:
        raise AudioAnalysisError("AUDIO_ANALYSIS_PARSE_FAILED", "astats output contained no Peak/RMS level lines.")
    return {
        "peakDbFS": _to_db_or_none(peaks[-1]) if peaks else None,
        "rms": _to_db_or_none(rms[-1]) if rms else None,
    }


_INTEGRATED_RE = re.compile(r"^\s*I:\s*(-inf|-?[\d.]+)\s*LUFS", re.IGNORECASE | re.MULTILINE)
_LRA_RE = re.compile(r"^\s*LRA:\s*(-inf|-?[\d.]+)\s*LU\b", re.IGNORECASE | re.MULTILINE)
_TRUE_PEAK_RE = re.compile(r"True peak:\s*\r?\n\s*Peak:\s*(-inf|-?[\d.]+)\s*dBFS", re.IGNORECASE)


def parse_ebur128_output(stderr_text: str) -> dict[str, Any]:
    """Parses ffmpeg's `ebur128` filter summary block (printed once at the
    end of the run, after the per-frame lines this does not attempt to parse)."""
    summary_start = stderr_text.rfind("Integrated loudness:")
    summary_text = stderr_text[summary_start:] if summary_start != -1 else stderr_text
    integrated = _INTEGRATED_RE.search(summary_text)
    lra = _LRA_RE.search(summary_text)
    true_peak = _TRUE_PEAK_RE.search(summary_text)
    if not integrated:
        raise AudioAnalysisError("AUDIO_ANALYSIS_PARSE_FAILED", "ebur128 output did not contain an Integrated loudness summary.")
    return {
        "integratedLufs": _to_db_or_none(integrated.group(1)),
        "loudnessRange": _to_db_or_none(lra.group(1)) if lra else None,
        "truePeakDbTP": _to_db_or_none(true_peak.group(1)) if true_peak else None,
    }


_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)\s*\|\s*silence_duration:\s*(-?[\d.]+)")


def parse_silencedetect_output(stderr_text: str) -> list[dict[str, float]]:
    """Parses ffmpeg's `silencedetect` filter stderr lines into closed
    intervals. A trailing silence_start with no matching silence_end (the
    source ends while still silent) is reported with endSeconds=None rather
    than guessed."""
    starts = [float(m.group(1)) for m in _SILENCE_START_RE.finditer(stderr_text)]
    ends = [(float(m.group(1)), float(m.group(2))) for m in _SILENCE_END_RE.finditer(stderr_text)]
    intervals = []
    for i, start in enumerate(starts):
        if i < len(ends):
            end, duration = ends[i]
            intervals.append({"startSeconds": start, "endSeconds": end, "durationSeconds": duration})
        else:
            intervals.append({"startSeconds": start, "endSeconds": None, "durationSeconds": None})
    return intervals


def build_analysis_command(ffmpeg_path: str, file_path: str, analysis: str, *,
                            start_seconds: float | None, duration_seconds: float | None,
                            silence_threshold_db: float, silence_min_duration_seconds: float) -> list[str]:
    """Builds an argv list (never a shell string) for one analysis pass."""
    if analysis == "peak_rms":
        audio_filter = "astats=metadata=0:reset=0"
    elif analysis == "ebur128":
        audio_filter = "ebur128=peak=true"
    elif analysis == "silence":
        audio_filter = f"silencedetect=noise={silence_threshold_db}dB:d={silence_min_duration_seconds}"
    else:
        raise AudioAnalysisError("INVALID_ANALYSIS_RANGE", f"Unknown analysis pass: {analysis}")
    command = [ffmpeg_path, "-nostdin", "-v", "info"]
    if start_seconds is not None:
        command += ["-ss", str(start_seconds)]
    command += ["-i", file_path]
    if duration_seconds is not None:
        command += ["-t", str(duration_seconds)]
    command += ["-af", audio_filter, "-f", "null", "-"]
    return command


def sanitize_for_json(value: Any) -> tuple[Any, bool]:
    """Recursively replaces non-finite floats with None. Returns (value, replaced)."""
    if isinstance(value, float):
        if math.isfinite(value):
            return value, False
        return None, True
    if isinstance(value, dict):
        replaced = False
        out = {}
        for key, item in value.items():
            new_item, item_replaced = sanitize_for_json(item)
            out[key] = new_item
            replaced = replaced or item_replaced
        return out, replaced
    if isinstance(value, list):
        replaced = False
        out = []
        for item in value:
            new_item, item_replaced = sanitize_for_json(item)
            out.append(new_item)
            replaced = replaced or item_replaced
        return out, replaced
    return value, False
