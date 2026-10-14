"""Persistent, atomic on-disk state for run_end_to_end_edit_workflow, so a
workflowId survives an MCP process restart (First Production Pilot &
Persistent Recovery Pack v1). Reuses premiere_mutations.redact_sensitive
directly (not reimplemented) so persisted state follows the exact same
privacy policy as the existing JSONL mutation journal. State lives outside
the repository working tree, under the OS temp directory - never inside
D:\\Dev\\adb-mcp - one JSON file per workflowId so concurrent workflows can
never corrupt each other's state.

Only the atomic file I/O (write-to-temp, fsync, os.replace, readback) is
impure; schema/hash validation is pure and independently unit-testable.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .premiere_mutations import redact_sensitive

STATE_SCHEMA_VERSION = "1.0"

# The fingerprint scheme used for sourceFingerprint/workingFingerprint below
# (Export Restart Recovery & Fingerprint Consistency Patch v1, section 12).
# A state file missing this key predates schema versioning entirely, but was
# still produced by the same sequence_fingerprint() hash math via
# _edit_sequence_record (which has always fetched with include_items=True) -
# so it is treated as compatible rather than guess-converted. A state file
# that DOES carry this key with a *different* value came from an
# incompatible future scheme and must never be compared against a live
# fingerprint computed under the current one.
FINGERPRINT_SCHEMA_VERSION = "production-sequence-fingerprint/1"

_UNSAFE_FILENAME_CHARS = set('/\\:*?"<>|')


class WorkflowStateError(ValueError):
    """A fail-closed workflow-state persistence/recovery error with a stable
    public error code (WORKFLOW_STATE_NOT_FOUND/_CORRUPT/_PERSIST_FAILED)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def default_state_dir() -> Path:
    """Runtime state directory: OS temp dir (same base as
    premiere_mutations.DEFAULT_JOURNAL), never the repository working tree."""
    return Path(tempfile.gettempdir()) / "adb-mcp-workflow-state"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(workflow_id: str, state_dir: Path) -> Path:
    if not isinstance(workflow_id, str) or not workflow_id or any(c in _UNSAFE_FILENAME_CHARS for c in workflow_id):
        raise WorkflowStateError("INVALID_ARGUMENT", f"Unsafe or empty workflow_id for a state filename: {workflow_id!r}")
    return state_dir / f"{workflow_id}.json"


def _canonical_json(content: dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)


def compute_content_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()


