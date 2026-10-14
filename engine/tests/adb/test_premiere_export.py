import sys
import unittest
from pathlib import Path


from lumina.adb.premiere_export import (
    ExportError,
    build_track_item_snapshot,
    checkpoint_verification,
    classify_preset_source,
    is_size_history_stable,
    predict_split_destination_collision,
    preset_entry,
    simulate_plan_split_collisions,
    summarize_probe,
    validate_media_probe,
)


class ClassifyPresetSourceTests(unittest.TestCase):
    def test_system_ame_path(self):
        self.assertEqual(
            classify_preset_source(r"C:\Program Files\Adobe\Adobe Media Encoder 2026\MediaIO\systempresets\x\H264.epr"),
            "system",
        )

    def test_system_premiere_program_files_path(self):
        self.assertEqual(
            classify_preset_source(r"C:\Program Files\Adobe\Adobe Premiere Pro 2026\CEP\extensions\x\y.epr"),
            "system",
        )

    def test_user_appdata_path(self):
        self.assertEqual(
            classify_preset_source(r"C:\Users\HETX\AppData\Roaming\Adobe\Premiere Pro\26.0\Placeholder Preset.epr"),
            "user",
        )

    def test_other_path(self):
        self.assertEqual(classify_preset_source(r"D:\some\repo\presets\custom.epr"), "other")


class PresetEntryTests(unittest.TestCase):
    def test_fields(self):
        entry = preset_entry(Path(r"C:\Program Files\Adobe\Adobe Media Encoder 2026\MediaIO\systempresets\x\H264 Match Source.epr"), exists=True, size=130681, modified=1770400000.0)
        self.assertEqual(entry["name"], "H264 Match Source")
        self.assertTrue(entry["fileExists"])
        self.assertEqual(entry["fileSizeBytes"], 130681)
        self.assertEqual(entry["sourceCategory"], "system")


class SizeHistoryStabilityTests(unittest.TestCase):
    def test_too_few_polls_not_stable(self):
        self.assertFalse(is_size_history_stable([100], required_stable_polls=2))

    def test_growing_size_not_stable(self):
        self.assertFalse(is_size_history_stable([100, 200, 300], required_stable_polls=2))

    def test_two_identical_polls_stable(self):
        self.assertTrue(is_size_history_stable([100, 500, 500], required_stable_polls=2))

    def test_zero_size_never_stable(self):
        self.assertFalse(is_size_history_stable([0, 0, 0], required_stable_polls=2))

    def test_invalid_required_polls_raises(self):
        with self.assertRaises(ExportError):
            is_size_history_stable([1, 1], required_stable_polls=0)


class CheckpointVerificationTests(unittest.TestCase):
    def test_missing_file_not_verified(self):
        result = checkpoint_verification(exists=False, size_bytes=None, modified_at=None)
        self.assertFalse(result["verified"])

    def test_empty_file_not_verified(self):
        result = checkpoint_verification(exists=True, size_bytes=0, modified_at=100.0)
        self.assertFalse(result["verified"])

    def test_unchanged_size_and_mtime_not_verified(self):
        result = checkpoint_verification(exists=True, size_bytes=500, modified_at=100.0, before_size_bytes=500, before_modified_at=100.0)
        self.assertFalse(result["verified"])

    def test_changed_mtime_verified(self):
        result = checkpoint_verification(exists=True, size_bytes=500, modified_at=200.0, before_size_bytes=500, before_modified_at=100.0)
        self.assertTrue(result["verified"])

    def test_no_before_state_and_existing_nonempty_file_verified(self):
        result = checkpoint_verification(exists=True, size_bytes=500, modified_at=100.0)
        self.assertTrue(result["verified"])


def _probe(*, duration="10.000000", size="1234567", video=None, audio=None):
    streams = []
    if video is not None:
        streams.append({"codec_type": "video", **video})
    if audio is not None:
        streams.append({"codec_type": "audio", **audio})
    return {"format": {"duration": duration, "size": size, "format_name": "mov,mp4,m4a,3gp,3g2,mj2", "bit_rate": "5000000"}, "streams": streams}


class SummarizeProbeTests(unittest.TestCase):
    def test_extracts_video_and_audio(self):
        probe = _probe(
            video={"codec_name": "h264", "width": 320, "height": 240, "pix_fmt": "yuv420p", "avg_frame_rate": "25/1", "r_frame_rate": "25/1"},
            audio={"codec_name": "aac", "sample_rate": "48000", "channels": 2, "channel_layout": "stereo"},
        )
        summary = summarize_probe(probe)
        self.assertEqual(summary["durationSeconds"], 10.0)
        self.assertEqual(summary["videoStreamCount"], 1)
        self.assertEqual(summary["audioStreamCount"], 1)
        self.assertEqual(summary["video"]["width"], 320)
        self.assertEqual(summary["video"]["avgFrameRate"], 25.0)
        self.assertEqual(summary["audio"]["sampleRate"], 48000)

    def test_missing_streams(self):
        summary = summarize_probe(_probe())
        self.assertIsNone(summary["video"])
        self.assertIsNone(summary["audio"])
        self.assertEqual(summary["videoStreamCount"], 0)

    def test_fractional_frame_rate_parsing(self):
        summary = summarize_probe(_probe(video={"codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001"}))
        self.assertAlmostEqual(summary["video"]["avgFrameRate"], 29.97, places=2)


