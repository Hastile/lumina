"""Unified Premiere timeline edit plan schema (Silence/Scene/Black Edit Planning Pack v1).

Pure functions only - no Premiere calls, no subprocess calls, no filesystem
writes. Combines the candidate lists premiere_edit_planning.py's silence/
scene/black builders already produced into one deterministic, versioned
plan: merges near-duplicate cross-kind candidates, detects conflicts,
flattens surviving candidates into a globally-ordered operation list, and
assembles the final schema. Callers (pr-mcp.py) own all I/O - fetching
TrackItem state, running ffmpeg passes, reading markers - and pass
already-computed candidate lists in here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"
TICKS_PER_SECOND = 254_016_000_000

CONFLICT_POLICIES = {"prefer_silence", "prefer_scene", "prefer_black", "keep_all_non_overlapping", "manual"}
OPERATION_POLICIES = {"markers_only", "cuts_only", "destructive_plan"}

_ROLLBACK_CLASS = {
    "split_track_item": "disposable_sequence_only",
    "remove_track_item": "disposable_sequence_only",
    "trim_track_item": "inverse",
    "ripple_trim_track_item": "inverse",
    "create_sequence_marker": "inverse",
    "set_audio_clip_volume": "inverse",
    "apply_audio_fade": "inverse",
    "close_timeline_gap": "inverse",
    "no_op_note": "read_only",
}

# Premiere exposes no razor/merge/undelete Action, so these operations have no
# inverse: once executed there is no way to restore the prior TrackItem layout
# from inside the API. The original safety model handled that by refusing to
# run them anywhere except disposable ADB_MCP_*_TEST_ sequences. That
# name-based restriction has been retired (real project sequences are now a
# supported edit target), so the replacement contract is explicit caller
# confirmation: an irreversible operation never mutates without the caller
# spelling out confirm_destructive=true.
IRREVERSIBLE_ROLLBACK_CLASS = "disposable_sequence_only"

# sequence.delete is not part of the planner's operation vocabulary (plans
# never delete a sequence) but carries the same "no inverse exists" property,
# so it shares the same confirmation contract.
# The QE timeline edits belong here for a stronger reason than the rest. The
# official operations at least fail loudly; a QE mutation has no inverse at
# all, and the one candidate - qe.project.undo() - pops Premiere's GLOBAL undo
# stack, so calling it can revert whatever the user did last instead of this
# edit. undoStackIndex reads back NaN on 26.3, so there is no way to check
# first. The only real recovery is a project checkpoint taken beforehand.
IRREVERSIBLE_OPERATIONS = frozenset(
    {name for name, rollback_class in _ROLLBACK_CLASS.items() if rollback_class == IRREVERSIBLE_ROLLBACK_CLASS}
    | {
        "sequence.delete",
        "trackItem.duplicateToTrack.removeOriginal",
        "timeline.razor",
        "timeline.ripple_delete_range",
    }
)

DESTRUCTIVE_CONFIRMATION_CODE = "DESTRUCTIVE_CONFIRMATION_REQUIRED"


def requires_destructive_confirmation(operation: str, *, dry_run: bool, confirm_destructive: bool) -> bool:
    """True when a real, irreversible operation has not been explicitly confirmed.

    A dry run never mutates, so it is always allowed. Reversible operations
    (trim, transform, property/keyframe, audio, effect add/remove) all have a
    verified inverse action plus readback rollback and are not gated here.
    `confirm_destructive` must be exactly True - a truthy non-bool never
    counts as confirmation.
    """
    if dry_run:
        return False
    if operation not in IRREVERSIBLE_OPERATIONS:
        return False
    return confirm_destructive is not True


def irreversible_plan_operation_ids(operations) -> list[str]:
    """operationIds in an execution/plan whose rollbackClass has no inverse."""
    return [
        op.get("operationId")
        for op in operations or []
        if isinstance(op, dict) and op.get("rollbackClass") == IRREVERSIBLE_ROLLBACK_CLASS
    ]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _interval_ticks(candidate: dict[str, Any]) -> tuple[int, int] | None:
    interval = candidate.get("sequenceInterval")
    if not interval:
        return None
    return int(interval["startTicks"]), int(interval["endTicks"])


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def merge_candidates_across_kinds(
    silence_candidates: list[dict[str, Any]],
    scene_candidates: list[dict[str, Any]],
    black_candidates: list[dict[str, Any]],
    conflict_policy: str,
) -> dict[str, Any]:
    """Deterministically merges/conflicts candidates from the three planners
    (section 11): a scene cut inside a removal interval is dropped (it will
    be removed anyway); two overlapping removal intervals (one silence, one
    black) are combined into one union-interval removal candidate; anything
    still overlapping after that is reported as a conflict instead of being
    silently kept."""
    if conflict_policy not in CONFLICT_POLICIES:
        raise ValueError(f"Unsupported conflict_policy: {conflict_policy}")

    removal_candidates = [dict(c, _kind="silence") for c in silence_candidates] + [dict(c, _kind="black") for c in black_candidates]
    removal_candidates.sort(key=lambda c: _interval_ticks(c)[0])

    preferred_kind = {"prefer_silence": "silence", "prefer_black": "black"}.get(conflict_policy)
    merged_removals: list[dict[str, Any]] = []
    for candidate in removal_candidates:
        interval = _interval_ticks(candidate)
        merged_into = next(
            (row for row in merged_removals if _overlaps(interval, (int(row["sequenceInterval"]["startTicks"]), int(row["sequenceInterval"]["endTicks"])))),
            None,
        )
        if merged_into is None:
            row = dict(candidate)
            row["mergedFrom"] = [candidate["candidateId"]]
            row["mergedKinds"] = [candidate["_kind"]]
            merged_removals.append(row)
            continue
        existing_start, existing_end = int(merged_into["sequenceInterval"]["startTicks"]), int(merged_into["sequenceInterval"]["endTicks"])
        union_start, union_end = min(existing_start, interval[0]), max(existing_end, interval[1])
        merged_into["sequenceInterval"] = {
            "startTicks": str(union_start), "endTicks": str(union_end),
            "startSeconds": union_start / TICKS_PER_SECOND, "endSeconds": union_end / TICKS_PER_SECOND,
        }
        merged_into["durationSeconds"] = (union_end - union_start) / TICKS_PER_SECOND
        merged_into["mergedFrom"].append(candidate["candidateId"])
        merged_into["mergedKinds"].append(candidate["_kind"])
        merged_into["kind"] = "combined_removal"
        # Silence and black candidates target different tracks (audio vs. video), so a
        # single merged interval still needs both sides' operations - concatenate rather
        # than discard the second source's plannedOperations, offsetting its local
        # dependency indices past the first source's already-assigned ones.
        existing_ops = merged_into.get("plannedOperations") or []
        offset = len(existing_ops)
        offset_ops = [
            {**op, "dependencies": [d + offset for d in (op.get("dependencies") or [])]}
            for op in (candidate.get("plannedOperations") or [])
        ]
        merged_into["plannedOperations"] = existing_ops + offset_ops
        merged_into["touchesTrackItemStart"] = bool(merged_into.get("touchesTrackItemStart")) or bool(candidate.get("touchesTrackItemStart"))
        merged_into["touchesTrackItemEnd"] = bool(merged_into.get("touchesTrackItemEnd")) or bool(candidate.get("touchesTrackItemEnd"))
        if preferred_kind and candidate["_kind"] == preferred_kind and merged_into.get("_preferredApplied") != preferred_kind:
            for key in ("executable", "blockedReasons", "warnings", "recommendedAction", "audioContinues", "confidence"):
                if key in candidate:
                    merged_into[key] = candidate[key]
            merged_into["_preferredApplied"] = preferred_kind

    for row in merged_removals:
        row.pop("_kind", None)
        row.pop("_preferredApplied", None)

    kept_scene = []
    dropped_scene = []
    for scene in scene_candidates:
        scene_ticks = int(scene["sequenceTicks"])
        covering = next(
            (row for row in merged_removals if _overlaps((scene_ticks, scene_ticks + 1), (int(row["sequenceInterval"]["startTicks"]), int(row["sequenceInterval"]["endTicks"])))),
            None,
        )
        if covering is not None:
            dropped_scene.append({"sourceId": scene["candidateId"], "reason": f"scene cut falls inside removal candidate {covering['candidateId']}."})
            covering.setdefault("mergedFrom", []).append(scene["candidateId"])
            covering.setdefault("mergedKinds", []).append("scene")
            continue
        kept_scene.append(scene)

    conflicts = []
    for index, first in enumerate(merged_removals):
        first_interval = (int(first["sequenceInterval"]["startTicks"]), int(first["sequenceInterval"]["endTicks"]))
        for second in merged_removals[index + 1:]:
            second_interval = (int(second["sequenceInterval"]["startTicks"]), int(second["sequenceInterval"]["endTicks"]))
            if _overlaps(first_interval, second_interval):
                conflicts.append({
                    "type": "overlapping_destructive_intervals",
                    "candidateIds": [first["candidateId"], second["candidateId"]],
                    "reason": "Two removal candidates still overlap after cross-kind merging.",
                })

    return {
        "candidates": merged_removals + kept_scene,
        "removalCandidates": merged_removals,
        "sceneCandidates": kept_scene,
        "droppedScene": dropped_scene,
        "conflicts": conflicts,
    }


def flatten_operations(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Assigns global operationIds to every surviving candidate's
    plannedOperations, remapping each candidate's local (index-based)
    dependencies to the new global ids, and orders candidates latest-
    sequence-time-first (section 11's ordering rationale: a naive top-to-
    bottom executor applying edits earliest-first would invalidate not-yet-
    processed selectors via ripple; processing latest-first avoids that for
    a single pass). The next pack's Batch Executor is still expected to
    re-resolve selectors immediately before each mutation rather than trust
    this ordering blindly - Premiere's live TrackItem indices are the source
    of truth, not this plan."""
    def _primary_ticks(candidate: dict[str, Any]) -> int:
        if "sequenceTicks" in candidate:
            return int(candidate["sequenceTicks"])
        interval = candidate.get("sequenceInterval")
        return int(interval["startTicks"]) if interval else 0

    ordered_candidates = sorted(candidates, key=_primary_ticks, reverse=True)
    operations: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    for candidate in ordered_candidates:
        local_ops = candidate.get("plannedOperations") or []
        local_to_global = {index: f"op_{uuid.uuid4().hex[:12]}" for index in range(len(local_ops))}
        for local_index, op in enumerate(local_ops):
            global_id = local_to_global[local_index]
            global_deps = [local_to_global[d] for d in (op.get("dependencies") or []) if d in local_to_global]
            operations.append({
                "operationId": global_id,
                "candidateId": candidate.get("candidateId"),
                "type": op.get("operation"),
                "targetSelector": op.get("target"),
                "parameters": {k: v for k, v in op.items() if k not in ("operation", "dependencies", "target")},
                "dependencies": global_deps,
                "mutuallyExclusiveWith": [],
                "rollbackClass": _ROLLBACK_CLASS.get(op.get("operation"), "none"),
                "backendRequirement": "uxp",
                "executable": bool(candidate.get("executable")),
                "blockedReasons": list(candidate.get("blockedReasons") or []),
            })
            for dep in global_deps:
                dependencies.append({"operationId": global_id, "dependsOn": dep})
    return {
        "operations": operations,
        "dependencies": dependencies,
        "orderingRationale": (
            "Candidates are ordered latest-sequence-time-first so a naive single-pass "
            "executor never invalidates an earlier, not-yet-processed candidate's selector "
            "via ripple; a Batch Executor should still re-resolve each selector immediately "
            "before mutating rather than trust this order blindly."
        ),
    }


