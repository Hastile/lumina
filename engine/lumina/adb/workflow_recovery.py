"""Operation receipts and execution-state reconciliation
(Production Hardening & Mid-Batch Recovery Pack v1).

Pure schema/classification/idempotency-key logic lives here, plus the same
atomic-file-I/O pattern workflow_state_store.py already established for
persisted workflow state (one JSON file per receipt, temp-write + fsync +
os.replace + mandatory post-write readback/hash verification). mcp/pr-mcp.py
owns the actual UXP mutation calls, journal writes, and live-sequence reads;
this module never touches Premiere.

Section 3 of the pack spec lists six crash windows (A-F). The underlying
split_track_item/trim_track_item/remove_track_item calls this codebase
already had (from earlier packs) perform mutation + readback + journal-write
within a single Python function call, with no exposed intermediate
checkpoint - so crash windows C ("after backend mutation succeeds, before
Python receives the response") and D ("after response, before journal") are
not independently distinguishable from a receipt's status field alone in
this v1. A receipt sitting at "mutation_dispatched" with no later
"completed"/"failed" update represents that whole C/D window collectively;
resolving it always requires checking whether the live sequence matches
expectedAfter, which is exactly what classify_operation_state does. Crash
windows A and B (before or shortly after dispatch, no mutation yet) and E/F
(candidate/workflow-level, already covered by mcp/production_workflow.py's
and edit_executor.py's own checkpoint machinery from earlier packs) are
fully distinguishable.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .premiere_mutations import redact_sensitive

RECEIPT_SCHEMA_VERSION = "1.0"
RECONCILIATION_SCHEMA_VERSION = "1.0"

OPERATION_RECEIPT_STATUSES = {
    "pending", "started", "mutation_dispatched", "mutation_observed",
    "readback_verified", "journal_committed", "checkpoint_committed",
    "completed", "applied_unverified", "failed", "indeterminate",
}

RESUME_STATUSES = {
    "resume_prepared", "reconciling", "resuming", "candidate_recovered",
    "completed", "partially_completed", "blocked", "stale", "ambiguous", "failed",
}

CRASH_WINDOWS = {
    "A": "before_operation_start",
    "B": "after_receipt_before_dispatch",
    "C": "after_backend_dispatch_before_response",
    "D": "after_response_before_journal",
    "E": "after_operation_before_candidate_checkpoint",
    "F": "after_checkpoint_before_workflow_persist",
}

OPERATION_VERDICTS = {
    "definitely_not_applied", "definitely_applied", "applied_and_verified",
    "partially_applied", "ambiguous", "target_missing", "sequence_stale",
}

CANDIDATE_STATES = {
    "not_started", "in_progress_consistent", "completed",
    "partially_applied_recoverable", "partially_applied_unrecoverable", "inconsistent",
}


class ReceiptError(ValueError):
    """A fail-closed operation-receipt error with a stable public error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def default_receipt_dir() -> Path:
    """Runtime receipt directory: OS temp dir (same base as
    workflow_state_store.default_state_dir), never the repository working tree."""
    return Path(tempfile.gettempdir()) / "adb-mcp-operation-receipts"


def default_reconciliation_dir() -> Path:
    """Runtime reconciliation snapshots, kept outside the repository."""
    return Path(tempfile.gettempdir()) / "adb-mcp-reconciliations"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def compute_receipt_set_hash(receipts: list[dict[str, Any]]) -> str:
    """Stable hash over a complete execution receipt set.

    Ordering on disk is not security-relevant, so receipts are normalized by
    operationId before hashing. Corrupt placeholders are included rather than
    silently discarded, making a later repair/replacement stale the snapshot.
    """
    normalized = sorted(receipts, key=lambda row: str(row.get("operationId")))
    return canonical_hash(normalized)


def journal_snapshot(path: Path) -> dict[str, Any]:
    """Return a byte offset and content hash for the mutation journal."""
    if not path.is_file():
        return {"journalOffset": 0, "journalHash": canonical_hash("")}
    payload = path.read_bytes()
    return {"journalOffset": len(payload), "journalHash": hashlib.sha256(payload).hexdigest()}


def compute_idempotency_key(
    *, project_id: str | None, sequence_id: str, plan_hash: str | None, candidate_id: str,
    operation_id: str, operation_type: str, target_selector: dict[str, Any] | None,
    parameters: dict[str, Any] | None, expected_before_fingerprint: str | None,
) -> str:
    """Deterministic key covering everything section 5 lists - if any of
    this changes between two calls claiming the "same" operation, they are
    NOT treated as retries of each other."""
    canonical = {
        "projectId": project_id, "sequenceId": sequence_id, "planHash": plan_hash,
        "candidateId": candidate_id, "operationId": operation_id, "operationType": operation_type,
        "targetSelector": target_selector or {}, "parameters": parameters or {},
        "expectedBeforeFingerprint": expected_before_fingerprint,
    }
    return hashlib.sha256(_canonical_json(canonical).encode("utf-8")).hexdigest()


