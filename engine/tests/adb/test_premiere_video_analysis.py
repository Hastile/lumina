from __future__ import annotations

import unittest

from lumina.adb.premiere_video_analysis import (
    VideoAnalysisError,
    build_video_analysis_command,
    classify_scene_analysis_failure,
    parse_blackdetect_output,
    parse_freezedetect_output,
    parse_scene_metadata_output,
    summarize_video_media_info,
)

# Representative excerpts of real ffmpeg filter stderr output - not
# live-captured in this file, but shaped exactly like the output this pack's
# live-verification pass captured from a real ffmpeg N-125628 run (a
# synthetic red/blue/black/green concat clip; see
# docs/premiere-capability-matrix.md for the live-verification summary).
SCENE_SAMPLE = """
[Parsed_metadata_1 @ 0000] frame:0    pts:25600   pts_time:2
[Parsed_metadata_1 @ 0000] lavfi.scene_score=0.400000
[Parsed_metadata_1 @ 0000] frame:1    pts:51200   pts_time:4
[Parsed_metadata_1 @ 0000] lavfi.scene_score=0.250000
[Parsed_metadata_1 @ 0000] frame:2    pts:64000   pts_time:5
[Parsed_metadata_1 @ 0000] lavfi.scene_score=0.650000
"""

SCENE_TRUNCATED_SAMPLE = """
[Parsed_metadata_1 @ 0000] frame:0    pts:25600   pts_time:2
"""

BLACK_SAMPLE = "[Parsed_blackdetect_0 @ 0000] black_start:4 black_end:5 black_duration:1\n"

BLACK_OPEN_SAMPLE = (
    "[Parsed_blackdetect_0 @ 0000] black_start:0 black_end:1.52 black_duration:1.52\n"
    "[Parsed_blackdetect_0 @ 0000] black_start:3.52\n"
)

FREEZE_SAMPLE = (
    "[Parsed_freezedetect_0 @ 0000] lavfi.freezedetect.freeze_start: 0\n"
    "[Parsed_freezedetect_0 @ 0000] lavfi.freezedetect.freeze_duration: 2\n"
    "[Parsed_freezedetect_0 @ 0000] lavfi.freezedetect.freeze_end: 2\n"
)

FREEZE_OPEN_SAMPLE = "[Parsed_freezedetect_0 @ 0000] lavfi.freezedetect.freeze_start: 5\n"

PROBE_JSON_SAMPLE = {
    "streams": [
        {
            "codec_type": "video", "codec_name": "h264", "width": 320, "height": 240,
            "r_frame_rate": "25/1", "avg_frame_rate": "25/1", "time_base": "1/12800",
            "pix_fmt": "yuv420p", "tags": {"rotate": "90"},
        },
        {
            "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
            "channels": 2, "channel_layout": "stereo",
        },
    ],
    "format": {"duration": "7.000000"},
}


class SceneParsingTests(unittest.TestCase):
    def test_parses_three_events_in_order(self):
        events = parse_scene_metadata_output(SCENE_SAMPLE)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0], {"frame": 0, "ptsTicks": 25600, "ptsSeconds": 2.0, "sceneScore": 0.4})
        self.assertEqual(events[2]["ptsSeconds"], 5.0)
        self.assertEqual(events[2]["sceneScore"], 0.65)

    def test_first_frame_of_source_is_never_misclassified_as_scene_cut(self):
        # ffmpeg never emits a scene_score for frame 0 of the whole source
        # (no previous frame to diff against); the first real event is at
        # pts_time 2, not 0.
        events = parse_scene_metadata_output(SCENE_SAMPLE)
        self.assertGreater(events[0]["ptsSeconds"], 0.0)

    def test_frame_line_without_trailing_score_is_dropped(self):
        events = parse_scene_metadata_output(SCENE_TRUNCATED_SAMPLE)
        self.assertEqual(events, [])

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(parse_scene_metadata_output(""), [])


class BlackDetectParsingTests(unittest.TestCase):
    def test_parses_closed_interval(self):
        intervals = parse_blackdetect_output(BLACK_SAMPLE)
        self.assertEqual(intervals, [{"startSeconds": 4.0, "endSeconds": 5.0, "durationSeconds": 1.0}])

    def test_open_ended_trailing_interval_has_none_end(self):
        intervals = parse_blackdetect_output(BLACK_OPEN_SAMPLE)
        self.assertEqual(len(intervals), 2)
        self.assertEqual(intervals[0], {"startSeconds": 0.0, "endSeconds": 1.52, "durationSeconds": 1.52})
        self.assertEqual(intervals[1], {"startSeconds": 3.52, "endSeconds": None, "durationSeconds": None})

    def test_no_black_intervals_returns_empty_list(self):
        self.assertEqual(parse_blackdetect_output("no black here"), [])


class FreezeDetectParsingTests(unittest.TestCase):
    def test_parses_closed_interval(self):
        intervals = parse_freezedetect_output(FREEZE_SAMPLE)
        self.assertEqual(intervals, [{"startSeconds": 0.0, "endSeconds": 2.0, "durationSeconds": 2.0}])

    def test_open_ended_trailing_interval_has_none_end(self):
        intervals = parse_freezedetect_output(FREEZE_OPEN_SAMPLE)
        self.assertEqual(intervals, [{"startSeconds": 5.0, "endSeconds": None, "durationSeconds": None}])


