"""Pure helpers for sequence export preparation and verified export/validate
flows: preset classification, ffprobe-result validation, and output-file
stabilization/checkpoint verification logic. No subprocess or filesystem
I/O lives here (mirrors premiere_video_analysis.py's convention) so all of
it is unit-testable without ffmpeg, Premiere, or a real disk.
"""

from __future__ import annotations

import copy
from pathlib import Path, PurePath
from typing import Any


class ExportError(ValueError):
    """A fail-closed export/validation error with a stable public error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


EXPORT_STATUSES = {
    "prepared", "exporting", "waiting_for_output", "validating",
    "completed", "failed", "timed_out", "cancelled",
}

PRESET_EXTENSIONS = (".epr",)


# --- preset classification -----------------------------------------------

def classify_preset_source(path: str) -> str:
    """Categorizes a preset file's source from its path alone: 'system'
    (under an Adobe Media Encoder/Premiere Pro program installation),
    'user' (under the current user's AppData/Documents), or 'other' (any
    other location, e.g. an explicit search_roots entry)."""
    normalized = str(PurePath(path)).lower()
    if "adobe media encoder" in normalized or ("adobe premiere pro" in normalized and "program files" in normalized):
        return "system"
    if "appdata" in normalized or "documents" in normalized or "\\users\\" in normalized or "/users/" in normalized:
        return "user"
    return "other"


def preset_entry(path: Path, *, exists: bool, size: int | None, modified: float | None) -> dict[str, Any]:
    return {
        "name": path.stem,
        "path": str(path),
        "fileExists": exists,
        "fileSizeBytes": size,
        "modifiedAt": modified,
        "sourceCategory": classify_preset_source(str(path)),
    }


# --- output-file stabilization --------------------------------------------

def is_size_history_stable(size_history: list[int], *, required_stable_polls: int = 2) -> bool:
    """True once the last `required_stable_polls` poll results are a
    positive, identical file size. A single positive-size poll is never
    treated as stable on its own - Premiere/AME may still be writing."""
    if required_stable_polls < 1:
        raise ExportError("INVALID_ARGUMENT", "required_stable_polls must be >= 1.")
    if len(size_history) < required_stable_polls:
        return False
    tail = size_history[-required_stable_polls:]
    return tail[0] > 0 and all(size == tail[0] for size in tail)


def checkpoint_verification(
    *, exists: bool, size_bytes: int | None, modified_at: float | None,
    before_size_bytes: int | None = None, before_modified_at: float | None = None,
) -> dict[str, Any]:
    """Pure decision of whether a project-save API's success return value is
    actually corroborated by the file on disk having grown/changed. Never
    trusts the save API's own boolean alone (PROJECT_CHECKPOINT_NOT_VERIFIED
    is the caller's job to raise when verified=False)."""
    if not exists:
        return {"verified": False, "reason": "Checkpoint file does not exist after save."}
    if size_bytes is None or size_bytes <= 0:
        return {"verified": False, "reason": "Checkpoint file exists but is empty or unreadable."}
    if before_size_bytes is not None and before_modified_at is not None:
        unchanged = size_bytes == before_size_bytes and modified_at == before_modified_at
        if unchanged:
            return {"verified": False, "reason": "Checkpoint file's size and modified time are unchanged from before the save call."}
    return {"verified": True, "reason": None}


# --- ffprobe-based export validation ---------------------------------------

def _find_streams(probe: dict[str, Any], codec_type: str) -> list[dict[str, Any]]:
    return [s for s in (probe.get("streams") or []) if s.get("codec_type") == codec_type]


def _parse_frame_rate(raw: str | None) -> float | None:
    if not raw:
        return None
    if "/" in raw:
        num, _, den = raw.partition("/")
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            return None
        return num_f / den_f if den_f else None
    try:
        return float(raw)
    except ValueError:
        return None


