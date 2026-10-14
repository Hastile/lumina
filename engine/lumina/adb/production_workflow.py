"""Export & End-to-End Production Pack v1 - pure workflow-model helpers.

No Premiere calls, no subprocess calls, no filesystem I/O here - mirrors
edit_executor.py's and premiere_export.py's convention of keeping the
state-machine/bookkeeping logic pure and unit-testable. pr-mcp.py owns all
I/O (UXP mutation calls, ffmpeg/ffprobe, journal writes) and drives this
schema/status machine from the outside.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

WORKFLOW_SCHEMA_VERSION = "1.0"

WORKFLOW_STATUSES = {
    "prepared", "analyzing", "planning", "awaiting_approval", "editing",
    "post_processing", "exporting", "validating", "completed",
    "partially_completed", "failed", "cleanup_pending", "rolled_back",
}

# Ordered per section 16 of the pack spec. Each entry is recorded at most
# once as a *completed* stage checkpoint; resume must never repeat one.
WORKFLOW_STAGES = (
    "project_checkpoint_created",
    "working_sequence_created",
    "analysis_completed",
    "plan_created",
    "plan_approved",
    "edit_execution_completed",
    "audio_completed",
    "effects_completed",
    "caption_completed",
    "export_started",
    "export_completed",
    "validation_completed",
)

WORKING_SEQUENCE_POLICIES = {"disposable_duplicate", "existing_test_sequence", "production_in_place"}
CLEANUP_POLICIES = {"keep_all", "remove_working_sequence_on_success", "remove_working_sequence_on_failure", "remove_temp_files_only"}

# Working sequences this pack creates via disposable_duplicate - reuses the
# Batch Editing Orchestrator Pack's own ADB_MCP_EXEC_TEST_ prefix scheme so
# every existing disposable-sequence safety gate (is_executable_sequence_name,
# split_track_item's require_disposable, etc.) already recognizes and
# protects/allows it with zero extra plumbing.
WORKING_SEQUENCE_NAME_PREFIX = "ADB_MCP_PRODUCTION_TEST_"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_workflow_id() -> str:
    return f"workflow_{uuid.uuid4().hex}"


def working_sequence_name(timestamp: str | None = None, source_name: str | None = None) -> str:
    """Disposable working-sequence name. When source_name is given (First
    Production Pilot Pack v1, section 7), embeds a sanitized version of it
    for traceability (e.g. ADB_MCP_PRODUCTION_TEST_shorts_seq_<timestamp>)
    while keeping the same already-verified ADB_MCP_PRODUCTION_TEST_ prefix
    - a distinct ADB_MCP_PILOT_ prefix would need its own registration in
    edit_executor.EXECUTABLE_SEQUENCE_PREFIXES and both UXP JS disposable-
    sequence gates (a real gap found live in the previous pack), so this
    reuses the one prefix already wired and live-verified everywhere."""
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if source_name:
        sanitized = re.sub(r"[^A-Za-z0-9_]+", "_", source_name).strip("_")[:40] or "seq"
        return f"{WORKING_SEQUENCE_NAME_PREFIX}{sanitized}_{ts}"
    return f"{WORKING_SEQUENCE_NAME_PREFIX}{ts}"


def new_workflow(
    *, source_sequence_id: str, working_sequence_policy: str, production_mode: bool,
    cleanup_policy: str, expected_source_fingerprint: str | None,
) -> dict[str, Any]:
    if working_sequence_policy not in WORKING_SEQUENCE_POLICIES:
        raise ValueError(f"Unsupported working_sequence_policy: {working_sequence_policy!r}")
    if cleanup_policy not in CLEANUP_POLICIES:
        raise ValueError(f"Unsupported cleanup_policy: {cleanup_policy!r}")
    return {
        "schemaVersion": WORKFLOW_SCHEMA_VERSION,
        "workflowId": new_workflow_id(),
        "status": "prepared",
        "project": None,
        "sourceSequence": {"id": source_sequence_id, "fingerprint": expected_source_fingerprint},
        "workingSequence": None,
        "workingSequencePolicy": working_sequence_policy,
        "productionMode": bool(production_mode),
        "checkpoint": None,
        "analysis": None,
        "editPlan": None,
        "editExecution": None,
        "audio": None,
        "effects": None,
        "captions": None,
        "export": None,
        "validation": None,
        "cleanupPolicy": cleanup_policy,
        "cleanup": None,
        "completedStages": [],
        "stageCheckpoints": [],
        "currentStage": None,
        "warnings": [],
        "createdAt": utc_now_iso(),
        "startedAt": None,
        "completedAt": None,
        "error": None,
    }


def record_stage_completed(
    workflow: dict[str, Any], stage: str, *, source_fingerprint: str | None, working_fingerprint: str | None,
    related_id: str | None = None, journal_offset: int | None = None, artifacts: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Appends a stage checkpoint (section 16) and marks the stage completed.
    Never re-adds a stage that's already in completedStages (idempotent)."""
    if stage not in WORKFLOW_STAGES:
        raise ValueError(f"Unknown workflow stage: {stage!r}")
    checkpoint = {
        "workflowId": workflow["workflowId"], "stage": stage,
        "sourceFingerprint": source_fingerprint, "workingFingerprint": working_fingerprint,
        "relatedId": related_id, "journalOffset": journal_offset,
        "artifacts": list(artifacts or []), "warnings": list(warnings or []),
        "recordedAt": utc_now_iso(),
    }
    workflow["stageCheckpoints"].append(checkpoint)
    if stage not in workflow["completedStages"]:
        workflow["completedStages"].append(stage)
    workflow["currentStage"] = stage
    return checkpoint


