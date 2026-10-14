from __future__ import annotations

import sys
import unittest
from pathlib import Path

# premiere_edit_planning.py does a bare sibling import of premiere_track_items
# (matching the rest of mcp/*.py); make that resolve regardless of which other
# test modules have already run, instead of relying on collection order.

from lumina.adb.premiere_edit_planning import (
    EditPlanError,
    TICKS_PER_SECOND,
    apply_padding_ticks,
    build_black_candidates,
    build_scene_candidates,
    build_silence_candidates,
    is_normal_speed,
    map_source_interval_to_sequence,
    map_source_timestamp_to_sequence,
    markers_to_protected_intervals,
    merge_adjacent_intervals,
    plan_black_operations,
    plan_scene_operations,
    plan_silence_operations,
    soft_protected_overlaps,
    subtract_hard_protected_intervals,
)

ONE_FRAME_30FPS = round(TICKS_PER_SECOND / 30)


def _item(*, start=0.0, end=10.0, in_point=0.0, out_point=10.0, speed=1.0):
    return {
        "startTicks": str(round(start * TICKS_PER_SECOND)),
        "endTicks": str(round(end * TICKS_PER_SECOND)),
        "inPointTicks": str(round(in_point * TICKS_PER_SECOND)),
        "outPointTicks": str(round(out_point * TICKS_PER_SECOND)),
        "speed": speed,
    }


class SourceToSequenceMappingTests(unittest.TestCase):
    def test_basic_mapping_no_offsets(self):
        item = _item()
        mapping = map_source_interval_to_sequence(item, 2.0, 3.0, ONE_FRAME_30FPS)
        self.assertAlmostEqual(mapping["sourceStartSeconds"], 2.0)
        self.assertEqual(int(mapping["sequenceStartTicks"]), round(2.0 * TICKS_PER_SECOND))
        self.assertEqual(int(mapping["sequenceEndTicks"]), round(3.0 * TICKS_PER_SECOND))

    def test_source_in_offset_mapping(self):
        # TrackItem uses source [5, 15) mapped to timeline [0, 10).
        item = _item(start=0.0, end=10.0, in_point=5.0, out_point=15.0)
        mapping = map_source_interval_to_sequence(item, 7.0, 8.0, ONE_FRAME_30FPS)
        # clip-relative = source - inPoint = 2.0 -> sequence = start(0) + 2.0 = 2.0
        self.assertEqual(int(mapping["sequenceStartTicks"]), round(2.0 * TICKS_PER_SECOND))
        self.assertEqual(int(mapping["sequenceEndTicks"]), round(3.0 * TICKS_PER_SECOND))

    def test_sequence_start_offset_mapping(self):
        item = _item(start=20.0, end=30.0, in_point=0.0, out_point=10.0)
        mapping = map_source_interval_to_sequence(item, 2.0, 3.0, ONE_FRAME_30FPS)
        self.assertEqual(int(mapping["sequenceStartTicks"]), round(22.0 * TICKS_PER_SECOND))
        self.assertEqual(int(mapping["sequenceEndTicks"]), round(23.0 * TICKS_PER_SECOND))

    def test_non_normal_speed_blocks_mapping(self):
        item = _item(speed=1.5)
        with self.assertRaises(EditPlanError) as ctx:
            map_source_interval_to_sequence(item, 2.0, 3.0, ONE_FRAME_30FPS)
        self.assertEqual(ctx.exception.code, "TRACK_ITEM_SPEED_MAPPING_UNAVAILABLE")

    def test_unreadable_speed_blocks_mapping(self):
        item = _item()
        item["speed"] = None
        with self.assertRaises(EditPlanError) as ctx:
            map_source_interval_to_sequence(item, 2.0, 3.0, ONE_FRAME_30FPS)
        self.assertEqual(ctx.exception.code, "TRACK_ITEM_SPEED_MAPPING_UNAVAILABLE")

    def test_interval_outside_source_range_returns_none(self):
        item = _item(in_point=0.0, out_point=10.0)
        self.assertIsNone(map_source_interval_to_sequence(item, 20.0, 21.0, ONE_FRAME_30FPS))

    def test_is_normal_speed(self):
        self.assertTrue(is_normal_speed(1.0))
        self.assertFalse(is_normal_speed(1.001))
        self.assertFalse(is_normal_speed(100.0))
        self.assertFalse(is_normal_speed(None))
        self.assertFalse(is_normal_speed(True))


