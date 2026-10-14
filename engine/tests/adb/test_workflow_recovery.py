import json
import sys
import tempfile
import unittest
from pathlib import Path


from lumina.adb.workflow_recovery import (
    EXPORT_RECEIPT_SCHEMA_VERSION,
    RECEIPT_SCHEMA_VERSION,
    ReceiptError,
    atomic_write_export_receipt,
    atomic_write_reconciliation,
    atomic_write_receipt,
    check_operation_live_state,
    classify_candidate_state,
    classify_operation_state,
    compute_export_idempotency_key,
    compute_export_validation_hash,
    compute_idempotency_key,
    compute_receipt_set_hash,
    export_idempotency_verdict,
    find_export_receipt_by_idempotency_key,
    idempotency_verdict,
    list_export_receipts,
    list_receipts_for_execution,
    load_export_receipt,
    load_receipt,
    load_receipt_file,
    load_reconciliation,
    mark_export_receipt_ambiguous,
    mark_export_receipt_completed,
    mark_export_receipt_dispatched,
    mark_export_receipt_failed,
    mark_export_receipt_output_detected,
    mark_export_receipt_validated,
    mark_receipt_completed,
    mark_receipt_failed,
    mark_receipt_reconciled_applied,
    new_export_receipt,
    new_reconciliation_snapshot,
    new_receipt,
    validate_export_receipt_schema,
    validate_receipt_schema,
    verify_export_receipt_hash,
    verify_receipt_hash,
    verify_reconciliation_hash,
)


def _sample_receipt(execution_id="exec1", operation_id="op1"):
    key = compute_idempotency_key(
        project_id="proj1", sequence_id="seq1", plan_hash="hash1", candidate_id="cand1",
        operation_id=operation_id, operation_type="split_track_item",
        target_selector={"mediaType": "audio", "trackIndex": 0, "itemIndex": 0},
        parameters={"atSequenceTicks": 1000}, expected_before_fingerprint="fpA",
    )
    return new_receipt(
        workflow_id="wf1", execution_id=execution_id, candidate_id="cand1", operation_id=operation_id,
        operation_type="split_track_item", idempotency_key=key, project_identity={"name": "demo.prproj"},
        sequence_identity={"id": "seq1", "name": "test seq"}, selector_before={"trackIndex": 0, "itemIndex": 0},
        expected_before={"startTicks": "0", "endTicks": "2000"}, expected_after={"splitTicks": "1000"},
        fingerprint_before="fpA",
    )


class IdempotencyKeyTests(unittest.TestCase):
    def test_same_inputs_same_key(self):
        k1 = compute_idempotency_key(project_id="p", sequence_id="s", plan_hash="h", candidate_id="c", operation_id="o", operation_type="split_track_item", target_selector={"a": 1}, parameters={"b": 2}, expected_before_fingerprint="fp")
        k2 = compute_idempotency_key(project_id="p", sequence_id="s", plan_hash="h", candidate_id="c", operation_id="o", operation_type="split_track_item", target_selector={"a": 1}, parameters={"b": 2}, expected_before_fingerprint="fp")
        self.assertEqual(k1, k2)

    def test_different_fingerprint_different_key(self):
        base = dict(project_id="p", sequence_id="s", plan_hash="h", candidate_id="c", operation_id="o", operation_type="split_track_item", target_selector={"a": 1}, parameters={"b": 2})
        k1 = compute_idempotency_key(expected_before_fingerprint="fp1", **base)
        k2 = compute_idempotency_key(expected_before_fingerprint="fp2", **base)
        self.assertNotEqual(k1, k2)

    def test_different_parameters_different_key(self):
        base = dict(project_id="p", sequence_id="s", plan_hash="h", candidate_id="c", operation_id="o", operation_type="split_track_item", target_selector={"a": 1}, expected_before_fingerprint="fp")
        k1 = compute_idempotency_key(parameters={"atSequenceTicks": 100}, **base)
        k2 = compute_idempotency_key(parameters={"atSequenceTicks": 200}, **base)
        self.assertNotEqual(k1, k2)


class ReceiptSchemaTests(unittest.TestCase):
    def test_new_receipt_is_dispatched(self):
        r = _sample_receipt()
        self.assertEqual(r["status"], "mutation_dispatched")
        self.assertFalse(r["mutationOccurred"])
        self.assertEqual(validate_receipt_schema({**r, "contentHash": "x"}), [])

    def test_mark_completed(self):
        r = mark_receipt_completed(_sample_receipt(), fingerprint_after="fpB", mutation_occurred=True)
        self.assertEqual(r["status"], "completed")
        self.assertTrue(r["mutationOccurred"])
        self.assertEqual(r["fingerprintAfter"], "fpB")
        self.assertIsNotNone(r["journalCommittedAt"])

    def test_mark_completed_false_mutation_is_failed(self):
        r = mark_receipt_completed(_sample_receipt(), fingerprint_after=None, mutation_occurred=False)
        self.assertEqual(r["status"], "failed")

    def test_mark_failed(self):
        r = mark_receipt_failed(_sample_receipt(), error={"code": "X", "message": "boom"})
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["code"], "X")