def _receipt_id(execution_id: str, operation_id: str) -> str:
    return f"{execution_id}__{operation_id}"


def _receipt_path(execution_id: str, operation_id: str, receipt_dir: Path) -> Path:
    return receipt_dir / f"{_receipt_id(execution_id, operation_id)}.json"


def new_receipt(
    *, workflow_id: str | None, execution_id: str, candidate_id: str, operation_id: str,
    operation_type: str, idempotency_key: str, project_identity: dict[str, Any] | None,
    sequence_identity: dict[str, Any] | None, selector_before: dict[str, Any] | None,
    expected_before: dict[str, Any] | None, expected_after: dict[str, Any] | None,
    fingerprint_before: str | None, backend: str = "uxp",
) -> dict[str, Any]:
    """A freshly-dispatched receipt (crash window B has just closed - this
    call itself is the receipt write; window C opens immediately after)."""
    return {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "workflowId": workflow_id, "executionId": execution_id, "candidateId": candidate_id,
        "operationId": operation_id, "operationType": operation_type, "idempotencyKey": idempotency_key,
        "status": "mutation_dispatched",
        "projectIdentity": project_identity, "sequenceIdentity": sequence_identity,
        "selectorBefore": selector_before, "expectedBefore": expected_before, "expectedAfter": expected_after,
        "fingerprintBefore": fingerprint_before, "fingerprintAfter": None,
        "mutationDispatchedAt": utc_now_iso(), "mutationObservedAt": None,
        "readbackVerifiedAt": None, "journalCommittedAt": None,
        "backend": backend, "mutationOccurred": False, "error": None,
        "resumeAttemptCount": 0,
    }


def mark_receipt_completed(receipt: dict[str, Any], *, fingerprint_after: str | None, mutation_occurred: bool) -> dict[str, Any]:
    """Updates a dispatched receipt once split_track_item/trim_track_item/
    remove has returned - mutation_observed/readback_verified/
    journal_committed all happen inside that one call in this v1 (see
    module docstring), so they're stamped with the same timestamp here."""
    now = utc_now_iso()
    updated = dict(receipt)
    updated.update({
        "status": "completed" if mutation_occurred else "failed",
        "fingerprintAfter": fingerprint_after, "mutationOccurred": mutation_occurred,
        "mutationObservedAt": now, "readbackVerifiedAt": now, "journalCommittedAt": now,
    })
    return updated


def mark_receipt_failed(receipt: dict[str, Any], *, error: dict[str, Any]) -> dict[str, Any]:
    updated = dict(receipt)
    updated.update({"status": "failed", "mutationOccurred": False, "error": error})
    return updated


def mark_receipt_reconciled_applied(receipt: dict[str, Any], *, fingerprint_after: str | None) -> dict[str, Any]:
    """Durably records positive live evidence for a previously indeterminate dispatch.

    `applied_unverified` means the backend response itself was lost, while the
    reconciliation readback proved expectedAfter. Idempotency treats it as
    applied, but the distinct status preserves the crash-window audit trail.
    """
    now = utc_now_iso()
    updated = dict(receipt)
    updated.update({
        "status": "applied_unverified", "fingerprintAfter": fingerprint_after,
        "mutationOccurred": True, "mutationObservedAt": now,
        "readbackVerifiedAt": now, "reconciledAt": now,
    })
    return updated


def validate_receipt_schema(receipt: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt must be an object."]
    if receipt.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"Unsupported schemaVersion: {receipt.get('schemaVersion')!r}")
    for key in ("executionId", "operationId", "idempotencyKey", "status", "contentHash"):
        if key not in receipt:
            errors.append(f"Missing required receipt key: {key!r}")
    if receipt.get("status") is not None and receipt.get("status") not in OPERATION_RECEIPT_STATUSES:
        errors.append(f"Unknown receipt status: {receipt.get('status')!r}")
    return errors


def compute_receipt_hash(receipt_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(receipt_without_hash).encode("utf-8")).hexdigest()


def verify_receipt_hash(receipt: dict[str, Any]) -> bool:
    stored = receipt.get("contentHash")
    if not isinstance(stored, str):
        return False
    rest = {k: v for k, v in receipt.items() if k != "contentHash"}
    return compute_receipt_hash(rest) == stored


