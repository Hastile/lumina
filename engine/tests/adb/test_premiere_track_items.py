from __future__ import annotations

import json
import unittest

from lumina.adb.premiere_track_items import (
    _find_safe_split_scratch_interval,
    _simulate_split_scratch_allocation,
    EditPlanError,
    ensure_no_destination_collisions,
    exact_tick_value,
    find_item,
    linked_candidates,
    ripple_plan,
    selector,
    speed_plan,
    split_plan,
    trim_plan,
)

FRAME = 100


def item(index=0, start=0, end=1000, source_in=0, source_out=1000, project="p1", **extra):
    return {
        "itemIndex": index, "projectItemId": project,
        "startTicks": str(start), "endTicks": str(end),
        "inPointTicks": str(source_in), "outPointTicks": str(source_out), **extra,
    }


def state(video=None, audio=None):
    return {
        "timebase": str(FRAME),
        "videoTracks": [{"trackIndex": i, "items": rows} for i, rows in enumerate(video or [[]])],
        "audioTracks": [{"trackIndex": i, "items": rows} for i, rows in enumerate(audio or [[]])],
    }


class DynamicScratchAllocationTests(unittest.TestCase):
    def test_prefers_after_track_end(self):
        target = item(start=0, end=1000)
        snapshot = state(audio=[[target, item(index=1, start=1200, end=1800, project="p2")]])
        result = _find_safe_split_scratch_interval(
            snapshot, media_type="audio", track_index=0, target_item=target,
            required_duration_ticks=1000, safety_margin_frames=2,
        )
        self.assertEqual(result["source"], "after_track_end")
        self.assertEqual(result["startTicks"], "2000")

    def test_other_track_can_make_after_sequence_candidate_later(self):
        target = item(start=0, end=1000)
        snapshot = state(video=[[item(start=0, end=5000, project="v")]], audio=[[target]])
        result = _find_safe_split_scratch_interval(snapshot, media_type="audio", track_index=0, target_item=target, required_duration_ticks=1000)
        self.assertEqual(result["source"], "after_track_end")
        self.assertLess(int(result["startTicks"]), 5000)

    def test_protected_interval_rejects_first_candidate(self):
        target = item(start=0, end=1000)
        snapshot = state(audio=[[target]])
        result = _find_safe_split_scratch_interval(
            snapshot, media_type="audio", track_index=0, target_item=target,
            required_duration_ticks=1000, protected_intervals=[{"startTicks": "1200", "endTicks": "2400", "reason": "transition"}],
        )
        self.assertNotEqual(result["startTicks"], "1200")
        self.assertTrue(result["rejectedCandidates"])

    def test_no_interval_with_tight_timeline_limit(self):
        target = item(start=0, end=1000)
        snapshot = state(audio=[[target]])
        with self.assertRaises(EditPlanError) as ctx:
            _find_safe_split_scratch_interval(snapshot, media_type="audio", track_index=0, target_item=target, required_duration_ticks=1000, maximum_timeline_ticks=1100)
        self.assertEqual(ctx.exception.code, "SPLIT_SCRATCH_INTERVAL_UNAVAILABLE")

    def test_simulation_preserves_source_continuity(self):
        target = item(start=0, end=1000)
        snapshot = state(audio=[[target]])
        result = _simulate_split_scratch_allocation(snapshot, media_type="audio", track_index=0, target_item=target, split_ticks=500)
        self.assertTrue(result["sourceContinuous"])
        self.assertEqual(result["splitStrategy"], "dynamic_scratch")

    def test_frame_margin_is_exact(self):
        target = item(start=0, end=1000)
        snapshot = state(audio=[[target]])
        result = _find_safe_split_scratch_interval(snapshot, media_type="audio", track_index=0, target_item=target, required_duration_ticks=1000, safety_margin_frames=3)
        self.assertEqual(result["safetyMarginTicks"], "300")


