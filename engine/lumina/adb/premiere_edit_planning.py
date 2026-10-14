"""Pure planners for the Silence/Scene/Black Edit Planning Pack v1.

No Premiere calls and no subprocess calls here - only tick-exact interval
math over already-fetched TrackItem snapshots (from inspect_sequence_tracks)
and already-parsed ffmpeg analysis events. Mirrors premiere_track_items.py's
convention of keeping planning logic free of I/O so it is unit-testable
without a live Premiere/ffmpeg connection.
"""

from __future__ import annotations

from typing import Any

from .premiere_track_items import EditPlanError, TICKS_PER_SECOND, quantize_ticks, seconds_to_ticks

_SPEED_EPSILON = 1e-6


def ticks_to_seconds(ticks: int) -> float:
    return int(ticks) / TICKS_PER_SECOND


def is_normal_speed(speed: Any) -> bool:
    """True only when speed is a confirmed, readable 1.0 (Premiere's getSpeed
    is a ratio, not a percentage - live-confirmed on Premiere 26.3: an
    unmodified TrackItem's getSpeed() returns 1, not 100). None (unreadable)
    or any other value is NOT treated as normal - callers must fail closed
    rather than silently apply a wrong 1:1 mapping to a speed-ramped or
    unreadable-speed TrackItem."""
    if speed is None or isinstance(speed, bool) or not isinstance(speed, (int, float)):
        return False
    return abs(float(speed) - 1.0) < _SPEED_EPSILON


def require_track_item_bounds(item: dict[str, Any]) -> None:
    required = ("startTicks", "endTicks", "inPointTicks", "outPointTicks")
    missing = [key for key in required if item.get(key) is None]
    if missing:
        raise EditPlanError("SOURCE_BOUNDS_UNVERIFIED", f"Unreadable TrackItem fields: {', '.join(missing)}")


def map_source_interval_to_sequence(
    item: dict[str, Any], source_start_seconds: float, source_end_seconds: float, one_frame_ticks: int,
) -> dict[str, Any] | None:
    """Maps a [start,end) media-source-time interval to sequence time via the
    TrackItem's source in/out and timeline start (section 2's time model).
    Returns None when the interval falls entirely outside the TrackItem's
    source range. Raises TRACK_ITEM_SPEED_MAPPING_UNAVAILABLE for anything
    other than a confirmed 100% speed clip - never guesses a speed-scaled
    mapping."""
    require_track_item_bounds(item)
    if not is_normal_speed(item.get("speed", 1.0)):
        raise EditPlanError(
            "TRACK_ITEM_SPEED_MAPPING_UNAVAILABLE",
            "TrackItem speed is not a confirmed 100%; accurate source-to-sequence time mapping is unavailable.",
        )
    start_ticks, end_ticks = int(item["startTicks"]), int(item["endTicks"])
    in_ticks, out_ticks = int(item["inPointTicks"]), int(item["outPointTicks"])

    source_start_ticks = seconds_to_ticks(source_start_seconds)
    source_end_ticks = seconds_to_ticks(source_end_seconds)
    clipped_start = max(source_start_ticks, in_ticks)
    clipped_end = min(source_end_ticks, out_ticks)
    if clipped_end <= clipped_start:
        return None

    clip_relative_start = clipped_start - in_ticks
    clip_relative_end = clipped_end - in_ticks
    sequence_start = start_ticks + clip_relative_start
    sequence_end = start_ticks + clip_relative_end
    quantized_start = quantize_ticks(sequence_start, one_frame_ticks)
    quantized_end = quantize_ticks(sequence_end, one_frame_ticks)
    return {
        "sourceStartSeconds": ticks_to_seconds(clipped_start),
        "sourceEndSeconds": ticks_to_seconds(clipped_end),
        "clipRelativeStartTicks": str(clip_relative_start),
        "clipRelativeEndTicks": str(clip_relative_end),
        "sequenceStartTicks": str(sequence_start),
        "sequenceEndTicks": str(sequence_end),
        "quantizedSequenceStartTicks": str(quantized_start),
        "quantizedSequenceEndTicks": str(quantized_end),
        "quantizationDeltaSeconds": ticks_to_seconds(quantized_start - sequence_start),
        "withinFrameTolerance": abs(quantized_start - sequence_start) <= one_frame_ticks // 2
        and abs(quantized_end - sequence_end) <= one_frame_ticks // 2,
        "clippedByBounds": clipped_start != source_start_ticks or clipped_end != source_end_ticks,
    }