class ValidateMediaProbeTests(unittest.TestCase):
    def test_no_expectations_no_errors(self):
        result = validate_media_probe(_probe(), None)
        self.assertEqual(result["errors"], [])

    def test_duration_within_tolerance_passes(self):
        probe = _probe(duration="10.020000", video={"codec_name": "h264", "width": 320, "height": 240, "pix_fmt": "yuv420p", "avg_frame_rate": "25/1", "r_frame_rate": "25/1"})
        result = validate_media_probe(probe, {"duration_seconds": 10.0, "duration_tolerance_frames": 1, "frame_rate": 25.0})
        self.assertEqual(result["errors"], [])

    def test_duration_outside_tolerance_fails(self):
        probe = _probe(duration="10.500000")
        result = validate_media_probe(probe, {"duration_seconds": 10.0, "duration_tolerance_frames": 1, "frame_rate": 25.0})
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("EXPORT_DURATION_MISMATCH", codes)

    def test_duration_29_97_tolerance(self):
        # 1 frame at 29.97fps ~= 0.0334s; 0.03s delta must pass, larger must fail.
        probe = _probe(duration="10.030000")
        result = validate_media_probe(probe, {"duration_seconds": 10.0, "duration_tolerance_frames": 1, "frame_rate": 30000 / 1001})
        self.assertEqual(result["errors"], [])
        probe_fail = _probe(duration="10.200000")
        result_fail = validate_media_probe(probe_fail, {"duration_seconds": 10.0, "duration_tolerance_frames": 1, "frame_rate": 30000 / 1001})
        codes = [e["code"] for e in result_fail["errors"]]
        self.assertIn("EXPORT_DURATION_MISMATCH", codes)

    def test_video_stream_missing(self):
        result = validate_media_probe(_probe(), {"video_stream_required": True})
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("EXPORT_VIDEO_STREAM_MISSING", codes)

    def test_audio_stream_missing(self):
        result = validate_media_probe(_probe(), {"audio_stream_required": True})
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("EXPORT_AUDIO_STREAM_MISSING", codes)

    def test_dimension_mismatch(self):
        probe = _probe(video={"codec_name": "h264", "width": 320, "height": 240, "pix_fmt": "yuv420p", "avg_frame_rate": "25/1", "r_frame_rate": "25/1"})
        result = validate_media_probe(probe, {"video_stream_required": True, "width": 1920, "height": 1080})
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("EXPORT_DIMENSION_MISMATCH", codes)

    def test_sample_rate_mismatch(self):
        probe = _probe(audio={"codec_name": "aac", "sample_rate": "44100", "channels": 2, "channel_layout": "stereo"})
        result = validate_media_probe(probe, {"audio_stream_required": True, "audio_sample_rate": 48000})
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("EXPORT_SAMPLE_RATE_MISMATCH", codes)

    def test_minimum_file_size(self):
        result = validate_media_probe(_probe(), {"minimum_file_size_bytes": 1000}, file_size_bytes=10)
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("EXPORT_OUTPUT_EMPTY", codes)

    def test_unspecified_fields_never_checked(self):
        # No width/height/codec expectations given at all - must not invent errors.
        probe = _probe(video={"codec_name": "prores", "width": 7, "height": 9, "pix_fmt": "yuv422p10le", "avg_frame_rate": "24/1", "r_frame_rate": "24/1"})
        result = validate_media_probe(probe, {"duration_seconds": 10.0})
        self.assertEqual(result["errors"], [])


class SplitDestinationCollisionPreflightTests(unittest.TestCase):
    def test_reproduces_live_confirmed_collision(self):
        # Matches the real ADB_MCP_EXEC_TEST_2 scenario from Pack 2: item0
        # =[0, 5.4s], split at 1.92s, item1 (post-ripple)=[5.4s, 8.48s-1.24s].
        collision = predict_split_destination_collision(
            item_start_ticks=0, item_end_ticks=1_371_686_400_000, split_ticks=487_710_720_000,
            other_item_intervals=[(1_371_686_400_000, 2_154_055_680_000 - 314_979_840_000)],
        )
        self.assertTrue(collision)

    def test_no_other_items_no_collision(self):
        collision = predict_split_destination_collision(
            item_start_ticks=0, item_end_ticks=1_371_686_400_000, split_ticks=487_710_720_000,
            other_item_intervals=[],
        )
        self.assertFalse(collision)

    def test_far_away_item_no_collision(self):
        collision = predict_split_destination_collision(
            item_start_ticks=0, item_end_ticks=1_000, split_ticks=500,
            other_item_intervals=[(1_000_000, 2_000_000)],
        )
        self.assertFalse(collision)

    def test_split_point_out_of_bounds_raises(self):
        with self.assertRaises(ExportError):
            predict_split_destination_collision(item_start_ticks=0, item_end_ticks=100, split_ticks=200, other_item_intervals=[])


