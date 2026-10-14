"""Tests for flattening a HyperFrames composition into a Premiere-shaped timeline.

Fixture payloads are built inline in the shape the node bridge emits, so
these run with no node install and no HyperFrames checkout. The shape itself
was taken from a real bridge run against parsers v0.7.85, not invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from lumina.adb.hyperframes_timeline import (
    TimelineError,
    flatten_composition,
    quantize_beats,
)
from lumina.adb.premiere_fcp7_xml import normalize_sequence_spec

PARENT = "C:/proj/parent.html"
CHILD = "C:/proj/child.html"


def element(el_id, *, type="video", start=0.0, duration=1.0, z=0, src="a.mp4", **extra):
    return {"id": el_id, "type": type, "name": el_id, "startTime": start,
            "duration": duration, "zIndex": z, "src": src, **extra}


def custom(el_id, *, treatment=None, composition_id=None, text=None):
    return {"id": el_id, "hfId": el_id, "tag": "div", "treatment": treatment,
            "beat": None, "compositionId": composition_id, "compositionSrc": None,
            "textContent": text}


def document(path, elements, customs, *, animations=(), depth=0, dimensions=None):
    return {
        "filePath": path, "depth": depth, "referencedBy": None,
        "resolution": "landscape",
        "dimensions": dimensions or {"width": 1920, "height": 1080},
        "elements": elements, "customAttributes": customs,
        "animations": list(animations), "gsapScriptPresent": bool(animations),
        "gsapError": None,
    }


def payload(documents, *, entry=PARENT, fps=30, warnings=()):
    return {"status": "SUCCESS", "bridgeVersion": 1, "parsersVersion": "0.7.85",
            "entry": entry, "project": {"fps": fps, "configPath": None},
            "documents": documents, "warnings": list(warnings)}


class FlattenTests(unittest.TestCase):
    def test_a_composition_root_is_not_a_beat(self):
        result = flatten_composition(payload([document(
            PARENT,
            [element("root", type="text", duration=10, src=None, isCompositionRoot=True),
             element("a", start=0, duration=2)],
            [custom("root", composition_id="parent"), custom("a")],
        )]))
        self.assertEqual(["a"], [b["domId"] for b in result["beats"]])

    def test_b_sparse_z_indices_are_dense_ranked(self):
        result = flatten_composition(payload([document(
            PARENT,
            [element("a", z=0, start=0), element("b", z=5, start=1), element("c", z=99, start=2)],
            [custom("a"), custom("b"), custom("c")],
        )]))
        self.assertEqual([0, 1, 2], [b["trackIndex"] for b in result["beats"]])

    def test_c_subcomposition_children_are_offset_by_the_host(self):
        result = flatten_composition(payload([
            document(PARENT,
                     [element("host", type="text", start=6, duration=4, src=None,
                              isSubComposition=True, resolvedSrc=CHILD)],
                     [custom("host")]),
            document(CHILD,
                     [element("croot", type="text", duration=4, src=None, isCompositionRoot=True),
                      element("c1", start=0, duration=2),
                      element("c2", start=2, duration=2)],
                     [custom("croot", composition_id="child"), custom("c1"), custom("c2")],
                     depth=1),
        ]))
        spans = {b["domId"]: (b["startSeconds"], b["endSeconds"]) for b in result["beats"]}
        self.assertEqual({"c1": (6.0, 8.0), "c2": (8.0, 10.0)}, spans)

    def test_d_child_layers_sit_at_the_hosts_position_not_interleaved(self):
        # The host is at z=2 between parent layers 0 and 5. Its children,
        # numbered 0 and 1 in their own document, must land between them -
        # merging the numbers arithmetically would put them under the
        # parent's own z=0 layer instead.
        result = flatten_composition(payload([
            document(PARENT,
                     [element("bg", z=0, start=0, duration=12),
                      element("host", type="text", z=2, start=0, duration=4, src=None,
                              isSubComposition=True, resolvedSrc=CHILD),
                      element("fg", z=5, start=0, duration=12)],
                     [custom("bg"), custom("host"), custom("fg")]),
            document(CHILD,
                     [element("c1", z=0, start=0, duration=2),
                      element("c2", z=1, start=2, duration=2)],
                     [custom("c1"), custom("c2")], depth=1),
        ]))
        tracks = {b["domId"]: b["trackIndex"] for b in result["beats"]}
        self.assertEqual({"bg": 0, "c1": 1, "c2": 2, "fg": 3}, tracks)

    def test_e_child_is_clipped_to_the_host_span(self):
        result = flatten_composition(payload([
            document(PARENT,
                     [element("host", type="text", start=0, duration=3, src=None,
                              isSubComposition=True, resolvedSrc=CHILD)],
                     [custom("host")]),
            document(CHILD, [element("c1", start=0, duration=10)], [custom("c1")], depth=1),
        ]))
        beat = result["beats"][0]
        self.assertEqual(3.0, beat["endSeconds"])
        self.assertTrue(beat["clippedToHost"])

    def test_f_a_reused_subcomposition_is_instantiated_twice(self):
        # The bridge dedupes documents; the timeline must not - two hosts
        # means the same clips appear at two different offsets.
        result = flatten_composition(payload([
            document(PARENT,
                     [element("h1", type="text", start=0, duration=2, src=None,
                              isSubComposition=True, resolvedSrc=CHILD),
                      element("h2", type="text", start=10, duration=2, src=None,
                              isSubComposition=True, resolvedSrc=CHILD)],
                     [custom("h1"), custom("h2")]),
            document(CHILD, [element("c1", start=0, duration=2)], [custom("c1")], depth=1),
        ]))
        self.assertEqual([0.0, 10.0], sorted(b["startSeconds"] for b in result["beats"]))

    def test_g_a_cycle_is_reported_not_recursed(self):
        result = flatten_composition(payload([
            document(PARENT,
                     [element("toChild", type="text", start=0, duration=5, src=None,
                              isSubComposition=True, resolvedSrc=CHILD)],
                     [custom("toChild")]),
            document(CHILD,
                     [element("backToParent", type="text", start=0, duration=5, src=None,
                              isSubComposition=True, resolvedSrc=PARENT)],
                     [custom("backToParent")], depth=1),
        ]))
        self.assertIn("SUBCOMPOSITION_CYCLE", [w["code"] for w in result["warnings"]])

    def test_h_only_id_selectors_are_attributed_to_a_beat(self):
        animations = [
            {"targetSelector": "#a", "resolvedStart": 0, "duration": 2, "properties": {"scale": 1.2}},
            {"targetSelector": ".card span", "resolvedStart": 0, "duration": 2, "properties": {"x": 10}},
        ]
        result = flatten_composition(payload([document(
            PARENT, [element("a", start=0, duration=3)], [custom("a")], animations=animations,
        )]))
        self.assertEqual(1, len(result["beats"][0]["animations"]))
        # The unattributed tween still proves the frame is not inert.
        self.assertEqual(2, len(result["animationSpans"]))

    def test_i_unresolved_timing_is_reported_not_dropped_silently(self):
        result = flatten_composition(payload([document(
            PARENT,
            [element("a", start=None, duration=2), element("b", start=0, duration=1)],
            [custom("a"), custom("b")],
        )]))
        self.assertEqual(["b"], [b["domId"] for b in result["beats"]])
        self.assertIn("ELEMENT_TIMING_UNRESOLVED", [w["code"] for w in result["warnings"]])

    def test_j_a_missing_entry_document_is_an_error(self):
        with self.assertRaises(TimelineError):
            flatten_composition(payload([document(CHILD, [], [])], entry=PARENT))


class QuantizationTests(unittest.TestCase):
    def _flatten(self, elements, customs=None):
        customs = customs or [custom(e["id"]) for e in elements]
        return flatten_composition(payload([document(PARENT, elements, customs)]))

    def test_a_abutting_beats_survive_the_fcp7_overlap_check(self):
        # The regression this whole step exists for: two beats that abut at a
        # non-frame boundary can round into a one-frame overlap, and
        # normalize_sequence_spec rejects the entire spec when that happens.
        flattened = self._flatten([
            element("a", z=0, start=0.0, duration=1.017),
            element("b", z=0, start=1.017, duration=1.017),
            element("c", z=0, start=2.034, duration=1.017),
        ])
        quantized = quantize_beats(flattened["beats"], fps=30)
        spec = {
            "name": "Q", "frameRate": 30, "width": 1920, "height": 1080,
            "clips": [{
                "mediaType": "video", "trackIndex": beat["trackIndex"],
                "filePath": r"D:\media\slug.mp4", "name": beat["beatId"],
                "startSeconds": beat["startSeconds"],
                "sourceInSeconds": 0.0, "sourceOutSeconds": beat["durationSeconds"],
            } for beat in quantized["beats"]],
        }
        normalized = normalize_sequence_spec(spec)  # raises CLIP_OVERLAP if this regressed
        self.assertEqual(3, len(normalized["clips"]))

    def test_b_a_beat_that_rounds_away_is_forced_to_one_frame_and_reported(self):
        # 0.01s at 30fps rounds to zero frames, which normalize_sequence_spec
        # rejects outright. A rapid-fire beat legitimately gets this short.
        flattened = self._flatten([element("a", start=0.0, duration=0.01)])
        quantized = quantize_beats(flattened["beats"], fps=30)
        self.assertEqual(1, quantized["beats"][0]["endFrame"] - quantized["beats"][0]["startFrame"])
        self.assertEqual(["SHORTER_THAN_ONE_FRAME"], [a["reason"] for a in quantized["adjustments"]])

    def test_b2_a_beat_that_rounds_to_a_frame_on_its_own_is_not_reported(self):
        flattened = self._flatten([element("a", start=0.0, duration=0.02)])
        quantized = quantize_beats(flattened["beats"], fps=30)
        self.assertEqual(1, quantized["beats"][0]["endFrame"] - quantized["beats"][0]["startFrame"])
        self.assertEqual([], quantized["adjustments"])

    def test_c_a_real_overlap_trims_the_earlier_beat(self):
        flattened = self._flatten([
            element("a", z=0, start=0.0, duration=5.0),
            element("b", z=0, start=2.0, duration=3.0),
        ])
        quantized = quantize_beats(flattened["beats"], fps=30)
        by_id = {b["domId"]: b for b in quantized["beats"]}
        self.assertEqual(60, by_id["a"]["endFrame"])  # trimmed back to b's start
        self.assertEqual(60, by_id["b"]["startFrame"])  # the authored cut is not moved
        self.assertEqual(["TRIMMED_FOR_OVERLAP"], [a["reason"] for a in quantized["adjustments"]])

    def test_d_beats_on_different_tracks_may_overlap_freely(self):
        flattened = self._flatten([
            element("a", z=0, start=0.0, duration=5.0),
            element("b", z=1, start=2.0, duration=3.0),
        ])
        quantized = quantize_beats(flattened["beats"], fps=30)
        self.assertEqual([], quantized["adjustments"])

    def test_e_zero_fps_is_rejected(self):
        flattened = self._flatten([element("a", start=0.0, duration=1.0)])
        with self.assertRaises(TimelineError):
            quantize_beats(flattened["beats"], fps=0)


if __name__ == "__main__":
    unittest.main()
