"""Pure planners for Premiere TrackItem Editing Pack v1.

The module contains no Premiere calls.  It validates selectors and builds exact
tick-based split/trim/ripple plans which the UXP backend re-validates immediately
before creating any Action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TICKS_PER_SECOND = 254_016_000_000


class EditPlanError(ValueError):
    """A fail-closed planning error with a stable public error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def strict_number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EditPlanError("INVALID_ARGUMENT", f"{name} must be numeric, not bool.")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise EditPlanError("INVALID_ARGUMENT", f"{name} must be finite.")
    if positive and number <= 0:
        raise EditPlanError("INVALID_ARGUMENT", f"{name} must be greater than zero.")
    return number


def seconds_to_ticks(value: Any, name: str = "seconds") -> int:
    return round(strict_number(value, name) * TICKS_PER_SECOND)


def exact_tick_value(*, seconds: Any = None, ticks: Any = None, name: str = "value") -> int:
    if (seconds is None) == (ticks is None):
        raise EditPlanError("INVALID_ARGUMENT", f"Exactly one of {name}_seconds or {name}_ticks is required.")
    if ticks is not None:
        if isinstance(ticks, bool) or not isinstance(ticks, (int, str)):
            raise EditPlanError("INVALID_ARGUMENT", f"{name}_ticks must be an integer or integer string.")
        try:
            return int(ticks)
        except (TypeError, ValueError):
            raise EditPlanError("INVALID_ARGUMENT", f"{name}_ticks is invalid.") from None
    return seconds_to_ticks(seconds, f"{name}_seconds")


def frame_ticks(timebase: Any) -> int:
    if isinstance(timebase, bool):
        raise EditPlanError("SEQUENCE_TIMEBASE_UNAVAILABLE", "Sequence timebase is invalid.")
    try:
        value = int(timebase)
    except (TypeError, ValueError):
        raise EditPlanError("SEQUENCE_TIMEBASE_UNAVAILABLE", "Sequence timebase is unreadable.") from None
    if value <= 0:
        raise EditPlanError("SEQUENCE_TIMEBASE_UNAVAILABLE", "Sequence timebase must be positive.")
    return value


def quantize_ticks(value: int, one_frame_ticks: int) -> int:
    """Round an exact tick value to the nearest sequence-frame boundary."""
    if one_frame_ticks <= 0:
        raise EditPlanError("SEQUENCE_TIMEBASE_UNAVAILABLE", "Sequence timebase must be positive.")
    quotient, remainder = divmod(int(value), one_frame_ticks)
    return (quotient + (1 if remainder * 2 >= one_frame_ticks else 0)) * one_frame_ticks