def load_receipt_file(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.is_file():
        return None, ["OPERATION_RECEIPT_NOT_FOUND: no receipt file at this path."]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            receipt = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"OPERATION_RECEIPT_CORRUPT: {exc}"]
    errors = validate_receipt_schema(receipt)
    if errors:
        return None, [f"OPERATION_RECEIPT_CORRUPT: {e}" for e in errors]
    if not verify_receipt_hash(receipt):
        return None, ["OPERATION_RECEIPT_CORRUPT: content hash does not match stored receipt."]
    return receipt, []


def atomic_write_receipt(receipt: dict[str, Any], *, receipt_dir: Path | None = None) -> Path:
    """Writes receipt to a uniquely-named temp file, fsyncs, then
    os.replace()'s it into place, then reads it back and hash-verifies it -
    never claims success on an unverified write. Applies redact_sensitive
    (the same policy as the mutation journal and persisted workflow state)
    before computing the hash, so no raw caption/transcript content is ever
    written even if a caller's `parameters` happened to carry any."""
    directory = receipt_dir or default_receipt_dir()
    execution_id, operation_id = receipt["executionId"], receipt["operationId"]
    redacted = redact_sensitive(dict(receipt))
    envelope = dict(redacted)
    # A receipt reloaded from disk (e.g. via list_receipts_for_execution,
    # then updated by mark_receipt_reconciled_applied) already carries its
    # own prior contentHash key. Hashing over that stale value produces a
    # hash that verify_receipt_hash's own contentHash-excluded computation
    # can never match on readback - confirmed live: updating an
    # already-applied operation's receipt during a reconciled resume
    # raised OPERATION_RECEIPT_CORRUPT on the immediate post-write
    # readback. Always strip any pre-existing contentHash before hashing,
    # exactly mirroring verify_receipt_hash's own exclusion.
    envelope.pop("contentHash", None)
    envelope["contentHash"] = compute_receipt_hash(envelope)
    final_path = _receipt_path(execution_id, operation_id, directory)
    tmp_path = directory / f".{_receipt_id(execution_id, operation_id)}.json.tmp-{uuid.uuid4().hex[:8]}"
    encoded = json.dumps(envelope, ensure_ascii=False)
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
        raise ReceiptError("OPERATION_RECEIPT_NOT_FOUND", f"Could not write receipt: {exc}") from exc
    readback, errors = load_receipt_file(final_path)
    if readback is None:
        raise ReceiptError("OPERATION_RECEIPT_CORRUPT", f"Post-write readback failed for {final_path}: {'; '.join(errors)}")
    return final_path