class SceneAnalysisFailureClassificationTests(unittest.TestCase):
    def test_clean_run_with_events_is_not_a_failure(self):
        self.assertIsNone(classify_scene_analysis_failure(0, SCENE_SAMPLE, 3))

    def test_clean_run_with_zero_events_is_not_a_failure(self):
        # The legitimate case this classifier exists to protect: a video with
        # no scene changes at all must not be reported as a failure.
        self.assertIsNone(classify_scene_analysis_failure(0, "", 0))

    def test_nonzero_return_with_parsed_events_is_ffmpegs_known_quirk(self):
        # ffmpeg documented behavior: -f null can exit non-zero on some
        # inputs while having already printed the full filter summary.
        self.assertIsNone(classify_scene_analysis_failure(1, SCENE_SAMPLE, 3))

    def test_decode_failure_is_classified(self):
        stderr = "Invalid data found when processing input\n"
        result = classify_scene_analysis_failure(1, stderr, 0)
        self.assertEqual(result["code"], "SCENE_ANALYSIS_DECODE_FAILED")

    def test_empty_string_media_path_invalid_argument_is_classified_as_decode_failure(self):
        # Matches the exact stderr text confirmed live for a TrackItem whose
        # mediaPath was an empty string (nested-sequence source): ffprobe/
        # ffmpeg fail with ": Invalid argument". Upstream callers now reject
        # this before ever reaching ffmpeg (looks_like_filesystem_path), but
        # the classifier itself must still fail closed if it ever sees it.
        stderr = ": Invalid argument\n{\n\n}\n"
        result = classify_scene_analysis_failure(1, stderr, 0)
        self.assertEqual(result["code"], "SCENE_ANALYSIS_DECODE_FAILED")

    def test_filter_failure_is_classified(self):
        stderr = "No such filter: 'select'\n"
        result = classify_scene_analysis_failure(1, stderr, 0)
        self.assertEqual(result["code"], "SCENE_ANALYSIS_FILTER_FAILED")

    def test_empty_stderr_with_nonzero_return_is_parse_failure(self):
        result = classify_scene_analysis_failure(1, "", 0)
        self.assertEqual(result["code"], "SCENE_ANALYSIS_PARSE_FAILED")

    def test_unrecognized_stderr_nonzero_return_and_zero_events_fails_closed(self):
        result = classify_scene_analysis_failure(1, "some unrelated ffmpeg banner text\n", 0)
        self.assertEqual(result["code"], "SCENE_ANALYSIS_PARSE_FAILED")


class MediaInfoSummaryTests(unittest.TestCase):
    def test_extracts_video_audio_and_rotation(self):
        info = summarize_video_media_info(PROBE_JSON_SAMPLE)
        self.assertEqual(info["durationSeconds"], 7.0)
        self.assertEqual(info["videoStreamCount"], 1)
        self.assertEqual(info["video"]["codecName"], "h264")
        self.assertEqual(info["video"]["width"], 320)
        self.assertEqual(info["video"]["frameRate"], 25.0)
        self.assertEqual(info["video"]["rotationDegrees"], 90.0)
        self.assertEqual(info["audio"]["sampleRate"], 48000)
        self.assertEqual(info["audio"]["channels"], 2)

    def test_no_video_stream_returns_none(self):
        info = summarize_video_media_info({"streams": [], "format": {}})
        self.assertIsNone(info["video"])
        self.assertIsNone(info["audio"])
        self.assertIsNone(info["durationSeconds"])

    def test_display_matrix_side_data_rotation(self):
        probe = {
            "streams": [{
                "codec_type": "video", "codec_name": "hevc", "width": 100, "height": 100,
                "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "time_base": "1/30",
                "pix_fmt": "yuv420p", "tags": {},
                "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
            }],
            "format": {"duration": "1.0"},
        }
        info = summarize_video_media_info(probe)
        self.assertEqual(info["video"]["rotationDegrees"], -90.0)


class BuildVideoAnalysisCommandTests(unittest.TestCase):
    def test_scene_command_uses_argv_list_never_shell_string(self):
        command = build_video_analysis_command(
            "ffmpeg", "C:/media/한글 경로.mp4", "scene",
            start_seconds=None, duration_seconds=None, scene_threshold=0.35,
            black_min_duration_seconds=0.25, black_pixel_threshold=None, black_picture_ratio=None,
        )
        self.assertIsInstance(command, list)
        self.assertIn("C:/media/한글 경로.mp4", command)
        self.assertTrue(any("select=" in part for part in command))

    def test_black_command_includes_optional_thresholds(self):
        command = build_video_analysis_command(
            "ffmpeg", "in.mp4", "black",
            start_seconds=1.0, duration_seconds=5.0, scene_threshold=0.35,
            black_min_duration_seconds=0.25, black_pixel_threshold=0.1, black_picture_ratio=0.98,
        )
        filter_arg = command[command.index("-vf") + 1]
        self.assertIn("d=0.25", filter_arg)
        self.assertIn("pix_th=0.1", filter_arg)
        self.assertIn("pic_th=0.98", filter_arg)
        self.assertIn("-ss", command)
        self.assertIn("-t", command)

    def test_unknown_analysis_raises(self):
        with self.assertRaises(VideoAnalysisError):
            build_video_analysis_command(
                "ffmpeg", "in.mp4", "bogus",
                start_seconds=None, duration_seconds=None, scene_threshold=0.35,
                black_min_duration_seconds=0.25, black_pixel_threshold=None, black_picture_ratio=None,
            )


if __name__ == "__main__":
    unittest.main()