class PaddingTests(unittest.TestCase):
    def test_padding_grows_both_sides(self):
        start, end = apply_padding_ticks(
            1000, 2000, 100, 200, bound_start_ticks=0, bound_end_ticks=10000,
        )
        self.assertEqual((start, end), (900, 2200))

    def test_padding_clamped_to_bounds(self):
        start, end = apply_padding_ticks(
            50, 100, 100, 100, bound_start_ticks=0, bound_end_ticks=150,
        )
        self.assertEqual((start, end), (0, 150))


class MergeTests(unittest.TestCase):
    def test_adjacent_intervals_within_gap_are_merged(self):
        intervals = [
            {"startTicks": "0", "endTicks": "100", "id": "a"},
            {"startTicks": "110", "endTicks": "200", "id": "b"},
        ]
        merged = merge_adjacent_intervals(intervals, merge_gap_ticks=20)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["startTicks"], "0")
        self.assertEqual(merged[0]["endTicks"], "200")
        self.assertEqual(merged[0]["mergedFrom"], ["a", "b"])

    def test_intervals_beyond_gap_stay_separate(self):
        intervals = [
            {"startTicks": "0", "endTicks": "100", "id": "a"},
            {"startTicks": "500", "endTicks": "600", "id": "b"},
        ]
        merged = merge_adjacent_intervals(intervals, merge_gap_ticks=20)
        self.assertEqual(len(merged), 2)

    def test_unordered_input_is_sorted_first(self):
        intervals = [
            {"startTicks": "500", "endTicks": "600", "id": "b"},
            {"startTicks": "0", "endTicks": "100", "id": "a"},
        ]
        merged = merge_adjacent_intervals(intervals, merge_gap_ticks=1000)
        self.assertEqual(merged[0]["mergedFrom"], ["a", "b"])


class ProtectedIntervalTests(unittest.TestCase):
    def test_hard_protected_interval_splits_candidate(self):
        pieces = subtract_hard_protected_intervals(0, 1000, [
            {"startTicks": "400", "endTicks": "600", "hard": True},
        ])
        self.assertEqual(pieces, [(0, 400), (600, 1000)])

    def test_soft_protected_interval_is_not_subtracted(self):
        pieces = subtract_hard_protected_intervals(0, 1000, [
            {"startTicks": "400", "endTicks": "600", "hard": False},
        ])
        self.assertEqual(pieces, [(0, 1000)])
        hits = soft_protected_overlaps(0, 1000, [{"startTicks": "400", "endTicks": "600", "hard": False}])
        self.assertEqual(len(hits), 1)

    def test_fully_covered_by_hard_protection_leaves_no_pieces(self):
        pieces = subtract_hard_protected_intervals(400, 600, [
            {"startTicks": "0", "endTicks": "1000", "hard": True},
        ])
        self.assertEqual(pieces, [])

    def test_markers_to_protected_intervals_applies_padding(self):
        markers = [{"startSeconds": 5.0, "durationSeconds": 1.0, "guid": "m1"}]
        intervals = markers_to_protected_intervals(markers, padding_seconds=0.25)
        self.assertEqual(len(intervals), 1)
        self.assertTrue(intervals[0]["hard"])
        self.assertEqual(int(intervals[0]["startTicks"]), round(4.75 * TICKS_PER_SECOND))
        self.assertEqual(int(intervals[0]["endTicks"]), round(6.25 * TICKS_PER_SECOND))