class AtomicWriteReceiptTests(unittest.TestCase):
    def test_write_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            r = _sample_receipt()
            path = atomic_write_receipt(r, receipt_dir=receipt_dir)
            self.assertTrue(path.is_file())
            loaded, errors = load_receipt("exec1", "op1", receipt_dir=receipt_dir)
            self.assertEqual(errors, [])
            self.assertEqual(loaded["operationId"], "op1")
            self.assertTrue(verify_receipt_hash(loaded))

    def test_no_leftover_tmp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            atomic_write_receipt(_sample_receipt(), receipt_dir=receipt_dir)
            names = [p.name for p in receipt_dir.iterdir()]
            self.assertEqual(names, ["exec1__op1.json"])

    def test_update_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            atomic_write_receipt(_sample_receipt(), receipt_dir=receipt_dir)
            completed = mark_receipt_completed(_sample_receipt(), fingerprint_after="fpB", mutation_occurred=True)
            atomic_write_receipt(completed, receipt_dir=receipt_dir)
            loaded, errors = load_receipt("exec1", "op1", receipt_dir=receipt_dir)
            self.assertEqual(errors, [])
            self.assertEqual(loaded["status"], "completed")

    def test_update_a_receipt_reloaded_from_disk_round_trips(self):
        # Regression test for a real bug found live: a receipt object as
        # returned by load_receipt/list_receipts_for_execution already
        # carries its own prior contentHash key (unlike a fresh new_receipt()
        # dict). Passing *that* loaded object into mark_receipt_reconciled_applied
        # then atomic_write_receipt hashed over data that still contained the
        # stale contentHash key, producing a stored hash that could never
        # match verify_receipt_hash's own contentHash-excluded computation -
        # atomic_write_receipt raised OPERATION_RECEIPT_CORRUPT on its own
        # immediate post-write readback.
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            atomic_write_receipt(_sample_receipt(), receipt_dir=receipt_dir)
            loaded, errors = load_receipt("exec1", "op1", receipt_dir=receipt_dir)
            self.assertEqual(errors, [])
            self.assertIn("contentHash", loaded)
            reconciled = mark_receipt_reconciled_applied(loaded, fingerprint_after="fpB")
            atomic_write_receipt(reconciled, receipt_dir=receipt_dir)  # must not raise
            reloaded, errors = load_receipt("exec1", "op1", receipt_dir=receipt_dir)
            self.assertEqual(errors, [])
            self.assertEqual(reloaded["status"], "applied_unverified")
            self.assertTrue(verify_receipt_hash(reloaded))

    def test_load_missing_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, errors = load_receipt("exec_none", "op_none", receipt_dir=Path(tmp))
            self.assertIsNone(loaded)
            self.assertTrue(any("OPERATION_RECEIPT_NOT_FOUND" in e for e in errors))

    def test_load_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            (receipt_dir / "exec1__op1.json").write_text("{not json", encoding="utf-8")
            loaded, errors = load_receipt("exec1", "op1", receipt_dir=receipt_dir)
            self.assertIsNone(loaded)
            self.assertTrue(any("OPERATION_RECEIPT_CORRUPT" in e for e in errors))

    def test_load_tampered_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            path = atomic_write_receipt(_sample_receipt(), receipt_dir=receipt_dir)
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["status"] = "completed"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            loaded, errors = load_receipt("exec1", "op1", receipt_dir=receipt_dir)
            self.assertIsNone(loaded)
            self.assertTrue(any("hash" in e.lower() for e in errors))

    def test_write_failure_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a dir", encoding="utf-8")
            with self.assertRaises(ReceiptError) as ctx:
                atomic_write_receipt(_sample_receipt(), receipt_dir=blocker / "nested")
            self.assertEqual(ctx.exception.code, "OPERATION_RECEIPT_NOT_FOUND")

    def test_list_receipts_for_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            atomic_write_receipt(_sample_receipt(execution_id="execA", operation_id="op1"), receipt_dir=receipt_dir)
            atomic_write_receipt(_sample_receipt(execution_id="execA", operation_id="op2"), receipt_dir=receipt_dir)
            atomic_write_receipt(_sample_receipt(execution_id="execB", operation_id="op1"), receipt_dir=receipt_dir)
            results = list_receipts_for_execution("execA", receipt_dir=receipt_dir)
            self.assertEqual({r["operationId"] for r in results}, {"op1", "op2"})

    def test_redaction_applied_to_text_like_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            r = _sample_receipt()
            r["captionText"] = "some real subtitle content"
            atomic_write_receipt(r, receipt_dir=receipt_dir)
            loaded, _ = load_receipt("exec1", "op1", receipt_dir=receipt_dir)
            self.assertIsInstance(loaded["captionText"], dict)
            self.assertIn("redactedText", loaded["captionText"])


