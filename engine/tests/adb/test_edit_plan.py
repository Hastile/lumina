from __future__ import annotations

import sys
import unittest
from pathlib import Path


from lumina.adb.edit_plan import (
    TICKS_PER_SECOND,
    build_edit_plan,
    compute_plan_hash,
    detect_dependency_cycle,
    flatten_operations,
    merge_candidates_across_kinds,
    simulate_plan_operations,
    validate_plan_schema,
    verify_plan_hash,
)


def _interval_candidate(candidate_id, start_seconds, end_seconds, *, ops=None, executable=True):
    start_ticks, end_ticks = round(start_seconds * TICKS_PER_SECOND), round(end_seconds * TICKS_PER_SECOND)
    return {
        "candidateId": candidate_id, "kind": "silence_removal",
        "sequenceInterval": {
            "startTicks": str(start_ticks), "endTicks": str(end_ticks),
            "startSeconds": start_seconds, "endSeconds": end_seconds,
        },
        "durationSeconds": end_seconds - start_seconds,
        "touchesTrackItemStart": False, "touchesTrackItemEnd": False,
        "executable": executable, "blockedReasons": [], "warnings": [],
        "plannedOperations": ops or [
            {"operation": "split_track_item", "atSequenceTicks": str(start_ticks), "dependencies": []},
            {"operation": "split_track_item", "atSequenceTicks": str(end_ticks), "dependencies": [0]},
            {
                "operation": "remove_track_item", "rippleDelete": True,
                "removedInterval": {"startTicks": str(start_ticks), "endTicks": str(end_ticks)},
                "dependencies": [0, 1],
            },
        ],
    }


def _scene_candidate(candidate_id, seconds):
    ticks = round(seconds * TICKS_PER_SECOND)
    return {
        "candidateId": candidate_id, "kind": "scene_cut", "sequenceTicks": str(ticks),
        "sequenceSeconds": seconds, "executable": True, "blockedReasons": [], "warnings": [],
        "plannedOperations": [{"operation": "split_track_item", "atSequenceTicks": str(ticks), "dependencies": []}],
    }


class MergeCandidatesTests(unittest.TestCase):
    def test_overlapping_silence_and_black_merge_into_one_removal(self):
        silence = [_interval_candidate("silence_0", 4.0, 5.0)]
        black = [_interval_candidate("black_0", 4.8, 5.2)]
        result = merge_candidates_across_kinds(silence, [], black, "keep_all_non_overlapping")
        self.assertEqual(len(result["removalCandidates"]), 1)
        merged = result["removalCandidates"][0]
        self.assertEqual(merged["kind"], "combined_removal")
        self.assertEqual(set(merged["mergedFrom"]), {"silence_0", "black_0"})
        self.assertAlmostEqual(float(merged["sequenceInterval"]["startSeconds"]), 4.0)
        self.assertAlmostEqual(float(merged["sequenceInterval"]["endSeconds"]), 5.2)
        # Both sources' operations survive (they target different tracks), with the
        # second source's local dependency indices offset past the first source's.
        self.assertEqual(len(merged["plannedOperations"]), 6)
        self.assertEqual(merged["plannedOperations"][3]["dependencies"], [])
        self.assertEqual(merged["plannedOperations"][4]["dependencies"], [3])
        self.assertEqual(merged["plannedOperations"][5]["dependencies"], [3, 4])

    def test_non_overlapping_intervals_stay_separate(self):
        silence = [_interval_candidate("silence_0", 1.0, 2.0)]
        black = [_interval_candidate("black_0", 10.0, 11.0)]
        result = merge_candidates_across_kinds(silence, [], black, "keep_all_non_overlapping")
        self.assertEqual(len(result["removalCandidates"]), 2)
        self.assertEqual(result["conflicts"], [])

    def test_scene_cut_inside_removal_is_dropped(self):
        silence = [_interval_candidate("silence_0", 4.0, 5.0)]
        scene = [_scene_candidate("scene_0", 4.5)]
        result = merge_candidates_across_kinds(silence, scene, [], "keep_all_non_overlapping")
        self.assertEqual(result["sceneCandidates"], [])
        self.assertEqual(len(result["droppedScene"]), 1)
        self.assertIn("scene_0", result["removalCandidates"][0]["mergedFrom"])

    def test_scene_cut_outside_removal_is_kept(self):
        silence = [_interval_candidate("silence_0", 4.0, 5.0)]
        scene = [_scene_candidate("scene_0", 10.0)]
        result = merge_candidates_across_kinds(silence, scene, [], "keep_all_non_overlapping")
        self.assertEqual(len(result["sceneCandidates"]), 1)
        self.assertEqual(result["droppedScene"], [])

    def test_prefer_silence_policy_keeps_silence_fields(self):
        silence = [_interval_candidate("silence_0", 4.0, 5.0, executable=True)]
        black = [_interval_candidate("black_0", 4.8, 5.2, executable=False)]
        result = merge_candidates_across_kinds(silence, [], black, "prefer_silence")
        merged = result["removalCandidates"][0]
        self.assertTrue(merged["executable"])

    def test_invalid_conflict_policy_raises(self):
        with self.assertRaises(ValueError):
            merge_candidates_across_kinds([], [], [], "bogus_policy")


