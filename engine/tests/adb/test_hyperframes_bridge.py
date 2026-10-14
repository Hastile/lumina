"""Integration test for the node bridge to the HyperFrames parser.

Skipped unless both node and a HyperFrames checkout with a built
`packages/parsers/dist` are available, so CI and a bare clone stay green.
When it does run it is the only thing that catches upstream drift: every
other test in this area works from fixture JSON that this test is what
proves is still the real shape.

Point HYPERFRAMES_REPO_ROOT at the checkout to enable it.
"""

from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from lumina.adb.hyperframes_bridge import BridgeError, read_composition
from lumina.adb.hyperframes_timeline import flatten_composition

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hyperframes"


def _hyperframes_root():
    raw = os.environ.get("HYPERFRAMES_REPO_ROOT")
    if not raw:
        return None
    root = Path(raw)
    return root if (root / "packages" / "parsers" / "dist" / "index.js").is_file() else None


HF_ROOT = _hyperframes_root()
SKIP_REASON = (
    "Set HYPERFRAMES_REPO_ROOT to a built HyperFrames checkout to run the bridge integration test."
)


@unittest.skipUnless(HF_ROOT and shutil.which("node"), SKIP_REASON)
class BridgeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = read_composition(FIXTURES / "bridge_probe.html", repo_root=str(HF_ROOT))

    def test_a_both_documents_are_reached(self):
        names = sorted(Path(doc["filePath"]).name for doc in self.payload["documents"])
        self.assertEqual(["bridge_child.html", "bridge_probe.html"], names)
        self.assertEqual([], self.payload["warnings"])

    def test_b_reference_starts_are_resolved_to_the_referenced_end(self):
        # data-start="a + 0.5" with a spanning 0-3 means 3.5, not 0.5.
        entry = next(d for d in self.payload["documents"]
                     if Path(d["filePath"]).name == "bridge_probe.html")
        by_src = {el.get("src"): el for el in entry["elements"]}
        self.assertEqual(3.5, by_src["b.png"]["startTime"])

    def test_c_data_treatment_survives_the_parse(self):
        # parseHtml's attribute allowlist drops data-treatment, so the bridge
        # runs its own pass. If ensureHfIds were not applied first, these ids
        # would not line up with the parsed elements.
        entry = next(d for d in self.payload["documents"]
                     if Path(d["filePath"]).name == "bridge_probe.html")
        treatments = {rec["id"]: rec["treatment"] for rec in entry["customAttributes"]}
        self.assertEqual("full-bleed/hold-escalate/ramp", treatments["a"])
        self.assertEqual("split/rapid-fire/burst-settle", treatments["b"])

    def test_d_a_sub_composition_host_is_detected_by_attribute(self):
        # getElementType only returns "composition" for an explicit
        # data-type="composition"; the documented authoring shape has none,
        # so the host is typed "text" and only the attribute finds it.
        entry = next(d for d in self.payload["documents"]
                     if Path(d["filePath"]).name == "bridge_probe.html")
        hosts = [el for el in entry["elements"] if el.get("isSubComposition")]
        self.assertEqual(1, len(hosts))
        self.assertEqual("bridge_child.html", Path(hosts[0]["resolvedSrc"]).name)

    def test_e_the_composition_root_is_marked_so_it_can_be_dropped(self):
        for document in self.payload["documents"]:
            roots = [el for el in document["elements"] if el.get("isCompositionRoot")]
            self.assertEqual(1, len(roots), Path(document["filePath"]).name)

    def test_f_gsap_tweens_are_extracted_without_a_browser(self):
        entry = next(d for d in self.payload["documents"]
                     if Path(d["filePath"]).name == "bridge_probe.html")
        self.assertEqual(1, len(entry["animations"]))
        self.assertEqual("#a", entry["animations"][0]["targetSelector"])

    def test_g_the_flattener_agrees_with_the_live_payload(self):
        flattened = flatten_composition(self.payload)
        spans = {b["domId"]: (b["startSeconds"], b["endSeconds"]) for b in flattened["beats"]}
        # Child beats offset by the host's 6s start; the root divs are gone.
        self.assertEqual((6.0, 8.0), spans["c1"])
        self.assertEqual((8.0, 10.0), spans["c2"])
        self.assertNotIn("root", spans)
        self.assertNotIn("childroot", spans)
        # Child layers land between the parent's own, at the host's position.
        tracks = {b["domId"]: b["trackIndex"] for b in flattened["beats"]}
        self.assertLess(tracks["a"], tracks["c1"])
        self.assertLess(tracks["c2"], tracks["t"])


class BridgeErrorTests(unittest.TestCase):
    def test_a_a_missing_composition_is_typed(self):
        with self.assertRaises(BridgeError) as caught:
            read_composition(FIXTURES / "does_not_exist.html", repo_root=str(HF_ROOT or REPO_ROOT))
        self.assertEqual("BRIDGE_HTML_NOT_FOUND", caught.exception.code)

    def test_b_a_non_hyperframes_repo_root_is_rejected(self):
        with self.assertRaises(BridgeError) as caught:
            read_composition(FIXTURES / "bridge_probe.html", repo_root=str(REPO_ROOT))
        self.assertEqual("BRIDGE_REPO_ROOT_INVALID", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