class IdempotencyVerdictTests(unittest.TestCase):
    def test_no_receipt_is_safe(self):
        self.assertIsNone(idempotency_verdict(None))

    def test_completed_with_mutation_is_already_applied(self):
        r = mark_receipt_completed(_sample_receipt(), fingerprint_after="fpB", mutation_occurred=True)
        self.assertEqual(idempotency_verdict(r), "already_applied")

    def test_dispatched_needs_reconciliation(self):
        self.assertEqual(idempotency_verdict(_sample_receipt()), "needs_reconciliation")

    def test_failed_without_mutation_is_safe_to_retry(self):
        r = mark_receipt_failed(_sample_receipt(), error={"code": "X", "message": "boom"})
        self.assertIsNone(idempotency_verdict(r))

    def test_reconciled_applied_is_never_retried(self):
        r = mark_receipt_reconciled_applied(_sample_receipt(), fingerprint_after="fpB")
        self.assertEqual(r["status"], "applied_unverified")
        self.assertEqual(idempotency_verdict(r), "already_applied")

    def test_new_receipt_starts_with_zero_resume_attempts(self):
        self.assertEqual(_sample_receipt()["resumeAttemptCount"], 0)

    def test_first_failure_is_not_blocked(self):
        # A brand-new failed receipt (resumeAttemptCount still 0) is the
        # original dispatch attempt, not yet a retry - always safe to retry once.
        r = mark_receipt_failed(_sample_receipt(), error={"code": "SPLIT_DESTINATION_COLLISION", "message": "boom"})
        self.assertEqual(r["resumeAttemptCount"], 0)
        self.assertIsNone(idempotency_verdict(r))

    def test_second_consecutive_failure_is_blocked(self):
        # After one retry attempt has already failed again under identical
        # conditions, resumeAttemptCount is 1 - a further identical retry
        # must be refused rather than looping forever on the same real failure.
        r = mark_receipt_failed(_sample_receipt(), error={"code": "SPLIT_DESTINATION_COLLISION", "message": "boom"})
        r["resumeAttemptCount"] = 1
        self.assertEqual(idempotency_verdict(r), "repeated_failure_blocked")

    def test_failed_receipt_with_high_resume_attempt_count_still_blocked(self):
        r = mark_receipt_failed(_sample_receipt(), error={"code": "SPLIT_DESTINATION_COLLISION", "message": "boom"})
        r["resumeAttemptCount"] = 5
        self.assertEqual(idempotency_verdict(r), "repeated_failure_blocked")


class ReconciliationSnapshotTests(unittest.TestCase):
    def _snapshot(self):
        return new_reconciliation_snapshot(
            execution_id="exec1", workflow_id="wf1", live_fingerprint="fp-live",
            receipts=[{**_sample_receipt(), "contentHash": "receipt-hash"}],
            journal_offset=42, journal_hash="journal-hash",
            operation_states={"op1": "definitely_not_applied"},
            candidate_states={"cand1": "not_started"}, next_operation="op1",
            workflow_state_hash="workflow-hash",
        )

    def test_receipt_set_hash_is_order_independent(self):
        rows = [{"operationId": "b", "x": 2}, {"operationId": "a", "x": 1}]
        self.assertEqual(compute_receipt_set_hash(rows), compute_receipt_set_hash(list(reversed(rows))))

    def test_atomic_round_trip_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot()
            atomic_write_reconciliation(snapshot, reconciliation_dir=Path(tmp))
            loaded, errors = load_reconciliation(snapshot["reconciliationId"], reconciliation_dir=Path(tmp))
            self.assertEqual(errors, [])
            self.assertTrue(verify_reconciliation_hash(loaded))
            self.assertEqual(loaded["nextOperation"], "op1")

    def test_tamper_is_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot()
            path = atomic_write_reconciliation(snapshot, reconciliation_dir=Path(tmp))
            content = json.loads(path.read_text(encoding="utf-8"))
            content["nextOperation"] = "op2"
            path.write_text(json.dumps(content), encoding="utf-8")
            loaded, errors = load_reconciliation(snapshot["reconciliationId"], reconciliation_dir=Path(tmp))
            self.assertIsNone(loaded)
            self.assertTrue(any("HASH_MISMATCH" in error for error in errors))

    def test_operation_states_hash_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._snapshot()
            path = atomic_write_reconciliation(snapshot, reconciliation_dir=Path(tmp))
            content = json.loads(path.read_text(encoding="utf-8"))
            content["operationStates"]["op1"] = "ambiguous"
            content.pop("contentHash")
            from lumina.adb.workflow_recovery import compute_reconciliation_hash
            content["contentHash"] = compute_reconciliation_hash(content)
            path.write_text(json.dumps(content), encoding="utf-8")
            loaded, errors = load_reconciliation(snapshot["reconciliationId"], reconciliation_dir=Path(tmp))
            self.assertIsNone(loaded)
            self.assertTrue(any("operationStatesHash" in error for error in errors))