class FlattenOperationsTests(unittest.TestCase):
    def test_dependencies_remapped_to_global_ids(self):
        candidates = [_interval_candidate("silence_0", 4.0, 5.0)]
        result = flatten_operations(candidates)
        ops = result["operations"]
        self.assertEqual(len(ops), 3)
        split_a, split_b, remove = ops
        self.assertEqual(split_a["dependencies"], [])
        self.assertEqual(split_b["dependencies"], [split_a["operationId"]])
        self.assertEqual(set(remove["dependencies"]), {split_a["operationId"], split_b["operationId"]})
        self.assertNotEqual(split_a["operationId"], split_b["operationId"])

    def test_ordered_latest_sequence_time_first(self):
        candidates = [_interval_candidate("early", 1.0, 2.0), _interval_candidate("late", 10.0, 11.0)]
        result = flatten_operations(candidates)
        first_candidate_id = result["operations"][0]["candidateId"]
        self.assertEqual(first_candidate_id, "late")

    def test_rollback_class_assigned(self):
        candidates = [_interval_candidate("silence_0", 4.0, 5.0)]
        ops = flatten_operations(candidates)["operations"]
        self.assertEqual(ops[0]["rollbackClass"], "disposable_sequence_only")

    def test_scene_candidate_has_no_dependencies(self):
        result = flatten_operations([_scene_candidate("scene_0", 4.0)])
        self.assertEqual(result["operations"][0]["dependencies"], [])


class DependencyCycleTests(unittest.TestCase):
    def test_no_cycle_in_valid_chain(self):
        ops = [
            {"operationId": "a", "dependencies": []},
            {"operationId": "b", "dependencies": ["a"]},
            {"operationId": "c", "dependencies": ["a", "b"]},
        ]
        self.assertIsNone(detect_dependency_cycle(ops))

    def test_direct_cycle_detected(self):
        ops = [
            {"operationId": "a", "dependencies": ["b"]},
            {"operationId": "b", "dependencies": ["a"]},
        ]
        cycle = detect_dependency_cycle(ops)
        self.assertIsNotNone(cycle)
        self.assertIn("a", cycle)
        self.assertIn("b", cycle)

    def test_self_dependency_detected(self):
        ops = [{"operationId": "a", "dependencies": ["a"]}]
        self.assertIsNotNone(detect_dependency_cycle(ops))

    def test_indirect_cycle_detected(self):
        ops = [
            {"operationId": "a", "dependencies": ["c"]},
            {"operationId": "b", "dependencies": ["a"]},
            {"operationId": "c", "dependencies": ["b"]},
        ]
        self.assertIsNotNone(detect_dependency_cycle(ops))


class PlanHashTests(unittest.TestCase):
    def _plan(self, **overrides):
        base = {
            "schemaVersion": "1.0", "planId": "plan_x", "createdAt": "t0",
            "candidates": [{"candidateId": "a"}], "operations": [], "warnings": [],
        }
        base.update(overrides)
        return base

    def test_hash_stable_across_plan_id_and_created_at(self):
        hash_1 = compute_plan_hash(self._plan(planId="plan_a", createdAt="2020-01-01"))
        hash_2 = compute_plan_hash(self._plan(planId="plan_b", createdAt="2021-06-06"))
        self.assertEqual(hash_1, hash_2)

    def test_hash_changes_with_content(self):
        hash_1 = compute_plan_hash(self._plan())
        hash_2 = compute_plan_hash(self._plan(candidates=[{"candidateId": "b"}]))
        self.assertNotEqual(hash_1, hash_2)

    def test_hash_stable_across_approval_status_change(self):
        # approve_timeline_edit_plan only ever edits the approval block - that's
        # a legitimate, tracked workflow decision, not the hand-editing/corruption
        # this hash exists to catch, so it must never invalidate the hash.
        pending = self._plan(approval={"status": "pending", "approvedOperationIds": []}, executable=False)
        approved = self._plan(approval={"status": "approved", "approvedOperationIds": ["op1"], "approvedAt": "t1"}, executable=False)
        self.assertEqual(compute_plan_hash(pending), compute_plan_hash(approved))

    def test_hash_stable_across_int_vs_float_representation(self):
        # A plain JSON round-trip (or a client re-serializing the plan) can
        # legitimately collapse 1.0 to 1 without changing its meaning - that
        # must not look like hand-edited content.
        as_float = self._plan(candidates=[{"candidateId": "a", "confidence": 1.0, "trackIndex": 0}])
        as_int = self._plan(candidates=[{"candidateId": "a", "confidence": 1, "trackIndex": 0}])
        self.assertEqual(compute_plan_hash(as_float), compute_plan_hash(as_int))

    def test_hash_still_changes_for_genuinely_different_numbers(self):
        low = self._plan(candidates=[{"candidateId": "a", "confidence": 0.8}])
        high = self._plan(candidates=[{"candidateId": "a", "confidence": 1.0}])
        self.assertNotEqual(compute_plan_hash(low), compute_plan_hash(high))


