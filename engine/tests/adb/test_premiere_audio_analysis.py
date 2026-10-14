from __future__ import annotations

import unittest

from lumina.adb.premiere_audio_analysis import (
    AudioAnalysisError,
    build_analysis_command,
    parse_astats_output,
    parse_ebur128_output,
    parse_silencedetect_output,
    sanitize_for_json,
)

# Representative excerpts of real ffmpeg filter stderr output (trimmed to the
# lines the parsers actually read) - not live-captured, but shaped exactly
# like ffmpeg N-125628's astats/ebur128/silencedetect text, and mcp/test
# coverage was additionally cross-checked against a real live ffmpeg run in
# this pack's live-verification pass (see docs/premiere-capability-matrix.md).
ASTATS_SAMPLE = """
[Parsed_astats_0 @ 0000] Channel: 1
[Parsed_astats_0 @ 0000]   DC offset: 0.000001
[Parsed_astats_0 @ 0000]   Peak level dB: -3.010300
[Parsed_astats_0 @ 0000]   RMS level dB: -18.062318
[Parsed_astats_0 @ 0000] Overall
[Parsed_astats_0 @ 0000]   DC offset: 0.000001
[Parsed_astats_0 @ 0000]   Peak level dB: -3.010300
[Parsed_astats_0 @ 0000]   RMS level dB: -18.062318
"""

ASTATS_SILENT_SAMPLE = """
[Parsed_astats_0 @ 0000] Overall
[Parsed_astats_0 @ 0000]   Peak level dB: -inf
[Parsed_astats_0 @ 0000]   RMS level dB: -inf
"""

EBUR128_SAMPLE = """
[Parsed_ebur128_0 @ 0000] t: 9.000000 TARGET:-23 LUFS    M: -20.1 S: -19.8     I: -22.5 LUFS       LRA:   4.2 LU
Integrated loudness:
  I:         -22.5 LUFS
  Threshold: -35.2 LUFS

Loudness range:
  LRA:         4.2 LU
  Threshold: -45.0 LUFS
  LRA low:    -25.9 LUFS
  LRA high:   -21.7 LUFS

True peak:
  Peak:        -1.3 dBFS
"""

SILENCEDETECT_SAMPLE = """
[silencedetect @ 0000] silence_start: 1.234
[silencedetect @ 0000] silence_end: 2.345 | silence_duration: 1.111
[silencedetect @ 0000] silence_start: 8.5
"""


class AstatsParsingTests(unittest.TestCase):
    def test_parses_overall_peak_and_rms(self):
        result = parse_astats_output(ASTATS_SAMPLE)
        self.assertAlmostEqual(-3.0103, result["peakDbFS"], places=3)
        self.assertAlmostEqual(-18.062318, result["rms"], places=3)

    def test_true_silence_becomes_none_not_negative_infinity(self):
        result = parse_astats_output(ASTATS_SILENT_SAMPLE)
        self.assertIsNone(result["peakDbFS"])
        self.assertIsNone(result["rms"])

    def test_empty_output_fails_closed(self):
        with self.assertRaises(AudioAnalysisError) as caught:
            parse_astats_output("no relevant lines here")
        self.assertEqual("AUDIO_ANALYSIS_PARSE_FAILED", caught.exception.code)


class Ebur128ParsingTests(unittest.TestCase):
    def test_parses_integrated_lra_and_true_peak(self):
        result = parse_ebur128_output(EBUR128_SAMPLE)
        self.assertAlmostEqual(-22.5, result["integratedLufs"], places=2)
        self.assertAlmostEqual(4.2, result["loudnessRange"], places=2)
        self.assertAlmostEqual(-1.3, result["truePeakDbTP"], places=2)

    def test_missing_summary_fails_closed(self):
        with self.assertRaises(AudioAnalysisError) as caught:
            parse_ebur128_output("t: 1.0 M: -20 S: -19 I: -22 LUFS LRA: 4 LU")
        self.assertEqual("AUDIO_ANALYSIS_PARSE_FAILED", caught.exception.code)


class SilencedetectParsingTests(unittest.TestCase):
    def test_parses_closed_and_trailing_open_intervals(self):
        intervals = parse_silencedetect_output(SILENCEDETECT_SAMPLE)
        self.assertEqual(2, len(intervals))
        self.assertEqual({"startSeconds": 1.234, "endSeconds": 2.345, "durationSeconds": 1.111}, intervals[0])
        self.assertEqual(8.5, intervals[1]["startSeconds"])
        self.assertIsNone(intervals[1]["endSeconds"])

    def test_no_silence_returns_empty_list(self):
        self.assertEqual([], parse_silencedetect_output("nothing silent here"))


class BuildCommandTests(unittest.TestCase):
    def test_never_uses_shell_string(self):
        command = build_analysis_command(
            "ffmpeg", "D:\\path with spaces\\한글.wav", "peak_rms",
            start_seconds=1.5, duration_seconds=3.0,
            silence_threshold_db=-40.0, silence_min_duration_seconds=0.5,
        )
        self.assertIsInstance(command, list)
        self.assertIn("D:\\path with spaces\\한글.wav", command)
        self.assertNotIn(" ", "".join(c for c in command if "path" not in c))

    def test_silence_filter_includes_threshold_and_duration(self):
        command = build_analysis_command(
            "ffmpeg", "file.wav", "silence",
            start_seconds=None, duration_seconds=None,
            silence_threshold_db=-30.0, silence_min_duration_seconds=0.25,
        )
        joined = " ".join(command)
        self.assertIn("silencedetect=noise=-30.0dB:d=0.25", joined)

    def test_unknown_analysis_rejected(self):
        with self.assertRaises(AudioAnalysisError):
            build_analysis_command("ffmpeg", "file.wav", "nonsense", start_seconds=None, duration_seconds=None,
                                    silence_threshold_db=-40.0, silence_min_duration_seconds=0.5)


class SanitizeForJsonTests(unittest.TestCase):
    def test_replaces_nonfinite_floats_with_none(self):
        value, replaced = sanitize_for_json({"a": float("inf"), "b": [1.0, float("nan")], "c": {"d": float("-inf")}})
        self.assertTrue(replaced)
        self.assertEqual({"a": None, "b": [1.0, None], "c": {"d": None}}, value)

    def test_finite_values_pass_through_unchanged(self):
        value, replaced = sanitize_for_json({"a": 1.5, "b": [1, 2, 3], "c": "text"})
        self.assertFalse(replaced)
        self.assertEqual({"a": 1.5, "b": [1, 2, 3], "c": "text"}, value)


if __name__ == "__main__":
    unittest.main()