class ClassifyOperationStateTests(unittest.TestCase):
    def test_no_receipt_matches_before_is_not_applied(self):
        verdict = classify_operation_state(receipt=None, live_target_found=True, live_matches_expected_before=True, live_matches_expected_after=False)
        self.assertEqual(verdict, "definitely_not_applied")

    def test_no_receipt_target_missing(self):
        verdict = classify_operation_state(receipt=None, live_target_found=False, live_matches_expected_before=False, live_matches_expected_after=False)
        self.assertEqual(verdict, "target_missing")

    def test_completed_and_matches_after_is_verified(self):
        r = mark_receipt_completed(_sample_receipt(), fingerprint_after="fpB", mutation_occurred=True)
        verdict = classify_operation_state(receipt=r, live_target_found=True, live_matches_expected_before=False, live_matches_expected_after=True)
        self.assertEqual(verdict, "applied_and_verified")

    def test_completed_but_live_still_matches_before_is_ambiguous(self):
        r = mark_receipt_completed(_sample_receipt(), fingerprint_after="fpB", mutation_occurred=True)
        verdict = classify_operation_state(receipt=r, live_target_found=True, live_matches_expected_before=True, live_matches_expected_after=False)
        self.assertEqual(verdict, "ambiguous")

    def test_dispatched_matches_after_is_definitely_applied(self):
        # Crash window C/D: dispatched but never confirmed by this process,
        # yet the live sequence already shows the mutation's effect.
        r = _sample_receipt()
        verdict = classify_operation_state(receipt=r, live_target_found=True, live_matches_expected_before=False, live_matches_expected_after=True)
        self.assertEqual(verdict, "definitely_applied")

    def test_dispatched_matches_before_is_definitely_not_applied(self):
        r = _sample_receipt()
        verdict = classify_operation_state(receipt=r, live_target_found=True, live_matches_expected_before=True, live_matches_expected_after=False)
        self.assertEqual(verdict, "definitely_not_applied")

    def test_dispatched_neither_match_is_ambiguous(self):
        r = _sample_receipt()
        verdict = classify_operation_state(receipt=r, live_target_found=True, live_matches_expected_before=False, live_matches_expected_after=False)
        self.assertEqual(verdict, "ambiguous")

    def test_stale_sequence_overrides_everything(self):
        r = mark_receipt_completed(_sample_receipt(), fingerprint_after="fpB", mutation_occurred=True)
        verdict = classify_operation_state(receipt=r, live_target_found=True, live_matches_expected_before=False, live_matches_expected_after=True, sequence_is_stale=True)
        self.assertEqual(verdict, "sequence_stale")


class ClassifyCandidateStateTests(unittest.TestCase):
    def test_empty_is_not_started(self):
        self.assertEqual(classify_candidate_state([]), "not_started")

    def test_all_not_applied(self):
        self.assertEqual(classify_candidate_state(["definitely_not_applied"] * 3), "not_started")

    def test_all_applied(self):
        self.assertEqual(classify_candidate_state(["applied_and_verified", "definitely_applied"]), "completed")

    def test_partial_recoverable_prefix_applied(self):
        # First split done (real receipt: candidate one-split state), rest not yet.
        self.assertEqual(classify_candidate_state(["applied_and_verified", "definitely_not_applied", "definitely_not_applied"]), "partially_applied_recoverable")

    def test_two_split_state_still_recoverable(self):
        # candidate two-split state: both splits done, remove not yet run.
        self.assertEqual(classify_candidate_state(["applied_and_verified", "applied_and_verified", "definitely_not_applied"]), "partially_applied_recoverable")

    def test_removed_before_ripple_is_unrecoverable_if_out_of_order(self):
        # An applied op appearing after a not-applied one violates this
        # executor's own strict sequential order - not trustworthy.
        self.assertEqual(classify_candidate_state(["definitely_not_applied", "applied_and_verified"]), "partially_applied_unrecoverable")

    def test_ambiguous_operation_makes_candidate_inconsistent(self):
        self.assertEqual(classify_candidate_state(["applied_and_verified", "ambiguous", "definitely_not_applied"]), "inconsistent")

    def test_target_missing_makes_candidate_inconsistent(self):
        self.assertEqual(classify_candidate_state(["applied_and_verified", "target_missing"]), "inconsistent")


def _op(op_type, *, target_selector=None, parameters=None):
    return {"type": op_type, "targetSelector": target_selector or {"mediaType": "audio", "trackIndex": 1, "itemIndex": 0}, "parameters": parameters or {}}


