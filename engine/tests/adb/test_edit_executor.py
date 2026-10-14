from __future__ import annotations

import sys
import unittest
from pathlib import Path


from lumina.adb.edit_executor import (
    EXECUTABLE_SEQUENCE_PREFIXES,
    build_execution_manifest,
    build_operation_expectations,
    classify_sequence,
    group_operations_by_candidate,
    is_executable_sequence_name,
    new_execution,
    rebase_selector,
)

TICKS_PER_SECOND = 254_016_000_000


def _op(op_id, candidate_id, op_type="split_track_item", target=None, rollback="disposable_sequence_only", executable=True):
    return {
        "operationId": op_id, "candidateId": candidate_id, "type": op_type,
        "targetSelector": target or {"mediaType": "audio", "trackIndex": 0}, "parameters": {},
        "dependencies": [], "rollbackClass": rollback, "executable": executable, "blockedReasons": [],
    }


class IsExecutableSequenceNameTests(unittest.TestCase):
    def test_edit_test_prefix_is_executable(self):
        self.assertTrue(is_executable_sequence_name("ADB_MCP_EDIT_TEST_20260719"))

    def test_exec_test_prefix_is_executable(self):
        self.assertTrue(is_executable_sequence_name("ADB_MCP_EXEC_TEST_20260719"))


class SequenceClassificationTests(unittest.TestCase):
    def test_production_source_identity_outranks_prefix(self):
        result = classify_sequence(sequence_id="s", name="ADB_MCP_EDIT_TEST_fake", workflow_source_ids={"s"})
        self.assertEqual(result["sequenceClass"], "production_source")

    def test_working_copy_identity_outranks_prefix(self):
        result = classify_sequence(sequence_id="s", name="ADB_MCP_PRODUCTION_TEST_x", workflow_working_ids={"s"})
        self.assertEqual(result["sequenceClass"], "workflow_working_copy")
        self.assertEqual(result["destructivePolicy"], "workflow_only")

    def test_disposable_prefix_is_high_confidence(self):
        result = classify_sequence(sequence_id="s", name="ADB_MCP_EXEC_TEST_x")
        self.assertEqual(result["sequenceClass"], "disposable_test")
        self.assertEqual(result["confidence"], "high")

    def test_unknown_is_treated_as_production(self):
        result = classify_sequence(sequence_id="s", name="ordinary")
        self.assertEqual(result["sequenceClass"], "unknown")
        self.assertEqual(result["destructivePolicy"], "treat_as_production")


class OperationExpectationTests(unittest.TestCase):
    def _groups(self):
        target = {"mediaType": "audio", "trackIndex": 0, "itemIndex": 0}
        return [{"candidateId": "c1", "operations": [
            {**_op("split_start", "c1", target=target), "parameters": {"atSequenceTicks": "300"}},
            {**_op("split_end", "c1", target=target), "parameters": {"atSequenceTicks": "700"}},
            {**_op("remove", "c1", op_type="remove_track_item", target=target), "parameters": {"removedInterval": {"startTicks": "300", "endTicks": "700"}}},
        ]}]

    def test_first_split_freezes_original_item(self):
        snapshot = {"audioTracks": [{"trackIndex": 0, "items": [{"projectItemId": "p", "startTicks": "0", "endTicks": "1000"}]}]}
        expected = build_operation_expectations(snapshot, self._groups())
        self.assertEqual(expected["split_start"], {"projectItemId": "p", "startTicks": "0", "endTicks": "1000"})

    def test_second_split_targets_right_lineage(self):
        snapshot = {"audioTracks": [{"trackIndex": 0, "items": [{"projectItemId": "p", "startTicks": "0", "endTicks": "1000"}]}]}
        expected = build_operation_expectations(snapshot, self._groups())
        self.assertEqual(expected["split_end"]["startTicks"], "300")
        self.assertEqual(expected["split_end"]["endTicks"], "1000")

    def test_remove_targets_exact_middle_segment(self):
        snapshot = {"audioTracks": [{"trackIndex": 0, "items": [{"projectItemId": "p", "startTicks": "0", "endTicks": "1000"}]}]}
        expected = build_operation_expectations(snapshot, self._groups())
        self.assertEqual(expected["remove"]["startTicks"], "300")
        self.assertEqual(expected["remove"]["endTicks"], "700")

    def test_missing_item_does_not_invent_expectation(self):
        snapshot = {"audioTracks": [{"trackIndex": 0, "items": []}]}
        self.assertEqual(build_operation_expectations(snapshot, self._groups()), {})

    def test_plan_test_prefix_is_not_executable(self):
        self.assertFalse(is_executable_sequence_name("ADB_MCP_PLAN_TEST_20260719"))

    def test_production_name_is_not_executable(self):
        self.assertFalse(is_executable_sequence_name("SEQ"))

    def test_none_is_not_executable(self):
        self.assertFalse(is_executable_sequence_name(None))