def selector(media_type: str, track_index: int, item_index: int, *,
             project_item_id: str | None = None, start_ticks: str | int | None = None,
             end_ticks: str | int | None = None) -> dict[str, Any]:
    if media_type not in {"video", "audio"}:
        raise EditPlanError("INVALID_ARGUMENT", "media_type must be video or audio.")
    for name, value in (("track_index", track_index), ("item_index", item_index)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EditPlanError("INVALID_ARGUMENT", f"{name} must be a non-negative integer.")
    return {
        "mediaType": media_type,
        "trackIndex": track_index,
        "itemIndex": item_index,
        "expectedProjectItemId": project_item_id,
        "expectedStartTicks": None if start_ticks is None else str(int(start_ticks)),
        "expectedEndTicks": None if end_ticks is None else str(int(end_ticks)),
    }


def find_item(state: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    tracks = state.get("videoTracks" if target["mediaType"] == "video" else "audioTracks") or []
    track = next((row for row in tracks if row.get("trackIndex") == target["trackIndex"]), None)
    if track is None:
        raise EditPlanError("TRACK_NOT_FOUND", "Track identity was not found.")
    items = track.get("items") or []
    if target["itemIndex"] >= len(items):
        raise EditPlanError("TRACK_ITEM_NOT_FOUND", "TrackItem index does not exist.")
    item = items[target["itemIndex"]]
    for key, expected in (
        ("projectItemId", target.get("expectedProjectItemId")),
        ("startTicks", target.get("expectedStartTicks")),
        ("endTicks", target.get("expectedEndTicks")),
    ):
        if expected is not None and str(item.get(key)) != str(expected):
            raise EditPlanError("STALE_TRACK_ITEM_STATE", f"TrackItem {key} changed since preflight.")
    return item


def _require_readable(item: dict[str, Any], *keys: str) -> None:
    unreadable = [key for key in keys if item.get(key) is None]
    if unreadable:
        raise EditPlanError("SOURCE_BOUNDS_UNVERIFIED", f"Unreadable TrackItem fields: {', '.join(unreadable)}")


def split_plan(item: dict[str, Any], split_ticks: int, one_frame_ticks: int) -> dict[str, Any]:
    _require_readable(item, "startTicks", "endTicks", "inPointTicks", "outPointTicks")
    start, end = int(item["startTicks"]), int(item["endTicks"])
    source_in, source_out = int(item["inPointTicks"]), int(item["outPointTicks"])
    split_ticks = quantize_ticks(split_ticks, one_frame_ticks)
    if split_ticks <= start or split_ticks >= end:
        raise EditPlanError("INVALID_SPLIT_POINT", "Split point must be strictly inside the TrackItem.")
    if split_ticks - start < one_frame_ticks or end - split_ticks < one_frame_ticks:
        raise EditPlanError("SPLIT_TOO_CLOSE_TO_EDGE", "Both split segments must be at least one frame long.")
    source_split = source_in + (split_ticks - start)
    if not source_in < source_split < source_out:
        raise EditPlanError("SPLIT_RESULT_MISMATCH", "Source interval cannot be split continuously.")
    return {
        "splitTicks": str(split_ticks),
        "left": {"startTicks": str(start), "endTicks": str(split_ticks), "inPointTicks": str(source_in), "outPointTicks": str(source_split)},
        "right": {"startTicks": str(split_ticks), "endTicks": str(end), "inPointTicks": str(source_split), "outPointTicks": str(source_out)},
        "cloneOffsetTicks": str(split_ticks - start),
        "durationPreserved": (split_ticks - start) + (end - split_ticks) == end - start,
        "sourceContinuous": source_split - source_in + source_out - source_split == source_out - source_in,
    }


def _find_safe_split_scratch_interval(
    sequence_snapshot: dict[str, Any], *, media_type: str, track_index: int,
    target_item: dict[str, Any], required_duration_ticks: int,
    safety_margin_frames: int = 2, protected_intervals: list[dict[str, Any]] | None = None,
    maximum_timeline_ticks: int | None = None,
) -> dict[str, Any]:
    """Find a non-overlapping clone interval for experimental split.

    Candidates follow the requested priority order and use exact ticks. The
    helper is pure: it allocates nothing and returns evidence for the caller's
    dry-run/readback record.
    """
    if required_duration_ticks <= 0 or safety_margin_frames < 0:
        raise EditPlanError("INVALID_ARGUMENT", "required_duration_ticks must be positive and safety_margin_frames non-negative.")
    one_frame = frame_ticks(sequence_snapshot.get("timebase"))
    margin = one_frame * safety_margin_frames
    tracks = sequence_snapshot.get("videoTracks" if media_type == "video" else "audioTracks") or []
    track = next((row for row in tracks if row.get("trackIndex") == track_index), None)
    if track is None:
        raise EditPlanError("TRACK_NOT_FOUND", "Track identity was not found.")
    occupied = []
    for item in track.get("items") or []:
        if item is target_item:
            occupied.append((int(item["startTicks"]), int(item["endTicks"]), "target"))
        else:
            occupied.append((int(item["startTicks"]), int(item["endTicks"]), "track_item"))
    protected = [(int(row["startTicks"]), int(row["endTicks"]), row.get("reason", "protected")) for row in (protected_intervals or [])]
    blocked = occupied + protected
    all_items = [item for rows in (sequence_snapshot.get("videoTracks") or []) + (sequence_snapshot.get("audioTracks") or []) for item in (rows.get("items") or [])]
    sequence_end = max([int(item.get("endTicks") or 0) for item in all_items] or [0])
    track_end = max([end for _start, end, _kind in occupied] or [0])

    def candidate(start: int, source: str) -> dict[str, Any]:
        return {"startTicks": start, "endTicks": start + required_duration_ticks, "source": source}

    candidates = [candidate(track_end + margin, "after_track_end"), candidate(sequence_end + margin, "after_sequence_end")]
    ordered = sorted((start, end) for start, end, _kind in blocked)
    cursor = margin
    for start, end in ordered:
        if start - cursor >= required_duration_ticks:
            candidates.append(candidate(cursor, "existing_empty_interval"))
        cursor = max(cursor, end + margin)
    candidates.append(candidate(cursor, "verified_gap"))

    rejected = []
    target_start, target_end = int(target_item["startTicks"]), int(target_item["endTicks"])
    for row in candidates:
        start, end = row["startTicks"], row["endTicks"]
        reasons = []
        if maximum_timeline_ticks is not None and end > maximum_timeline_ticks:
            reasons.append("timeline_limit")
        if start < target_end + margin and end > target_start - margin:
            reasons.append("target_proximity")
        collisions = [kind for b_start, b_end, kind in blocked if start < b_end and end > b_start]
        if collisions:
            reasons.append(f"collision:{','.join(collisions)}")
        if not reasons:
            return {
                "status": "available", "startTicks": str(start), "endTicks": str(end),
                "cloneOffsetTicks": str(start - target_start), "source": row["source"],
                "requiredDurationTicks": str(required_duration_ticks), "safetyMarginTicks": str(margin),
                "rejectedCandidates": rejected,
            }
        rejected.append({**row, "reasons": reasons})
    raise EditPlanError("SPLIT_SCRATCH_INTERVAL_UNAVAILABLE", f"No safe scratch interval; rejected {len(rejected)} candidate(s).")


def _simulate_split_scratch_allocation(
    sequence_snapshot: dict[str, Any], *, media_type: str, track_index: int,
    target_item: dict[str, Any], split_ticks: int, safety_margin_frames: int = 2,
    protected_intervals: list[dict[str, Any]] | None = None,
    maximum_timeline_ticks: int | None = None,
) -> dict[str, Any]:
    plan = split_plan(target_item, split_ticks, frame_ticks(sequence_snapshot.get("timebase")))
    allocation = _find_safe_split_scratch_interval(
        sequence_snapshot, media_type=media_type, track_index=track_index,
        target_item=target_item, required_duration_ticks=int(target_item["endTicks"]) - int(target_item["startTicks"]),
        safety_margin_frames=safety_margin_frames, protected_intervals=protected_intervals,
        maximum_timeline_ticks=maximum_timeline_ticks,
    )
    return {**plan, "splitStrategy": "dynamic_scratch", "scratchAllocation": allocation, "cloneOffsetTicks": allocation["cloneOffsetTicks"]}


def _split_track_item_with_dynamic_scratch(
    sequence_snapshot: dict[str, Any], *, media_type: str, track_index: int,
    target_item: dict[str, Any], split_ticks: int, safety_margin_frames: int = 2,
    protected_intervals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the experimental backend plan; the UXP caller owns mutation/readback."""
    return _simulate_split_scratch_allocation(
        sequence_snapshot, media_type=media_type, track_index=track_index,
        target_item=target_item, split_ticks=split_ticks,
        safety_margin_frames=safety_margin_frames, protected_intervals=protected_intervals,
    )


TRIM_MODES = {
    "trim_start_to", "trim_end_to", "trim_start_by", "trim_end_by",
    "set_source_in", "set_source_out", "set_source_range",
}


def trim_plan(item: dict[str, Any], mode: str, value_ticks: int | tuple[int, int], one_frame_ticks: int,
              *, media_duration_ticks: int | None = None) -> dict[str, Any]:
    if mode not in TRIM_MODES:
        raise EditPlanError("INVALID_ARGUMENT", f"Unsupported trim mode: {mode}")
    _require_readable(item, "startTicks", "endTicks", "inPointTicks", "outPointTicks")
    start, end = int(item["startTicks"]), int(item["endTicks"])
    source_in, source_out = int(item["inPointTicks"]), int(item["outPointTicks"])
    before = {"startTicks": start, "endTicks": end, "inPointTicks": source_in, "outPointTicks": source_out}
    after = dict(before)
    if mode == "set_source_range":
        if not isinstance(value_ticks, tuple) or len(value_ticks) != 2:
            raise EditPlanError("INVALID_ARGUMENT", "set_source_range requires two tick values.")
        normalized_value: int | tuple[int, int] = tuple(quantize_ticks(value, one_frame_ticks) for value in value_ticks)
    else:
        normalized_value = quantize_ticks(int(value_ticks), one_frame_ticks)
    if mode == "trim_start_to":
        after["startTicks"] = int(normalized_value)
        after["inPointTicks"] = source_in + after["startTicks"] - start
    elif mode == "trim_end_to":
        after["endTicks"] = int(normalized_value)
        after["outPointTicks"] = source_out + after["endTicks"] - end
    elif mode == "trim_start_by":
        after["startTicks"] = start + int(normalized_value)
        after["inPointTicks"] = source_in + int(normalized_value)
    elif mode == "trim_end_by":
        after["endTicks"] = end + int(normalized_value)
        after["outPointTicks"] = source_out + int(normalized_value)
    elif mode == "set_source_in":
        after["inPointTicks"] = int(normalized_value)
        after["startTicks"] = start + after["inPointTicks"] - source_in
    elif mode == "set_source_out":
        after["outPointTicks"] = int(normalized_value)
        after["endTicks"] = end + after["outPointTicks"] - source_out
    else:
        after["inPointTicks"], after["outPointTicks"] = map(int, normalized_value)
        after["startTicks"] = start + after["inPointTicks"] - source_in
        after["endTicks"] = end + after["outPointTicks"] - source_out
    if after["startTicks"] < 0:
        raise EditPlanError("INVALID_TRIM_RESULT", "Timeline start cannot be negative.")
    if after["endTicks"] - after["startTicks"] < one_frame_ticks:
        raise EditPlanError("INVALID_TRIM_RESULT", "Resulting timeline duration must be at least one frame.")
    if after["outPointTicks"] - after["inPointTicks"] < one_frame_ticks:
        raise EditPlanError("RIPPLE_SOURCE_RANGE_INVALID", "Resulting source duration must be at least one frame.")
    if after["inPointTicks"] < 0:
        raise EditPlanError("RIPPLE_SOURCE_RANGE_INVALID", "Source in-point cannot be negative.")
    if media_duration_ticks is not None and after["outPointTicks"] > media_duration_ticks:
        raise EditPlanError("RIPPLE_SOURCE_RANGE_INVALID", "Requested source out-point exceeds media duration.")
    changed = {key: str(value) for key, value in after.items() if value != before[key]}
    return {
        "mode": mode,
        "before": {key: str(value) for key, value in before.items()},
        "after": {key: str(value) for key, value in after.items()},
        "changed": changed,
        "inverse": {key: str(before[key]) for key in changed},
    }


def linked_candidates(state: dict[str, Any], item: dict[str, Any], media_type: str) -> dict[str, Any]:
    opposite = state.get("audioTracks" if media_type == "video" else "videoTracks") or []
    exact = []
    for track in opposite:
        for candidate in track.get("items") or []:
            if (candidate.get("projectItemId") and candidate.get("projectItemId") == item.get("projectItemId")
                    and candidate.get("startTicks") == item.get("startTicks")
                    and candidate.get("endTicks") == item.get("endTicks")):
                exact.append(candidate)
    confidence = "inferred_high" if len(exact) == 1 else ("unknown" if not exact else "inferred_low")
    return {"confidence": confidence, "items": exact}


def ensure_no_destination_collisions(moves: list[dict[str, Any]], stationary: list[dict[str, Any]]) -> None:
    """Validate already-projected intervals without assuming how they were selected."""
    for moved in moves:
        start, end = int(moved["startTicks"]), int(moved["endTicks"])
        for other in stationary:
            if moved.get("mediaType") != other.get("mediaType") or moved.get("trackIndex") != other.get("trackIndex"):
                continue
            other_start, other_end = int(other["startTicks"]), int(other["endTicks"])
            if start < other_end and end > other_start:
                raise EditPlanError("RIPPLE_DESTINATION_COLLISION", "A projected destination overlaps a stationary TrackItem.")


def ripple_plan(state: dict[str, Any], target: dict[str, Any], edge: str, new_boundary_ticks: int,
                one_frame_ticks: int, *, video_tracks: list[int] | None = None,
                audio_tracks: list[int] | None = None, media_duration_ticks: int | None = None) -> dict[str, Any]:
    if edge not in {"start", "end"}:
        raise EditPlanError("INVALID_ARGUMENT", "edge must be start or end.")
    item = find_item(state, target)
    mode = "trim_end_to" if edge == "end" else "trim_start_to"
    trim = trim_plan(item, mode, new_boundary_ticks, one_frame_ticks, media_duration_ticks=media_duration_ticks)
    old_boundary = int(item["endTicks"] if edge == "end" else item["startTicks"])
    delta = int(trim["after"]["endTicks" if edge == "end" else "startTicks"]) - old_boundary
    selected = {
        "video": set(video_tracks if video_tracks is not None else [row.get("trackIndex") for row in state.get("videoTracks") or []]),
        "audio": set(audio_tracks if audio_tracks is not None else [row.get("trackIndex") for row in state.get("audioTracks") or []]),
    }
    crossings, downstream = [], []
    for media_type, key in (("video", "videoTracks"), ("audio", "audioTracks")):
        for track in state.get(key) or []:
            track_index = track.get("trackIndex")
            if track_index not in selected[media_type]:
                continue
            for candidate in track.get("items") or []:
                is_target = media_type == target["mediaType"] and track_index == target["trackIndex"] and candidate.get("itemIndex") == target["itemIndex"]
                if is_target:
                    continue
                start, end = candidate.get("startTicks"), candidate.get("endTicks")
                if start is None or end is None:
                    raise EditPlanError("SOURCE_BOUNDS_UNVERIFIED", "A selected ripple TrackItem has unreadable bounds.")
                start, end = int(start), int(end)
                if start < old_boundary < end:
                    crossings.append({"mediaType": media_type, "trackIndex": track_index, **candidate})
                elif start >= old_boundary:
                    downstream.append({"mediaType": media_type, "trackIndex": track_index, **candidate, "shiftTicks": str(delta)})
    if crossings:
        code = "RIPPLE_CROSSING_ITEM"
        if any(row["mediaType"] == "audio" for row in crossings):
            code = "RIPPLE_BOUNDARY_OCCUPIED"
        raise EditPlanError(code, "A selected TrackItem crosses the ripple boundary.")
    # Check destination intervals per selected track, including target's new interval.
    moved_ids = {(row["mediaType"], row["trackIndex"], row["itemIndex"]) for row in downstream}
    for moved in downstream:
        new_start, new_end = int(moved["startTicks"]) + delta, int(moved["endTicks"]) + delta
        if new_start < 0:
            raise EditPlanError("RIPPLE_DESTINATION_COLLISION", "Ripple move would create negative timeline time.")
        key = "videoTracks" if moved["mediaType"] == "video" else "audioTracks"
        track = next(row for row in state.get(key) or [] if row.get("trackIndex") == moved["trackIndex"])
        for other in track.get("items") or []:
            identity = (moved["mediaType"], moved["trackIndex"], other.get("itemIndex"))
            if identity in moved_ids or other.get("itemIndex") == moved.get("itemIndex"):
                continue
            if (moved["mediaType"] == target["mediaType"] and moved["trackIndex"] == target["trackIndex"]
                    and other.get("itemIndex") == target["itemIndex"]):
                other_start, other_end = int(trim["after"]["startTicks"]), int(trim["after"]["endTicks"])
            else:
                other_start, other_end = int(other["startTicks"]), int(other["endTicks"])
            if new_start < other_end and new_end > other_start:
                raise EditPlanError("RIPPLE_DESTINATION_COLLISION", "A downstream destination collides with a stationary TrackItem.")
    return {"edge": edge, "deltaTicks": str(delta), "trim": trim, "downstream": downstream, "crossingItems": crossings}


def speed_plan(item: dict[str, Any], one_frame_ticks: int, *, speed_percent: Any = None,
               target_duration_seconds: Any = None, still_image: bool = False) -> dict[str, Any]:
    if (speed_percent is None) == (target_duration_seconds is None):
        raise EditPlanError("INVALID_SPEED", "Exactly one of speed_percent or target_duration_seconds is required.")
    if still_image:
        raise EditPlanError("INVALID_SPEED", "Still images do not support clip speed.")
    _require_readable(item, "startTicks", "endTicks", "inPointTicks", "outPointTicks")
    before_duration = int(item["endTicks"]) - int(item["startTicks"])
    if speed_percent is not None:
        speed = strict_number(speed_percent, "speed_percent", positive=True)
        target_duration = round(before_duration * 100.0 / speed)
    else:
        target_duration = seconds_to_ticks(strict_number(target_duration_seconds, "target_duration_seconds", positive=True))
        speed = before_duration * 100.0 / target_duration
    if target_duration < one_frame_ticks:
        raise EditPlanError("INVALID_TARGET_DURATION", "Target duration must be at least one frame.")
    return {
        "requestedSpeedPercent": speed,
        "beforeDurationTicks": str(before_duration),
        "expectedDurationTicks": str(target_duration),
        "expectedEndTicks": str(int(item["startTicks"]) + target_duration),
    }