class CheckOperationLiveStateSplitTests(unittest.TestCase):
    # Regression coverage for a live-reproduced bug: a real batch execution
    # (silence removal on "Intro Comp") had its second split rejected by
    # Premiere with INVALID_SPLIT_POINT because the split point coincided
    # exactly with the still-unsplit item's own pre-existing endTicks.
    # check_operation_live_state's old single-edge check ("does *any* item
    # edge sit at split_ticks") treated that untouched, coincidental edge as
    # proof the split had happened, so classify_operation_state reported a
    # receipt-confirmed "failed" split as "definitely_applied" -
    # reconcile_timeline_edit_execution then told a caller it was safe to
    # resume when in fact the batch was still incomplete.

    def test_untouched_item_edge_coinciding_with_split_point_is_not_a_match(self):
        state = {"audioTracks": [{"trackIndex": 1, "items": [
            {"projectItemId": "pi1", "startTicks": "703480377600", "endTicks": "889945056000"},
        ]}]}
        op = _op("split_track_item", parameters={"atSequenceTicks": "889945056000"})
        expected_before = {"projectItemId": "pi1", "startTicks": "703480377600", "endTicks": "889945056000"}
        expected_after = {"splitTicks": "889945056000"}
        found, matches_before, matches_after = check_operation_live_state(state, op, expected_before, expected_after)
        self.assertTrue(found)
        self.assertTrue(matches_before)
        self.assertFalse(matches_after)

    def test_real_split_with_two_sided_adjacency_matches(self):
        state = {"audioTracks": [{"trackIndex": 1, "items": [
            {"projectItemId": "pi1", "startTicks": "203416012800", "endTicks": "703480377600"},
            {"projectItemId": "pi1", "startTicks": "703480377600", "endTicks": "889945056000"},
        ]}]}
        op = _op("split_track_item", parameters={"atSequenceTicks": "703480377600"})
        expected_before = {"projectItemId": "pi1", "startTicks": "203416012800", "endTicks": "889945056000"}
        expected_after = {"splitTicks": "703480377600"}
        found, matches_before, matches_after = check_operation_live_state(state, op, expected_before, expected_after)
        self.assertTrue(found)
        self.assertFalse(matches_before)  # the pre-split single item no longer exists
        self.assertTrue(matches_after)

    def test_only_left_piece_present_is_not_a_match(self):
        state = {"audioTracks": [{"trackIndex": 1, "items": [
            {"projectItemId": "pi1", "startTicks": "0", "endTicks": "1000"},
        ]}]}
        op = _op("split_track_item", parameters={"atSequenceTicks": "1000"})
        found, matches_before, matches_after = check_operation_live_state(
            state, op, {"projectItemId": "pi1", "startTicks": "0", "endTicks": "2000"}, {"splitTicks": "1000"},
        )
        self.assertFalse(matches_after)


class CheckOperationLiveStateTrimTests(unittest.TestCase):
    def test_trim_start_matches_only_when_retained_end_also_matches(self):
        # Two items share a projectItemId (e.g. after an earlier split); a
        # trim_start_to targeting the second piece must not match the first
        # piece just because its startTicks happens to equal value_ticks.
        state = {"videoTracks": [{"trackIndex": 0, "items": [
            {"projectItemId": "pi1", "startTicks": "500", "endTicks": "1000"},
            {"projectItemId": "pi1", "startTicks": "1000", "endTicks": "2000"},
        ]}]}
        op = _op("trim_track_item", target_selector={"mediaType": "video", "trackIndex": 0, "itemIndex": 1}, parameters={})
        expected_before = {"projectItemId": "pi1", "startTicks": "1000", "endTicks": "2000"}
        expected_after = {"mode": "trim_start_to", "valueTicks": "1500"}
        # No item currently has startTicks=1500, so this must not match yet.
        found, matches_before, matches_after = check_operation_live_state(state, op, expected_before, expected_after)
        self.assertTrue(matches_before)
        self.assertFalse(matches_after)

    def test_trim_start_matches_the_specific_item_by_retained_end(self):
        state = {"videoTracks": [{"trackIndex": 0, "items": [
            {"projectItemId": "pi1", "startTicks": "500", "endTicks": "1000"},
            {"projectItemId": "pi1", "startTicks": "1500", "endTicks": "2000"},
        ]}]}
        op = _op("trim_track_item", target_selector={"mediaType": "video", "trackIndex": 0, "itemIndex": 1}, parameters={})
        expected_before = {"projectItemId": "pi1", "startTicks": "1000", "endTicks": "2000"}
        expected_after = {"mode": "trim_start_to", "valueTicks": "1500"}
        found, matches_before, matches_after = check_operation_live_state(state, op, expected_before, expected_after)
        self.assertFalse(matches_before)
        self.assertTrue(matches_after)

    def test_trim_start_does_not_match_a_different_item_with_same_start_but_wrong_end(self):
        # The other item (500-1000) coincidentally has nothing at 1500, but
        # if a *different* untouched item happened to start at 1500 with a
        # different retained end, it must still not be confused for the
        # trimmed target.
        state = {"videoTracks": [{"trackIndex": 0, "items": [
            {"projectItemId": "pi1", "startTicks": "1500", "endTicks": "9999"},
        ]}]}
        op = _op("trim_track_item", target_selector={"mediaType": "video", "trackIndex": 0, "itemIndex": 1}, parameters={})
        expected_before = {"projectItemId": "pi1", "startTicks": "1000", "endTicks": "2000"}
        expected_after = {"mode": "trim_start_to", "valueTicks": "1500"}
        found, matches_before, matches_after = check_operation_live_state(state, op, expected_before, expected_after)
        self.assertFalse(matches_after)