def is_stage_completed(workflow: dict[str, Any], stage: str) -> bool:
    return stage in (workflow.get("completedStages") or [])


def last_stage_checkpoint(workflow: dict[str, Any]) -> dict[str, Any] | None:
    checkpoints = workflow.get("stageCheckpoints") or []
    return checkpoints[-1] if checkpoints else None


def check_resume_staleness(
    workflow: dict[str, Any], *, current_source_fingerprint: str | None, current_working_fingerprint: str | None,
) -> str | None:
    """Returns a human-readable staleness reason (WORKFLOW_RESUME_STALE cause)
    if the workflow's last stage checkpoint no longer matches the live
    fingerprints, else None. A workflow with no checkpoints yet is never
    stale (nothing to compare against)."""
    checkpoint = last_stage_checkpoint(workflow)
    if checkpoint is None:
        return None
    if checkpoint.get("sourceFingerprint") is not None and checkpoint["sourceFingerprint"] != current_source_fingerprint:
        return f"Source sequence fingerprint changed since stage {checkpoint['stage']!r} (expected {checkpoint['sourceFingerprint']!r}, got {current_source_fingerprint!r})."
    if checkpoint.get("workingFingerprint") is not None and current_working_fingerprint is not None and checkpoint["workingFingerprint"] != current_working_fingerprint:
        return f"Working sequence fingerprint changed since stage {checkpoint['stage']!r} (expected {checkpoint['workingFingerprint']!r}, got {current_working_fingerprint!r})."
    return None


def public_workflow_view(workflow: dict[str, Any]) -> dict[str, Any]:
    return dict(workflow)


# Stages whose completion always implies at least one real mutation (project
# save, sequence creation, TrackItem edits, audio/effect/caption writes,
# export). Used only as workflow_failure_envelope's *default* for
# mutationOccurred - a failure mid-way through edit_execution_completed
# (e.g. one candidate's real split/remove succeeded before a later candidate
# hit SPLIT_DESTINATION_COLLISION) must still report mutationOccurred=true
# even though that stage itself never reached record_stage_completed.
_MUTATING_STAGES = {
    "project_checkpoint_created", "working_sequence_created", "edit_execution_completed",
    "audio_completed", "effects_completed", "caption_completed", "export_started", "export_completed",
}


def workflow_has_mutated(workflow: dict[str, Any]) -> bool:
    if any(stage in _MUTATING_STAGES for stage in workflow.get("completedStages") or []):
        return True
    operation_results = (workflow.get("editExecution") or {}).get("operationResults") or []
    return any(r.get("mutationOccurred") for r in operation_results)


def workflow_failure_envelope(workflow: dict[str, Any], code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Standard failure shape required by section 20: every failure response
    must report mutationOccurred/currentStage/completedStages/workingSequence/
    checkpoint/editExecution/export/cleanup/residualArtifacts. mutationOccurred
    defaults to workflow_has_mutated(workflow) (accurate even for a failure
    that happens mid-stage, e.g. a partially_completed batch edit) but callers
    can still override it explicitly via the mutationOccurred kwarg."""
    return {
        "status": "FAILED",
        "mutationOccurred": extra.pop("mutationOccurred", workflow_has_mutated(workflow)),
        "workflowId": workflow.get("workflowId"),
        "currentStage": workflow.get("currentStage"),
        "completedStages": list(workflow.get("completedStages") or []),
        "workingSequence": workflow.get("workingSequence"),
        "checkpoint": workflow.get("checkpoint"),
        "editExecution": workflow.get("editExecution"),
        "export": workflow.get("export"),
        "cleanup": workflow.get("cleanup"),
        "residualArtifacts": extra.pop("residualArtifacts", []),
        "error": {"code": code, "message": message},
        **extra,
    }