def summarize_probe(probe: dict[str, Any]) -> dict[str, Any]:
    """Extracts the fields validate_media_probe/callers care about from a raw
    ffprobe -show_format -show_streams JSON payload, without judging them."""
    fmt = probe.get("format") or {}
    video_streams = _find_streams(probe, "video")
    audio_streams = _find_streams(probe, "audio")
    primary_video = video_streams[0] if video_streams else None
    primary_audio = audio_streams[0] if audio_streams else None
    return {
        "container": fmt.get("format_name"),
        "durationSeconds": float(fmt["duration"]) if fmt.get("duration") is not None else None,
        "sizeBytes": int(fmt["size"]) if fmt.get("size") is not None else None,
        "bitRate": int(fmt["bit_rate"]) if fmt.get("bit_rate") is not None else None,
        "videoStreamCount": len(video_streams),
        "audioStreamCount": len(audio_streams),
        "video": None if primary_video is None else {
            "codec": primary_video.get("codec_name"),
            "width": primary_video.get("width"),
            "height": primary_video.get("height"),
            "pixelFormat": primary_video.get("pix_fmt"),
            "avgFrameRate": _parse_frame_rate(primary_video.get("avg_frame_rate")),
            "rFrameRate": _parse_frame_rate(primary_video.get("r_frame_rate")),
        },
        "audio": None if primary_audio is None else {
            "codec": primary_audio.get("codec_name"),
            "sampleRate": int(primary_audio["sample_rate"]) if primary_audio.get("sample_rate") is not None else None,
            "channels": primary_audio.get("channels"),
            "channelLayout": primary_audio.get("channel_layout"),
        },
        "tags": fmt.get("tags") or {},
    }


def validate_media_probe(probe: dict[str, Any], expected: dict[str, Any] | None, *, file_size_bytes: int | None = None) -> dict[str, Any]:
    """Pure comparison of a summarized ffprobe result against caller-supplied
    expectations. Only checks fields the caller actually specified in
    `expected` - never invents an expected codec/frame-rate from a preset
    name. Returns {"errors": [...], "warnings": [...], "summary": {...}}."""
    expected = expected or {}
    summary = summarize_probe(probe)
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []

    min_size = expected.get("minimum_file_size_bytes")
    if min_size is not None and file_size_bytes is not None and file_size_bytes < min_size:
        errors.append({"code": "EXPORT_OUTPUT_EMPTY", "message": f"Output file is {file_size_bytes} bytes, below the required minimum of {min_size}."})

    duration_expected = expected.get("duration_seconds")
    if duration_expected is not None:
        if summary["durationSeconds"] is None:
            errors.append({"code": "EXPORT_DURATION_MISMATCH", "message": "ffprobe reported no duration."})
        else:
            tolerance_frames = expected.get("duration_tolerance_frames", 1)
            frame_rate = expected.get("frame_rate") or (summary["video"] or {}).get("avgFrameRate") or 30.0
            tolerance_seconds = tolerance_frames / frame_rate if frame_rate else 0.0
            delta = abs(summary["durationSeconds"] - duration_expected)
            if delta > tolerance_seconds:
                errors.append({
                    "code": "EXPORT_DURATION_MISMATCH",
                    "message": f"Expected duration {duration_expected:.3f}s +/-{tolerance_seconds:.3f}s, got {summary['durationSeconds']:.3f}s (delta {delta:.3f}s).",
                })

    if expected.get("video_stream_required"):
        if summary["videoStreamCount"] == 0:
            errors.append({"code": "EXPORT_VIDEO_STREAM_MISSING", "message": "Expected at least one video stream; found none."})
        else:
            video = summary["video"] or {}
            width_expected, height_expected = expected.get("width"), expected.get("height")
            if width_expected is not None and video.get("width") != width_expected:
                errors.append({"code": "EXPORT_DIMENSION_MISMATCH", "message": f"Expected width {width_expected}, got {video.get('width')}."})
            if height_expected is not None and video.get("height") != height_expected:
                errors.append({"code": "EXPORT_DIMENSION_MISMATCH", "message": f"Expected height {height_expected}, got {video.get('height')}."})

    if expected.get("audio_stream_required"):
        if summary["audioStreamCount"] == 0:
            errors.append({"code": "EXPORT_AUDIO_STREAM_MISSING", "message": "Expected at least one audio stream; found none."})
        else:
            audio = summary["audio"] or {}
            sample_rate_expected = expected.get("audio_sample_rate")
            if sample_rate_expected is not None and audio.get("sampleRate") != sample_rate_expected:
                errors.append({"code": "EXPORT_SAMPLE_RATE_MISMATCH", "message": f"Expected audio sample rate {sample_rate_expected}, got {audio.get('sampleRate')}."})

    container_expected = expected.get("container")
    if container_expected is not None and summary["container"]:
        names = [n.strip().lower() for n in summary["container"].split(",")]
        if container_expected.lower() not in names:
            warnings.append(f"Requested container {container_expected!r} not found among ffprobe format_name values {summary['container']!r}.")

    return {"errors": errors, "warnings": warnings, "summary": summary}


