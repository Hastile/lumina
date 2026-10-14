"""Pure parsers for ffmpeg/ffprobe-based read-only video analysis (scene/black/freeze).

No subprocess calls here - only regex parsing of already-captured stdout/
stderr text and ffprobe JSON, so parsing logic is unit-testable without
ffmpeg installed. Mirrors premiere_audio_analysis.py's convention of keeping
planning/parsing free of I/O.
"""

from __future__ import annotations

import re
from typing import Any


class VideoAnalysisError(ValueError):
    """A fail-closed parsing error with a stable public error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --- scene detection ---------------------------------------------------
# ffmpeg's `select='gt(scene,T)',metadata=print` writes, per selected frame,
# a "frame:N pts:P pts_time:T" line to stderr immediately followed by a
# "lavfi.scene_score=X" line. The very first frame of the source never has
# a previous frame to compare against, so ffmpeg never emits a scene_score
# for it - the first event is always a real transition, not frame 0.
_SCENE_FRAME_RE = re.compile(r"frame:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*(-?[\d.]+)")
_SCENE_SCORE_RE = re.compile(r"lavfi\.scene_score=(-?[\d.]+)")


def parse_scene_metadata_output(stderr_text: str) -> list[dict[str, Any]]:
    """Parses ffmpeg's `select=gt(scene,T),metadata=print` stderr text into
    ordered scene-change events. A frame line with no following score line
    (a truncated/killed run) is dropped rather than guessed."""
    events: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for line in stderr_text.splitlines():
        frame_match = _SCENE_FRAME_RE.search(line)
        if frame_match:
            pending = {
                "frame": int(frame_match.group(1)),
                "ptsTicks": int(frame_match.group(2)),
                "ptsSeconds": float(frame_match.group(3)),
            }
            continue
        score_match = _SCENE_SCORE_RE.search(line)
        if score_match and pending is not None:
            pending["sceneScore"] = float(score_match.group(1))
            events.append(pending)
            pending = None
    return events


# --- black frame detection ----------------------------------------------
# ffmpeg's `blackdetect` filter normally prints "black_start:S black_end:E
# black_duration:D" on one stderr line once the black run ends. If the
# source ends while still black, only "black_start:S" is ever printed for
# that run (no matching black_end/black_duration) - reported here with
# endSeconds/durationSeconds as None rather than guessed.
_BLACK_LINE_RE = re.compile(
    r"black_start:\s*(-?[\d.]+)(?:\s+black_end:\s*(-?[\d.]+)\s+black_duration:\s*(-?[\d.]+))?"
)


def parse_blackdetect_output(stderr_text: str) -> list[dict[str, Any]]:
    intervals = []
    for match in _BLACK_LINE_RE.finditer(stderr_text):
        start = float(match.group(1))
        end = float(match.group(2)) if match.group(2) is not None else None
        duration = float(match.group(3)) if match.group(3) is not None else None
        intervals.append({"startSeconds": start, "endSeconds": end, "durationSeconds": duration})
    return intervals


# --- freeze-frame detection ----------------------------------------------
# ffmpeg's `freezedetect` filter prints three separate stderr lines per
# closed interval: "lavfi.freezedetect.freeze_start: S", then
# "...freeze_duration: D", then "...freeze_end: E". A trailing freeze_start
# with no matching duration/end (source ends while still frozen) is
# reported with endSeconds/durationSeconds as None.
_FREEZE_START_RE = re.compile(r"lavfi\.freezedetect\.freeze_start:\s*(-?[\d.]+)")
_FREEZE_DURATION_RE = re.compile(r"lavfi\.freezedetect\.freeze_duration:\s*(-?[\d.]+)")
_FREEZE_END_RE = re.compile(r"lavfi\.freezedetect\.freeze_end:\s*(-?[\d.]+)")


def parse_freezedetect_output(stderr_text: str) -> list[dict[str, Any]]:
    starts = [float(m.group(1)) for m in _FREEZE_START_RE.finditer(stderr_text)]
    durations = [float(m.group(1)) for m in _FREEZE_DURATION_RE.finditer(stderr_text)]
    ends = [float(m.group(1)) for m in _FREEZE_END_RE.finditer(stderr_text)]
    intervals = []
    for i, start in enumerate(starts):
        if i < len(durations) and i < len(ends):
            intervals.append({"startSeconds": start, "endSeconds": ends[i], "durationSeconds": durations[i]})
        else:
            intervals.append({"startSeconds": start, "endSeconds": None, "durationSeconds": None})
    return intervals


# --- failure classification ---------------------------------------------
# _run_ffmpeg_video_analysis previously never inspected the process return
# code at all (a real gap found in production pilot #1): a genuine decode/
# filter failure and a legitimately quiet video (zero scene changes) both
# produced RC!=0-or-0 with parseable-or-unparseable stderr, and both were
# treated identically as "success with N events". This classifier runs
# *after* parsing so it can tell "zero events, clean run" apart from
# "stderr never contained recognizable filter output at all".
_DECODE_FAILURE_PATTERNS = (
    "invalid data found when processing input",
    "could not find codec parameters",
    "error while decoding",
    "moov atom not found",
    "decoder failed",
    "invalid argument",
)
_FILTER_FAILURE_PATTERNS = (
    "no such filter",
    "error initializing filter",
    "unable to parse option value",
    "invalid filter",
    "failed to configure",
)


def classify_scene_analysis_failure(
    returncode: int | None, stderr_text: str, event_count: int
) -> dict[str, str] | None:
    """Pure classifier for one ffmpeg scene-detection pass. Returns None when
    the result should be trusted (including a legitimate zero-event result),
    or an error dict with a stable code otherwise. Never called on a timeout -
    that is classified as SCENE_ANALYSIS_TIMEOUT by the caller directly."""
    text = (stderr_text or "").lower()
    for pattern in _FILTER_FAILURE_PATTERNS:
        if pattern in text:
            return {"code": "SCENE_ANALYSIS_FILTER_FAILED", "message": stderr_text[-2000:]}
    for pattern in _DECODE_FAILURE_PATTERNS:
        if pattern in text:
            return {"code": "SCENE_ANALYSIS_DECODE_FAILED", "message": stderr_text[-2000:]}
    if returncode == 0:
        return None
    if not text.strip():
        return {"code": "SCENE_ANALYSIS_PARSE_FAILED", "message": "ffmpeg produced no stderr output to parse."}
    if event_count == 0:
        # Nonzero return code, no recognized failure pattern, and nothing
        # parsed - can't distinguish "no cuts" from "silently failed" here,
        # so fail closed rather than reporting a false SCENE_ANALYSIS_NO_EVENTS.
        return {"code": "SCENE_ANALYSIS_PARSE_FAILED", "message": stderr_text[-2000:]}
    # Nonzero return code but we still parsed real events and no known
    # failure pattern matched - ffmpeg's documented "legitimately exits
    # non-zero on some inputs while still printing the filter summary".
    return None


def _parse_rate_string(rate: Any) -> float | None:
    if not isinstance(rate, str) or "/" not in rate:
        return None
    numerator, _, denominator = rate.partition("/")
    try:
        numerator_value, denominator_value = float(numerator), float(denominator)
    except ValueError:
        return None
    if denominator_value == 0:
        return None
    return numerator_value / denominator_value


def summarize_video_media_info(probe_json: dict[str, Any]) -> dict[str, Any]:
    """Normalizes raw ffprobe JSON into the fields analyze_video_file needs:
    duration, primary video stream (codec/size/frame rate/time base/pixel
    format/rotation), and an audio stream summary. Pure/no I/O."""
    fmt = probe_json.get("format") or {}
    streams = probe_json.get("streams") or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    primary_video = video_streams[0] if video_streams else None

    rotation_degrees = None
    if primary_video is not None:
        tags = primary_video.get("tags") or {}
        if "rotate" in tags:
            try:
                rotation_degrees = float(tags["rotate"])
            except (TypeError, ValueError):
                rotation_degrees = None
        for side_data in primary_video.get("side_data_list") or []:
            if isinstance(side_data, dict) and "rotation" in side_data:
                try:
                    rotation_degrees = float(side_data["rotation"])
                except (TypeError, ValueError):
                    pass

    duration_raw = fmt.get("duration")
    try:
        duration_seconds = float(duration_raw) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration_seconds = None

    video_info = None
    if primary_video is not None:
        video_info = {
            "codecName": primary_video.get("codec_name"),
            "width": primary_video.get("width"),
            "height": primary_video.get("height"),
            "frameRate": _parse_rate_string(primary_video.get("r_frame_rate")),
            "averageFrameRate": _parse_rate_string(primary_video.get("avg_frame_rate")),
            "timeBase": primary_video.get("time_base"),
            "pixelFormat": primary_video.get("pix_fmt"),
            "rotationDegrees": rotation_degrees,
        }

    audio_info = None
    if audio_streams:
        first = audio_streams[0]
        sample_rate_raw = first.get("sample_rate")
        try:
            sample_rate = int(sample_rate_raw) if sample_rate_raw is not None else None
        except (TypeError, ValueError):
            sample_rate = None
        audio_info = {
            "codecName": first.get("codec_name"),
            "sampleRate": sample_rate,
            "channels": first.get("channels"),
            "channelLayout": first.get("channel_layout"),
            "streamCount": len(audio_streams),
        }

    return {
        "durationSeconds": duration_seconds,
        "videoStreamCount": len(video_streams),
        "video": video_info,
        "audio": audio_info,
    }


def build_video_analysis_command(
    ffmpeg_path: str, file_path: str, analysis: str, *,
    start_seconds: float | None, duration_seconds: float | None,
    scene_threshold: float, black_min_duration_seconds: float,
    black_pixel_threshold: float | None, black_picture_ratio: float | None,
) -> list[str]:
    """Builds an argv list (never a shell string) for one analysis pass."""
    if analysis == "scene":
        video_filter = f"select='gt(scene,{scene_threshold})',metadata=print"
    elif analysis == "black":
        params = [f"d={black_min_duration_seconds}"]
        if black_pixel_threshold is not None:
            params.append(f"pix_th={black_pixel_threshold}")
        if black_picture_ratio is not None:
            params.append(f"pic_th={black_picture_ratio}")
        video_filter = "blackdetect=" + ":".join(params)
    elif analysis == "freeze":
        video_filter = "freezedetect=n=0.001:d=2.0"
    else:
        raise VideoAnalysisError("VIDEO_ANALYSIS_FAILED", f"Unknown analysis pass: {analysis}")
    command = [ffmpeg_path, "-nostdin", "-v", "info"]
    if start_seconds is not None:
        command += ["-ss", str(start_seconds)]
    command += ["-i", file_path]
    if duration_seconds is not None:
        command += ["-t", str(duration_seconds)]
    command += ["-vf", video_filter, "-an", "-f", "null", "-"]
    return command