class BuildSilenceCandidatesTests(unittest.TestCase):
    def test_simple_silence_produces_one_executable_candidate(self):
        item = _item(start=0.0, end=10.0)
        result = build_silence_candidates(
            item=item, silence_intervals=[{"startSeconds": 4.0, "endSeconds": 4.5, "durationSeconds": 0.5}],
            one_frame_ticks=ONE_FRAME_30FPS, padding_before_seconds=0.0, padding_after_seconds=0.0,
            remove_min_duration_seconds=0.35, minimum_remaining_clip_seconds=0.2,
            preserve_first_seconds=0.0, preserve_last_seconds=0.0, protected_intervals=[], merge_gap_seconds=0.1,
        )
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertTrue(candidate["executable"])
        self.assertAlmostEqual(candidate["durationSeconds"], 0.5, places=2)

    def test_too_short_silence_is_skipped(self):
        item = _item()
        result = build_silence_candidates(
            item=item, silence_intervals=[{"startSeconds": 4.0, "endSeconds": 4.1, "durationSeconds": 0.1}],
            one_frame_ticks=ONE_FRAME_30FPS, padding_before_seconds=0.0, padding_after_seconds=0.0,
            remove_min_duration_seconds=0.35, minimum_remaining_clip_seconds=0.2,
            preserve_first_seconds=0.0, preserve_last_seconds=0.0, protected_intervals=[], merge_gap_seconds=0.1,
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_removing_nearly_whole_clip_is_blocked_not_dropped(self):
        item = _item(start=0.0, end=1.0)
        result = build_silence_candidates(
            item=item, silence_intervals=[{"startSeconds": 0.0, "endSeconds": 0.95, "durationSeconds": 0.95}],
            one_frame_ticks=ONE_FRAME_30FPS, padding_before_seconds=0.0, padding_after_seconds=0.0,
            remove_min_duration_seconds=0.1, minimum_remaining_clip_seconds=0.2,
            preserve_first_seconds=0.0, preserve_last_seconds=0.0, protected_intervals=[], merge_gap_seconds=0.1,
        )
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertFalse(candidate["executable"])
        self.assertIn("EDIT_CANDIDATE_TOO_SHORT", candidate["blockedReasons"])

    def test_preserve_first_seconds_excludes_leading_silence(self):
        item = _item(start=0.0, end=10.0)
        result = build_silence_candidates(
            item=item, silence_intervals=[{"startSeconds": 0.0, "endSeconds": 1.0, "durationSeconds": 1.0}],
            one_frame_ticks=ONE_FRAME_30FPS, padding_before_seconds=0.0, padding_after_seconds=0.0,
            remove_min_duration_seconds=0.1, minimum_remaining_clip_seconds=0.2,
            preserve_first_seconds=1.0, preserve_last_seconds=0.0, protected_intervals=[], merge_gap_seconds=0.1,
        )
        self.assertEqual(result["candidates"], [])

    def test_padding_extends_candidate_interval(self):
        item = _item(start=0.0, end=10.0)
        result = build_silence_candidates(
            item=item, silence_intervals=[{"startSeconds": 4.0, "endSeconds": 4.5, "durationSeconds": 0.5}],
            one_frame_ticks=ONE_FRAME_30FPS, padding_before_seconds=0.1, padding_after_seconds=0.1,
            remove_min_duration_seconds=0.1, minimum_remaining_clip_seconds=0.2,
            preserve_first_seconds=0.0, preserve_last_seconds=0.0, protected_intervals=[], merge_gap_seconds=0.1,
        )
        candidate = result["candidates"][0]
        self.assertAlmostEqual(candidate["durationSeconds"], 0.7, places=2)

    def test_unclosed_trailing_silence_is_skipped_not_guessed(self):
        item = _item()
        result = build_silence_candidates(
            item=item, silence_intervals=[{"startSeconds": 8.0, "endSeconds": None, "durationSeconds": None}],
            one_frame_ticks=ONE_FRAME_30FPS, padding_before_seconds=0.0, padding_after_seconds=0.0,
            remove_min_duration_seconds=0.1, minimum_remaining_clip_seconds=0.2,
            preserve_first_seconds=0.0, preserve_last_seconds=0.0, protected_intervals=[], merge_gap_seconds=0.1,
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["skipped"]), 1)


