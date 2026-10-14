"""Batch Editing Orchestrator Pack v1 - pure execution-model helpers.

No Premiere calls, no subprocess calls here. pr-mcp.py owns all I/O (UXP
mutation calls, journal writes) and passes already-fetched TrackItem state
into these functions. Mirrors premiere_edit_planning.py's and edit_plan.py's
convention of keeping planning/bookkeeping logic pure and unit-testable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

EXECUTION_SCHEMA_VERSION = "1.0"

EXECUTION_STATUSES = {
    "prepared", "validating", "executing", "completed", "partially_completed",
    "failed", "rollback_pending", "rolling_back", "rolled_back",
    "rollback_failed", "stale", "cancelled",
}

# v1 executor only implements the operation kinds this pack's own planners
# (Silence/Scene/Black Edit Planning Pack v1) actually generate. Anything
# else is rejected before mutation with EDIT_EXECUTION_OPERATION_UNSUPPORTED.
SUPPORTED_OPERATION_TYPES = {"split_track_item", "trim_track_item", "remove_track_item", "create_sequence_marker"}

EXECUTION_POLICIES = {"fail_fast", "stop_after_candidate"}
ROLLBACK_POLICIES = {"rollback_current_candidate", "rollback_all_executed", "no_automatic_rollback"}
ROLLBACK_SCOPES = {"current_candidate", "to_checkpoint", "all"}

# Only sequences whose name starts with one of these prefixes may ever have a
# real (non-dry-run) mutation performed on them by this pack's executor -
# matches split_track_item/trim_track_item/ripple_trim_track_item's own
# existing require_disposable gate (ADB_MCP_EDIT_TEST_), extended with the
# Batch Editing Orchestrator Pack's own disposable prefix (ADB_MCP_EXEC_TEST_)
# and the Export & End-to-End Production Pack's disposable_duplicate working
# sequence prefix (ADB_MCP_PRODUCTION_TEST_, see
# production_workflow.working_sequence_name). Planning-pack sequences
# (ADB_MCP_PLAN_TEST_) are intentionally NOT included - that pack's own
# design never intended its fixtures to become executable.
EXECUTABLE_SEQUENCE_PREFIXES = (
    "ADB_MCP_EDIT_TEST_", "ADB_MCP_EXEC_TEST_", "ADB_MCP_PRODUCTION_TEST_",
    "ADB_MCP_RECONCILED_RESUME_TEST_", "ADB_MCP_SPLIT_SCRATCH_TEST_", "ADB_MCP_PILOT3_",
)

DESTRUCTIVE_OPERATION_TYPES = {"split_track_item", "trim_track_item", "remove_track_item"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_executable_sequence_name(name: Any) -> bool:
    return bool(isinstance(name, str) and name.startswith(EXECUTABLE_SEQUENCE_PREFIXES))


def classify_sequence(
    *, sequence_id: str | None, name: str | None, active: bool = False,
    protected: bool = False, workflow_working_ids: set[str] | None = None,
    workflow_source_ids: set[str] | None = None, journal_created_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Conservative, evidence-bearing sequence classification.

    Identity and workflow lineage outrank names. Unknown is deliberately
    production-gated; a disposable-looking prefix alone is accepted only for
    the long-established test prefixes.
    """
    working = workflow_working_ids or set()
    sources = workflow_source_ids or set()
    journal_created = journal_created_ids or set()
    evidence: list[str] = []
    if sequence_id in working:
        evidence.append("workflow-created working sequence identity")
        if sequence_id in journal_created:
            evidence.append("journal sequence creation record")
        return {"sequenceClass": "workflow_working_copy", "confidence": "exact", "evidence": evidence, "destructivePolicy": "workflow_only"}
    if protected or sequence_id in sources:
        if protected:
            evidence.append("explicit protected/source sequence registry")
        if sequence_id in sources:
            evidence.append("workflow source sequence identity")
        if active:
            evidence.append("currently active sequence")
        return {"sequenceClass": "production_source", "confidence": "exact", "evidence": evidence, "destructivePolicy": "workflow_only"}
    if is_executable_sequence_name(name):
        evidence.append(f"known disposable prefix: {next(p for p in EXECUTABLE_SEQUENCE_PREFIXES if name.startswith(p))}")
        return {"sequenceClass": "disposable_test", "confidence": "high", "evidence": evidence, "destructivePolicy": "raw_allowed_with_confirmation"}
    if active:
        evidence.append("active sequence without disposable/workflow lineage")
    else:
        evidence.append("no trusted disposable or workflow lineage")
    return {"sequenceClass": "unknown", "confidence": "low", "evidence": evidence, "destructivePolicy": "treat_as_production"}