def _collect_artifacts(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = []
    checkpoint_path = (workflow.get("checkpoint") or {}).get("checkpointPath")
    if checkpoint_path:
        artifacts.append({"type": "project_checkpoint", "path": checkpoint_path})
    export_path = (workflow.get("export") or {}).get("outputPath")
    if export_path:
        artifacts.append({"type": "export_output", "path": export_path})
    return artifacts


def build_state(workflow: dict[str, Any]) -> dict[str, Any]:
    """Projects an in-memory end-to-end workflow dict into the persisted
    schema (section 2 of the pack spec). Applies redact_sensitive to a full
    snapshot of the workflow - the same policy the existing mutation journal
    already uses - so no raw caption/transcript/Source Text/subtitle content
    is ever written to disk; the "workflow" key carries that full redacted
    snapshot (structural fields like ids/ticks/stage names are untouched,
    since redaction only triggers on keys containing text/caption/subtitle/
    transcript) so an "exact" recovery can rebuild the in-process workflow
    object verbatim, not just report a summary."""
    redacted_workflow = redact_sensitive(dict(workflow))
    source = workflow.get("sourceSequence") or {}
    working = workflow.get("workingSequence") or {}
    content = {
        "workflowId": workflow.get("workflowId"),
        "status": workflow.get("status"),
        "projectIdentity": workflow.get("project"),
        "sourceSequence": source,
        "workingSequence": working,
        "sourceFingerprint": source.get("fingerprint"),
        "workingFingerprint": working.get("fingerprint"),
        "fingerprintSchemaVersion": FINGERPRINT_SCHEMA_VERSION,
        "completedStages": list(workflow.get("completedStages") or []),
        "currentStage": workflow.get("currentStage"),
        "editPlan": redacted_workflow.get("editPlan"),
        "editExecutionId": (workflow.get("editExecution") or {}).get("executionId"),
        "exportId": (workflow.get("export") or {}).get("exportId"),
        "checkpoints": workflow.get("stageCheckpoints") or [],
        "artifacts": _collect_artifacts(workflow),
        "cleanup": workflow.get("cleanup"),
        "updatedAt": utc_now_iso(),
        "workflow": redacted_workflow,
    }
    envelope = {"schemaVersion": STATE_SCHEMA_VERSION, **content}
    envelope["contentHash"] = compute_content_hash(envelope)
    return envelope


def validate_state_schema(state: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(state, dict):
        return ["state must be an object."]
    if state.get("schemaVersion") != STATE_SCHEMA_VERSION:
        errors.append(f"Unsupported schemaVersion: {state.get('schemaVersion')!r}")
    for key in ("workflowId", "status", "contentHash", "updatedAt"):
        if key not in state:
            errors.append(f"Missing required state key: {key!r}")
    return errors


def verify_state_hash(state: dict[str, Any]) -> bool:
    stored_hash = state.get("contentHash")
    if not isinstance(stored_hash, str):
        return False
    recomputed_input = {k: v for k, v in state.items() if k != "contentHash"}
    return compute_content_hash(recomputed_input) == stored_hash


def load_state_file(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Pure(ish) read + validate - never raises for an ordinary missing/
    corrupt file, returns (None, [reasons]) instead so callers can build
    their own WORKFLOW_STATE_NOT_FOUND/_CORRUPT response."""
    if not path.is_file():
        return None, ["WORKFLOW_STATE_NOT_FOUND: no state file at this path."]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"WORKFLOW_STATE_CORRUPT: {exc}"]
    schema_errors = validate_state_schema(state)
    if schema_errors:
        return None, [f"WORKFLOW_STATE_CORRUPT: {e}" for e in schema_errors]
    if not verify_state_hash(state):
        return None, ["WORKFLOW_STATE_CORRUPT: content hash does not match stored state (possible partial write or tampering)."]
    return state, []


def atomic_write_state(state: dict[str, Any], *, state_dir: Path | None = None) -> Path:
    """Writes state to a uniquely-named temp file in the same directory,
    flushes+fsyncs it, then os.replace()'s it into place - atomic on both
    POSIX and Windows, so a reader never observes a partially-written file.
    Always reads the result back and hash-verifies it before returning;
    raises WorkflowStateError("WORKFLOW_STATE_PERSIST_FAILED", ...) rather
    than ever claiming success on an unverified write."""
    workflow_id = state.get("workflowId")
    directory = state_dir or default_state_dir()
    final_path = _state_path(workflow_id, directory)
    tmp_path = directory / f".{workflow_id}.json.tmp-{uuid.uuid4().hex[:8]}"
    encoded = json.dumps(state, ensure_ascii=False)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, final_path)
    except OSError as exc:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise WorkflowStateError("WORKFLOW_STATE_PERSIST_FAILED", f"Could not write state file: {exc}") from exc

    readback, errors = load_state_file(final_path)
    if readback is None:
        raise WorkflowStateError("WORKFLOW_STATE_PERSIST_FAILED", f"Post-write readback failed for {final_path}: {'; '.join(errors)}")
    return final_path


def load_workflow_state(workflow_id: str, *, state_dir: Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    directory = state_dir or default_state_dir()
    return load_state_file(_state_path(workflow_id, directory))


def list_workflow_states(*, state_dir: Path | None = None) -> list[dict[str, Any]]:
    directory = state_dir or default_state_dir()
    if not directory.is_dir():
        return []
    summaries = []
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("."):
            continue
        state, errors = load_state_file(path)
        if state is None:
            summaries.append({"workflowId": path.stem, "status": "corrupt", "currentStage": None, "updatedAt": None, "errors": errors})
            continue
        summaries.append({
            "workflowId": state["workflowId"], "status": state["status"],
            "currentStage": state.get("currentStage"), "updatedAt": state.get("updatedAt"),
            "errors": [],
        })
    return summaries


def delete_workflow_state(workflow_id: str, *, state_dir: Path | None = None) -> bool:
    directory = state_dir or default_state_dir()
    path = _state_path(workflow_id, directory)
    if path.is_file():
        path.unlink()
        return True
    return False