class RebaseSelectorTests(unittest.TestCase):
    def _state(self, items):
        return {"audioTracks": [{"trackIndex": 0, "items": items}], "videoTracks": []}

    def test_exact_match_by_identity_and_boundaries(self):
        state = self._state([
            {"projectItemId": "pi1", "startTicks": "0", "endTicks": str(2 * TICKS_PER_SECOND)},
            {"projectItemId": "pi1", "startTicks": str(2 * TICKS_PER_SECOND), "endTicks": str(5 * TICKS_PER_SECOND)},
        ])
        result = rebase_selector(
            state, media_type="audio", track_index=0, project_item_id="pi1",
            expected_start_ticks=2 * TICKS_PER_SECOND, expected_end_ticks=5 * TICKS_PER_SECOND,
        )
        self.assertEqual(result["confidence"], "exact")
        self.assertEqual(result["itemIndex"], 1)

    def test_inferred_high_within_tolerance(self):
        state = self._state([{"projectItemId": "pi1", "startTicks": "10", "endTicks": str(2 * TICKS_PER_SECOND)}])
        result = rebase_selector(
            state, media_type="audio", track_index=0, project_item_id="pi1",
            expected_start_ticks=0, expected_end_ticks=2 * TICKS_PER_SECOND, tolerance_ticks=100,
        )
        self.assertEqual(result["confidence"], "inferred_high")

    def test_ambiguous_when_multiple_items_match(self):
        state = self._state([
            {"projectItemId": "pi1", "startTicks": "0", "endTicks": str(2 * TICKS_PER_SECOND)},
            {"projectItemId": "pi1", "startTicks": "0", "endTicks": str(2 * TICKS_PER_SECOND)},
        ])
        result = rebase_selector(
            state, media_type="audio", track_index=0, project_item_id="pi1",
            expected_start_ticks=0, expected_end_ticks=2 * TICKS_PER_SECOND,
        )
        self.assertEqual(result["confidence"], "ambiguous")

    def test_missing_when_no_item_matches(self):
        state = self._state([{"projectItemId": "pi1", "startTicks": "0", "endTicks": str(2 * TICKS_PER_SECOND)}])
        result = rebase_selector(
            state, media_type="audio", track_index=0, project_item_id="pi1",
            expected_start_ticks=999 * TICKS_PER_SECOND, expected_end_ticks=1000 * TICKS_PER_SECOND,
        )
        self.assertEqual(result["confidence"], "missing")

    def test_missing_when_track_not_found(self):
        state = self._state([])
        result = rebase_selector(
            state, media_type="audio", track_index=5, project_item_id="pi1",
            expected_start_ticks=0, expected_end_ticks=1,
        )
        self.assertEqual(result["confidence"], "missing")

    def test_wrong_project_item_id_excluded(self):
        state = self._state([{"projectItemId": "other", "startTicks": "0", "endTicks": str(2 * TICKS_PER_SECOND)}])
        result = rebase_selector(
            state, media_type="audio", track_index=0, project_item_id="pi1",
            expected_start_ticks=0, expected_end_ticks=2 * TICKS_PER_SECOND,
        )
        self.assertEqual(result["confidence"], "missing")


class GroupOperationsByCandidateTests(unittest.TestCase):
    def test_preserves_input_order_and_groups_correctly(self):
        ops = [_op("op1", "cand_late"), _op("op2", "cand_late"), _op("op3", "cand_early")]
        groups = group_operations_by_candidate(ops)
        self.assertEqual([g["candidateId"] for g in groups], ["cand_late", "cand_early"])
        self.assertEqual(len(groups[0]["operations"]), 2)
        self.assertEqual(len(groups[1]["operations"]), 1)


class BuildExecutionManifestTests(unittest.TestCase):
    def test_filters_to_approved_operations_only(self):
        plan = {"operations": [_op("op1", "c1"), _op("op2", "c1")]}
        manifest = build_execution_manifest(plan, {"op1"})
        self.assertEqual(manifest["operationOrder"], ["op1"])

    def test_flags_unsupported_operation_types(self):
        plan = {"operations": [_op("op1", "c1", op_type="apply_audio_fade")]}
        manifest = build_execution_manifest(plan, {"op1"})
        self.assertEqual(manifest["unsupportedOperationIds"], ["op1"])

    def test_supported_types_not_flagged(self):
        plan = {"operations": [_op("op1", "c1", op_type="split_track_item"), _op("op2", "c1", op_type="remove_track_item")]}
        manifest = build_execution_manifest(plan, {"op1", "op2"})
        self.assertEqual(manifest["unsupportedOperationIds"], [])


class NewExecutionTests(unittest.TestCase):
    def test_starts_in_prepared_status_with_no_mutation(self):
        plan = {"planId": "plan_x", "planHash": "hash_x", "project": {}, "sequence": {"id": "s1"}, "operations": [_op("op1", "c1")]}
        execution = new_execution(
            plan, approved_operation_ids=["op1"], execution_policy="fail_fast",
            rollback_policy="rollback_current_candidate", production_mode=False, initial_fingerprint="fp1",
        )
        self.assertEqual(execution["status"], "prepared")
        self.assertEqual(execution["executionMode"], "synchronous")
        self.assertEqual(len(execution["operationResults"]), 1)
        self.assertEqual(execution["operationResults"][0]["status"], "pending")
        self.assertFalse(execution["productionMode"])
        self.assertEqual(execution["rollback"]["status"], "none")


if __name__ == "__main__":
    unittest.main()