def _sample_export_receipt(export_id="export1", workflow_id="wf1"):
    key = compute_export_idempotency_key(
        project_id="proj1", workflow_id=workflow_id, sequence_id="seq1", sequence_fingerprint="fpA",
        preset_path="C:/preset.epr", preset_hash="ph1", output_path_normalized="c:/out/x.mp4",
        export_range="entire_sequence", expected_duration_seconds=8.48,
    )
    return new_export_receipt(
        export_id=export_id, workflow_id=workflow_id, project_identity={"name": "demo.prproj"},
        sequence_identity={"id": "seq1", "name": "test seq"}, sequence_fingerprint="fpA",
        preset_path="C:/preset.epr", preset_hash="ph1", output_path="c:/out/x.mp4",
        export_range="entire_sequence", expected_duration_seconds=8.48, idempotency_key=key,
    )


class ExportIdempotencyKeyTests(unittest.TestCase):
    def test_same_inputs_same_key(self):
        base = dict(project_id="p", workflow_id="w", sequence_id="s", sequence_fingerprint="fp",
                    preset_path="pr", preset_hash="ph", output_path_normalized="o", export_range="entire_sequence",
                    expected_duration_seconds=1.0)
        self.assertEqual(compute_export_idempotency_key(**base), compute_export_idempotency_key(**base))

    def test_different_output_path_different_key(self):
        base = dict(project_id="p", workflow_id="w", sequence_id="s", sequence_fingerprint="fp",
                    preset_path="pr", preset_hash="ph", export_range="entire_sequence", expected_duration_seconds=1.0)
        k1 = compute_export_idempotency_key(output_path_normalized="a", **base)
        k2 = compute_export_idempotency_key(output_path_normalized="b", **base)
        self.assertNotEqual(k1, k2)

    def test_different_preset_different_key(self):
        base = dict(project_id="p", workflow_id="w", sequence_id="s", sequence_fingerprint="fp",
                    output_path_normalized="o", export_range="entire_sequence", expected_duration_seconds=1.0)
        k1 = compute_export_idempotency_key(preset_path="pr1", preset_hash="ph1", **base)
        k2 = compute_export_idempotency_key(preset_path="pr2", preset_hash="ph2", **base)
        self.assertNotEqual(k1, k2)


class ExportReceiptSchemaTests(unittest.TestCase):
    def test_new_receipt_is_prepared(self):
        r = _sample_export_receipt()
        self.assertEqual(r["status"], "prepared")
        self.assertIsNone(r["completedAt"])
        self.assertEqual(validate_export_receipt_schema({**r, "contentHash": "x"}), [])

    def test_mark_dispatched(self):
        r = mark_export_receipt_dispatched(_sample_export_receipt(), backend_dispatch_id="disp1")
        self.assertEqual(r["status"], "dispatched")
        self.assertEqual(r["backendDispatchId"], "disp1")
        self.assertIsNotNone(r["startedAt"])

    def test_mark_output_detected(self):
        r = mark_export_receipt_output_detected(_sample_export_receipt(), stable_size=4318503)
        self.assertEqual(r["status"], "output_detected")
        self.assertEqual(r["stableSize"], 4318503)

    def test_mark_validated_sets_validation_hash(self):
        sig = {"sizeBytes": 100, "mtimeNs": 123}
        ffprobe = {"durationSeconds": 8.48, "codec": "h264"}
        r = mark_export_receipt_validated(_sample_export_receipt(), output_file_signature=sig, ffprobe_summary=ffprobe)
        self.assertEqual(r["status"], "validated")
        self.assertEqual(r["validationHash"], compute_export_validation_hash(output_file_signature=sig, ffprobe_summary=ffprobe))

    def test_different_signature_different_validation_hash(self):
        h1 = compute_export_validation_hash(output_file_signature={"sizeBytes": 100}, ffprobe_summary={"durationSeconds": 8.48})
        h2 = compute_export_validation_hash(output_file_signature={"sizeBytes": 200}, ffprobe_summary={"durationSeconds": 8.48})
        self.assertNotEqual(h1, h2)

    def test_mark_completed(self):
        r = mark_export_receipt_completed(_sample_export_receipt())
        self.assertEqual(r["status"], "completed")
        self.assertIsNotNone(r["completedAt"])

    def test_mark_failed(self):
        r = mark_export_receipt_failed(_sample_export_receipt(), error={"code": "X", "message": "boom"})
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["code"], "X")

    def test_mark_ambiguous(self):
        r = mark_export_receipt_ambiguous(_sample_export_receipt(), reason="output path mismatch")
        self.assertEqual(r["status"], "ambiguous")
        self.assertEqual(r["error"]["code"], "EXPORT_STATE_AMBIGUOUS")