def build_operation_expectations(state: dict[str, Any], candidate_groups: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Freeze expected-before identity/bounds for every approved operation.

    This is captured during prepare, before mutation. It gives reconciliation
    positive evidence for operations that never received a dispatch receipt.
    """
    expectations: dict[str, dict[str, Any]] = {}
    for group in candidate_groups:
        operations = group.get("operations") or []
        if not operations:
            continue
        selector = operations[0].get("targetSelector") or {}
        tracks = state.get("videoTracks" if selector.get("mediaType") == "video" else "audioTracks") or []
        track = next((row for row in tracks if row.get("trackIndex") == selector.get("trackIndex")), None)
        items = (track or {}).get("items") or []
        item_index = selector.get("itemIndex")
        if not isinstance(item_index, int) or item_index < 0 or item_index >= len(items):
            continue
        item = items[item_index]
        original = {
            "projectItemId": item.get("projectItemId"),
            "startTicks": str(item.get("startTicks")), "endTicks": str(item.get("endTicks")),
        }
        context = dict(original)
        split_points: list[int] = []
        for index, op in enumerate(operations):
            op_id, op_type = op.get("operationId"), op.get("type")
            params = op.get("parameters") or {}
            if op_type == "remove_track_item":
                interval = params.get("removedInterval") or {}
                if interval.get("startTicks") is not None and interval.get("endTicks") is not None:
                    context = {**context, "startTicks": str(interval["startTicks"]), "endTicks": str(interval["endTicks"])}
            if op_id:
                expectations[op_id] = dict(context)
            if op_type != "split_track_item":
                continue
            split_ticks = int(params.get("atSequenceTicks"))
            start, end = int(context["startTicks"]), int(context["endTicks"])
            split_points.append(split_ticks)
            next_op = operations[index + 1] if index + 1 < len(operations) else None
            if next_op and next_op.get("type") == "split_track_item":
                next_ticks = int((next_op.get("parameters") or {}).get("atSequenceTicks"))
                context = {**context, "startTicks": str(split_ticks), "endTicks": str(end)} if next_ticks > split_ticks else {**context, "startTicks": str(start), "endTicks": str(split_ticks)}
            elif next_op and next_op.get("type") == "remove_track_item" and len(split_points) >= 2:
                low, high = sorted(split_points[-2:])
                context = {**context, "startTicks": str(low), "endTicks": str(high)}
            else:
                context = {**context, "startTicks": str(split_ticks), "endTicks": str(end)}
    return expectations


def new_execution_id() -> str:
    return f"exec_{uuid.uuid4().hex}"


def new_checkpoint_id() -> str:
    return f"ckpt_{uuid.uuid4().hex[:12]}"


def group_operations_by_candidate(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Groups the plan's already latest-time-first-ordered operations list
    (see edit_plan.flatten_operations) back into per-candidate execution
    groups, preserving that same candidate order. Operation order within
    each group is preserved as-is (already dependency-consistent)."""
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for op in operations:
        candidate_id = op.get("candidateId")
        if candidate_id not in groups:
            groups[candidate_id] = {"candidateId": candidate_id, "operations": []}
            order.append(candidate_id)
        groups[candidate_id]["operations"].append(op)
    return [groups[cid] for cid in order]


def rebase_selector(
    state: dict[str, Any], *, media_type: str, track_index: int, project_item_id: str | None,
    expected_start_ticks: int | None = None, expected_end_ticks: int | None = None,
    tolerance_ticks: int = 0,
) -> dict[str, Any]:
    """Re-finds a TrackItem after a mutation by identity + expected boundary
    signals instead of reusing a stale itemIndex (section 8). Returns
    {"item":..., "itemIndex":..., "confidence": exact|inferred_high|ambiguous|missing, "evidence": [...], "warnings": [...]}."""
    if expected_start_ticks is None and expected_end_ticks is None:
        return {"item": None, "itemIndex": None, "confidence": "missing", "evidence": [], "warnings": ["No expected boundary was supplied to rebase against."]}
    tracks = state.get("videoTracks" if media_type == "video" else "audioTracks") or []
    track = next((row for row in tracks if row.get("trackIndex") == track_index), None)
    if track is None:
        return {"item": None, "itemIndex": None, "confidence": "missing", "evidence": [], "warnings": ["Track identity was not found."]}

    matches = []
    for index, item in enumerate(track.get("items") or []):
        if project_item_id is not None and item.get("projectItemId") != project_item_id:
            continue
        item_start = item.get("startTicks")
        item_end = item.get("endTicks")
        if item_start is None or item_end is None:
            continue
        item_start, item_end = int(item_start), int(item_end)
        start_ok = expected_start_ticks is None or abs(item_start - expected_start_ticks) <= tolerance_ticks
        end_ok = expected_end_ticks is None or abs(item_end - expected_end_ticks) <= tolerance_ticks
        if start_ok and end_ok:
            matches.append((index, item, item_start, item_end))

    if not matches:
        return {
            "item": None, "itemIndex": None, "confidence": "missing", "evidence": [],
            "warnings": [f"No TrackItem matched projectItemId={project_item_id!r} start={expected_start_ticks} end={expected_end_ticks}."],
        }
    if len(matches) > 1:
        return {
            "item": None, "itemIndex": None, "confidence": "ambiguous",
            "evidence": [{"itemIndex": m[0], "startTicks": str(m[2]), "endTicks": str(m[3])} for m in matches],
            "warnings": [f"{len(matches)} TrackItems matched the same identity/boundary signals."],
        }
    index, item, item_start, item_end = matches[0]
    exact = (
        (expected_start_ticks is None or item_start == expected_start_ticks)
        and (expected_end_ticks is None or item_end == expected_end_ticks)
    )
    return {
        "item": item, "itemIndex": index, "confidence": "exact" if exact else "inferred_high",
        "evidence": [{"itemIndex": index, "startTicks": str(item_start), "endTicks": str(item_end), "projectItemId": item.get("projectItemId")}],
        "warnings": [],
    }


def build_execution_manifest(plan: dict[str, Any], approved_operation_ids: set[str]) -> dict[str, Any]:
    """Filters the plan's operations to only the approved ones, groups them by
    candidate (preserving the plan's own latest-time-first order), and
    classifies each operation's supportability/rollback class up front -
    all pure, no mutation (section 5, step 8-11)."""
    operations = [op for op in (plan.get("operations") or []) if op.get("operationId") in approved_operation_ids]
    unsupported = [op["operationId"] for op in operations if op.get("type") not in SUPPORTED_OPERATION_TYPES]
    groups = group_operations_by_candidate(operations)
    return {
        "operationOrder": [op["operationId"] for op in operations],
        "candidateGroups": groups,
        "unsupportedOperationIds": unsupported,
        "rollbackClasses": {op["operationId"]: op.get("rollbackClass", "none") for op in operations},
    }


def new_operation_result(op: dict[str, Any]) -> dict[str, Any]:
    return {
        "operationId": op.get("operationId"), "candidateId": op.get("candidateId"), "type": op.get("type"), "status": "pending",
        "originalSelector": op.get("targetSelector"), "resolvedSelector": None, "backend": "uxp",
        "beforeSnapshot": None, "expectedAfter": None, "actualAfter": None,
        "fingerprintBefore": None, "fingerprintAfter": None, "mutationOccurred": False,
        "readbackMatched": None, "inverseRecord": None, "residualMutation": False,
        "startedAt": None, "completedAt": None, "warnings": [], "error": None,
    }


def new_checkpoint(candidate_id: str, completed_operation_ids: list[str], *, fingerprint: str | None,
                    sequence_duration_ticks: int | None, estimated_removed_ticks: int, actual_removed_ticks: int,
                    journal_offset: int) -> dict[str, Any]:
    return {
        "checkpointId": new_checkpoint_id(), "candidateId": candidate_id,
        "completedOperationIds": list(completed_operation_ids), "fingerprint": fingerprint,
        "sequenceDurationTicks": str(sequence_duration_ticks) if sequence_duration_ticks is not None else None,
        "estimatedRemovedDurationSeconds": estimated_removed_ticks / 254_016_000_000,
        "actualRemovedDurationSeconds": actual_removed_ticks / 254_016_000_000,
        "createdAt": utc_now_iso(), "journalOffset": journal_offset,
    }


def new_execution(plan: dict[str, Any], *, approved_operation_ids: list[str], execution_policy: str,
                   rollback_policy: str, production_mode: bool, initial_fingerprint: str | None,
                   workflow_id: str | None = None, expected_before_by_operation: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    manifest = build_execution_manifest(plan, set(approved_operation_ids))
    return {
        "schemaVersion": EXECUTION_SCHEMA_VERSION,
        "executionId": new_execution_id(),
        "planId": plan.get("planId"), "planHash": plan.get("planHash"), "workflowId": workflow_id,
        "status": "prepared", "executionMode": "synchronous",
        "project": plan.get("project"), "sequence": plan.get("sequence"),
        "initialFingerprint": initial_fingerprint, "currentFingerprint": initial_fingerprint,
        "approvedOperationIds": list(approved_operation_ids),
        "expectedBeforeByOperation": expected_before_by_operation or {},
        "operationOrder": manifest["operationOrder"],
        "candidateGroups": manifest["candidateGroups"],
        "unsupportedOperationIds": manifest["unsupportedOperationIds"],
        "operationResults": [new_operation_result(op) for op in (plan.get("operations") or []) if op.get("operationId") in set(approved_operation_ids)],
        "checkpoints": [],
        "rollback": {"status": "none", "attempted": False, "restoredCheckpointId": None, "residualMutations": []},
        "executionPolicy": execution_policy, "rollbackPolicy": rollback_policy, "productionMode": production_mode,
        "summary": {}, "createdAt": utc_now_iso(), "startedAt": None, "completedAt": None,
        "warnings": [], "error": None,
    }
