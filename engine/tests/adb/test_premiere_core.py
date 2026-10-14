import copy
import sys
import tempfile
import unittest
from pathlib import Path

copy_deep = copy.deepcopy


REPO_ROOT = Path(__file__).resolve().parents[1]

from lumina.adb.premiere_core import (
    PRODUCTION_FINGERPRINT_SCHEMA_VERSION,
    analyze_offline_media,
    find_duplicate_media_groups,
    marker_identity,
    marker_patch_plan,
    looks_like_filesystem_path,
    normalized_media_path,
    plan_track_index_shift,
    production_sequence_fingerprint,
    protected_sequence_ids,
    sequence_fingerprint,
    sequence_fingerprint_completeness,
    sequence_is_empty,
    stale_fingerprint,
    track_is_empty,
)


def item(name, path=None, node_id="id", **extra):
    return {
        "identity": {"nodeId": node_id}, "name": name, "treePath": name,
        "mediaPath": path, "sequence": False, "premiereOffline": False, **extra,
    }


class PremiereCoreTests(unittest.TestCase):
    def test_a_recursive_tree_shape_is_preserved(self):
        tree = {"root": {"children": [{"name": "하위", "children": []}]}, "items": []}
        self.assertEqual("하위", tree["root"]["children"][0]["name"])

    def test_b_unicode_path_normalization(self):
        value = normalized_media_path(r"D:\자막\야마다 タヤマ.srt")
        self.assertIn("야마다", value)

    def test_c_null_project_item_does_not_break_analysis(self):
        report = analyze_offline_media({"items": [None]})
        self.assertEqual(0, report["checkedItemCount"])

    def test_d_offline_and_missing_path_are_distinct(self):
        report = analyze_offline_media({"items": [
            item("offline", None, premiereOffline=True),
            item("missing", str(REPO_ROOT / "does-not-exist.mov")),
        ]})
        self.assertEqual(1, len(report["offlineItems"]))
        self.assertEqual({"mediaPathUnavailable", "mediaPathMissing"}, {x["pathStatus"] for x in report["missingPathItems"]})

    def test_e_duplicate_normalized_path(self):
        path = str(REPO_ROOT / "README.md")
        report = find_duplicate_media_groups({"items": [item("a", path, "1"), item("b", path.upper(), "2")]}, method="normalized_path")
        self.assertEqual(1, report["duplicateGroupCount"])

    def test_f_duplicate_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "a.bin"; second = Path(directory) / "b.bin"
            first.write_bytes(b"same"); second.write_bytes(b"same")
            report = find_duplicate_media_groups({"items": [item("a", str(first), "1"), item("b", str(second), "2")]}, method="sha256", compute_hash=True)
            self.assertEqual(1, report["duplicateGroupCount"])

    def test_g_sequence_identity_changes_fingerprint(self):
        base = {"sequenceId": "one", "name": "x", "videoTracks": [], "audioTracks": []}
        other = {**base, "sequenceId": "two"}
        self.assertNotEqual(sequence_fingerprint(base), sequence_fingerprint(other))

    # protected_sequence_ids used to hard-block deleting the active sequence
    # and the specific V7/36-item caption-graphics sequence. That name- and
    # shape-based protection was deliberately retired so real project
    # sequences became valid edit targets; the replacement is the explicit
    # confirm_destructive contract, covered in
    # tests/test_destructive_confirmation.py. These two tests pin the
    # retirement so it stays a decision rather than an accident.

    def test_h_v7_sequence_is_not_protected_by_shape(self):
        sequences = {"sequences": [{"sequenceId": "s", "active": False, "videoTracks": [{"trackIndex": 6, "itemCount": 36}]}]}
        self.assertNotIn("s", protected_sequence_ids(sequences))

    def test_i_active_sequence_is_not_protected_by_name(self):
        self.assertEqual({}, protected_sequence_ids({"sequences": [{"sequenceId": "s", "active": True}]}))

    def test_i2_deleting_any_sequence_still_requires_confirmation(self):
        # The protection that replaced the above: no sequence is exempt.
        from lumina.adb.edit_plan import requires_destructive_confirmation
        self.assertTrue(requires_destructive_confirmation("sequence.delete", dry_run=False, confirm_destructive=False))
        self.assertFalse(requires_destructive_confirmation("sequence.delete", dry_run=False, confirm_destructive=True))

    def test_j_nonempty_sequence(self):
        self.assertFalse(sequence_is_empty({"videoTracks": [{"itemCount": 1}], "audioTracks": []}))
        self.assertTrue(sequence_is_empty({"videoTracks": [{"itemCount": 0}], "audioTracks": []}))

    def test_k_track_shift_plan(self):
        self.assertEqual([{"beforeIndex": 2, "afterIndex": 1}], plan_track_index_shift(3, 1))

    def test_l_nonempty_track(self):
        self.assertFalse(track_is_empty({"itemCount": 1}))

    def test_m_marker_exact_identity(self):
        marker = {"guid": "g", "startTicks": "10", "type": "WebLink", "name": "n"}
        self.assertEqual(("g", "10", "WebLink", "n"), marker_identity(marker))

    def test_n_marker_update_rollback(self):
        plan = marker_patch_plan({"name": "before", "comments": "old"}, {"name": "after", "comments": "new"})
        self.assertEqual({"name": "before", "comments": "old"}, plan["rollback"])

    def test_o_stale_fingerprint(self):
        self.assertTrue(stale_fingerprint("expected", "actual"))
        self.assertFalse(stale_fingerprint(None, "actual"))

    def _complete_sequence(self):
        return {
            "sequenceId": "s", "name": "x",
            "videoTracks": [{"trackIndex": 0, "mediaType": "video", "name": "V1", "itemCount": 1, "items": [
                {"startTicks": "0", "endTicks": "100", "projectItemId": "p1"},
            ]}],
            "audioTracks": [],
        }

    def test_p_production_fingerprint_matches_plain_fingerprint_when_complete(self):
        # production_sequence_fingerprint must never be a second hashing
        # scheme - only an added completeness guard around the exact same
        # sequence_fingerprint computation.
        seq = self._complete_sequence()
        self.assertEqual(production_sequence_fingerprint(seq), sequence_fingerprint(seq))

    def test_q_production_fingerprint_raises_on_incomplete_items(self):
        # Regression test for a real bug found live: the same sequence,
        # fingerprinted via the same sequence_fingerprint function through
        # two different call sites, produced two different hex digests
        # purely because one path (list_sequences_detailed's default
        # include_items=False) never populated track items. That silent,
        # item-blind hash must never be usable as the authoritative
        # production-invariance value.
        seq = self._complete_sequence()
        seq["videoTracks"][0]["items"] = []  # itemCount=1 but items missing
        self.assertEqual(sequence_fingerprint_completeness(seq), ["video:0"])
        with self.assertRaises(ValueError):
            production_sequence_fingerprint(seq)

    def test_r_completeness_empty_for_zero_item_tracks(self):
        seq = {"videoTracks": [{"trackIndex": 0, "mediaType": "video", "itemCount": 0, "items": []}], "audioTracks": []}
        self.assertEqual(sequence_fingerprint_completeness(seq), [])
        production_sequence_fingerprint(seq)  # must not raise

    def test_s_production_fingerprint_schema_version_is_stable_string(self):
        self.assertEqual(PRODUCTION_FINGERPRINT_SCHEMA_VERSION, "production-sequence-fingerprint/1")

    # --- fingerprint regression battery (section 13) -----------------------
    # sequence_fingerprint's actual hash inputs are: sequenceId, name, track
    # counts, duration, markerCount, and per-track name/itemCount/items
    # (each item's startTicks/endTicks/projectItemId only). Tests below
    # reflect that real schema exactly - they are deliberately NOT written
    # against section 11's full aspirational field list (effect/keyframe/
    # caption structural summaries), since sequence_fingerprint does not
    # carry that data today; expanding it would change every existing
    # persisted fingerprint value and is out of scope for this pack (see
    # the final report's documented limitation).

    def test_t_same_snapshot_same_fingerprint(self):
        seq = self._complete_sequence()
        self.assertEqual(sequence_fingerprint(seq), sequence_fingerprint(copy_deep(seq)))

    def test_u_item_timing_change_changes_fingerprint(self):
        seq = self._complete_sequence()
        other = copy_deep(seq)
        other["videoTracks"][0]["items"][0]["endTicks"] = "200"
        self.assertNotEqual(sequence_fingerprint(seq), sequence_fingerprint(other))

    def test_v_source_in_out_change_changes_fingerprint(self):
        seq = self._complete_sequence()
        other = copy_deep(seq)
        other["videoTracks"][0]["items"][0]["startTicks"] = "50"
        self.assertNotEqual(sequence_fingerprint(seq), sequence_fingerprint(other))

    def test_w_track_reorder_changes_fingerprint(self):
        seq = self._complete_sequence()
        seq["videoTracks"].append({"trackIndex": 1, "mediaType": "video", "name": "V2", "itemCount": 0, "items": []})
        reordered = copy_deep(seq)
        reordered["videoTracks"] = list(reversed(reordered["videoTracks"]))
        self.assertNotEqual(sequence_fingerprint(seq), sequence_fingerprint(reordered))

    def test_x_marker_count_change_changes_fingerprint_but_content_only_change_does_not(self):
        # Documented limitation: sequence_fingerprint only ever sees
        # markerCount, never individual marker guid/startTicks/name - so
        # renaming or moving an existing marker without changing the total
        # count is invisible to this fingerprint. A caller that needs to
        # detect marker-content-only changes must compare markers
        # separately (e.g. via marker_identity), not via this fingerprint.
        seq = self._complete_sequence()
        seq["markerCount"] = 1
        seq["markers"] = [{"guid": "g1", "startTicks": "10", "name": "before"}]
        more_markers = copy_deep(seq)
        more_markers["markerCount"] = 2
        self.assertNotEqual(sequence_fingerprint(seq), sequence_fingerprint(more_markers))
        renamed_marker_same_count = copy_deep(seq)
        renamed_marker_same_count["markers"][0]["name"] = "after"
        self.assertEqual(sequence_fingerprint(seq), sequence_fingerprint(renamed_marker_same_count))

    def test_y_active_sequence_only_flag_does_not_affect_fingerprint(self):
        # "active" is not one of sequence_fingerprint's hashed fields, so a
        # user merely switching the active sequence in the Premiere UI must
        # never change a production source's fingerprint (section 14).
        seq = self._complete_sequence()
        seq["active"] = False
        toggled = copy_deep(seq)
        toggled["active"] = True
        self.assertEqual(sequence_fingerprint(seq), sequence_fingerprint(toggled))

    def test_z_unicode_is_deterministic(self):
        seq = self._complete_sequence()
        seq["name"] = "자막 시퀀스 タヤマ"
        self.assertEqual(sequence_fingerprint(seq), sequence_fingerprint(copy_deep(seq)))

    def test_aa_ntsc_23_976_ticks_are_hashed_as_given_without_rounding(self):
        seq = self._complete_sequence()
        seq["duration"] = {"seconds": 8.508375, "ticks": "216216000000"}  # 23.976fps-exact tick value
        other = copy_deep(seq)
        other["duration"] = {"seconds": 8.508375, "ticks": "216216000001"}
        self.assertNotEqual(sequence_fingerprint(seq), sequence_fingerprint(other))

    def test_ab_null_unreadable_fields_normalize_without_raising(self):
        seq = {
            "sequenceId": "s", "name": None,
            "videoTracks": [{"trackIndex": 0, "mediaType": "video", "itemCount": 1, "items": [
                {"startTicks": None, "endTicks": None, "projectItemId": None},
            ]}],
            "audioTracks": None,
        }
        digest = sequence_fingerprint(seq)
        self.assertIsInstance(digest, str)
        self.assertEqual(64, len(digest))

    def test_same_filename_different_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            a = Path(directory) / "a" / "same.mov"; b = Path(directory) / "b" / "same.mov"
            a.parent.mkdir(); b.parent.mkdir(); a.write_bytes(b"a"); b.write_bytes(b"b")
            report = find_duplicate_media_groups({"items": [item("a", str(a), "1"), item("b", str(b), "2")]})
            self.assertEqual(1, len(report["sameFilenameDifferentPathGroups"]))

    def test_premiere_synthetic_media_token_is_not_missing_file(self):
        self.assertFalse(looks_like_filesystem_path("1112293707"))
        report = analyze_offline_media({"items": [item("adjustment", "1112293707")]})
        self.assertEqual("unsupportedItemType", report["missingPathItems"][0]["pathStatus"])
        self.assertEqual(0, report["checkedItemCount"])

    def test_empty_string_media_path_is_rejected(self):
        # Regression test: TrackItems sourced from a nested Premiere sequence
        # (rather than a flat media file) report mediaPath as "" (not None,
        # not a numeric synthetic token). Confirmed live: passing "" straight
        # to ffprobe produces ": Invalid argument" with RC=1.
        self.assertFalse(looks_like_filesystem_path(""))
        self.assertFalse(looks_like_filesystem_path("   "))
        self.assertFalse(looks_like_filesystem_path(None))


if __name__ == "__main__":
    unittest.main()