class PlanSilenceOperationsTests(unittest.TestCase):
    def _candidate(self, **overrides):
        base = {
            "sequenceInterval": {"startTicks": str(round(4 * TICKS_PER_SECOND)), "endTicks": str(round(5 * TICKS_PER_SECOND))},
            "touchesTrackItemStart": False, "touchesTrackItemEnd": False,
        }
        base.update(overrides)
        return base

    def test_remove_and_ripple_produces_two_splits_and_a_ripple_remove(self):
        ops, blocked = plan_silence_operations(self._candidate(), {"trackIndex": 0}, "remove_and_ripple")
        self.assertEqual([op["operation"] for op in ops], ["split_track_item", "split_track_item", "remove_track_item"])
        self.assertEqual(ops[2]["dependencies"], [0, 1])
        self.assertEqual(blocked, [])

    def test_split_only_has_no_remove_operation(self):
        ops, _ = plan_silence_operations(self._candidate(), {"trackIndex": 0}, "split_only")
        self.assertEqual([op["operation"] for op in ops], ["split_track_item", "split_track_item"])

    def test_markers_only_has_no_timeline_mutation(self):
        ops, _ = plan_silence_operations(self._candidate(), {"trackIndex": 0}, "markers_only")
        self.assertTrue(all(op["operation"] == "create_sequence_marker" for op in ops))

    def test_trim_edges_only_blocks_interior_silence(self):
        ops, blocked = plan_silence_operations(self._candidate(), {"trackIndex": 0}, "trim_edges_only")
        self.assertEqual(ops, [])
        self.assertIn("EDIT_CANDIDATE_CONFLICT", blocked)

    def test_trim_edges_only_allows_edge_touching_silence(self):
        ops, blocked = plan_silence_operations(
            self._candidate(touchesTrackItemStart=True), {"trackIndex": 0}, "trim_edges_only",
        )
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["mode"], "trim_start_to")
        self.assertEqual(blocked, [])

    def test_invalid_strategy_raises(self):
        with self.assertRaises(EditPlanError):
            plan_silence_operations(self._candidate(), {"trackIndex": 0}, "bogus_strategy")


class SceneTimestampMappingTests(unittest.TestCase):
    def test_basic_timestamp_mapping(self):
        item = _item()
        mapping = map_source_timestamp_to_sequence(item, 4.0, ONE_FRAME_30FPS)
        self.assertEqual(int(mapping["quantizedSequenceTicks"]), round(4.0 * TICKS_PER_SECOND))

    def test_timestamp_outside_source_range_returns_none(self):
        item = _item(in_point=0.0, out_point=10.0)
        self.assertIsNone(map_source_timestamp_to_sequence(item, 20.0, ONE_FRAME_30FPS))

    def test_non_normal_speed_blocks_mapping(self):
        item = _item(speed=1.5)
        with self.assertRaises(EditPlanError):
            map_source_timestamp_to_sequence(item, 4.0, ONE_FRAME_30FPS)


class BuildSceneCandidatesTests(unittest.TestCase):
    def test_event_near_clip_edge_is_filtered(self):
        item = _item(start=0.0, end=10.0)
        # 0.02s from clip start, well inside a 2-frame (30fps) edge guard.
        events = [{"ptsSeconds": 0.02, "sceneScore": 0.5}]
        result = build_scene_candidates(
            item=item, scene_events=events, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_scene_duration_seconds=0.5, minimum_cut_distance_frames=2,
            ignore_near_clip_edges_frames=2, merge_nearby_events_frames=1,
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_nearby_events_are_merged_keeping_highest_score(self):
        item = _item(start=0.0, end=10.0)
        events = [
            {"ptsSeconds": 4.0, "sceneScore": 0.3},
            {"ptsSeconds": 4.0 + (1 / 30), "sceneScore": 0.9},
        ]
        result = build_scene_candidates(
            item=item, scene_events=events, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_scene_duration_seconds=0.5, minimum_cut_distance_frames=2,
            ignore_near_clip_edges_frames=2, merge_nearby_events_frames=2,
        )
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["score"], 0.9)
        self.assertEqual(len(result["candidates"][0]["mergedFrom"]), 2)

    def test_minimum_scene_duration_drops_too_close_cut(self):
        item = _item(start=0.0, end=10.0)
        events = [
            {"ptsSeconds": 4.0, "sceneScore": 0.5},
            {"ptsSeconds": 4.2, "sceneScore": 0.5},
        ]
        result = build_scene_candidates(
            item=item, scene_events=events, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_scene_duration_seconds=1.0, minimum_cut_distance_frames=0,
            ignore_near_clip_edges_frames=2, merge_nearby_events_frames=0,
        )
        # The second cut is only 0.2s after the first - below the 1.0s minimum scene duration.
        self.assertEqual(len(result["candidates"]), 1)
        self.assertAlmostEqual(result["candidates"][0]["sequenceSeconds"], 4.0, places=2)

    def test_final_segment_too_short_drops_last_cut(self):
        item = _item(start=0.0, end=10.0)
        events = [{"ptsSeconds": 9.8, "sceneScore": 0.5}]
        result = build_scene_candidates(
            item=item, scene_events=events, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_scene_duration_seconds=1.0, minimum_cut_distance_frames=0,
            ignore_near_clip_edges_frames=0, merge_nearby_events_frames=0,
        )
        # 10.0 - 9.8 = 0.2s final segment, below the 1.0s minimum.
        self.assertEqual(result["candidates"], [])

    def test_previous_and_next_scene_durations(self):
        item = _item(start=0.0, end=10.0)
        events = [{"ptsSeconds": 4.0, "sceneScore": 0.5}]
        result = build_scene_candidates(
            item=item, scene_events=events, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_scene_duration_seconds=0.5, minimum_cut_distance_frames=0,
            ignore_near_clip_edges_frames=0, merge_nearby_events_frames=0,
        )
        candidate = result["candidates"][0]
        self.assertAlmostEqual(candidate["previousSceneDurationSeconds"], 4.0, places=2)
        self.assertAlmostEqual(candidate["nextSceneDurationSeconds"], 6.0, places=2)