# --- split-destination collision preflight ---------------------------------

def predict_split_destination_collision(
    *, item_start_ticks: int, item_end_ticks: int, split_ticks: int,
    other_item_intervals: list[tuple[int, int]],
) -> bool:
    """Reproduces splitTrackItemCore's own collision check (track-item-
    editing.js) at plan time, without touching Premiere: the clone used to
    perform a split is placed at [split_ticks, split_ticks + (item_end_ticks
    - item_start_ticks)) - i.e. the *entire original item's duration*,
    shifted forward to start at the split point - so it collides with any
    other item whose interval overlaps that destination range, even though
    the split conceptually only needs the (item_end_ticks - split_ticks)
    tail. Returns True if any other_item_intervals entry would collide."""
    if not (item_start_ticks < split_ticks < item_end_ticks):
        raise ExportError("INVALID_ARGUMENT", "split_ticks must be strictly inside the item interval.")
    offset = split_ticks - item_start_ticks
    destination_start = split_ticks
    destination_end = item_end_ticks + offset
    for other_start, other_end in other_item_intervals:
        if other_start < destination_end and other_end > destination_start:
            return True
    return False


def build_track_item_snapshot(edit_state: dict[str, Any]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """Builds a per-track (mediaType, trackIndex) -> sorted item-interval list
    snapshot from a getTrackItemEditState payload, for feeding into
    simulate_plan_split_collisions. Items without readable start/end ticks
    are skipped (never guessed)."""
    snapshot: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for media_type, tracks_key in (("video", "videoTracks"), ("audio", "audioTracks")):
        for track in edit_state.get(tracks_key) or []:
            track_index = track.get("trackIndex")
            items = []
            for item in track.get("items") or []:
                start, end = item.get("startTicks"), item.get("endTicks")
                if start is None or end is None:
                    continue
                items.append({"projectItemId": item.get("projectItemId"), "startTicks": int(start), "endTicks": int(end)})
            items.sort(key=lambda entry: entry["startTicks"])
            snapshot[(media_type, track_index)] = items
    return snapshot


def _find_item_containing_tick(items: list[dict[str, Any]], tick: int) -> dict[str, Any] | None:
    for item in items:
        if item["startTicks"] <= tick < item["endTicks"]:
            return item
    return None


def simulate_plan_split_collisions(
    operations: list[dict[str, Any]], track_snapshot: dict[tuple[str, int], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Preflight-simulates a timeline edit plan's operations (in their own
    already-established execution order - candidates ordered latest-
    sequence-time-first, see edit_plan.flatten_operations) against a live
    per-track item-interval snapshot, WITHOUT touching Premiere, to predict
    which split_track_item operations would hit SPLIT_DESTINATION_COLLISION
    at real execution time - specifically the case where an earlier-
    processed (later-in-time) candidate's ripple-delete has already closed
    the gap a not-yet-processed (earlier-in-time) split's clone destination
    needs. This mirrors the real executor's own processing order exactly, so
    a predicted collision here is the same collision execute_timeline_edit_plan
    would hit later - just caught before any mutation happens.

    remove_track_item (rippleDelete=True) shifts every later item on that
    track backward by the removed duration; trim_track_item in this pack's
    v1 (trim_start_to/trim_end_to point edits, never ripple) only adjusts the
    trimmed item's own bound and does not shift siblings. Returns
    {"collisions": [{"operationId", "candidateId", "reason"}], "warnings": [...]}
    - never mutates the caller's track_snapshot (works on a deep copy)."""
    snapshot = copy.deepcopy(track_snapshot)
    collisions: list[dict[str, Any]] = []
    warnings: list[str] = []

    for op in operations:
        op_type = op.get("type")
        selector = op.get("targetSelector") or {}
        key = (selector.get("mediaType"), selector.get("trackIndex"))
        items = snapshot.get(key)
        if items is None:
            continue
        params = op.get("parameters") or {}

        if op_type == "split_track_item":
            raw_split = params.get("atSequenceTicks")
            if raw_split is None:
                continue
            split_ticks = int(raw_split)
            target_item = _find_item_containing_tick(items, split_ticks)
            if target_item is None:
                warnings.append(f"{op.get('operationId')}: split point {split_ticks} was not found inside any simulated item on this track; preflight skipped for this operation.")
                continue
            others = [(i["startTicks"], i["endTicks"]) for i in items if i is not target_item]
            try:
                collided = predict_split_destination_collision(
                    item_start_ticks=target_item["startTicks"], item_end_ticks=target_item["endTicks"],
                    split_ticks=split_ticks, other_item_intervals=others,
                )
            except ExportError:
                collided = False
            if collided:
                collisions.append({
                    "operationId": op.get("operationId"), "candidateId": op.get("candidateId"),
                    "reason": "SPLIT_DESTINATION_COLLISION predicted: this split's clone destination would overlap another item on this track, after simulating prior candidates' effect in their real execution order.",
                })
            items.remove(target_item)
            items.append({"projectItemId": target_item["projectItemId"], "startTicks": target_item["startTicks"], "endTicks": split_ticks})
            items.append({"projectItemId": target_item["projectItemId"], "startTicks": split_ticks, "endTicks": target_item["endTicks"]})
            items.sort(key=lambda entry: entry["startTicks"])

        elif op_type == "remove_track_item":
            interval = params.get("removedInterval") or {}
            start, end = interval.get("startTicks"), interval.get("endTicks")
            if start is None or end is None:
                continue
            start, end = int(start), int(end)
            removed_duration = end - start
            new_items = []
            for item in items:
                if item["startTicks"] >= start and item["endTicks"] <= end:
                    continue
                if item["startTicks"] >= end:
                    new_items.append({**item, "startTicks": item["startTicks"] - removed_duration, "endTicks": item["endTicks"] - removed_duration})
                else:
                    new_items.append(item)
            snapshot[key] = sorted(new_items, key=lambda entry: entry["startTicks"])

        elif op_type == "trim_track_item":
            mode, raw_value = params.get("mode"), params.get("valueTicks")
            if raw_value is None or mode not in ("trim_start_to", "trim_end_to"):
                continue
            value_ticks = int(raw_value)
            tick_hint = value_ticks
            target_item = next((i for i in items if i["startTicks"] <= tick_hint <= i["endTicks"]), None)
            if target_item is None:
                continue
            if mode == "trim_start_to":
                target_item["startTicks"] = value_ticks
            else:
                target_item["endTicks"] = value_ticks

    return {"collisions": collisions, "warnings": warnings}