def load_receipt(execution_id: str, operation_id: str, *, receipt_dir: Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    directory = receipt_dir or default_receipt_dir()
    return load_receipt_file(_receipt_path(execution_id, operation_id, directory))


def list_receipts_for_execution(execution_id: str, *, receipt_dir: Path | None = None) -> list[dict[str, Any]]:
    directory = receipt_dir or default_receipt_dir()
    if not directory.is_dir():
        return []
    prefix = f"{execution_id}__"
    results: list[dict[str, Any]] = []
    for path in sorted(directory.glob(f"{prefix}*.json")):
        if path.name.startswith("."):
            continue
        receipt, errors = load_receipt_file(path)
        if receipt is None:
            results.append({"operationId": path.stem.split("__", 1)[-1], "status": "corrupt", "errors": errors})
        else:
            results.append(receipt)
    return results


def idempotency_verdict(existing_receipt: dict[str, Any] | None) -> str | None:
    """For a fresh dispatch attempt with a matching idempotencyKey (section
    5): 'already_applied' (skip the backend call entirely),
    'needs_reconciliation' (a dispatched-but-unconfirmed prior attempt
    exists - resolve via classify_operation_state before retrying),
    'repeated_failure_blocked' (this exact operation already failed once,
    was already retried once via reconciled resume, and failed again with
    nothing about the plan/selector/expected-before changing - refuse to
    retry a third time and loop forever on a genuinely unresolvable
    failure like SPLIT_DESTINATION_COLLISION), or None (no conflicting
    receipt, safe to proceed normally). The first retry of a failed
    operation is always allowed; only a second consecutive retry with an
    unchanged idempotencyKey is blocked."""
    if existing_receipt is None:
        return None
    status = existing_receipt.get("status")
    if status in {"completed", "applied_unverified"} and existing_receipt.get("mutationOccurred"):
        return "already_applied"
    if status == "mutation_dispatched":
        return "needs_reconciliation"
    if status == "failed" and int(existing_receipt.get("resumeAttemptCount") or 0) >= 1:
        return "repeated_failure_blocked"
    return None


# --- reconciliation classification (pure) ----------------------------------

def classify_operation_state(
    *, receipt: dict[str, Any] | None, live_target_found: bool,
    live_matches_expected_before: bool | None, live_matches_expected_after: bool | None,
    sequence_is_stale: bool = False,
) -> str:
    """Pure verdict per section 4's list. Never returns an "applied" verdict
    without positive live evidence, and never returns "not applied" for a
    receipt claiming completion without live evidence contradicting it."""
    if sequence_is_stale:
        return "sequence_stale"
    if receipt is None:
        if live_matches_expected_before:
            return "definitely_not_applied"
        if not live_target_found:
            return "target_missing"
        return "ambiguous"

    status = receipt.get("status")
    if status in {"completed", "applied_unverified"} and receipt.get("mutationOccurred"):
        if live_matches_expected_after:
            return "applied_and_verified"
        if live_matches_expected_before:
            return "ambiguous"
        return "definitely_applied"
    if status == "failed":
        if live_matches_expected_after:
            return "definitely_applied"
        if live_matches_expected_before:
            return "definitely_not_applied"
        return "ambiguous"
    if status == "mutation_dispatched":
        if live_matches_expected_after:
            return "definitely_applied"
        if live_matches_expected_before:
            return "definitely_not_applied"
        if not live_target_found:
            return "target_missing"
        return "ambiguous"
    if status in ("pending", "started"):
        return "definitely_not_applied" if live_matches_expected_before else "ambiguous"
    return "ambiguous"


def check_operation_live_state(
    state: dict[str, Any], op: dict[str, Any], expected_before: dict[str, Any] | None, expected_after: dict[str, Any] | None,
) -> tuple[bool, bool, bool]:
    """Pure comparison of one plan operation's expectedBefore/expectedAfter
    against a getTrackItemEditState-shaped `state` dict (the same shape
    edit_executor.rebase_selector already consumes). Returns
    (live_target_found, matches_expected_before, matches_expected_after) -
    the three signals classify_operation_state needs. Never touches
    Premiere itself; the caller is responsible for fetching `state` fresh."""
    selector = op.get("targetSelector") or {}
    media_type, track_index = selector.get("mediaType"), selector.get("trackIndex")
    tracks = (state.get("videoTracks") if media_type == "video" else state.get("audioTracks")) or []
    track = next((t for t in tracks if t.get("trackIndex") == track_index), None)
    items = (track.get("items") or []) if track is not None else []
    found_any = bool(items)

    project_item_id = (expected_before or {}).get("projectItemId")
    before_start, before_end = (expected_before or {}).get("startTicks"), (expected_before or {}).get("endTicks")
    matches_before = bool(before_start is not None and before_end is not None and any(
        item.get("projectItemId") == project_item_id
        and str(item.get("startTicks")) == str(before_start) and str(item.get("endTicks")) == str(before_end)
        for item in items
    ))

    op_type = op.get("type")
    matches_after = False
    if op_type == "split_track_item":
        # A single item edge landing on split_ticks is not evidence a split
        # happened here: an untouched item's own pre-existing endTicks can
        # coincidentally equal the very split point this operation failed
        # to create (confirmed live: a split rejected as
        # INVALID_SPLIT_POINT because split_ticks == the item's own end was
        # otherwise misread as "definitely_applied", since that end edge
        # trivially "matched"). Require both a left piece ending at
        # split_ticks AND a right piece starting at split_ticks (same
        # projectItemId) - genuine two-sided adjacency only a real split
        # produces.
        split_ticks = (expected_after or {}).get("splitTicks")
        if split_ticks is not None:
            has_left = any(item.get("projectItemId") == project_item_id and str(item.get("endTicks")) == str(split_ticks) for item in items)
            has_right = any(item.get("projectItemId") == project_item_id and str(item.get("startTicks")) == str(split_ticks) for item in items)
            matches_after = has_left and has_right
    elif op_type == "remove_track_item":
        # Applied iff the pre-removal exact interval no longer exists as a
        # live item on this track (the track itself must still have some
        # content, or "found_any" would already have signaled target_missing).
        matches_after = found_any and not matches_before
    elif op_type == "trim_track_item":
        # Same single-edge coincidence risk as split above: after an earlier
        # split, multiple live items can share this projectItemId, so also
        # require the item's *other* (untouched) edge to still match
        # expected_before - pinning down the one specific item this trim
        # targeted rather than any same-sourced item with a matching edge.
        value_ticks, mode = (expected_after or {}).get("valueTicks"), (expected_after or {}).get("mode")
        if value_ticks is not None and mode:
            changed_key = "startTicks" if mode in {"trim_start_to", "trim_start_by", "set_source_in"} else "endTicks"
            retained_key = "endTicks" if changed_key == "startTicks" else "startTicks"
            retained_expected = (expected_before or {}).get(retained_key)
            matches_after = any(
                item.get("projectItemId") == project_item_id
                and str(item.get(changed_key)) == str(value_ticks)
                and (retained_expected is None or str(item.get(retained_key)) == str(retained_expected))
                for item in items
            )
    return found_any, matches_before, matches_after


def classify_candidate_state(operation_verdicts: list[str]) -> str:
    """Pure per section 8. operation_verdicts must be in the candidate's own
    execution order (matches _execute_candidate_group's own sequencing)."""
    if not operation_verdicts:
        return "not_started"
    applied = {"applied_and_verified", "definitely_applied"}
    not_applied = {"definitely_not_applied"}
    unresolved = {"ambiguous", "target_missing", "sequence_stale"}
    if any(v in unresolved for v in operation_verdicts):
        return "inconsistent"
    if all(v in applied for v in operation_verdicts):
        return "completed"
    if all(v in not_applied for v in operation_verdicts):
        return "not_started"
    # Mixed applied/not-applied is only recoverable if every applied
    # operation precedes every not-applied one, matching this executor's
    # own strictly sequential per-candidate order - an applied operation
    # appearing *after* a not-applied one means execution order was
    # violated somehow and cannot be trusted.
    seen_not_applied = False
    for verdict in operation_verdicts:
        if verdict in not_applied:
            seen_not_applied = True
        elif verdict in applied and seen_not_applied:
            return "partially_applied_unrecoverable"
    return "partially_applied_recoverable"


# --- immutable reconciliation snapshots ----------------------------------

def new_reconciliation_snapshot(
    *, execution_id: str, workflow_id: str | None, live_fingerprint: str,
    receipts: list[dict[str, Any]], journal_offset: int, journal_hash: str,
    operation_states: dict[str, str], candidate_states: dict[str, str],
    next_operation: str | None, workflow_state_hash: str | None = None,
    expires_in_seconds: int | None = None,
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc)
    snapshot = {
        "schemaVersion": RECONCILIATION_SCHEMA_VERSION,
        "reconciliationId": f"recon_{uuid.uuid4().hex}",
        "executionId": execution_id, "workflowId": workflow_id,
        "createdAt": created_at.isoformat(), "liveFingerprint": live_fingerprint,
        "receiptSetHash": compute_receipt_set_hash(receipts),
        "journalOffset": int(journal_offset), "journalHash": journal_hash,
        "operationStates": dict(operation_states),
        "operationStatesHash": canonical_hash(operation_states),
        "candidateStates": dict(candidate_states),
        "candidateStatesHash": canonical_hash(candidate_states),
        "nextOperation": next_operation, "workflowStateHash": workflow_state_hash,
        "expiresAt": ((created_at + timedelta(seconds=expires_in_seconds)).isoformat()
                      if expires_in_seconds is not None else None),
    }
    return snapshot


def compute_reconciliation_hash(snapshot_without_hash: dict[str, Any]) -> str:
    return canonical_hash(snapshot_without_hash)


def verify_reconciliation_hash(snapshot: dict[str, Any]) -> bool:
    stored = snapshot.get("contentHash")
    if not isinstance(stored, str):
        return False
    return compute_reconciliation_hash({k: v for k, v in snapshot.items() if k != "contentHash"}) == stored


def validate_reconciliation_snapshot(snapshot: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(snapshot, dict):
        return ["reconciliation snapshot must be an object."]
    if snapshot.get("schemaVersion") != RECONCILIATION_SCHEMA_VERSION:
        errors.append(f"Unsupported schemaVersion: {snapshot.get('schemaVersion')!r}")
    for key in (
        "reconciliationId", "executionId", "createdAt", "liveFingerprint",
        "receiptSetHash", "journalOffset", "journalHash", "operationStates",
        "operationStatesHash", "candidateStates", "candidateStatesHash",
        "contentHash",
    ):
        if key not in snapshot:
            errors.append(f"Missing required reconciliation key: {key!r}")
    if isinstance(snapshot.get("operationStates"), dict) and canonical_hash(snapshot["operationStates"]) != snapshot.get("operationStatesHash"):
        errors.append("operationStatesHash mismatch.")
    if isinstance(snapshot.get("candidateStates"), dict) and canonical_hash(snapshot["candidateStates"]) != snapshot.get("candidateStatesHash"):
        errors.append("candidateStatesHash mismatch.")
    return errors


def _reconciliation_path(reconciliation_id: str, directory: Path) -> Path:
    if not isinstance(reconciliation_id, str) or not reconciliation_id or any(c in reconciliation_id for c in '/\\:*?"<>|'):
        raise ReceiptError("RECONCILIATION_NOT_FOUND", f"Unsafe or empty reconciliation_id: {reconciliation_id!r}")
    return directory / f"{reconciliation_id}.json"


def load_reconciliation_file(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.is_file():
        return None, ["RECONCILIATION_NOT_FOUND: no snapshot file at this path."]
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"RECONCILIATION_HASH_MISMATCH: {exc}"]
    errors = validate_reconciliation_snapshot(snapshot)
    if errors:
        return None, [f"RECONCILIATION_HASH_MISMATCH: {error}" for error in errors]
    if not verify_reconciliation_hash(snapshot):
        return None, ["RECONCILIATION_HASH_MISMATCH: content hash mismatch."]
    return snapshot, []


def atomic_write_reconciliation(snapshot: dict[str, Any], *, reconciliation_dir: Path | None = None) -> Path:
    directory = reconciliation_dir or default_reconciliation_dir()
    redacted = redact_sensitive(dict(snapshot))
    envelope = dict(redacted)
    # Same defensive strip as atomic_write_receipt - never hash over a
    # pre-existing contentHash key, so re-writing an already-persisted
    # snapshot dict can never desync from verify_reconciliation_hash's own
    # contentHash-excluded computation.
    envelope.pop("contentHash", None)
    envelope["contentHash"] = compute_reconciliation_hash(envelope)
    final_path = _reconciliation_path(envelope["reconciliationId"], directory)
    tmp_path = directory / f".{envelope['reconciliationId']}.json.tmp-{uuid.uuid4().hex[:8]}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(envelope, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, final_path)
    except OSError as exc:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise ReceiptError("RECONCILIATION_NOT_FOUND", f"Could not persist reconciliation: {exc}") from exc
    readback, errors = load_reconciliation_file(final_path)
    if readback is None:
        raise ReceiptError("RECONCILIATION_HASH_MISMATCH", "; ".join(errors))
    return final_path


def load_reconciliation(reconciliation_id: str, *, reconciliation_dir: Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    directory = reconciliation_dir or default_reconciliation_dir()
    try:
        path = _reconciliation_path(reconciliation_id, directory)
    except ReceiptError as exc:
        return None, [f"{exc.code}: {exc.message}"]
    return load_reconciliation_file(path)


# --- persistent export receipts (Export Restart Recovery & Fingerprint
# Consistency Patch v1) ----------------------------------------------------
#
# _SEQUENCE_EXPORTS (mcp/pr-mcp.py) is a plain in-process dict with no disk
# backing at all - unlike operation receipts above, an export previously
# had *no* durable trace once the MCP process restarted, other than
# whatever the owning workflow's own persisted `export` field happened to
# record (and only if the export ran through the workflow pipeline, not a
# raw prepare_sequence_export/export_sequence_verified call). These
# functions give every export a receipt using the exact same atomic-write-
# then-readback-verify pattern as the operation receipts above, including
# the contentHash-stripped-before-hashing fix from day one (the earlier
# Orphaned Execution Recovery patch found and fixed this exact bug in the
# operation-receipt functions after it corrupted a real receipt on re-write).

EXPORT_RECEIPT_SCHEMA_VERSION = "1.0"

EXPORT_RECEIPT_STATUSES = {
    "prepared", "dispatched", "output_detected", "stabilizing",
    "validating", "validated", "completed", "failed", "ambiguous",
}


def default_export_receipt_dir() -> Path:
    return Path(tempfile.gettempdir()) / "adb-mcp-export-receipts"


def compute_export_idempotency_key(
    *, project_id: str | None, workflow_id: str | None, sequence_id: str,
    sequence_fingerprint: str | None, preset_path: str, preset_hash: str | None,
    output_path_normalized: str, export_range: str, expected_duration_seconds: float | None,
) -> str:
    """Section 5: two prepare_sequence_export calls are the "same" export
    request iff every one of these agrees - a different output_path or
    preset is a genuinely different export, never treated as a retry."""
    canonical = {
        "projectId": project_id, "workflowId": workflow_id, "sequenceId": sequence_id,
        "sequenceFingerprint": sequence_fingerprint, "presetPath": preset_path, "presetHash": preset_hash,
        "outputPathNormalized": output_path_normalized, "range": export_range,
        "expectedDurationSeconds": expected_duration_seconds,
    }
    return hashlib.sha256(_canonical_json(canonical).encode("utf-8")).hexdigest()


def new_export_receipt(
    *, export_id: str, workflow_id: str | None, project_identity: dict[str, Any] | None,
    sequence_identity: dict[str, Any] | None, sequence_fingerprint: str | None,
    preset_path: str, preset_hash: str | None, output_path: str, export_range: str,
    expected_duration_seconds: float | None, idempotency_key: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": EXPORT_RECEIPT_SCHEMA_VERSION,
        "exportId": export_id, "workflowId": workflow_id,
        "projectIdentity": project_identity, "sequenceIdentity": sequence_identity,
        "sequenceFingerprint": sequence_fingerprint,
        "presetPath": preset_path, "presetHash": preset_hash,
        "outputPath": output_path, "range": export_range, "expectedDurationSeconds": expected_duration_seconds,
        "idempotencyKey": idempotency_key, "status": "prepared", "backendDispatchId": None,
        "preparedAt": utc_now_iso(), "startedAt": None, "backendReturnedAt": None, "outputDetectedAt": None,
        "stableSize": None, "outputFileSignature": None, "ffprobeSummary": None,
        "validationHash": None, "validatedAt": None, "completedAt": None, "error": None,
    }


def mark_export_receipt_dispatched(receipt: dict[str, Any], *, backend_dispatch_id: str | None) -> dict[str, Any]:
    updated = dict(receipt)
    updated.update({"status": "dispatched", "backendDispatchId": backend_dispatch_id, "startedAt": utc_now_iso()})
    return updated


def mark_export_receipt_output_detected(receipt: dict[str, Any], *, stable_size: int) -> dict[str, Any]:
    updated = dict(receipt)
    updated.update({"status": "output_detected", "outputDetectedAt": utc_now_iso(), "stableSize": stable_size})
    return updated


def compute_export_validation_hash(*, output_file_signature: dict[str, Any], ffprobe_summary: dict[str, Any] | None) -> str:
    """Binds the output file's own tamper-evident signature (size/mtime/
    edge-byte samples - never a full-file hash, since export outputs can be
    large) together with the ffprobe summary, so section 8's "append data
    to the end of a validated file" test is caught: appending changes both
    size and the last-bytes sample."""
    return canonical_hash({"outputFileSignature": output_file_signature, "ffprobeSummary": ffprobe_summary})


def mark_export_receipt_validated(receipt: dict[str, Any], *, output_file_signature: dict[str, Any], ffprobe_summary: dict[str, Any] | None) -> dict[str, Any]:
    validation_hash = compute_export_validation_hash(output_file_signature=output_file_signature, ffprobe_summary=ffprobe_summary)
    updated = dict(receipt)
    updated.update({
        "status": "validated", "outputFileSignature": output_file_signature,
        "ffprobeSummary": ffprobe_summary, "validationHash": validation_hash, "validatedAt": utc_now_iso(),
    })
    return updated


def mark_export_receipt_completed(receipt: dict[str, Any]) -> dict[str, Any]:
    updated = dict(receipt)
    updated.update({"status": "completed", "completedAt": utc_now_iso()})
    return updated


def mark_export_receipt_failed(receipt: dict[str, Any], *, error: dict[str, Any]) -> dict[str, Any]:
    updated = dict(receipt)
    updated.update({"status": "failed", "error": error})
    return updated


def mark_export_receipt_ambiguous(receipt: dict[str, Any], *, reason: str) -> dict[str, Any]:
    updated = dict(receipt)
    updated.update({"status": "ambiguous", "error": {"code": "EXPORT_STATE_AMBIGUOUS", "message": reason}})
    return updated


def validate_export_receipt_schema(receipt: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["export receipt must be an object."]
    if receipt.get("schemaVersion") != EXPORT_RECEIPT_SCHEMA_VERSION:
        errors.append(f"Unsupported schemaVersion: {receipt.get('schemaVersion')!r}")
    for key in ("exportId", "outputPath", "idempotencyKey", "status", "contentHash"):
        if key not in receipt:
            errors.append(f"Missing required export receipt key: {key!r}")
    if receipt.get("status") is not None and receipt.get("status") not in EXPORT_RECEIPT_STATUSES:
        errors.append(f"Unknown export receipt status: {receipt.get('status')!r}")
    return errors


def compute_export_receipt_hash(receipt_without_hash: dict[str, Any]) -> str:
    return canonical_hash(receipt_without_hash)


def verify_export_receipt_hash(receipt: dict[str, Any]) -> bool:
    stored = receipt.get("contentHash")
    if not isinstance(stored, str):
        return False
    rest = {k: v for k, v in receipt.items() if k != "contentHash"}
    return compute_export_receipt_hash(rest) == stored


def _export_receipt_path(export_id: str, directory: Path) -> Path:
    if not isinstance(export_id, str) or not export_id or any(c in export_id for c in '/\\:*?"<>|'):
        raise ReceiptError("EXPORT_RECEIPT_NOT_FOUND", f"Unsafe or empty export_id: {export_id!r}")
    return directory / f"{export_id}.json"


def load_export_receipt_file(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.is_file():
        return None, ["EXPORT_RECEIPT_NOT_FOUND: no receipt file at this path."]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            receipt = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"EXPORT_RECEIPT_CORRUPT: {exc}"]
    errors = validate_export_receipt_schema(receipt)
    if errors:
        return None, [f"EXPORT_RECEIPT_CORRUPT: {e}" for e in errors]
    if not verify_export_receipt_hash(receipt):
        return None, ["EXPORT_RECEIPT_CORRUPT: content hash does not match stored receipt."]
    return receipt, []


def atomic_write_export_receipt(receipt: dict[str, Any], *, receipt_dir: Path | None = None) -> Path:
    directory = receipt_dir or default_export_receipt_dir()
    export_id = receipt["exportId"]
    redacted = redact_sensitive(dict(receipt))
    envelope = dict(redacted)
    envelope.pop("contentHash", None)
    envelope["contentHash"] = compute_export_receipt_hash(envelope)
    final_path = _export_receipt_path(export_id, directory)
    tmp_path = directory / f".{export_id}.json.tmp-{uuid.uuid4().hex[:8]}"
    encoded = json.dumps(envelope, ensure_ascii=False)
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
        raise ReceiptError("EXPORT_RECEIPT_NOT_FOUND", f"Could not write export receipt: {exc}") from exc
    readback, errors = load_export_receipt_file(final_path)
    if readback is None:
        raise ReceiptError("EXPORT_RECEIPT_CORRUPT", f"Post-write readback failed for {final_path}: {'; '.join(errors)}")
    return final_path


def load_export_receipt(export_id: str, *, receipt_dir: Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    directory = receipt_dir or default_export_receipt_dir()
    try:
        path = _export_receipt_path(export_id, directory)
    except ReceiptError as exc:
        return None, [f"{exc.code}: {exc.message}"]
    return load_export_receipt_file(path)


def export_idempotency_verdict(existing_receipt: dict[str, Any] | None) -> str | None:
    """'already_completed' (skip backend dispatch entirely, return
    EXPORT_ALREADY_COMPLETED), 'reconciliation_required' (a dispatched-but-
    unresolved prior attempt exists for this exact idempotency key - resolve
    via export reconstruction before dispatching again), or None (safe to
    dispatch normally)."""
    if existing_receipt is None:
        return None
    status = existing_receipt.get("status")
    if status in {"validated", "completed"}:
        return "already_completed"
    if status in {"dispatched", "output_detected", "stabilizing", "validating"}:
        return "reconciliation_required"
    return None


def list_export_receipts(*, receipt_dir: Path | None = None) -> list[dict[str, Any]]:
    """All export receipts on disk, newest-modified first. Export receipts
    are filed by exportId (`{export_id}.json`), not by idempotencyKey, so
    finding "does an export with this idempotency key already exist" (needed
    since a restart wipes the in-process _SEQUENCE_EXPORTS store and a fresh
    prepare_sequence_export call would otherwise mint a brand-new exportId
    with no way to find its predecessor) requires scanning this list rather
    than a direct path lookup."""
    directory = receipt_dir or default_export_receipt_dir()
    if not directory.is_dir():
        return []
    paths = [p for p in directory.glob("*.json") if not p.name.startswith(".")]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    results: list[dict[str, Any]] = []
    for path in paths:
        receipt, errors = load_export_receipt_file(path)
        if receipt is not None:
            results.append(receipt)
    return results


def find_export_receipt_by_idempotency_key(idempotency_key: str, *, receipt_dir: Path | None = None, exclude_export_id: str | None = None) -> dict[str, Any] | None:
    """All receipts sharing this idempotency key besides exclude_export_id's
    own. prepare_sequence_export always writes its own 'prepared' receipt
    under this same key before export_sequence_verified ever looks for a
    duplicate, so without exclude_export_id this would self-match that
    fresh receipt (newest by mtime) instead of an actually-completed prior
    export under the same key - confirmed live as a real bug: a second
    real dispatch went through because the caller's own
    `other.get("exportId") != export_id` guard silently no-opped on the
    self-match rather than continuing to search. Prefers any
    validated/completed match over an in-flight one - and the newest
    within either tier - so a genuinely completed duplicate is always
    found even if a newer abandoned/in-flight attempt under the same key
    also exists."""
    candidates = [
        r for r in list_export_receipts(receipt_dir=receipt_dir)
        if r.get("idempotencyKey") == idempotency_key and r.get("exportId") != exclude_export_id
    ]
    for r in candidates:
        if r.get("status") in ("validated", "completed"):
            return r
    return candidates[0] if candidates else None