class PlanSceneOperationsTests(unittest.TestCase):
    def test_split_only_produces_single_split(self):
        candidate = {"sequenceTicks": str(round(4 * TICKS_PER_SECOND))}
        ops = plan_scene_operations(candidate, {"trackIndex": 0}, "split_only")
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["operation"], "split_track_item")

    def test_markers_only_produces_marker(self):
        candidate = {"sequenceTicks": str(round(4 * TICKS_PER_SECOND))}
        ops = plan_scene_operations(candidate, {"trackIndex": 0}, "markers_only")
        self.assertEqual(ops[0]["operation"], "create_sequence_marker")

    def test_invalid_strategy_raises(self):
        with self.assertRaises(EditPlanError):
            plan_scene_operations({"sequenceTicks": "0"}, {"trackIndex": 0}, "bogus")


class BuildBlackCandidatesTests(unittest.TestCase):
    def test_interior_black_with_no_overlapping_silence_blocks_removal(self):
        item = _item(start=0.0, end=10.0)
        intervals = [{"startSeconds": 4.0, "endSeconds": 4.5, "durationSeconds": 0.5}]
        result = build_black_candidates(
            item=item, black_intervals=intervals, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_black_duration_seconds=0.25, padding_before_seconds=0.0, padding_after_seconds=0.0,
            protected_intervals=[], audio_silence_intervals=[],
        )
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        # Continuity was checked and no matching silence interval was found -> audio confirmed continuing.
        self.assertEqual(candidate["audioContinues"], True)
        self.assertEqual(candidate["recommendedAction"], "review_audio_continuity")
        self.assertFalse(candidate["executable"])

    def test_interior_black_with_matching_silence_recommends_removal(self):
        item = _item(start=0.0, end=10.0)
        intervals = [{"startSeconds": 4.0, "endSeconds": 4.5, "durationSeconds": 0.5}]
        result = build_black_candidates(
            item=item, black_intervals=intervals, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_black_duration_seconds=0.25, padding_before_seconds=0.0, padding_after_seconds=0.0,
            protected_intervals=[], audio_silence_intervals=[{"startSeconds": 3.9, "endSeconds": 4.6}],
        )
        candidate = result["candidates"][0]
        self.assertEqual(candidate["audioContinues"], False)
        self.assertEqual(candidate["recommendedAction"], "remove_and_ripple")
        self.assertTrue(candidate["executable"])

    def test_unverified_audio_continuity_fails_closed(self):
        # audio_silence_intervals not supplied at all (None) - continuity was
        # never checked. Must NOT silently default to the permissive action.
        item = _item(start=0.0, end=10.0)
        intervals = [{"startSeconds": 4.0, "endSeconds": 4.5, "durationSeconds": 0.5}]
        result = build_black_candidates(
            item=item, black_intervals=intervals, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_black_duration_seconds=0.25, padding_before_seconds=0.0, padding_after_seconds=0.0,
            protected_intervals=[],
        )
        candidate = result["candidates"][0]
        self.assertIsNone(candidate["audioContinues"])
        self.assertEqual(candidate["recommendedAction"], "review_audio_continuity")
        self.assertFalse(candidate["executable"])

    def test_no_audio_stream_overrides_to_safe_removal(self):
        item = _item(start=0.0, end=10.0)
        intervals = [{"startSeconds": 4.0, "endSeconds": 4.5, "durationSeconds": 0.5}]
        result = build_black_candidates(
            item=item, black_intervals=intervals, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_black_duration_seconds=0.25, padding_before_seconds=0.0, padding_after_seconds=0.0,
            protected_intervals=[], has_audio_stream=False,
        )
        candidate = result["candidates"][0]
        self.assertEqual(candidate["audioContinues"], False)
        self.assertEqual(candidate["recommendedAction"], "remove_and_ripple")
        self.assertTrue(candidate["executable"])

    def test_edge_touching_black_recommends_trim(self):
        item = _item(start=0.0, end=10.0)
        intervals = [{"startSeconds": 0.0, "endSeconds": 0.5, "durationSeconds": 0.5}]
        result = build_black_candidates(
            item=item, black_intervals=intervals, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_black_duration_seconds=0.25, padding_before_seconds=0.0, padding_after_seconds=0.0,
            protected_intervals=[],
        )
        candidate = result["candidates"][0]
        self.assertTrue(candidate["touchesTrackItemStart"])
        self.assertEqual(candidate["recommendedAction"], "trim")

    def test_open_ended_black_interval_is_skipped(self):
        item = _item(start=0.0, end=10.0)
        intervals = [{"startSeconds": 8.0, "endSeconds": None, "durationSeconds": None}]
        result = build_black_candidates(
            item=item, black_intervals=intervals, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_black_duration_seconds=0.25, padding_before_seconds=0.0, padding_after_seconds=0.0,
            protected_intervals=[],
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_below_minimum_duration_is_skipped(self):
        item = _item(start=0.0, end=10.0)
        intervals = [{"startSeconds": 4.0, "endSeconds": 4.1, "durationSeconds": 0.1}]
        result = build_black_candidates(
            item=item, black_intervals=intervals, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_black_duration_seconds=0.25, padding_before_seconds=0.0, padding_after_seconds=0.0,
            protected_intervals=[],
        )
        self.assertEqual(result["candidates"], [])

    def test_short_black_is_flagged_as_likely_fade(self):
        item = _item(start=0.0, end=10.0)
        intervals = [{"startSeconds": 4.0, "endSeconds": 4.5, "durationSeconds": 0.5}]
        result = build_black_candidates(
            item=item, black_intervals=intervals, one_frame_ticks=ONE_FRAME_30FPS,
            minimum_black_duration_seconds=0.25, padding_before_seconds=0.0, padding_after_seconds=0.0,
            protected_intervals=[], audio_silence_intervals=[],
        )
        self.assertTrue(result["candidates"][0]["likelyFade"])


class PlanBlackOperationsTests(unittest.TestCase):
    def _candidate(self, **overrides):
        base = {
            "sequenceInterval": {"startTicks": str(round(4 * TICKS_PER_SECOND)), "endTicks": str(round(5 * TICKS_PER_SECOND))},
            "touchesTrackItemStart": False, "touchesTrackItemEnd": False,
        }
        base.update(overrides)
        return base

    def test_remove_and_ripple_produces_two_splits_and_a_ripple_remove(self):
        ops, blocked = plan_black_operations(self._candidate(), {"trackIndex": 0}, "remove_and_ripple")
        self.assertEqual([op["operation"] for op in ops], ["split_track_item", "split_track_item", "remove_track_item"])
        self.assertEqual(blocked, [])

    def test_trim_edges_blocks_interior_black(self):
        ops, blocked = plan_black_operations(self._candidate(), {"trackIndex": 0}, "trim_edges")
        self.assertEqual(ops, [])
        self.assertIn("EDIT_CANDIDATE_CONFLICT", blocked)

    def test_markers_only(self):
        ops, _ = plan_black_operations(self._candidate(), {"trackIndex": 0}, "markers_only")
        self.assertTrue(all(op["operation"] == "create_sequence_marker" for op in ops))

    def test_invalid_strategy_raises(self):
        with self.assertRaises(EditPlanError):
            plan_black_operations(self._candidate(), {"trackIndex": 0}, "bogus")


if __name__ == "__main__":
    unittest.main()