class AtomicWriteExportReceiptTests(unittest.TestCase):
    def test_write_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            r = _sample_export_receipt()
            path = atomic_write_export_receipt(r, receipt_dir=receipt_dir)
            self.assertTrue(path.is_file())
            loaded, errors = load_export_receipt("export1", receipt_dir=receipt_dir)
            self.assertEqual(errors, [])
            self.assertEqual(loaded["exportId"], "export1")
            self.assertTrue(verify_export_receipt_hash(loaded))

    def test_no_leftover_tmp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            atomic_write_export_receipt(_sample_export_receipt(), receipt_dir=receipt_dir)
            names = [p.name for p in receipt_dir.iterdir()]
            self.assertEqual(names, ["export1.json"])

    def test_update_a_receipt_reloaded_from_disk_round_trips(self):
        # Same regression shape as the operation-receipt bug found live in
        # the prior patch: a receipt reloaded from disk already carries its
        # own contentHash key. Updating it (dispatched -> validated) and
        # writing it back must not corrupt the stored hash.
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            atomic_write_export_receipt(_sample_export_receipt(), receipt_dir=receipt_dir)
            loaded, errors = load_export_receipt("export1", receipt_dir=receipt_dir)
            self.assertEqual(errors, [])
            dispatched = mark_export_receipt_dispatched(loaded, backend_dispatch_id="disp1")
            atomic_write_export_receipt(dispatched, receipt_dir=receipt_dir)  # must not raise
            reloaded, errors = load_export_receipt("export1", receipt_dir=receipt_dir)
            self.assertEqual(errors, [])
            self.assertEqual(reloaded["status"], "dispatched")
            self.assertTrue(verify_export_receipt_hash(reloaded))
            validated = mark_export_receipt_validated(reloaded, output_file_signature={"sizeBytes": 1}, ffprobe_summary={"durationSeconds": 1.0})
            atomic_write_export_receipt(validated, receipt_dir=receipt_dir)  # must not raise either
            reloaded2, errors = load_export_receipt("export1", receipt_dir=receipt_dir)
            self.assertEqual(errors, [])
            self.assertEqual(reloaded2["status"], "validated")
            self.assertTrue(verify_export_receipt_hash(reloaded2))

    def test_load_missing_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, errors = load_export_receipt("export_none", receipt_dir=Path(tmp))
            self.assertIsNone(loaded)
            self.assertTrue(any("EXPORT_RECEIPT_NOT_FOUND" in e for e in errors))

    def test_load_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            (receipt_dir / "export1.json").write_text("{not json", encoding="utf-8")
            loaded, errors = load_export_receipt("export1", receipt_dir=receipt_dir)
            self.assertIsNone(loaded)
            self.assertTrue(any("EXPORT_RECEIPT_CORRUPT" in e for e in errors))

    def test_load_tampered_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp)
            path = atomic_write_export_receipt(_sample_export_receipt(), receipt_dir=receipt_dir)
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["status"] = "completed"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            loaded, errors = load_export_receipt("export1", receipt_dir=receipt_dir)
            self.assertIsNone(loaded)
            self.assertTrue(any("hash" in e.lower() for e in errors))

    def test_write_failure_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a dir", encoding="utf-8")
            with self.assertRaises(ReceiptError) as ctx:
                atomic_write_export_receipt(_sample_export_receipt(), receipt_dir=blocker / "nested")
            self.assertEqual(ctx.exception.code, "EXPORT_RECEIPT_NOT_FOUND")


class ExportIdempotencyVerdictTests(unittest.TestCase):
    def test_no_receipt_is_safe(self):
        self.assertIsNone(export_idempotency_verdict(None))

    def test_prepared_is_safe_to_dispatch(self):
        self.assertIsNone(export_idempotency_verdict(_sample_export_receipt()))

    def test_validated_is_already_completed(self):
        r = mark_export_receipt_validated(_sample_export_receipt(), output_file_signature={"sizeBytes": 1}, ffprobe_summary={})
        self.assertEqual(export_idempotency_verdict(r), "already_completed")

    def test_completed_is_already_completed(self):
        r = mark_export_receipt_completed(_sample_export_receipt())
        self.assertEqual(export_idempotency_verdict(r), "already_completed")

    def test_dispatched_needs_reconciliation(self):
        r = mark_export_receipt_dispatched(_sample_export_receipt(), backend_dispatch_id="d1")
        self.assertEqual(export_idempotency_verdict(r), "reconciliation_required")

    def test_output_detected_needs_reconciliation(self):
        r = mark_export_receipt_output_detected(_sample_export_receipt(), stable_size=100)
        self.assertEqual(export_idempotency_verdict(r), "reconciliation_required")

    def test_failed_is_safe_to_retry(self):
        r = mark_export_receipt_failed(_sample_export_receipt(), error={"code": "X", "message": "boom"})
        self.assertIsNone(export_idempotency_verdict(r))

    def test_ambiguous_is_not_auto_retried_or_completed(self):
        r = mark_export_receipt_ambiguous(_sample_export_receipt(), reason="x")
        self.assertIsNone(export_idempotency_verdict(r))


