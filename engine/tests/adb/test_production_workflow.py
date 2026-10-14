import sys
import unittest
from pathlib import Path


from lumina.adb.production_workflow import (
    WORKFLOW_STAGES,
    check_resume_staleness,
    is_stage_completed,
    new_workflow,
    record_stage_completed,
    working_sequence_name,
    workflow_failure_envelope,
    workflow_has_mutated,
)


class NewWorkflowTests(unittest.TestCase):
    def test_defaults(self):
        wf = new_workflow(
            source_sequence_id="seq1", working_sequence_policy="disposable_duplicate",
            production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint="fp1",
        )
        self.assertEqual(wf["status"], "prepared")
        self.assertEqual(wf["sourceSequence"], {"id": "seq1", "fingerprint": "fp1"})
        self.assertEqual(wf["completedStages"], [])
        self.assertIsNone(wf["currentStage"])

    def test_invalid_working_sequence_policy_raises(self):
        with self.assertRaises(ValueError):
            new_workflow(source_sequence_id="s", working_sequence_policy="bogus", production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint=None)

    def test_invalid_cleanup_policy_raises(self):
        with self.assertRaises(ValueError):
            new_workflow(source_sequence_id="s", working_sequence_policy="disposable_duplicate", production_mode=False, cleanup_policy="bogus", expected_source_fingerprint=None)


class WorkingSequenceNameTests(unittest.TestCase):
    def test_prefix_and_uniqueness(self):
        a = working_sequence_name()
        b = working_sequence_name()
        self.assertTrue(a.startswith("ADB_MCP_PRODUCTION_TEST_"))
        self.assertNotEqual(a, b)

    def test_source_name_embedded_and_sanitized(self):
        name = working_sequence_name(source_name="shorts seq")
        self.assertTrue(name.startswith("ADB_MCP_PRODUCTION_TEST_shorts_seq_"))

    def test_source_name_with_unsafe_characters_sanitized(self):
        name = working_sequence_name(source_name="야니네코/shorts:seq*!")
        self.assertTrue(name.startswith("ADB_MCP_PRODUCTION_TEST_"))
        self.assertNotIn("/", name)
        self.assertNotIn(":", name)
        self.assertNotIn("*", name)

    def test_empty_source_name_falls_back_to_seq(self):
        name = working_sequence_name(source_name="!!!")
        self.assertTrue(name.startswith("ADB_MCP_PRODUCTION_TEST_seq_"))

    def test_no_source_name_matches_original_behavior(self):
        name = working_sequence_name(timestamp="20260101T000000000000Z")
        self.assertEqual(name, "ADB_MCP_PRODUCTION_TEST_20260101T000000000000Z")


class StageCheckpointTests(unittest.TestCase):
    def _wf(self):
        return new_workflow(source_sequence_id="seq1", working_sequence_policy="disposable_duplicate", production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint="fp0")

    def test_record_stage_marks_completed_and_current(self):
        wf = self._wf()
        record_stage_completed(wf, "project_checkpoint_created", source_fingerprint="fp0", working_fingerprint=None)
        self.assertTrue(is_stage_completed(wf, "project_checkpoint_created"))
        self.assertEqual(wf["currentStage"], "project_checkpoint_created")
        self.assertEqual(len(wf["stageCheckpoints"]), 1)

    def test_record_stage_idempotent_in_completed_list(self):
        wf = self._wf()
        record_stage_completed(wf, "plan_created", source_fingerprint="fp0", working_fingerprint="wfp0")
        record_stage_completed(wf, "plan_created", source_fingerprint="fp0", working_fingerprint="wfp0")
        self.assertEqual(wf["completedStages"].count("plan_created"), 1)
        # Both checkpoint records are still appended (audit trail), only the
        # completedStages *set membership* is idempotent.
        self.assertEqual(len(wf["stageCheckpoints"]), 2)

    def test_unknown_stage_rejected(self):
        wf = self._wf()
        with self.assertRaises(ValueError):
            record_stage_completed(wf, "not_a_real_stage", source_fingerprint=None, working_fingerprint=None)

    def test_all_stages_are_recordable(self):
        wf = self._wf()
        for stage in WORKFLOW_STAGES:
            record_stage_completed(wf, stage, source_fingerprint="fp0", working_fingerprint=None)
        self.assertEqual(wf["completedStages"], list(WORKFLOW_STAGES))