def _video_track_state(items):
    return {"videoTracks": [{"trackIndex": 1, "items": items}], "audioTracks": []}


def _op(operation_id, candidate_id, op_type, *, media_type="video", track_index=1, parameters=None):
    return {
        "operationId": operation_id, "candidateId": candidate_id, "type": op_type,
        "targetSelector": {"mediaType": media_type, "trackIndex": track_index},
        "parameters": parameters or {},
    }


class BuildTrackItemSnapshotTests(unittest.TestCase):
    def test_extracts_sorted_items_and_skips_unreadable(self):
        state = _video_track_state([
            {"projectItemId": "p1", "startTicks": "1000", "endTicks": "2000"},
            {"projectItemId": "p0", "startTicks": 0, "endTicks": 1000},
            {"projectItemId": "p2", "startTicks": None, "endTicks": None},
        ])
        snapshot = build_track_item_snapshot(state)
        items = snapshot[("video", 1)]
        self.assertEqual(len(items), 2)
        self.assertEqual([i["projectItemId"] for i in items], ["p0", "p1"])


class SimulatePlanSplitCollisionsTests(unittest.TestCase):
    def test_no_collision_when_gap_present(self):
        snapshot = {("video", 1): [
            {"projectItemId": "p0", "startTicks": 0, "endTicks": 1000},
            {"projectItemId": "p1", "startTicks": 3000, "endTicks": 4000},
        ]}
        ops = [_op("op1", "candA", "split_track_item", parameters={"atSequenceTicks": 500})]
        result = simulate_plan_split_collisions(ops, snapshot)
        self.assertEqual(result["collisions"], [])

    def test_collision_predicted_after_prior_candidate_remove_closes_gap(self):
        # Real-world shape (Batch Editing Orchestrator Pack notes): item0=[0,1000),
        # gap, item1=[3000,4000). candB's remove (processed first - it is the
        # later-in-time, i.e. first-processed, candidate per latest-time-first
        # ordering) closes the gap by removing [1000,3000), shifting item1 to
        # [1000,2000). candA's split of item0 at 500 then needs destination
        # [500,1500) - which now collides with item1's shifted [1000,2000).
        snapshot = {("video", 1): [
            {"projectItemId": "p0", "startTicks": 0, "endTicks": 1000},
            {"projectItemId": "p1", "startTicks": 3000, "endTicks": 4000},
        ]}
        ops = [
            _op("op_remove", "candB", "remove_track_item", parameters={"removedInterval": {"startTicks": 1000, "endTicks": 3000}}),
            _op("op_split", "candA", "split_track_item", parameters={"atSequenceTicks": 500}),
        ]
        result = simulate_plan_split_collisions(ops, snapshot)
        self.assertEqual(len(result["collisions"]), 1)
        self.assertEqual(result["collisions"][0]["operationId"], "op_split")
        self.assertEqual(result["collisions"][0]["candidateId"], "candA")

    def test_split_point_not_found_warns_without_crashing(self):
        snapshot = {("video", 1): [{"projectItemId": "p0", "startTicks": 0, "endTicks": 1000}]}
        ops = [_op("op1", "candA", "split_track_item", parameters={"atSequenceTicks": 5000})]
        result = simulate_plan_split_collisions(ops, snapshot)
        self.assertEqual(result["collisions"], [])
        self.assertTrue(any("not found" in w for w in result["warnings"]))

    def test_trim_does_not_shift_sibling_items(self):
        snapshot = {("video", 1): [
            {"projectItemId": "p0", "startTicks": 0, "endTicks": 1000},
            {"projectItemId": "p1", "startTicks": 1000, "endTicks": 2000},
        ]}
        ops = [
            _op("op_trim", "candB", "trim_track_item", parameters={"mode": "trim_end_to", "valueTicks": 800}),
            _op("op_split", "candA", "split_track_item", parameters={"atSequenceTicks": 1500}),
        ]
        result = simulate_plan_split_collisions(ops, snapshot)
        # p1 is untouched by the non-ripple trim, so splitting it at 1500 (destination
        # [1500, 2500)) sees no other items nearby and must not collide.
        self.assertEqual(result["collisions"], [])

    def test_original_snapshot_is_not_mutated(self):
        snapshot = {("video", 1): [{"projectItemId": "p0", "startTicks": 0, "endTicks": 1000}]}
        ops = [_op("op1", "candA", "split_track_item", parameters={"atSequenceTicks": 500})]
        simulate_plan_split_collisions(ops, snapshot)
        self.assertEqual(len(snapshot[("video", 1)]), 1)


if __name__ == "__main__":
    unittest.main()