class TrackItemPlannerTests(unittest.TestCase):
    def assert_code(self, code, callback):
        with self.assertRaises(EditPlanError) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)

    def test_split_point_validation_and_edges(self):
        self.assert_code("INVALID_SPLIT_POINT", lambda: split_plan(item(), -1, FRAME))
        for value in (0, 1000):
            self.assert_code("INVALID_SPLIT_POINT", lambda value=value: split_plan(item(), value, FRAME))

    def test_split_minimum_frame_and_interval_continuity(self):
        self.assert_code("INVALID_SPLIT_POINT", lambda: split_plan(item(), 49, FRAME))
        one_frame = split_plan(item(), 99, FRAME)
        self.assertEqual("100", one_frame["left"]["endTicks"])
        plan = split_plan(item(source_in=500, source_out=1500), 400, FRAME)
        self.assertEqual(plan["left"]["endTicks"], plan["right"]["startTicks"])
        self.assertEqual("400", plan["right"]["startTicks"])
        self.assertEqual(plan["left"]["outPointTicks"], plan["right"]["inPointTicks"])
        self.assertEqual("900", plan["right"]["inPointTicks"])
        self.assertTrue(plan["durationPreserved"] and plan["sourceContinuous"])

    def test_split_quantizes_to_sequence_frame(self):
        plan = split_plan(item(end=1200, source_out=1200), 455, FRAME)
        self.assertEqual("500", plan["splitTicks"])
        self.assertEqual("500", plan["left"]["endTicks"])

    def test_linked_av_split_planning_signal(self):
        clip = item()
        result = linked_candidates(state(video=[[clip]], audio=[[item()]]), clip, "video")
        self.assertEqual("inferred_high", result["confidence"])
        self.assertEqual(1, len(result["items"]))
        ambiguous = linked_candidates(state(video=[[clip]], audio=[[item(), item(index=1)]]), clip, "video")
        self.assertEqual("inferred_low", ambiguous["confidence"])

    def test_trim_start_and_end(self):
        start = trim_plan(item(source_in=200, source_out=1200), "trim_start_by", 200, FRAME)
        self.assertEqual({"startTicks": "200", "endTicks": "1000", "inPointTicks": "400", "outPointTicks": "1200"}, start["after"])
        end = trim_plan(item(source_in=200, source_out=1200), "trim_end_by", -200, FRAME)
        self.assertEqual({"startTicks": "0", "endTicks": "800", "inPointTicks": "200", "outPointTicks": "1000"}, end["after"])

    def test_trim_quantizes_to_sequence_frame(self):
        start = trim_plan(item(), "trim_start_to", 155, FRAME)
        self.assertEqual("200", start["after"]["startTicks"])
        self.assertEqual("200", start["after"]["inPointTicks"])
        source_range = trim_plan(item(), "set_source_range", (149, 851), FRAME)
        self.assertEqual("100", source_range["after"]["inPointTicks"])
        self.assertEqual("900", source_range["after"]["outPointTicks"])

    def test_source_bounds_and_zero_duration_rejected(self):
        self.assert_code("INVALID_TRIM_RESULT", lambda: trim_plan(item(), "set_source_in", 1000, FRAME))
        self.assert_code("INVALID_TRIM_RESULT", lambda: trim_plan(item(), "trim_end_to", 0, FRAME))
        self.assert_code("RIPPLE_SOURCE_RANGE_INVALID", lambda: trim_plan(item(), "trim_end_by", 500, FRAME, media_duration_ticks=1200))

    def test_ripple_downstream_selection(self):
        plan = ripple_plan(state(video=[[item(), item(1, 1500, 2500)]], audio=[[]]), selector("video", 0, 0), "end", 800, FRAME)
        self.assertEqual("-200", plan["deltaTicks"])
        self.assertEqual([(1, "-200")], [(row["itemIndex"], row["shiftTicks"]) for row in plan["downstream"]])

    def test_ripple_crossing_audio_blocked(self):
        timeline = state(video=[[item(), item(1, 1500, 2500)]], audio=[[item(0, 900, 1800, project="audio")]])
        self.assert_code("RIPPLE_BOUNDARY_OCCUPIED", lambda: ripple_plan(timeline, selector("video", 0, 0), "end", 800, FRAME))

    def test_ripple_destination_collision(self):
        self.assert_code("RIPPLE_DESTINATION_COLLISION", lambda: ensure_no_destination_collisions(
            [{"mediaType": "video", "trackIndex": 0, "startTicks": "800", "endTicks": "1300"}],
            [{"mediaType": "video", "trackIndex": 0, "startTicks": "1200", "endTicks": "1600"}],
        ))

    def test_bool_as_number_and_invalid_speed(self):
        self.assert_code("INVALID_ARGUMENT", lambda: exact_tick_value(seconds=True, name="split"))
        for value in (True, 0, -1, float("nan"), float("inf")):
            self.assertRaises(EditPlanError, speed_plan, item(), FRAME, speed_percent=value)

    def test_speed_duration_conversion_and_tolerance(self):
        plan = speed_plan(item(end=1000), FRAME, speed_percent=125)
        self.assertEqual("800", plan["expectedDurationTicks"])
        duration = speed_plan(item(end=1000), FRAME, target_duration_seconds=200 / 254_016_000_000)
        self.assertGreater(float(duration["requestedSpeedPercent"]), 0)
        self.assert_code("INVALID_TARGET_DURATION", lambda: speed_plan(item(end=1000), FRAME, target_duration_seconds=1 / 254_016_000_000))

    def test_still_image_reverse_and_frame_hold_limits(self):
        self.assert_code("INVALID_SPEED", lambda: speed_plan(item(), FRAME, speed_percent=100, still_image=True))
        self.assertNotIn("reverse", speed_plan(item(), FRAME, speed_percent=100))

    def test_json_serialization_and_journal_redaction(self):
        plan = ripple_plan(state(video=[[item(), item(1, 1500, 2500)]], audio=[[]]), selector("video", 0, 0), "end", 900, FRAME)
        encoded = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn("sourceText", encoded)
        self.assertNotIn("captionText", encoded)

    def test_selector_rejects_invalid_index(self):
        self.assert_code("INVALID_ARGUMENT", lambda: selector("video", True, 0))
        self.assert_code("INVALID_ARGUMENT", lambda: selector("audio", 0, -1))

    def test_selector_detects_stale_project_identity(self):
        timeline = state(video=[[item(project="actual")]])
        target = selector("video", 0, 0, project_item_id="expected")
        self.assert_code("STALE_TRACK_ITEM_STATE", lambda: find_item(timeline, target))

    def test_selector_detects_stale_timeline_bounds(self):
        timeline = state(video=[[item()]])
        target = selector("video", 0, 0, start_ticks=1, end_ticks=1000)
        self.assert_code("STALE_TRACK_ITEM_STATE", lambda: find_item(timeline, target))

    def test_exact_tick_value_requires_one_representation(self):
        self.assert_code("INVALID_ARGUMENT", lambda: exact_tick_value(name="value"))
        self.assert_code("INVALID_ARGUMENT", lambda: exact_tick_value(seconds=1, ticks="1", name="value"))

    def test_source_range_plan_and_inverse(self):
        plan = trim_plan(item(source_in=100, source_out=1000), "set_source_range", (200, 900), FRAME)
        self.assertEqual({"startTicks": "100", "endTicks": "900", "inPointTicks": "200", "outPointTicks": "900"}, plan["changed"])
        self.assertEqual({"startTicks": "0", "endTicks": "1000", "inPointTicks": "100", "outPointTicks": "1000"}, plan["inverse"])

    def test_source_bounds_unreadable_fails_closed(self):
        broken = item()
        broken["outPointTicks"] = None
        self.assert_code("SOURCE_BOUNDS_UNVERIFIED", lambda: trim_plan(broken, "trim_end_by", -100, FRAME))

    def test_negative_timeline_trim_rejected(self):
        self.assert_code("INVALID_TRIM_RESULT", lambda: trim_plan(item(), "trim_start_by", -200, FRAME))

    def test_ripple_expand_moves_downstream_right(self):
        timeline = state(video=[[item(), item(1, 1500, 2500)]], audio=[[]])
        plan = ripple_plan(timeline, selector("video", 0, 0), "end", 1200, FRAME, media_duration_ticks=2000)
        self.assertEqual("200", plan["deltaTicks"])
        self.assertEqual("200", plan["downstream"][0]["shiftTicks"])

    def test_ripple_video_crossing_blocked(self):
        timeline = state(video=[[item(), item(1, 900, 1500)]], audio=[[]])
        self.assert_code("RIPPLE_CROSSING_ITEM", lambda: ripple_plan(timeline, selector("video", 0, 0), "end", 800, FRAME))

    def test_ripple_partial_track_selection(self):
        timeline = state(video=[[item(), item(1, 1500, 2500)], [item(0, 1500, 2500, project="v2")]], audio=[[]])
        plan = ripple_plan(timeline, selector("video", 0, 0), "end", 900, FRAME, video_tracks=[0])
        self.assertEqual(1, len(plan["downstream"]))
        self.assertEqual(0, plan["downstream"][0]["trackIndex"])

    def test_speed_requires_exactly_one_input(self):
        self.assert_code("INVALID_SPEED", lambda: speed_plan(item(), FRAME))
        self.assert_code("INVALID_SPEED", lambda: speed_plan(item(), FRAME, speed_percent=100, target_duration_seconds=1))

    def test_linked_unknown_is_not_promoted(self):
        result = linked_candidates(state(video=[[item()]], audio=[[item(project="other")]]), item(), "video")
        self.assertEqual("unknown", result["confidence"])
        self.assertEqual([], result["items"])


if __name__ == "__main__":
    unittest.main()