class ResumeStalenessTests(unittest.TestCase):
    def _wf_with_checkpoint(self, source_fp="fp0", working_fp="wfp0"):
        wf = new_workflow(source_sequence_id="seq1", working_sequence_policy="disposable_duplicate", production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint=source_fp)
        record_stage_completed(wf, "edit_execution_completed", source_fingerprint=source_fp, working_fingerprint=working_fp)
        return wf

    def test_no_checkpoint_never_stale(self):
        wf = new_workflow(source_sequence_id="s", working_sequence_policy="disposable_duplicate", production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint=None)
        self.assertIsNone(check_resume_staleness(wf, current_source_fingerprint="anything", current_working_fingerprint="anything"))

    def test_matching_fingerprints_not_stale(self):
        wf = self._wf_with_checkpoint()
        self.assertIsNone(check_resume_staleness(wf, current_source_fingerprint="fp0", current_working_fingerprint="wfp0"))

    def test_source_fingerprint_drift_is_stale(self):
        wf = self._wf_with_checkpoint()
        reason = check_resume_staleness(wf, current_source_fingerprint="fp1", current_working_fingerprint="wfp0")
        self.assertIsNotNone(reason)
        self.assertIn("Source sequence fingerprint changed", reason)

    def test_working_fingerprint_drift_is_stale(self):
        wf = self._wf_with_checkpoint()
        reason = check_resume_staleness(wf, current_source_fingerprint="fp0", current_working_fingerprint="wfp1")
        self.assertIsNotNone(reason)
        self.assertIn("Working sequence fingerprint changed", reason)


class WorkflowFailureEnvelopeTests(unittest.TestCase):
    def test_required_fields_present(self):
        wf = new_workflow(source_sequence_id="s", working_sequence_policy="disposable_duplicate", production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint=None)
        record_stage_completed(wf, "plan_created", source_fingerprint=None, working_fingerprint=None)
        envelope = workflow_failure_envelope(wf, "WORKFLOW_STAGE_FAILED", "boom")
        for key in ("mutationOccurred", "currentStage", "completedStages", "workingSequence", "checkpoint", "editExecution", "export", "cleanup", "residualArtifacts"):
            self.assertIn(key, envelope)
        self.assertEqual(envelope["error"], {"code": "WORKFLOW_STAGE_FAILED", "message": "boom"})
        self.assertEqual(envelope["currentStage"], "plan_created")

    def test_mutation_occurred_defaults_false_before_any_mutating_stage(self):
        wf = new_workflow(source_sequence_id="s", working_sequence_policy="disposable_duplicate", production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint=None)
        record_stage_completed(wf, "plan_created", source_fingerprint=None, working_fingerprint=None)
        envelope = workflow_failure_envelope(wf, "WORKFLOW_STAGE_FAILED", "boom")
        self.assertFalse(envelope["mutationOccurred"])

    def test_mutation_occurred_defaults_true_after_mutating_stage(self):
        wf = new_workflow(source_sequence_id="s", working_sequence_policy="disposable_duplicate", production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint=None)
        record_stage_completed(wf, "project_checkpoint_created", source_fingerprint=None, working_fingerprint=None)
        envelope = workflow_failure_envelope(wf, "WORKFLOW_STAGE_FAILED", "boom")
        self.assertTrue(envelope["mutationOccurred"])

    def test_mutation_occurred_true_from_partial_edit_execution_even_without_stage_completed(self):
        # Regression test for a real bug caught in live verification: a batch
        # edit execution that completes one candidate for real (mutationOccurred
        # on its operationResults) before a later candidate fails
        # (SPLIT_DESTINATION_COLLISION) never reaches record_stage_completed
        # for edit_execution_completed, but real mutation still happened.
        wf = new_workflow(source_sequence_id="s", working_sequence_policy="disposable_duplicate", production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint=None)
        record_stage_completed(wf, "plan_approved", source_fingerprint=None, working_fingerprint=None)
        wf["editExecution"] = {"status": "partially_completed", "operationResults": [
            {"operationId": "op1", "status": "completed", "mutationOccurred": True},
            {"operationId": "op2", "status": "failed", "mutationOccurred": False},
        ]}
        self.assertTrue(workflow_has_mutated(wf))
        envelope = workflow_failure_envelope(wf, "SPLIT_DESTINATION_COLLISION", "boom")
        self.assertTrue(envelope["mutationOccurred"])

    def test_explicit_mutation_occurred_override_wins(self):
        wf = new_workflow(source_sequence_id="s", working_sequence_policy="disposable_duplicate", production_mode=False, cleanup_policy="keep_all", expected_source_fingerprint=None)
        record_stage_completed(wf, "project_checkpoint_created", source_fingerprint=None, working_fingerprint=None)
        envelope = workflow_failure_envelope(wf, "WORKFLOW_STAGE_FAILED", "boom", mutationOccurred=False)
        self.assertFalse(envelope["mutationOccurred"])


if __name__ == "__main__":
    unittest.main()