class BuildEditPlanTests(unittest.TestCase):
    def test_assembles_full_schema_with_pending_approval(self):
        merge_result = merge_candidates_across_kinds([_interval_candidate("silence_0", 4.0, 5.0)], [], [], "keep_all_non_overlapping")
        flatten_result = flatten_operations(merge_result["candidates"])
        plan = build_edit_plan(
            project={"name": "demo"}, sequence={"id": "seq1", "name": "SEQ"}, sequence_fingerprint="fp1",
            timebase={"fps": 30}, sources=[], analyses={"silence": True}, protected_intervals=[],
            merge_result=merge_result, flatten_result=flatten_result, warnings=[],
        )
        self.assertEqual(plan["schemaVersion"], "1.0")
        self.assertEqual(plan["approval"]["status"], "pending")
        self.assertFalse(plan["executable"])
        self.assertEqual(plan["summary"]["candidateCount"], 1)
        self.assertEqual(plan["summary"]["operationCount"], 3)
        self.assertIn("planHash", plan)
        self.assertIsNone(detect_dependency_cycle(plan["operations"]))

    def _build_valid_plan(self):
        merge_result = merge_candidates_across_kinds([_interval_candidate("silence_0", 4.0, 5.0)], [], [], "keep_all_non_overlapping")
        flatten_result = flatten_operations(merge_result["candidates"])
        return build_edit_plan(
            project={"name": "demo"}, sequence={"id": "seq1", "name": "SEQ"}, sequence_fingerprint="fp1",
            timebase={"fps": 30}, sources=[], analyses={"silence": True}, protected_intervals=[],
            merge_result=merge_result, flatten_result=flatten_result, warnings=[],
        )

    def test_valid_plan_passes_schema_validation(self):
        plan = self._build_valid_plan()
        self.assertEqual(validate_plan_schema(plan), [])
        self.assertTrue(verify_plan_hash(plan))

    def test_wrong_schema_version_is_rejected(self):
        plan = self._build_valid_plan()
        plan["schemaVersion"] = "0.9"
        errors = validate_plan_schema(plan)
        self.assertTrue(any("schemaVersion" in e for e in errors))

    def test_missing_required_key_is_rejected(self):
        plan = self._build_valid_plan()
        del plan["approval"]
        errors = validate_plan_schema(plan)
        self.assertTrue(any("Missing required plan keys" in e for e in errors))

    def test_duplicate_operation_id_is_rejected(self):
        plan = self._build_valid_plan()
        plan["operations"].append(dict(plan["operations"][0]))
        errors = validate_plan_schema(plan)
        self.assertTrue(any("Duplicate operationId" in e for e in errors))

    def test_dangling_dependency_is_rejected(self):
        plan = self._build_valid_plan()
        plan["operations"][0]["dependencies"] = ["op_does_not_exist"]
        errors = validate_plan_schema(plan)
        self.assertTrue(any("unknown operationId" in e for e in errors))

    def test_hand_edited_plan_fails_hash_verification(self):
        plan = self._build_valid_plan()
        plan["candidates"].append({"candidateId": "injected"})
        self.assertFalse(verify_plan_hash(plan))

    def test_simulate_reports_removed_duration_and_item_delta(self):
        plan = self._build_valid_plan()
        result = simulate_plan_operations(plan)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertAlmostEqual(result["totalRemovedSeconds"], 1.0, places=2)
        self.assertEqual(result["removeCount"], 1)
        self.assertEqual(result["estimatedItemCountDelta"], 1)
        self.assertEqual(result["collisions"], [])

    def test_simulate_detects_overlapping_removals_on_same_track(self):
        candidates = [_interval_candidate("a", 1.0, 3.0), _interval_candidate("b", 2.0, 4.0)]
        for candidate in candidates:
            for op in candidate["plannedOperations"]:
                op["target"] = {"mediaType": "audio", "trackIndex": 0}
        merge_result = {"candidates": candidates, "conflicts": [], "droppedScene": []}
        flatten_result = flatten_operations(candidates)
        plan = build_edit_plan(
            project={}, sequence={"id": "s"}, sequence_fingerprint="fp", timebase={},
            sources=[], analyses={}, protected_intervals=[], merge_result=merge_result,
            flatten_result=flatten_result, warnings=[],
        )
        result = simulate_plan_operations(plan)
        self.assertTrue(len(result["collisions"]) >= 1)

    def test_simulate_detects_dependency_cycle(self):
        plan = self._build_valid_plan()
        plan["operations"][0]["dependencies"] = [plan["operations"][-1]["operationId"]]
        plan["operations"][-1]["dependencies"].append(plan["operations"][0]["operationId"])
        result = simulate_plan_operations(plan)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error"]["code"], "EDIT_PLAN_DEPENDENCY_CYCLE")


if __name__ == "__main__":
    unittest.main()