def detect_dependency_cycle(operations: list[dict[str, Any]]) -> list[str] | None:
    """Returns a cycle (ordered list of operationIds) if the operations
    dependency graph has one, else None. Iterative DFS with a recursion-stack
    color marker (white/gray/black) - no recursion depth risk on large plans."""
    graph = {op["operationId"]: list(op.get("dependencies") or []) for op in operations}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}

    for start in graph:
        if color[start] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = [start]
        color[start] = GRAY
        while stack:
            node, next_child = stack[-1]
            neighbors = graph.get(node, [])
            if next_child < len(neighbors):
                stack[-1] = (node, next_child + 1)
                neighbor = neighbors[next_child]
                if neighbor not in color:
                    continue
                if color[neighbor] == GRAY:
                    cycle_start = path.index(neighbor)
                    return path[cycle_start:] + [neighbor]
                if color[neighbor] == WHITE:
                    color[neighbor] = GRAY
                    path.append(neighbor)
                    stack.append((neighbor, 0))
            else:
                stack.pop()
                path.pop()
                color[node] = BLACK
    return None


_PLAN_HASH_EXCLUDED_KEYS = ("planId", "createdAt", "planHash", "approval", "executable")


def _canonicalize_numbers(value: Any) -> Any:
    """Recursively renders every non-bool int/float as a float. JSON itself
    never distinguishes 1 from 1.0 as different *values* - only Python's
    parser does - and plenty of ordinary JSON round-trips (a client
    re-serializing a plan, a human copying it by hand) legitimately collapse
    a float like 1.0 to 1 without changing its meaning. Hashing the raw
    Python types would treat that as corruption; canonicalizing first makes
    the hash robust to this while still catching real content changes."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {k: _canonicalize_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonicalize_numbers(item) for item in value]
    return value


def compute_plan_hash(plan: dict[str, Any]) -> str:
    """Deterministic hash over the plan's real content (candidates/operations/
    dependencies/conflicts/etc.), excluding planId/createdAt/planHash (pure
    identity/administrative metadata) and approval/executable. approval is
    workflow state that legitimately changes via the official
    approve_timeline_edit_plan tool - that's a tracked decision, not the
    hand-editing/corruption this hash exists to catch, so it must not
    invalidate the hash. executable is always plan-build-time-false and is
    never itself part of the plan's content. Numeric values are canonicalized
    (int and float forms of the same value hash identically) so an ordinary
    JSON round-trip never looks like tampering."""
    stable = {k: v for k, v in plan.items() if k not in _PLAN_HASH_EXCLUDED_KEYS}
    canonical = _canonicalize_numbers(stable)
    encoded = json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_edit_plan(
    *, project: dict[str, Any], sequence: dict[str, Any], sequence_fingerprint: str | None,
    timebase: dict[str, Any], sources: list[dict[str, Any]], analyses: dict[str, Any],
    protected_intervals: list[dict[str, Any]], merge_result: dict[str, Any],
    flatten_result: dict[str, Any], warnings: list[str],
) -> dict[str, Any]:
    operations = flatten_result["operations"]
    executable_count = sum(1 for op in operations if op["executable"])
    plan: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "planId": f"plan_{uuid.uuid4().hex}",
        "createdAt": _utc_now_iso(),
        "project": project,
        "sequence": sequence,
        "sequenceFingerprint": sequence_fingerprint,
        "timebase": timebase,
        "sources": sources,
        "analyses": analyses,
        "protectedIntervals": protected_intervals,
        "candidates": merge_result["candidates"],
        "operations": operations,
        "dependencies": flatten_result["dependencies"],
        "orderingRationale": flatten_result["orderingRationale"],
        "conflicts": merge_result["conflicts"],
        "droppedScene": merge_result["droppedScene"],
        "summary": {
            "candidateCount": len(merge_result["candidates"]),
            "operationCount": len(operations),
            "executableOperationCount": executable_count,
            "conflictCount": len(merge_result["conflicts"]),
        },
        "approval": {
            "status": "pending", "approvedOperationIds": [], "rejectedOperationIds": [],
            "modifiedOperationIds": [], "approvedBy": None, "approvedAt": None,
        },
        "executable": False,
        "warnings": list(warnings),
    }
    plan["planHash"] = compute_plan_hash(plan)
    return plan


REQUIRED_PLAN_KEYS = {
    "schemaVersion", "planId", "createdAt", "sequence", "sequenceFingerprint",
    "candidates", "operations", "dependencies", "conflicts", "summary", "approval", "executable",
}


def validate_plan_schema(plan: dict[str, Any]) -> list[str]:
    """Pure structural validation (section 12) - returns a list of human-
    readable error strings, empty when the plan is structurally sound.
    Does not touch Premiere or the filesystem."""
    errors: list[str] = []
    missing = REQUIRED_PLAN_KEYS - set(plan.keys())
    if missing:
        errors.append(f"Missing required plan keys: {sorted(missing)}")
    if plan.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"Unsupported schemaVersion: {plan.get('schemaVersion')!r} (expected {SCHEMA_VERSION!r}).")
    if not isinstance(plan.get("planId"), str) or not plan.get("planId"):
        errors.append("planId must be a non-empty string.")

    operations = plan.get("operations")
    if not isinstance(operations, list):
        errors.append("operations must be a list.")
        operations = []
    seen_ids: set[str] = set()
    for op in operations:
        op_id = op.get("operationId") if isinstance(op, dict) else None
        if not op_id:
            errors.append("An operation is missing operationId.")
            continue
        if op_id in seen_ids:
            errors.append(f"Duplicate operationId: {op_id}")
        seen_ids.add(op_id)
    for op in operations:
        if not isinstance(op, dict):
            continue
        for dep in op.get("dependencies") or []:
            if dep not in seen_ids:
                errors.append(f"Operation {op.get('operationId')} depends on unknown operationId {dep}.")

    for candidate in plan.get("candidates") or []:
        interval = candidate.get("sequenceInterval")
        if interval and int(interval.get("endTicks", 0)) <= int(interval.get("startTicks", 0)):
            errors.append(f"Candidate {candidate.get('candidateId')} has a non-positive duration interval.")
    return errors


def verify_plan_hash(plan: dict[str, Any]) -> bool:
    """True when the plan's stored planHash matches its recomputed content
    hash - a mismatch means the plan was hand-edited or corrupted after
    build_timeline_edit_plan produced it."""
    stored = plan.get("planHash")
    if not stored:
        return False
    return compute_plan_hash(plan) == stored


def _topological_order(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {op["operationId"]: op for op in operations if op.get("operationId")}
    visited: set[str] = set()
    order: list[dict[str, Any]] = []

    def visit(op_id: str) -> None:
        if op_id in visited or op_id not in by_id:
            return
        visited.add(op_id)
        for dep in by_id[op_id].get("dependencies") or []:
            visit(dep)
        order.append(by_id[op_id])

    for op_id in by_id:
        visit(op_id)
    return order


def simulate_plan_operations(plan: dict[str, Any]) -> dict[str, Any]:
    """Pure, in-memory simulation of a plan's operations in dependency order
    (section 12). This tracks aggregate duration/count effects - total
    removed duration, predicted item-count delta, marker/split/trim/remove
    counts, and overlapping-removal collisions - rather than re-deriving
    exact TrackItem indices across cascading ripples on every track; a Batch
    Executor still re-resolves each live selector immediately before
    mutating, so this is a planning-time sanity check, not a substitute."""
    operations = plan.get("operations") or []
    cycle = detect_dependency_cycle(operations)
    if cycle:
        return {"status": "FAILED", "error": {"code": "EDIT_PLAN_DEPENDENCY_CYCLE", "message": f"Cycle: {' -> '.join(cycle)}"}}

    order = _topological_order(operations)
    split_count = trim_count = remove_count = marker_count = 0
    removed_ticks_total = 0
    seen_removed_intervals: list[tuple[int, int, str, tuple]] = []
    collisions: list[dict[str, Any]] = []

    for op in order:
        op_type = op.get("type")
        if op_type == "create_sequence_marker":
            marker_count += 1
        elif op_type == "split_track_item":
            split_count += 1
        elif op_type == "trim_track_item":
            trim_count += 1
        elif op_type == "remove_track_item":
            remove_count += 1
            interval = (op.get("parameters") or {}).get("removedInterval") or {}
            start, end = interval.get("startTicks"), interval.get("endTicks")
            if start is None or end is None:
                continue
            start, end = int(start), int(end)
            if end <= start:
                collisions.append({"operationId": op.get("operationId"), "reason": "removedInterval end <= start."})
                continue
            selector = op.get("targetSelector") or {}
            track_key = (selector.get("mediaType"), selector.get("trackIndex"))
            for other_start, other_end, other_id, other_key in seen_removed_intervals:
                if track_key == other_key and start < other_end and end > other_start:
                    collisions.append({
                        "operationId": op.get("operationId"), "conflictsWith": other_id,
                        "reason": "Removed intervals overlap on the same track.",
                    })
            seen_removed_intervals.append((start, end, op.get("operationId"), track_key))
            removed_ticks_total += end - start

    return {
        "status": "SUCCESS",
        "operationOrder": [op.get("operationId") for op in order],
        "splitCount": split_count, "trimCount": trim_count, "removeCount": remove_count, "markerCount": marker_count,
        "totalRemovedSeconds": removed_ticks_total / TICKS_PER_SECOND,
        # Each remove_and_ripple group nets one extra TrackItem (1 clip -> 2 splits -> 3
        # pieces -> remove the middle -> 2 remain), so the item-count delta is +removeCount.
        "estimatedItemCountDelta": remove_count,
        "collisions": collisions,
        "error": None,
    }
