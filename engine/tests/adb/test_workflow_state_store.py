import json
import sys
import tempfile
import unittest
from pathlib import Path


from lumina.adb.workflow_state_store import (
    FINGERPRINT_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    WorkflowStateError,
    atomic_write_state,
    build_state,
    compute_content_hash,
    delete_workflow_state,
    list_workflow_states,
    load_state_file,
    load_workflow_state,
    validate_state_schema,
    verify_state_hash,
)


def _sample_workflow(workflow_id="workflow_abc123", status="awaiting_approval"):
    return {
        "workflowId": workflow_id,
        "status": status,
        "project": {"name": "demo.prproj"},
        "sourceSequence": {"id": "seq-src", "name": "shorts seq", "fingerprint": "fpA"},
        "workingSequence": {"id": "seq-work", "name": "ADB_MCP_PILOT_shorts_20260101", "fingerprint": "fpB"},
        "workingSequencePolicy": "disposable_duplicate",
        "productionMode": False,
        "completedStages": ["project_checkpoint_created", "working_sequence_created", "plan_created"],
        "currentStage": "plan_created",
        "stageCheckpoints": [{"stage": "plan_created", "recordedAt": "2026-01-01T00:00:00+00:00"}],
        "editPlan": {"plan": {"planId": "plan1", "candidateNote": "no sensitive text field name here"}},
        "editExecution": {"executionId": "exec1", "status": "prepared"},
        "export": None,
        "checkpoint": {"checkpointPath": "D:/demo.prproj"},
        "cleanup": None,
        "options": {"caption_options": None},
        "warnings": [],
        "error": None,
    }


class BuildStateTests(unittest.TestCase):
    def test_schema_and_required_fields(self):
        state = build_state(_sample_workflow())
        self.assertEqual(state["schemaVersion"], STATE_SCHEMA_VERSION)
        self.assertEqual(state["workflowId"], "workflow_abc123")
        self.assertEqual(state["sourceFingerprint"], "fpA")
        self.assertEqual(state["workingFingerprint"], "fpB")
        self.assertEqual(state["completedStages"], ["project_checkpoint_created", "working_sequence_created", "plan_created"])
        self.assertIn("contentHash", state)
        self.assertEqual(validate_state_schema(state), [])
        self.assertTrue(verify_state_hash(state))

    def test_fingerprint_schema_version_is_stamped(self):
        # Export Restart Recovery & Fingerprint Consistency Patch v1,
        # section 12: every state persisted from now on records which
        # fingerprint scheme its sourceFingerprint/workingFingerprint values
        # came from, so a future incompatible scheme can be detected
        # (FINGERPRINT_SCHEMA_MISMATCH) instead of silently compared.
        state = build_state(_sample_workflow())
        self.assertEqual(state["fingerprintSchemaVersion"], FINGERPRINT_SCHEMA_VERSION)
        self.assertEqual(FINGERPRINT_SCHEMA_VERSION, "production-sequence-fingerprint/1")

    def test_artifacts_collected(self):
        state = build_state(_sample_workflow())
        types = {a["type"] for a in state["artifacts"]}
        self.assertIn("project_checkpoint", types)

    def test_redaction_does_not_touch_structural_fields(self):
        state = build_state(_sample_workflow())
        # planId/candidateNote have no text/caption/subtitle/transcript in
        # their key name, so redact_sensitive must leave them as plain strings.
        self.assertEqual(state["workflow"]["editPlan"]["plan"]["planId"], "plan1")

    def test_redaction_applies_to_text_like_keys(self):
        wf = _sample_workflow()
        wf["captions"] = {"srtText": "this looks like real subtitle content"}
        state = build_state(wf)
        redacted = state["workflow"]["captions"]["srtText"]
        self.assertIsInstance(redacted, dict)
        self.assertIn("redactedText", redacted)


class HashAndSchemaValidationTests(unittest.TestCase):
    def test_tampered_content_fails_hash(self):
        state = build_state(_sample_workflow())
        tampered = dict(state)
        tampered["status"] = "completed"  # mutate without recomputing contentHash
        self.assertFalse(verify_state_hash(tampered))

    def test_wrong_schema_version_rejected(self):
        state = build_state(_sample_workflow())
        state["schemaVersion"] = "99.0"
        errors = validate_state_schema(state)
        self.assertTrue(any("schemaVersion" in e for e in errors))

    def test_missing_required_key_rejected(self):
        state = build_state(_sample_workflow())
        del state["updatedAt"]
        errors = validate_state_schema(state)
        self.assertTrue(any("updatedAt" in e for e in errors))