class ListExportReceiptsTests(unittest.TestCase):
    def test_empty_directory_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(list_export_receipts(receipt_dir=Path(directory)), [])

    def test_lists_every_written_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt_dir = Path(directory)
            atomic_write_export_receipt(_sample_export_receipt(export_id="exportA"), receipt_dir=receipt_dir)
            atomic_write_export_receipt(_sample_export_receipt(export_id="exportB", workflow_id="wf2"), receipt_dir=receipt_dir)
            ids = {r["exportId"] for r in list_export_receipts(receipt_dir=receipt_dir)}
            self.assertEqual(ids, {"exportA", "exportB"})

    def test_corrupt_receipt_file_is_silently_skipped_not_crashed(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt_dir = Path(directory)
            atomic_write_export_receipt(_sample_export_receipt(export_id="exportGood"), receipt_dir=receipt_dir)
            (receipt_dir / "exportBad.json").write_text("{not valid json", encoding="utf-8")
            ids = {r["exportId"] for r in list_export_receipts(receipt_dir=receipt_dir)}
            self.assertEqual(ids, {"exportGood"})


class FindExportReceiptByIdempotencyKeyTests(unittest.TestCase):
    def test_finds_matching_receipt_under_a_different_export_id(self):
        # Section 5/9: a restart wipes the in-process export store, so a
        # fresh prepare_sequence_export call mints a brand-new exportId for
        # what is really the same export request. Finding the predecessor
        # by idempotencyKey (not exportId) is what lets duplicate dispatch
        # be detected across that exportId change.
        with tempfile.TemporaryDirectory() as directory:
            receipt_dir = Path(directory)
            original = _sample_export_receipt(export_id="exportOriginal")
            atomic_write_export_receipt(original, receipt_dir=receipt_dir)
            found = find_export_receipt_by_idempotency_key(original["idempotencyKey"], receipt_dir=receipt_dir)
            self.assertEqual(found["exportId"], "exportOriginal")

    def test_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt_dir = Path(directory)
            atomic_write_export_receipt(_sample_export_receipt(export_id="exportA"), receipt_dir=receipt_dir)
            self.assertIsNone(find_export_receipt_by_idempotency_key("nonexistent-key", receipt_dir=receipt_dir))

    def test_exclude_export_id_skips_self_match(self):
        # Real bug found live (Export Restart Recovery patch v1): prepare_sequence_export
        # always writes its own 'prepared' receipt under this same idempotency key
        # *before* export_sequence_verified ever looks for a duplicate. Without
        # exclude_export_id, the newest-by-mtime match is that fresh receipt itself,
        # so the completed predecessor under the same key is never found and a real
        # second dispatch goes through. Confirmed live: a second export_sequence_verified
        # call actually re-exported (new backendDispatchId, overwritten output file)
        # instead of returning EXPORT_ALREADY_COMPLETED.
        with tempfile.TemporaryDirectory() as directory:
            receipt_dir = Path(directory)
            completed = mark_export_receipt_completed(_sample_export_receipt(export_id="exportOriginal"))
            atomic_write_export_receipt(completed, receipt_dir=receipt_dir)
            # A fresh prepare_sequence_export call for the "same" export mints a new
            # exportId and writes its own 'prepared' receipt under the identical key,
            # strictly after (so newer-mtime than) the original.
            fresh = _sample_export_receipt(export_id="exportFresh")
            atomic_write_export_receipt(fresh, receipt_dir=receipt_dir)
            found = find_export_receipt_by_idempotency_key(
                fresh["idempotencyKey"], receipt_dir=receipt_dir, exclude_export_id="exportFresh",
            )
            self.assertEqual(found["exportId"], "exportOriginal")
            self.assertEqual(found["status"], "completed")

    def test_prefers_completed_match_over_newer_in_flight_match(self):
        # Even when an in-flight/abandoned receipt under the same key is newer
        # (by mtime) than a genuinely completed one, the completed match must
        # still be found - never masked by a more recent but unresolved attempt.
        with tempfile.TemporaryDirectory() as directory:
            receipt_dir = Path(directory)
            completed = mark_export_receipt_completed(_sample_export_receipt(export_id="exportDone"))
            atomic_write_export_receipt(completed, receipt_dir=receipt_dir)
            dispatched = mark_export_receipt_dispatched(_sample_export_receipt(export_id="exportStale"), backend_dispatch_id="disp1")
            atomic_write_export_receipt(dispatched, receipt_dir=receipt_dir)
            found = find_export_receipt_by_idempotency_key(
                completed["idempotencyKey"], receipt_dir=receipt_dir, exclude_export_id="exportCurrent",
            )
            self.assertEqual(found["exportId"], "exportDone")


if __name__ == "__main__":
    unittest.main()