def apply_padding_ticks(
    start_ticks: int, end_ticks: int, padding_before_ticks: int, padding_after_ticks: int,
    *, bound_start_ticks: int, bound_end_ticks: int,
) -> tuple[int, int]:
    """Grows an interval by padding on both sides, clamped to [bound_start, bound_end]."""
    padded_start = max(bound_start_ticks, start_ticks - padding_before_ticks)
    padded_end = min(bound_end_ticks, end_ticks + padding_after_ticks)
    return padded_start, padded_end


def subtract_hard_protected_intervals(
    start_ticks: int, end_ticks: int, protected: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    """Removes every hard=true protected sub-range from [start,end), returning
    the surviving pieces in order. Soft (hard=false) protected intervals are
    left untouched here - they only add warnings/confidence penalties, never
    a mandatory cut."""
    pieces = [(start_ticks, end_ticks)]
    for interval in protected:
        if not interval.get("hard"):
            continue
        p_start, p_end = int(interval["startTicks"]), int(interval["endTicks"])
        next_pieces = []
        for piece_start, piece_end in pieces:
            if p_end <= piece_start or p_start >= piece_end:
                next_pieces.append((piece_start, piece_end))
                continue
            if p_start > piece_start:
                next_pieces.append((piece_start, p_start))
            if p_end < piece_end:
                next_pieces.append((p_end, piece_end))
        pieces = next_pieces
    return pieces


def soft_protected_overlaps(start_ticks: int, end_ticks: int, protected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overlaps = []
    for interval in protected:
        if interval.get("hard"):
            continue
        p_start, p_end = int(interval["startTicks"]), int(interval["endTicks"])
        if p_start < end_ticks and p_end > start_ticks:
            overlaps.append(interval)
    return overlaps


def merge_adjacent_intervals(
    intervals: list[dict[str, Any]], merge_gap_ticks: int,
) -> list[dict[str, Any]]:
    """Merges intervals (each a dict with startTicks/endTicks/id) whose gap to
    the next interval is <= merge_gap_ticks. Deterministic left-to-right scan
    over intervals sorted by startTicks; mergedFrom preserves original ids."""
    ordered = sorted(intervals, key=lambda row: int(row["startTicks"]))
    merged: list[dict[str, Any]] = []
    for interval in ordered:
        start, end = int(interval["startTicks"]), int(interval["endTicks"])
        if merged and start - int(merged[-1]["endTicks"]) <= merge_gap_ticks:
            merged[-1]["endTicks"] = str(max(int(merged[-1]["endTicks"]), end))
            merged[-1]["mergedFrom"].append(interval.get("id"))
        else:
            merged.append({"startTicks": str(start), "endTicks": str(end), "mergedFrom": [interval.get("id")]})
    return merged


def markers_to_protected_intervals(
    markers: list[dict[str, Any]], padding_seconds: float, *, reason: str = "marker",
) -> list[dict[str, Any]]:
    intervals = []
    for marker in markers:
        start_seconds = marker.get("startSeconds")
        if start_seconds is None:
            continue
        duration_seconds = marker.get("durationSeconds") or 0.0
        start_ticks = seconds_to_ticks(start_seconds) - seconds_to_ticks(padding_seconds)
        end_ticks = seconds_to_ticks(start_seconds + duration_seconds) + seconds_to_ticks(padding_seconds)
        intervals.append({
            "startTicks": str(max(0, start_ticks)), "endTicks": str(max(0, end_ticks)),
            "reason": reason, "hard": True, "markerGuid": marker.get("guid"),
        })
    return intervals


SILENCE_EDIT_STRATEGIES = {"remove_and_ripple", "trim_edges_only", "split_only", "markers_only"}


def build_silence_candidates(
    *, item: dict[str, Any], silence_intervals: list[dict[str, Any]], one_frame_ticks: int,
    padding_before_seconds: float, padding_after_seconds: float, remove_min_duration_seconds: float,
    minimum_remaining_clip_seconds: float, preserve_first_seconds: float, preserve_last_seconds: float,
    protected_intervals: list[dict[str, Any]], merge_gap_seconds: float,
) -> dict[str, Any]:
    """Runs section 4's ten-step pipeline (map -> pad -> protect -> quantize ->
    filter -> merge -> candidate) over one TrackItem's silence intervals.
    Speed-mapping failure raises (fatal for the whole TrackItem); every other
    kind of exclusion is recorded in `skipped` instead of raising."""
    require_track_item_bounds(item)
    start_ticks, end_ticks = int(item["startTicks"]), int(item["endTicks"])
    clip_duration_ticks = end_ticks - start_ticks

    all_protected = list(protected_intervals)
    if preserve_first_seconds > 0:
        all_protected.append({
            "startTicks": str(start_ticks),
            "endTicks": str(min(end_ticks, start_ticks + seconds_to_ticks(preserve_first_seconds))),
            "reason": "preserve_first_seconds", "hard": True,
        })
    if preserve_last_seconds > 0:
        all_protected.append({
            "startTicks": str(max(start_ticks, end_ticks - seconds_to_ticks(preserve_last_seconds))),
            "endTicks": str(end_ticks),
            "reason": "preserve_last_seconds", "hard": True,
        })

    padding_before_ticks = seconds_to_ticks(padding_before_seconds)
    padding_after_ticks = seconds_to_ticks(padding_after_seconds)
    merge_gap_ticks = seconds_to_ticks(merge_gap_seconds)
    remove_min_ticks = seconds_to_ticks(remove_min_duration_seconds)
    minimum_remaining_ticks = seconds_to_ticks(minimum_remaining_clip_seconds)

    skipped: list[dict[str, Any]] = []
    raw_mapped = []
    for index, silence in enumerate(silence_intervals):
        source_id = f"silence_{index:04d}"
        if silence.get("endSeconds") is None:
            skipped.append({"sourceId": source_id, "reason": "unclosed silence interval (source ended while silent); cannot safely bound a removal."})
            continue
        mapping = map_source_interval_to_sequence(item, silence["startSeconds"], silence["endSeconds"], one_frame_ticks)
        if mapping is None:
            skipped.append({"sourceId": source_id, "reason": "silence interval falls outside the TrackItem's source range."})
            continue
        raw_mapped.append({
            "sourceId": source_id, "mapping": mapping,
            "startTicks": int(mapping["sequenceStartTicks"]), "endTicks": int(mapping["sequenceEndTicks"]),
        })

    if not raw_mapped:
        return {"candidates": [], "skipped": skipped}

    padded = []
    for row in raw_mapped:
        padded_start, padded_end = apply_padding_ticks(
            row["startTicks"], row["endTicks"], padding_before_ticks, padding_after_ticks,
            bound_start_ticks=start_ticks, bound_end_ticks=end_ticks,
        )
        if padded_end - padded_start < one_frame_ticks:
            skipped.append({"sourceId": row["sourceId"], "reason": "padded interval is less than one frame."})
            continue
        padded.append({"sourceId": row["sourceId"], "startTicks": padded_start, "endTicks": padded_end})

    merge_input = [{"startTicks": str(r["startTicks"]), "endTicks": str(r["endTicks"]), "id": r["sourceId"]} for r in padded]
    merged = merge_adjacent_intervals(merge_input, merge_gap_ticks)

    candidates = []
    for merge_index, group in enumerate(merged):
        group_start, group_end = int(group["startTicks"]), int(group["endTicks"])
        pieces = subtract_hard_protected_intervals(group_start, group_end, all_protected)
        soft_hits = soft_protected_overlaps(group_start, group_end, all_protected)
        if not pieces:
            skipped.append({"sourceId": group["mergedFrom"], "reason": "fully covered by a hard-protected interval."})
            continue
        fragmented = len(pieces) > 1
        for piece_index, (raw_piece_start, raw_piece_end) in enumerate(pieces):
            # Frame-boundary quantization (section 4 step 7): the padded/protected
            # interval is derived from source-media timestamps that rarely land on
            # a sequence frame boundary, so snap both edges to the nearest frame
            # before reporting or filtering on duration.
            piece_start = quantize_ticks(raw_piece_start, one_frame_ticks)
            piece_end = quantize_ticks(raw_piece_end, one_frame_ticks)
            quantization_delta_seconds = ticks_to_seconds((piece_start - raw_piece_start) + (piece_end - raw_piece_end))
            duration_ticks = piece_end - piece_start
            candidate_id = f"silence_{merge_index:04d}_{piece_index:04d}"
            if duration_ticks < remove_min_ticks:
                skipped.append({"sourceId": group["mergedFrom"], "reason": f"remainder ({ticks_to_seconds(duration_ticks):.3f}s) is below remove_min_duration_seconds."})
                continue
            remaining_clip_ticks = clip_duration_ticks - duration_ticks
            blocked_reasons = []
            warnings = []
            if remaining_clip_ticks < minimum_remaining_ticks:
                blocked_reasons.append("EDIT_CANDIDATE_TOO_SHORT")
            if fragmented:
                warnings.append("candidate was split by a hard-protected interval")
            for hit in soft_hits:
                # Soft protection (hard=false) only warns and reduces confidence; it never forces exclusion (section 6).
                warnings.append(f"overlaps soft-protected interval ({hit.get('reason', 'unspecified')})")
            confidence = round(max(0.0, 1.0 - 0.15 * len(soft_hits)), 4)
            touches_start = piece_start <= start_ticks + one_frame_ticks
            touches_end = piece_end >= end_ticks - one_frame_ticks
            candidates.append({
                "candidateId": candidate_id, "kind": "silence_removal",
                "sequenceInterval": {
                    "startTicks": str(piece_start), "endTicks": str(piece_end),
                    "startSeconds": ticks_to_seconds(piece_start), "endSeconds": ticks_to_seconds(piece_end),
                },
                "durationSeconds": ticks_to_seconds(duration_ticks),
                "quantizationDeltaSeconds": quantization_delta_seconds,
                "touchesTrackItemStart": touches_start, "touchesTrackItemEnd": touches_end,
                "mergedFrom": group["mergedFrom"],
                "confidence": confidence,
                "executable": not blocked_reasons,
                "blockedReasons": blocked_reasons,
                "warnings": warnings,
            })
    return {"candidates": candidates, "skipped": skipped}


def plan_silence_operations(
    candidate: dict[str, Any], target_selector: dict[str, Any], edit_strategy: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attaches plannedOperations (using only operation kinds this pack's
    existing tools already support - split_track_item/trim_track_item/
    remove_track_item/create_sequence_marker) to one candidate. Deep
    multi-track ripple/collision simulation is deferred to
    validate_timeline_edit_plan; this only checks what's decidable from the
    single TrackItem's own bounds."""
    if edit_strategy not in SILENCE_EDIT_STRATEGIES:
        raise EditPlanError("INVALID_ARGUMENT", f"Unsupported edit_strategy: {edit_strategy}")
    interval = candidate["sequenceInterval"]
    start_ticks, end_ticks = int(interval["startTicks"]), int(interval["endTicks"])
    extra_blocked: list[str] = []

    if edit_strategy == "markers_only":
        return [
            {"operation": "create_sequence_marker", "atSequenceTicks": str(start_ticks), "name": "Silence start", "dependencies": []},
            {"operation": "create_sequence_marker", "atSequenceTicks": str(end_ticks), "name": "Silence end", "dependencies": []},
        ], extra_blocked

    if edit_strategy == "split_only":
        return [
            {"operation": "split_track_item", "atSequenceTicks": str(start_ticks), "target": target_selector, "dependencies": []},
            {"operation": "split_track_item", "atSequenceTicks": str(end_ticks), "target": target_selector, "dependencies": [0]},
        ], extra_blocked

    if edit_strategy == "trim_edges_only":
        if not (candidate.get("touchesTrackItemStart") or candidate.get("touchesTrackItemEnd")):
            extra_blocked.append("EDIT_CANDIDATE_CONFLICT")
            return [], extra_blocked
        if candidate.get("touchesTrackItemStart"):
            return [{"operation": "trim_track_item", "mode": "trim_start_to", "valueTicks": str(end_ticks), "target": target_selector, "dependencies": []}], extra_blocked
        return [{"operation": "trim_track_item", "mode": "trim_end_to", "valueTicks": str(start_ticks), "target": target_selector, "dependencies": []}], extra_blocked

    # remove_and_ripple: split at both boundaries, then ripple-delete the middle piece.
    return [
        {"operation": "split_track_item", "atSequenceTicks": str(start_ticks), "target": target_selector, "dependencies": []},
        {"operation": "split_track_item", "atSequenceTicks": str(end_ticks), "target": target_selector, "dependencies": [0]},
        {
            "operation": "remove_track_item", "rippleDelete": True,
            "removedInterval": {"startTicks": str(start_ticks), "endTicks": str(end_ticks)},
            "target": target_selector, "dependencies": [0, 1],
        },
    ], extra_blocked


# --- scene edit planning ---------------------------------------------------

SCENE_EDIT_STRATEGIES = {"split_only", "markers_only", "scene_segments"}


def map_source_timestamp_to_sequence(
    item: dict[str, Any], source_timestamp_seconds: float, one_frame_ticks: int,
) -> dict[str, Any] | None:
    """Maps a single media-source timestamp (e.g. one scene-change event) into
    quantized sequence ticks. Returns None when the timestamp falls outside
    the TrackItem's source range. Same speed guard as the interval mapper."""
    require_track_item_bounds(item)
    if not is_normal_speed(item.get("speed", 1.0)):
        raise EditPlanError(
            "TRACK_ITEM_SPEED_MAPPING_UNAVAILABLE",
            "TrackItem speed is not a confirmed 100%; accurate source-to-sequence time mapping is unavailable.",
        )
    start_ticks = int(item["startTicks"])
    in_ticks, out_ticks = int(item["inPointTicks"]), int(item["outPointTicks"])
    source_ticks = seconds_to_ticks(source_timestamp_seconds)
    if source_ticks < in_ticks or source_ticks > out_ticks:
        return None
    clip_relative_ticks = source_ticks - in_ticks
    sequence_ticks = start_ticks + clip_relative_ticks
    quantized_ticks = quantize_ticks(sequence_ticks, one_frame_ticks)
    return {
        "sourceSeconds": ticks_to_seconds(source_ticks),
        "clipRelativeTicks": str(clip_relative_ticks),
        "sequenceTicks": str(sequence_ticks),
        "quantizedSequenceTicks": str(quantized_ticks),
        "quantizationDeltaSeconds": ticks_to_seconds(quantized_ticks - sequence_ticks),
    }


def build_scene_candidates(
    *, item: dict[str, Any], scene_events: list[dict[str, Any]], one_frame_ticks: int,
    minimum_scene_duration_seconds: float, minimum_cut_distance_frames: int,
    ignore_near_clip_edges_frames: int, merge_nearby_events_frames: int,
) -> dict[str, Any]:
    """Maps scene-change events to sequence time, drops events too close to a
    TrackItem edge, merges near-duplicate quantized cuts, enforces a minimum
    distance between surviving cuts, and finally enforces a minimum resulting
    scene-segment duration (greedy left-to-right, deterministic)."""
    require_track_item_bounds(item)
    start_ticks, end_ticks = int(item["startTicks"]), int(item["endTicks"])
    edge_guard_ticks = ignore_near_clip_edges_frames * one_frame_ticks
    min_scene_ticks = seconds_to_ticks(minimum_scene_duration_seconds)
    min_cut_distance_ticks = minimum_cut_distance_frames * one_frame_ticks
    merge_nearby_ticks = merge_nearby_events_frames * one_frame_ticks

    skipped: list[dict[str, Any]] = []
    mapped = []
    for index, event in enumerate(scene_events):
        source_id = f"scene_{index:04d}"
        mapping = map_source_timestamp_to_sequence(item, event["ptsSeconds"], one_frame_ticks)
        if mapping is None:
            skipped.append({"sourceId": source_id, "reason": "scene event falls outside the TrackItem's source range."})
            continue
        seq_ticks = int(mapping["quantizedSequenceTicks"])
        if seq_ticks <= start_ticks + edge_guard_ticks or seq_ticks >= end_ticks - edge_guard_ticks:
            skipped.append({"sourceId": source_id, "reason": "scene event is too close to a TrackItem edge."})
            continue
        mapped.append({"sourceId": source_id, "sequenceTicks": seq_ticks, "score": event.get("sceneScore")})

    if not mapped:
        return {"candidates": [], "skipped": skipped}
    mapped.sort(key=lambda row: row["sequenceTicks"])

    merged_groups: list[dict[str, Any]] = []
    for row in mapped:
        if merged_groups and row["sequenceTicks"] - merged_groups[-1]["sequenceTicks"] <= merge_nearby_ticks:
            previous = merged_groups[-1]
            if row["score"] is not None and (previous["score"] is None or row["score"] > previous["score"]):
                previous["sequenceTicks"] = row["sequenceTicks"]
                previous["score"] = row["score"]
            previous["mergedFrom"].append(row["sourceId"])
        else:
            merged_groups.append({"sequenceTicks": row["sequenceTicks"], "score": row["score"], "mergedFrom": [row["sourceId"]]})

    distance_filtered = []
    for group in merged_groups:
        if distance_filtered and group["sequenceTicks"] - distance_filtered[-1]["sequenceTicks"] < min_cut_distance_ticks:
            skipped.append({"sourceId": group["mergedFrom"], "reason": "too close to the previous surviving scene cut (minimum_cut_distance_frames)."})
            continue
        distance_filtered.append(group)

    kept_cuts: list[dict[str, Any]] = []
    prev_boundary = start_ticks
    for group in distance_filtered:
        if group["sequenceTicks"] - prev_boundary < min_scene_ticks:
            skipped.append({"sourceId": group["mergedFrom"], "reason": "resulting scene segment is shorter than minimum_scene_duration_seconds."})
            continue
        kept_cuts.append(group)
        prev_boundary = group["sequenceTicks"]
    if kept_cuts and (end_ticks - kept_cuts[-1]["sequenceTicks"]) < min_scene_ticks:
        dropped = kept_cuts.pop()
        skipped.append({"sourceId": dropped["mergedFrom"], "reason": "final scene segment after this cut would be shorter than minimum_scene_duration_seconds."})

    candidates = []
    for index, group in enumerate(kept_cuts):
        seq_ticks = group["sequenceTicks"]
        prev_cut = kept_cuts[index - 1]["sequenceTicks"] if index > 0 else start_ticks
        next_cut = kept_cuts[index + 1]["sequenceTicks"] if index + 1 < len(kept_cuts) else end_ticks
        candidates.append({
            "candidateId": f"scene_{index:04d}", "kind": "scene_cut",
            "sequenceTicks": str(seq_ticks), "sequenceSeconds": ticks_to_seconds(seq_ticks),
            "score": group["score"], "mergedFrom": group["mergedFrom"],
            "previousSceneDurationSeconds": ticks_to_seconds(seq_ticks - prev_cut),
            "nextSceneDurationSeconds": ticks_to_seconds(next_cut - seq_ticks),
            "confidence": 1.0, "executable": True, "blockedReasons": [], "warnings": [],
        })
    return {"candidates": candidates, "skipped": skipped}


def plan_scene_operations(
    candidate: dict[str, Any], target_selector: dict[str, Any], strategy: str,
) -> list[dict[str, Any]]:
    if strategy not in SCENE_EDIT_STRATEGIES:
        raise EditPlanError("INVALID_ARGUMENT", f"Unsupported scene strategy: {strategy}")
    seq_ticks = candidate["sequenceTicks"]
    if strategy == "markers_only":
        return [{"operation": "create_sequence_marker", "atSequenceTicks": seq_ticks, "name": "Scene cut", "dependencies": []}]
    # split_only and scene_segments both plan a single split at the cut point;
    # scene_segments' grouping-into-shots is a read-only summary concern, not a distinct operation.
    return [{"operation": "split_track_item", "atSequenceTicks": seq_ticks, "target": target_selector, "dependencies": []}]


# --- black-frame edit planning ----------------------------------------------

BLACK_EDIT_STRATEGIES = {"trim_edges", "remove_and_ripple", "markers_only"}


def build_black_candidates(
    *, item: dict[str, Any], black_intervals: list[dict[str, Any]], one_frame_ticks: int,
    minimum_black_duration_seconds: float, padding_before_seconds: float, padding_after_seconds: float,
    protected_intervals: list[dict[str, Any]], audio_silence_intervals: list[dict[str, Any]] | None = None,
    has_audio_stream: bool | None = None,
) -> dict[str, Any]:
    """Maps black intervals to sequence time, pads, subtracts hard-protected
    ranges, and quantizes - mirroring build_silence_candidates - then attaches
    black-specific fields (likelyFade, audioContinues, recommendedAction).
    likelyFade is a documented heuristic (duration <=1s), not a claim of
    distinguishing an intentional fade-to-black from a real black hold.

    audioContinues is a tri-state: False only when either the source has no
    audio stream at all (has_audio_stream=False) or a silence interval is
    confirmed to fully cover the black interval; True when audio is confirmed
    NOT silent there; None when continuity was never checked. Both True and
    None are treated as "unsafe to assume silent" - only a confirmed False
    unblocks remove_and_ripple, matching the pack's fail-closed convention
    (an unverified case must never silently default to the permissive action)."""
    require_track_item_bounds(item)
    start_ticks, end_ticks = int(item["startTicks"]), int(item["endTicks"])
    min_black_ticks = seconds_to_ticks(minimum_black_duration_seconds)
    padding_before_ticks = seconds_to_ticks(padding_before_seconds)
    padding_after_ticks = seconds_to_ticks(padding_after_seconds)

    skipped: list[dict[str, Any]] = []
    candidates = []
    for index, interval in enumerate(black_intervals):
        source_id = f"black_{index:04d}"
        if interval.get("endSeconds") is None:
            skipped.append({"sourceId": source_id, "reason": "unclosed black interval (source ended while black); cannot safely bound a removal."})
            continue
        duration_seconds = interval.get("durationSeconds")
        if duration_seconds is not None and duration_seconds < minimum_black_duration_seconds:
            skipped.append({"sourceId": source_id, "reason": "below minimum_black_duration_seconds."})
            continue
        mapping = map_source_interval_to_sequence(item, interval["startSeconds"], interval["endSeconds"], one_frame_ticks)
        if mapping is None:
            skipped.append({"sourceId": source_id, "reason": "black interval falls outside the TrackItem's source range."})
            continue
        raw_start, raw_end = int(mapping["sequenceStartTicks"]), int(mapping["sequenceEndTicks"])
        padded_start, padded_end = apply_padding_ticks(
            raw_start, raw_end, padding_before_ticks, padding_after_ticks,
            bound_start_ticks=start_ticks, bound_end_ticks=end_ticks,
        )
        pieces = subtract_hard_protected_intervals(padded_start, padded_end, protected_intervals)
        if not pieces:
            skipped.append({"sourceId": source_id, "reason": "fully covered by a hard-protected interval."})
            continue
        for piece_index, (raw_piece_start, raw_piece_end) in enumerate(pieces):
            piece_start = quantize_ticks(raw_piece_start, one_frame_ticks)
            piece_end = quantize_ticks(raw_piece_end, one_frame_ticks)
            if piece_end - piece_start < min_black_ticks:
                skipped.append({"sourceId": source_id, "reason": "quantized/protected remainder is below minimum_black_duration_seconds."})
                continue
            touches_start = piece_start <= start_ticks + one_frame_ticks
            touches_end = piece_end >= end_ticks - one_frame_ticks
            if has_audio_stream is False:
                audio_continues = False
            elif audio_silence_intervals is not None:
                audio_continues = not any(
                    s.get("startSeconds") is not None and s.get("endSeconds") is not None
                    and s["startSeconds"] <= interval["startSeconds"] and s["endSeconds"] >= interval["endSeconds"]
                    for s in audio_silence_intervals
                )
            else:
                audio_continues = None
            piece_duration_seconds = ticks_to_seconds(piece_end - piece_start)
            likely_fade = piece_duration_seconds <= 1.0
            warnings = []
            blocked_reasons = []
            if touches_start or touches_end:
                recommended_action = "trim"
            elif audio_continues is False:
                recommended_action = "remove_and_ripple"
            else:
                # True (confirmed continuing) or None (unverified) both fail closed.
                recommended_action = "review_audio_continuity"
                blocked_reasons.append("EDIT_CANDIDATE_CONFLICT")
                if audio_continues is None:
                    warnings.append("audio continuity under this black interval was not verified; video-only removal is blocked by default.")
                else:
                    warnings.append("audio continues under this black interval; video-only removal is blocked by default.")
            if likely_fade:
                warnings.append("short black interval may be an intentional fade-to-black, not a real black hold.")
            candidates.append({
                "candidateId": f"black_{index:04d}_{piece_index:04d}", "kind": "black_interval",
                "sequenceInterval": {
                    "startTicks": str(piece_start), "endTicks": str(piece_end),
                    "startSeconds": ticks_to_seconds(piece_start), "endSeconds": ticks_to_seconds(piece_end),
                },
                "durationSeconds": piece_duration_seconds,
                "touchesTrackItemStart": touches_start, "touchesTrackItemEnd": touches_end,
                "detectionConfidence": 1.0, "likelyFade": likely_fade,
                "audioContinues": audio_continues, "recommendedAction": recommended_action,
                "executable": not blocked_reasons, "blockedReasons": blocked_reasons, "warnings": warnings,
            })
    return {"candidates": candidates, "skipped": skipped}


def plan_black_operations(
    candidate: dict[str, Any], target_selector: dict[str, Any], strategy: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if strategy not in BLACK_EDIT_STRATEGIES:
        raise EditPlanError("INVALID_ARGUMENT", f"Unsupported black strategy: {strategy}")
    interval = candidate["sequenceInterval"]
    start_ticks, end_ticks = interval["startTicks"], interval["endTicks"]
    extra_blocked: list[str] = []

    if strategy == "markers_only":
        return [
            {"operation": "create_sequence_marker", "atSequenceTicks": start_ticks, "name": "Black start", "dependencies": []},
            {"operation": "create_sequence_marker", "atSequenceTicks": end_ticks, "name": "Black end", "dependencies": []},
        ], extra_blocked

    if strategy == "trim_edges":
        if not (candidate.get("touchesTrackItemStart") or candidate.get("touchesTrackItemEnd")):
            extra_blocked.append("EDIT_CANDIDATE_CONFLICT")
            return [], extra_blocked
        if candidate.get("touchesTrackItemStart"):
            return [{"operation": "trim_track_item", "mode": "trim_start_to", "valueTicks": end_ticks, "target": target_selector, "dependencies": []}], extra_blocked
        return [{"operation": "trim_track_item", "mode": "trim_end_to", "valueTicks": start_ticks, "target": target_selector, "dependencies": []}], extra_blocked

    # remove_and_ripple
    return [
        {"operation": "split_track_item", "atSequenceTicks": start_ticks, "target": target_selector, "dependencies": []},
        {"operation": "split_track_item", "atSequenceTicks": end_ticks, "target": target_selector, "dependencies": [0]},
        {
            "operation": "remove_track_item", "rippleDelete": True,
            "removedInterval": interval, "target": target_selector, "dependencies": [0, 1],
        },
    ], extra_blocked
