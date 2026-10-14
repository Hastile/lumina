"""Tests for the FCP7 XML generator.

The numbers pinned here are measured Premiere 26.3 behaviour, not guesses:
the speed/reverse readbacks and the audio level multiplier were observed on
real imports (see the module docstring). If a future Premiere changes them,
these tests are what will catch it.

The audio level in particular is pinned with four measured points plus a
no-filter control, because a single point had already produced a plausible
but wrong model once.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[1]

from lumina.adb.premiere_fcp7_xml import (
    FCP7_IMPORT_STILL_SCALE_WIDTH,
    FCP7_IMPORT_UNITY_RAW,
    Fcp7XmlError,
    audio_level_expected_raw,
    audio_level_xml_value,
    build_fcp7_xml,
    compare_imported_sequence,
    expected_import_result,
    motion_center_value,
    normalize_sequence_spec,
    path_to_pathurl,
    seconds_to_frames,
    timeline_frames_for_source,
)

VIDEO = r"D:\media\clip.mp4"
AUDIO = r"D:\media\track.wav"


def spec(**overrides):
    base = {
        "name": "TEST_SEQ",
        "frameRate": 60,
        "width": 1920,
        "height": 1080,
        "clips": [{"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
                   "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5}],
    }
    base.update(overrides)
    return base


class ConversionTests(unittest.TestCase):
    def test_a_frames_round_to_nearest(self):
        self.assertEqual(300, seconds_to_frames(5, 60))
        self.assertEqual(1, seconds_to_frames(0.01, 60))
        self.assertEqual(0, seconds_to_frames(0.004, 60))

    def test_b_speed_shortens_the_timeline_span(self):
        self.assertEqual(300, timeline_frames_for_source(300, 100))
        self.assertEqual(150, timeline_frames_for_source(300, 200))
        self.assertEqual(600, timeline_frames_for_source(300, 50))

    def test_c_reverse_does_not_change_duration(self):
        self.assertEqual(
            timeline_frames_for_source(300, 100),
            timeline_frames_for_source(300, -100),
        )

    def test_d_zero_speed_is_rejected(self):
        with self.assertRaises(Fcp7XmlError):
            timeline_frames_for_source(300, 0)

    def test_e_audio_level_is_a_multiplier_on_the_import_default(self):
        # Five measured points from a real 26.3 import, including a control
        # clip with no audiolevels filter. Every one fits raw = xml * unity.
        # A single-point reading earlier looked like raw = xml**2 purely
        # because that test value happened to equal the unity constant, so
        # more than one point is pinned here on purpose.
        measured = [
            (1.0, 0.17782794),          # == the no-filter control
            (0.5, 0.08891400),
            (0.4216965, 0.07498940),
            (0.17782794, 0.03162280),
        ]
        for xml_value, raw in measured:
            self.assertAlmostEqual(raw, xml_value * FCP7_IMPORT_UNITY_RAW, places=7)

        # A level of 1.0 must reproduce the import default exactly.
        unity_db = 20 * math.log10(FCP7_IMPORT_UNITY_RAW)
        self.assertAlmostEqual(1.0, audio_level_xml_value(unity_db), places=7)

    def test_e2_requested_db_round_trips_through_the_expected_raw(self):
        for db in (-30.0, -22.5, -15.0, -6.0, 0.0):
            self.assertAlmostEqual(10 ** (db / 20.0), audio_level_expected_raw(db), places=9)

    def test_e3_squared_model_is_rejected(self):
        # Regression guard for the wrong model: /40 would give 0.4216965.
        self.assertNotAlmostEqual(0.42169650, audio_level_xml_value(-15.0), places=4)
        self.assertAlmostEqual(1.0, audio_level_xml_value(-15.0), places=6)

    def test_f_non_ascii_paths_are_percent_encoded(self):
        url = path_to_pathurl(r"D:\유튜브 채널\a b.mp4")
        self.assertTrue(url.startswith("file://localhost/D:/"))
        self.assertNotIn(" ", url)
        self.assertIn("%20", url)
        self.assertNotIn("유", url)

    def test_g_backslashes_become_forward_slashes(self):
        self.assertIn("D:/media/clip.mp4", path_to_pathurl(VIDEO))


class SpecValidationTests(unittest.TestCase):
    def test_a_minimal_spec_normalizes(self):
        out = normalize_sequence_spec(spec())
        self.assertEqual(1, out["videoTrackCount"])
        self.assertEqual(0, out["audioTrackCount"])
        self.assertEqual(300, out["durationFrames"])

    def test_b_track_count_can_exceed_what_clips_use(self):
        out = normalize_sequence_spec(spec(videoTrackCount=6, audioTrackCount=2))
        self.assertEqual(6, out["videoTrackCount"])
        self.assertEqual(2, out["audioTrackCount"])

    def test_c_track_count_below_clip_usage_fails(self):
        with self.assertRaises(Fcp7XmlError) as ctx:
            normalize_sequence_spec(spec(
                clips=[{"mediaType": "video", "trackIndex": 3, "filePath": VIDEO,
                        "sourceInSeconds": 0, "sourceOutSeconds": 1}],
                videoTrackCount=2))
        self.assertEqual("INVALID_SPEC", ctx.exception.code)

    def test_d_negative_speed_implies_reverse(self):
        out = normalize_sequence_spec(spec(clips=[{
            "mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
            "sourceInSeconds": 0, "sourceOutSeconds": 5, "speedPercent": -100}]))
        clip = out["clips"][0]
        self.assertTrue(clip["reverse"])
        self.assertEqual(100.0, clip["speedPercent"])

    def test_e_overlapping_clips_on_one_track_fail_closed(self):
        with self.assertRaises(Fcp7XmlError) as ctx:
            normalize_sequence_spec(spec(clips=[
                {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
                 "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5},
                {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
                 "startSeconds": 2, "sourceInSeconds": 0, "sourceOutSeconds": 5},
            ]))
        self.assertEqual("CLIP_OVERLAP", ctx.exception.code)

    def test_f_same_range_on_different_tracks_is_fine(self):
        out = normalize_sequence_spec(spec(clips=[
            {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5},
            {"mediaType": "video", "trackIndex": 1, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5},
        ]))
        self.assertEqual(2, out["videoTrackCount"])

    def test_g_bad_source_range_fails(self):
        for bad in ({"sourceInSeconds": 5, "sourceOutSeconds": 5},
                    {"sourceInSeconds": 5, "sourceOutSeconds": 1}):
            with self.assertRaises(Fcp7XmlError):
                normalize_sequence_spec(spec(clips=[
                    {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO, **bad}]))

    def test_h_level_db_on_a_video_clip_is_rejected(self):
        with self.assertRaises(Fcp7XmlError):
            normalize_sequence_spec(spec(clips=[
                {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
                 "sourceInSeconds": 0, "sourceOutSeconds": 5, "levelDb": -6}]))

    def test_i_unverified_frame_rate_is_rejected(self):
        with self.assertRaises(Fcp7XmlError):
            normalize_sequence_spec(spec(frameRate=17))

    def test_j_missing_required_fields_fail(self):
        for bad in ({"name": ""}, {"clips": []}, {"clips": "nope"}):
            with self.assertRaises(Fcp7XmlError):
                normalize_sequence_spec(spec(**bad))


class XmlOutputTests(unittest.TestCase):
    def test_a_output_is_well_formed_xmeml(self):
        root = ET.fromstring(build_fcp7_xml(spec()))
        self.assertEqual("xmeml", root.tag)
        self.assertEqual("4", root.get("version"))
        self.assertEqual("TEST_SEQ", root.find("./sequence/name").text)

    def test_b_empty_tracks_are_emitted(self):
        # This is the whole point of the XML route: the API cannot add tracks.
        root = ET.fromstring(build_fcp7_xml(spec(videoTrackCount=6, audioTrackCount=2)))
        self.assertEqual(6, len(root.findall("./sequence/media/video/track")))
        self.assertEqual(2, len(root.findall("./sequence/media/audio/track")))
        populated = [t for t in root.findall("./sequence/media/video/track") if t.find("clipitem") is not None]
        self.assertEqual(1, len(populated))

    def test_c_speed_and_reverse_reach_the_timeremap_filter(self):
        xml = build_fcp7_xml(spec(clips=[
            {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5, "speedPercent": 200},
            {"mediaType": "video", "trackIndex": 1, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5, "reverse": True},
        ]))
        root = ET.fromstring(xml)
        values = {}
        for clipitem in root.iter("clipitem"):
            speed = reverse = None
            for parameter in clipitem.iter("parameter"):
                if parameter.findtext("parameterid") == "speed":
                    speed = parameter.findtext("value")
                if parameter.findtext("parameterid") == "reverse":
                    reverse = parameter.findtext("value")
            values[clipitem.findtext("name")] = (speed, reverse)
        self.assertEqual(("200", "FALSE"), values["clip_0"])
        self.assertEqual(("-100", "TRUE"), values["clip_1"])

    def test_d_two_hundred_percent_halves_the_timeline_span(self):
        root = ET.fromstring(build_fcp7_xml(spec(clips=[
            {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5, "speedPercent": 200}])))
        clipitem = root.find(".//clipitem")
        self.assertEqual("0", clipitem.findtext("start"))
        self.assertEqual("150", clipitem.findtext("end"))
        self.assertEqual("300", clipitem.findtext("out"))

    def test_e_a_media_file_is_declared_once_and_then_referenced(self):
        # Declaring the same <file> twice makes Premiere import it as two
        # separate project items.
        root = ET.fromstring(build_fcp7_xml(spec(clips=[
            {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 2},
            {"mediaType": "video", "trackIndex": 1, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 2},
        ])))
        files = list(root.iter("file"))
        self.assertEqual(2, len(files))
        self.assertEqual(1, sum(1 for f in files if f.find("pathurl") is not None))
        self.assertEqual({files[0].get("id")}, {f.get("id") for f in files})

    def test_f_audio_clip_carries_a_calibrated_level(self):
        root = ET.fromstring(build_fcp7_xml(spec(clips=[
            {"mediaType": "audio", "trackIndex": 0, "filePath": AUDIO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 4, "levelDb": -15.0}])))
        value = None
        for parameter in root.iter("parameter"):
            if parameter.findtext("parameterid") == "level":
                value = float(parameter.findtext("value"))
        self.assertIsNotNone(value)
        self.assertAlmostEqual(1.0, value, places=6)

    def test_g_markers_are_emitted_at_frame_positions(self):
        root = ET.fromstring(build_fcp7_xml(spec(markers=[
            {"name": "M1", "seconds": 0}, {"name": "M2", "comment": "mid", "seconds": 2}])))
        markers = root.findall("./sequence/marker")
        self.assertEqual(2, len(markers))
        self.assertEqual(["0", "120"], [m.findtext("in") for m in markers])

    def test_h_special_characters_are_escaped(self):
        xml = build_fcp7_xml(spec(name="A & B <test>"))
        self.assertIn("A &amp; B &lt;test&gt;", xml)
        ET.fromstring(xml)  # must still parse

    def test_i_building_from_an_already_normalized_spec_is_idempotent(self):
        normalized = normalize_sequence_spec(spec())
        self.assertEqual(build_fcp7_xml(normalized), build_fcp7_xml(normalize_sequence_spec(spec())))


class VerificationTests(unittest.TestCase):
    def test_a_expected_result_uses_premieres_ratio_convention(self):
        normalized = normalize_sequence_spec(spec(clips=[
            {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5, "speedPercent": 200},
            {"mediaType": "video", "trackIndex": 1, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5, "speedPercent": -100},
        ]))
        expected = expected_import_result(normalized)
        # getSpeed returns a ratio (1.0 = normal), negative when reversed.
        self.assertEqual(2.0, expected["items"][0]["speed"])
        self.assertEqual(-1.0, expected["items"][1]["speed"])
        self.assertEqual(2.5, expected["items"][0]["endSeconds"])

    def test_b_matching_live_state_produces_no_mismatch(self):
        normalized = normalize_sequence_spec(spec(videoTrackCount=3))
        expected = expected_import_result(normalized)
        actual = {
            "frameRate": 60, "videoTrackCount": 3, "audioTrackCount": 0, "markerCount": 0,
            "items": [{"mediaType": "video", "trackIndex": 0, "startSeconds": 0.0,
                       "endSeconds": 5.0, "speed": 1.0}],
        }
        self.assertEqual([], compare_imported_sequence(expected, actual))

    def test_c_a_wrong_speed_is_reported(self):
        normalized = normalize_sequence_spec(spec(clips=[
            {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5, "speedPercent": 200}]))
        expected = expected_import_result(normalized)
        actual = {
            "frameRate": 60, "videoTrackCount": 1, "audioTrackCount": 0, "markerCount": 0,
            "items": [{"mediaType": "video", "trackIndex": 0, "startSeconds": 0.0,
                       "endSeconds": 2.5, "speed": 1.0}],
        }
        mismatches = compare_imported_sequence(expected, actual)
        self.assertEqual(1, len(mismatches))
        self.assertTrue(mismatches[0]["field"].endswith(".speed"))

    def test_d_a_missing_track_count_is_reported(self):
        normalized = normalize_sequence_spec(spec(videoTrackCount=6))
        expected = expected_import_result(normalized)
        actual = {"frameRate": 60, "videoTrackCount": 1, "audioTrackCount": 0,
                  "markerCount": 0, "items": []}
        fields = {m["field"] for m in compare_imported_sequence(expected, actual)}
        self.assertIn("videoTrackCount", fields)

    def _five_on_one_track(self):
        return normalize_sequence_spec(spec(clips=[
            {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
             "startSeconds": i * 5, "sourceInSeconds": 0, "sourceOutSeconds": 5}
            for i in range(5)
        ]))

    def test_e_many_clips_on_one_track_all_match(self):
        # Keying the live items by (mediaType, trackIndex) alone collapsed a
        # track to its last clip, so every earlier clip was diffed against
        # clip 4 and reported wrong. Programmatic assembly puts many clips on
        # one track by definition, so this is the normal case, not an edge.
        expected = expected_import_result(self._five_on_one_track())
        actual = {
            "frameRate": 60, "videoTrackCount": 1, "audioTrackCount": 0, "markerCount": 0,
            "items": [{"mediaType": "video", "trackIndex": 0,
                       "startSeconds": float(i * 5), "endSeconds": float(i * 5 + 5),
                       "speed": 1.0} for i in range(5)],
        }
        self.assertEqual([], compare_imported_sequence(expected, actual))

    def test_f_only_the_genuinely_wrong_clip_is_reported(self):
        expected = expected_import_result(self._five_on_one_track())
        items = [{"mediaType": "video", "trackIndex": 0,
                  "startSeconds": float(i * 5), "endSeconds": float(i * 5 + 5),
                  "speed": 1.0} for i in range(5)]
        items[2]["endSeconds"] = 12.0  # clip 2 landed a second short
        actual = {"frameRate": 60, "videoTrackCount": 1, "audioTrackCount": 0,
                  "markerCount": 0, "items": items}
        mismatches = compare_imported_sequence(expected, actual)
        self.assertEqual(1, len(mismatches))
        self.assertTrue(mismatches[0]["field"].endswith(".endSeconds"))
        self.assertEqual(15.0, mismatches[0]["expected"])

    def test_g_ordinals_follow_start_order_not_input_order(self):
        # The live reader walks Premiere's track items; the spec may list
        # clips in any order. Pairing has to survive that disagreement.
        normalized = normalize_sequence_spec(spec(clips=[
            {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
             "startSeconds": 10, "sourceInSeconds": 0, "sourceOutSeconds": 5},
            {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
             "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 5},
        ]))
        expected = expected_import_result(normalized)
        by_start = {item["startSeconds"]: item["ordinal"] for item in expected["items"]}
        self.assertEqual({0.0: 0, 10.0: 1}, by_start)
        actual = {
            "frameRate": 60, "videoTrackCount": 1, "audioTrackCount": 0, "markerCount": 0,
            "items": [{"mediaType": "video", "trackIndex": 0, "startSeconds": 0.0,
                       "endSeconds": 5.0, "speed": 1.0},
                      {"mediaType": "video", "trackIndex": 0, "startSeconds": 10.0,
                       "endSeconds": 15.0, "speed": 1.0}],
        }
        self.assertEqual([], compare_imported_sequence(expected, actual))

    def test_h_comparison_does_not_renumber_the_callers_items(self):
        expected = expected_import_result(self._five_on_one_track())
        items = [{"mediaType": "video", "trackIndex": 0,
                  "startSeconds": float(i * 5), "endSeconds": float(i * 5 + 5),
                  "speed": 1.0} for i in range(5)]
        compare_imported_sequence(expected, {"frameRate": 60, "videoTrackCount": 1,
                                             "audioTrackCount": 0, "markerCount": 0,
                                             "items": items})
        self.assertEqual([], [i for i in items if "ordinal" in i])


class MotionCalibrationTests(unittest.TestCase):
    """Pinned against the live probe, not against the FCP7 spec.

    The centre unit is the one thing here that cannot be guessed: two sources
    of different width were sent the same value in the same sequence and moved
    by different pixel amounts, which is what proves the unit is the source's
    own dimension rather than the frame's.
    """

    def test_a_center_unit_is_the_source_dimension(self):
        # 480-wide source moved 120px; 960-wide source moved 240px.
        self.assertAlmostEqual(0.25, motion_center_value(120, 480))
        self.assertAlmostEqual(0.25, motion_center_value(240, 960))
        # Same pixel offset therefore needs different values per source.
        self.assertAlmostEqual(0.25, motion_center_value(120, 480))
        self.assertAlmostEqual(0.125, motion_center_value(120, 960))

    def test_b_vertical_uses_source_height(self):
        # vert=0.125 on a 270-tall source moved it 33.75px.
        self.assertAlmostEqual(0.125, motion_center_value(33.75, 270))

    def test_c_offsets_are_signed(self):
        self.assertAlmostEqual(-0.25, motion_center_value(-120, 480))
        self.assertEqual(0, motion_center_value(0, 480))

    def test_d_unknown_source_size_fails_closed(self):
        with self.assertRaises(Fcp7XmlError):
            motion_center_value(120, 0)

    def test_e_still_scale_width_baseline_is_recorded(self):
        # Read back on a no-filter control clip; kept so a readback check can
        # tell an import baseline apart from something the generator did.
        self.assertAlmostEqual(109.4020004272461, FCP7_IMPORT_STILL_SCALE_WIDTH)


class MotionSpecTests(unittest.TestCase):
    def _clip(self, **motion_extras):
        clip = {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
                "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 2}
        clip.update(motion_extras)
        return clip

    def test_a_static_scale_normalizes_to_one_point(self):
        normalized = normalize_sequence_spec(spec(clips=[self._clip(motion={"scale": 50})]))
        self.assertEqual([{"frames": 0, "value": 50.0}], normalized["clips"][0]["motion"]["scale"])

    def test_b_keyframes_are_stamped_in_frames(self):
        normalized = normalize_sequence_spec(spec(clips=[self._clip(
            motion={"scale": [{"seconds": 0, "value": 30}, {"seconds": 1, "value": 100}]})]))
        self.assertEqual(
            [{"frames": 0, "value": 30.0}, {"frames": 60, "value": 100.0}],
            normalized["clips"][0]["motion"]["scale"],
        )

    def test_c_keyframes_are_sorted_by_time(self):
        normalized = normalize_sequence_spec(spec(clips=[self._clip(
            motion={"scale": [{"seconds": 1, "value": 100}, {"seconds": 0, "value": 30}]})]))
        self.assertEqual([0, 60], [p["frames"] for p in normalized["clips"][0]["motion"]["scale"]])

    def test_d_two_keyframes_on_one_frame_fail_closed(self):
        with self.assertRaises(Fcp7XmlError):
            normalize_sequence_spec(spec(clips=[self._clip(
                motion={"scale": [{"seconds": 0, "value": 30}, {"seconds": 0, "value": 100}]})]))

    def test_e_pixel_offset_converts_using_the_clips_source_size(self):
        normalized = normalize_sequence_spec(spec(clips=[self._clip(motion={
            "positionOffsetPixels": {"x": 120, "y": 33.75},
            "sourceWidth": 480, "sourceHeight": 270,
        })]))
        center = normalized["clips"][0]["motion"]["center"]
        self.assertAlmostEqual(0.25, center[0]["horiz"])
        self.assertAlmostEqual(0.125, center[0]["vert"])

    def test_f_pixel_offset_without_source_size_fails_closed(self):
        with self.assertRaises(Fcp7XmlError):
            normalize_sequence_spec(spec(clips=[self._clip(
                motion={"positionOffsetPixels": {"x": 120, "y": 0}})]))

    def test_g_motion_on_an_audio_clip_is_rejected(self):
        with self.assertRaises(Fcp7XmlError):
            normalize_sequence_spec(spec(clips=[{
                "mediaType": "audio", "trackIndex": 0, "filePath": AUDIO,
                "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 2,
                "motion": {"scale": 50},
            }]))

    def test_h_an_empty_motion_object_is_rejected(self):
        with self.assertRaises(Fcp7XmlError):
            normalize_sequence_spec(spec(clips=[self._clip(motion={})]))

    def test_i_clips_without_motion_stay_none(self):
        normalized = normalize_sequence_spec(spec())
        self.assertIsNone(normalized["clips"][0]["motion"])
        self.assertIsNone(normalized["clips"][0]["opacity"])


class MotionXmlTests(unittest.TestCase):
    def _effects(self, xml):
        return {e.findtext("effectid"): e for e in ET.fromstring(xml).iter("effect")}

    def _clip(self, **extras):
        clip = {"mediaType": "video", "trackIndex": 0, "filePath": VIDEO,
                "startSeconds": 0, "sourceInSeconds": 0, "sourceOutSeconds": 2}
        clip.update(extras)
        return clip

    def test_a_static_scale_is_a_value_not_a_keyframe(self):
        effects = self._effects(build_fcp7_xml(spec(clips=[self._clip(motion={"scale": 50})])))
        scale = [p for p in effects["basic"].iter("parameter")
                 if p.findtext("parameterid") == "scale"][0]
        self.assertEqual("50", scale.findtext("value"))
        self.assertEqual([], list(scale.iter("keyframe")))

    def test_b_keyframed_scale_emits_frame_stamped_keyframes(self):
        effects = self._effects(build_fcp7_xml(spec(clips=[self._clip(
            motion={"scale": [{"seconds": 0, "value": 30}, {"seconds": 1, "value": 100}]})])))
        keyframes = list(effects["basic"].iter("keyframe"))
        self.assertEqual(["0", "60"], [k.findtext("when") for k in keyframes])
        self.assertEqual(["30", "100"], [k.findtext("value") for k in keyframes])

    def test_c_center_is_emitted_in_source_units(self):
        effects = self._effects(build_fcp7_xml(spec(clips=[self._clip(motion={
            "positionOffsetPixels": {"x": 120, "y": 0},
            "sourceWidth": 480, "sourceHeight": 270,
        })])))
        center = [p for p in effects["basic"].iter("parameter")
                  if p.findtext("parameterid") == "center"][0]
        self.assertEqual("0.25", center.find("value/horiz").text)

    def test_d_opacity_is_its_own_effect(self):
        effects = self._effects(build_fcp7_xml(spec(clips=[self._clip(opacity=50)])))
        self.assertIn("opacity", effects)
        self.assertNotIn("basic", effects)
        self.assertEqual("50", effects["opacity"].find("parameter/value").text)

    def test_e_motion_and_opacity_coexist_on_one_clip(self):
        effects = self._effects(build_fcp7_xml(spec(clips=[
            self._clip(motion={"scale": 50}, opacity=25)])))
        self.assertEqual({"timeremap", "basic", "opacity"}, set(effects))

    def test_f_a_clip_without_motion_emits_no_motion_filter(self):
        effects = self._effects(build_fcp7_xml(spec()))
        self.assertEqual({"timeremap"}, set(effects))

    def test_g1_declared_file_size_matches_what_center_converts_against(self):
        """The bug this pins put a clip off-screen with everything else correct.

        Declaring the sequence size while converting the pixel offset against
        the real source size scaled a -500px request by 1920 instead of 480.
        """
        xml = build_fcp7_xml(spec(clips=[self._clip(motion={
            "positionOffsetPixels": {"x": -500, "y": 0},
            "sourceWidth": 480, "sourceHeight": 270,
        })]))
        root = ET.fromstring(xml)
        characteristics = root.find(".//file/media/video/samplecharacteristics")
        self.assertEqual("480", characteristics.findtext("width"))
        self.assertEqual("270", characteristics.findtext("height"))

        horiz = float(root.find(".//effect[effectid='basic']/parameter[parameterid='center']/value/horiz").text)
        declared_width = int(characteristics.findtext("width"))
        self.assertAlmostEqual(-500, horiz * declared_width, places=2)

    def test_g2_without_source_size_the_sequence_size_is_still_used(self):
        characteristics = ET.fromstring(build_fcp7_xml(spec())).find(
            ".//file/media/video/samplecharacteristics")
        self.assertEqual("1920", characteristics.findtext("width"))

    def test_g3_half_a_source_size_is_rejected(self):
        with self.assertRaises(Fcp7XmlError):
            normalize_sequence_spec(spec(clips=[self._clip(sourceWidth=480)]))

    def test_g_output_stays_well_formed_with_every_parameter_set(self):
        xml = build_fcp7_xml(spec(clips=[self._clip(
            motion={
                "scale": [{"seconds": 0, "value": 30}, {"seconds": 1, "value": 100}],
                "rotation": 15,
                "positionOffsetPixels": [{"seconds": 0, "x": 0, "y": 0},
                                         {"seconds": 1, "x": 120, "y": 33.75}],
                "sourceWidth": 480, "sourceHeight": 270,
            },
            opacity=[{"seconds": 0, "value": 0}, {"seconds": 0.5, "value": 100}],
        )]))
        ET.fromstring(xml)  # raises if malformed
        effects = self._effects(xml)
        self.assertEqual({"timeremap", "basic", "opacity"}, set(effects))


if __name__ == "__main__":
    unittest.main()