class AtomicWriteAndLoadTests(unittest.TestCase):
    def test_write_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            state = build_state(_sample_workflow())
            path = atomic_write_state(state, state_dir=state_dir)
            self.assertTrue(path.is_file())
            loaded, errors = load_workflow_state("workflow_abc123", state_dir=state_dir)
            self.assertEqual(errors, [])
            self.assertEqual(loaded["workflowId"], "workflow_abc123")
            self.assertEqual(loaded["contentHash"], state["contentHash"])

    def test_no_leftover_tmp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            atomic_write_state(build_state(_sample_workflow()), state_dir=state_dir)
            names = [p.name for p in state_dir.iterdir()]
            self.assertEqual(names, ["workflow_abc123.json"])

    def test_overwrite_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            atomic_write_state(build_state(_sample_workflow(status="awaiting_approval")), state_dir=state_dir)
            atomic_write_state(build_state(_sample_workflow(status="completed")), state_dir=state_dir)
            loaded, errors = load_workflow_state("workflow_abc123", state_dir=state_dir)
            self.assertEqual(errors, [])
            self.assertEqual(loaded["status"], "completed")

    def test_load_missing_state_returns_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, errors = load_workflow_state("workflow_never_saved", state_dir=Path(tmp))
            self.assertIsNone(loaded)
            self.assertTrue(any("WORKFLOW_STATE_NOT_FOUND" in e for e in errors))

    def test_load_corrupt_json_returns_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "workflow_bad.json").write_text("{not valid json", encoding="utf-8")
            loaded, errors = load_workflow_state("workflow_bad", state_dir=state_dir)
            self.assertIsNone(loaded)
            self.assertTrue(any("WORKFLOW_STATE_CORRUPT" in e for e in errors))

    def test_load_tampered_hash_returns_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            state = build_state(_sample_workflow())
            path = atomic_write_state(state, state_dir=state_dir)
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["status"] = "completed"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            loaded, errors = load_workflow_state("workflow_abc123", state_dir=state_dir)
            self.assertIsNone(loaded)
            self.assertTrue(any("hash" in e.lower() for e in errors))

    def test_write_failure_raises_workflow_state_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Point state_dir at a path that is itself a file, not a directory,
            # so mkdir(parents=True, exist_ok=True) fails -> write must fail closed.
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            state_dir = blocker / "nested"
            with self.assertRaises(WorkflowStateError) as ctx:
                atomic_write_state(build_state(_sample_workflow()), state_dir=state_dir)
            self.assertEqual(ctx.exception.code, "WORKFLOW_STATE_PERSIST_FAILED")


class ListAndDeleteTests(unittest.TestCase):
    def test_list_workflow_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            atomic_write_state(build_state(_sample_workflow(workflow_id="workflow_1")), state_dir=state_dir)
            atomic_write_state(build_state(_sample_workflow(workflow_id="workflow_2", status="completed")), state_dir=state_dir)
            summaries = list_workflow_states(state_dir=state_dir)
            ids = {s["workflowId"] for s in summaries}
            self.assertEqual(ids, {"workflow_1", "workflow_2"})

    def test_list_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_workflow_states(state_dir=Path(tmp) / "nonexistent"), [])

    def test_list_flags_corrupt_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "workflow_bad.json").write_text("{broken", encoding="utf-8")
            summaries = list_workflow_states(state_dir=state_dir)
            self.assertEqual(summaries[0]["status"], "corrupt")

    def test_delete_removes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            atomic_write_state(build_state(_sample_workflow()), state_dir=state_dir)
            self.assertTrue(delete_workflow_state("workflow_abc123", state_dir=state_dir))
            self.assertFalse((state_dir / "workflow_abc123.json").exists())

    def test_delete_missing_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(delete_workflow_state("workflow_never_existed", state_dir=Path(tmp)))


if __name__ == "__main__":
    unittest.main()
