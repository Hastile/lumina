from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import lumina.adb.media_ingest as media_ingest
import lumina.adb.source_db as source_db

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "lumina" / "adb" / "database" / "schema.sql"


def _fresh_connection() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return con


def _event(pts_seconds: float) -> dict:
    return {"frame": 0, "ptsTicks": 0, "ptsSeconds": pts_seconds, "sceneScore": 0.5}


class TestClusterSceneEvents(unittest.TestCase):
    def test_far_apart_events_form_separate_clusters(self):
        events = [_event(1.0), _event(5.0), _event(9.0)]
        clusters = media_ingest.cluster_scene_events(events, cluster_window_ms=350)
        self.assertEqual([len(c) for c in clusters], [1, 1, 1])

    def test_close_events_chain_into_one_cluster(self):
        events = [_event(5.00), _event(5.10), _event(5.25)]
        clusters = media_ingest.cluster_scene_events(events, cluster_window_ms=350)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 3)

    def test_chain_linkage_bridges_indirectly_close_events(self):
        # 5.00 -> 5.30 is within window, 5.30 -> 5.60 is within window,
        # but 5.00 -> 5.60 (600ms) is not - chain linkage still merges all three.
        events = [_event(5.00), _event(5.30), _event(5.60)]
        clusters = media_ingest.cluster_scene_events(events, cluster_window_ms=350)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 3)

    def test_rejects_nonpositive_window(self):
        with self.assertRaises(ValueError):
            media_ingest.cluster_scene_events([_event(1.0)], cluster_window_ms=0)


class TestBuildCutBoundaries(unittest.TestCase):
    def test_no_events_yields_single_whole_clip_cut(self):
        cuts = media_ingest.build_cut_boundaries([], media_duration_ms=10000)
        self.assertEqual(cuts, [{
            "cutIndex": 0, "startMs": 0, "endMs": 10000,
            "boundaryConfidence": "high", "transitionSpanMs": 0, "sourceEventCount": 0,
        }])

    def test_single_clean_event_produces_two_high_confidence_cuts(self):
        cuts = media_ingest.build_cut_boundaries([_event(5.0)], media_duration_ms=10000)
        self.assertEqual(len(cuts), 2)
        self.assertEqual(cuts[0]["startMs"], 0)
        self.assertEqual(cuts[0]["endMs"], 5000)
        self.assertEqual(cuts[0]["boundaryConfidence"], "high")
        self.assertEqual(cuts[1]["startMs"], 5000)
        self.assertEqual(cuts[1]["endMs"], 10000)

    def test_clustered_events_flagged_low_confidence(self):
        events = [_event(4.9), _event(5.0), _event(5.1)]
        cuts = media_ingest.build_cut_boundaries(events, media_duration_ms=10000)
        self.assertEqual(len(cuts), 2)
        self.assertEqual(cuts[0]["boundaryConfidence"], "low")
        self.assertEqual(cuts[0]["sourceEventCount"], 3)
        self.assertGreater(cuts[0]["transitionSpanMs"], 0)
        # boundary lands at the midpoint of the cluster span (4.9s..5.1s -> 5.0s)
        self.assertEqual(cuts[0]["endMs"], 5000)

    def test_cut_indices_are_contiguous_after_dropping_zero_length_cuts(self):
        # Two events at the exact same timestamp collapse the boundary onto
        # itself and must not leave a gap in cutIndex.
        events = [_event(3.0), _event(3.0), _event(7.0)]
        cuts = media_ingest.build_cut_boundaries(events, media_duration_ms=10000)
        self.assertEqual([c["cutIndex"] for c in cuts], list(range(len(cuts))))

    def test_rejects_nonpositive_duration(self):
        with self.assertRaises(ValueError):
            media_ingest.build_cut_boundaries([], media_duration_ms=0)


class TestPickFrameTimestamps(unittest.TestCase):
    def test_ordinary_cut_uses_midpoint(self):
        cuts = [{"cutIndex": 0, "startMs": 0, "endMs": 1000, "boundaryConfidence": "high"}]
        self.assertEqual(media_ingest.pick_frame_timestamps_ms(cuts), {0: 500})

    def test_low_confidence_right_edge_pushes_frame_left(self):
        cuts = [
            {"cutIndex": 0, "startMs": 0, "endMs": 2000, "boundaryConfidence": "low"},
            {"cutIndex": 1, "startMs": 2000, "endMs": 4000, "boundaryConfidence": "high"},
        ]
        timestamps = media_ingest.pick_frame_timestamps_ms(cuts, avoid_ms=200)
        self.assertEqual(timestamps[0], 200)  # pulled toward the start, away from its own (right) low-confidence edge

    def test_low_confidence_left_edge_pushes_frame_right(self):
        cuts = [
            {"cutIndex": 0, "startMs": 0, "endMs": 2000, "boundaryConfidence": "low"},
            {"cutIndex": 1, "startMs": 2000, "endMs": 4000, "boundaryConfidence": "high"},
        ]
        timestamps = media_ingest.pick_frame_timestamps_ms(cuts, avoid_ms=200)
        self.assertEqual(timestamps[1], 3800)  # pulled toward the end, away from its left (previous cut's) low-confidence edge

    def test_both_edges_low_falls_back_to_midpoint(self):
        cuts = [
            {"cutIndex": 0, "startMs": 0, "endMs": 1000, "boundaryConfidence": "low"},
            {"cutIndex": 1, "startMs": 1000, "endMs": 3000, "boundaryConfidence": "low"},
            {"cutIndex": 2, "startMs": 3000, "endMs": 4000, "boundaryConfidence": "high"},
        ]
        timestamps = media_ingest.pick_frame_timestamps_ms(cuts, avoid_ms=200)
        self.assertEqual(timestamps[1], 2000)

    def test_short_cut_clamps_margin_instead_of_crossing_over(self):
        cuts = [
            {"cutIndex": 0, "startMs": 0, "endMs": 100, "boundaryConfidence": "low"},
            {"cutIndex": 1, "startMs": 100, "endMs": 500, "boundaryConfidence": "high"},
        ]
        timestamps = media_ingest.pick_frame_timestamps_ms(cuts, avoid_ms=200)
        self.assertEqual(timestamps[0], 50)  # avoid_ms(200) > half of 100ms duration, clamp to midpoint


class TestAlignCuesToCuts(unittest.TestCase):
    def test_cue_assigned_to_overlapping_cut_only(self):
        cuts = [
            {"cutIndex": 0, "startMs": 0, "endMs": 1000},
            {"cutIndex": 1, "startMs": 1000, "endMs": 2000},
        ]
        cues = [{"startMilliseconds": 200, "endMilliseconds": 700, "text": "a"}]
        aligned = media_ingest.align_cues_to_cuts(cues, cuts)
        self.assertEqual(len(aligned[0]), 1)
        self.assertEqual(len(aligned[1]), 0)

    def test_cue_spanning_boundary_assigned_to_both_cuts(self):
        cuts = [
            {"cutIndex": 0, "startMs": 0, "endMs": 1000},
            {"cutIndex": 1, "startMs": 1000, "endMs": 2000},
        ]
        cues = [{"startMilliseconds": 800, "endMilliseconds": 1200, "text": "b"}]
        aligned = media_ingest.align_cues_to_cuts(cues, cuts)
        self.assertEqual(len(aligned[0]), 1)
        self.assertEqual(len(aligned[1]), 1)


class TestSnapLowConfidenceBoundariesToCues(unittest.TestCase):
    def test_low_confidence_boundary_snaps_to_nearby_cue_edge(self):
        cuts = [
            {"cutIndex": 0, "startMs": 0, "endMs": 5100, "boundaryConfidence": "low"},
            {"cutIndex": 1, "startMs": 5100, "endMs": 10000, "boundaryConfidence": "high"},
        ]
        cues = [{"startMilliseconds": 4000, "endMilliseconds": 5000, "text": "x"}]
        snapped = media_ingest.snap_low_confidence_boundaries_to_cues(cuts, cues, tolerance_ms=300)
        self.assertEqual(snapped[0]["endMs"], 5000)
        self.assertEqual(snapped[1]["startMs"], 5000)
        self.assertTrue(snapped[0]["boundarySnappedToCueEdge"])

    def test_high_confidence_boundary_is_untouched(self):
        cuts = [
            {"cutIndex": 0, "startMs": 0, "endMs": 5100, "boundaryConfidence": "high"},
            {"cutIndex": 1, "startMs": 5100, "endMs": 10000, "boundaryConfidence": "high"},
        ]
        cues = [{"startMilliseconds": 4000, "endMilliseconds": 5000, "text": "x"}]
        snapped = media_ingest.snap_low_confidence_boundaries_to_cues(cuts, cues, tolerance_ms=300)
        self.assertEqual(snapped[0]["endMs"], 5100)

    def test_out_of_tolerance_edge_is_left_alone(self):
        cuts = [
            {"cutIndex": 0, "startMs": 0, "endMs": 5100, "boundaryConfidence": "low"},
            {"cutIndex": 1, "startMs": 5100, "endMs": 10000, "boundaryConfidence": "high"},
        ]
        cues = [{"startMilliseconds": 3000, "endMilliseconds": 4000, "text": "x"}]
        snapped = media_ingest.snap_low_confidence_boundaries_to_cues(cuts, cues, tolerance_ms=300)
        self.assertEqual(snapped[0]["endMs"], 5100)
        self.assertNotIn("boundarySnappedToCueEdge", snapped[0])

    def test_no_cues_returns_cuts_unchanged(self):
        cuts = [{"cutIndex": 0, "startMs": 0, "endMs": 5100, "boundaryConfidence": "low"}]
        self.assertEqual(media_ingest.snap_low_confidence_boundaries_to_cues(cuts, []), cuts)


class TestIngestCutsToDb(unittest.TestCase):
    def test_batch_write_creates_source_media_cuts_tags_dialogue(self):
        con = _fresh_connection()
        cuts = [
            {
                "cutIndex": 0, "startMs": 0, "endMs": 5000, "descriptionText": "desc one",
                "shotType": "medium", "tags": [{"category": "mood", "value": "calm"}],
                "dialogue": [{"text": "line one", "speaker": "A", "startMs": 100, "endMs": 900}],
            },
            {"cutIndex": 1, "startMs": 5000, "endMs": 10000, "descriptionText": "desc two"},
        ]
        result = media_ingest.ingest_cuts_to_db(
            con,
            source={"sourceCategory": "anime", "isOfficial": True, "workTitle": "W", "originRef": "E1"},
            media={"mediaType": "video", "filePath": "clip.mp4", "durationMs": 10000, "fps": 24.0},
            cuts=cuts,
        )
        self.assertEqual(len(result["cutIds"]), 2)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM scene_cut").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM dialogue").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM tag").fetchone()[0], 1)

        detail = source_db.get_cut_detail(con, result["cutIds"][0])
        self.assertEqual(detail["description_text"], "desc one")
        self.assertEqual(detail["dialogue"][0]["text"], "line one")
        self.assertEqual(detail["tags"][0]["value"], "calm")

    def test_missing_description_text_raises_before_any_write(self):
        con = _fresh_connection()
        cuts = [{"cutIndex": 0, "startMs": 0, "endMs": 5000}]
        with self.assertRaises(ValueError):
            media_ingest.ingest_cuts_to_db(
                con,
                source={"sourceCategory": "anime", "isOfficial": True},
                media={"mediaType": "video", "filePath": "clip.mp4"},
                cuts=cuts,
            )
        self.assertEqual(con.execute("SELECT COUNT(*) FROM source").fetchone()[0], 0)

    def test_rerunning_ingest_on_same_media_upserts_cuts_in_place(self):
        con = _fresh_connection()
        source = {"sourceCategory": "anime", "isOfficial": True, "workTitle": "W", "originRef": "E1"}
        # fileHash is what get_or_create_media dedupes on; without it a re-run
        # inserts a new media row every time (see source_db.get_or_create_media).
        media = {"mediaType": "video", "filePath": "clip.mp4", "fileHash": "same-file-hash"}
        first = media_ingest.ingest_cuts_to_db(
            con, source=source, media=media,
            cuts=[{"cutIndex": 0, "startMs": 0, "endMs": 5000, "descriptionText": "first pass"}],
        )
        second = media_ingest.ingest_cuts_to_db(
            con, source=source, media=media,
            cuts=[{"cutIndex": 0, "startMs": 0, "endMs": 5000, "descriptionText": "corrected"}],
        )
        self.assertEqual(first["mediaId"], second["mediaId"])
        self.assertEqual(first["cutIds"], second["cutIds"])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM scene_cut").fetchone()[0], 1)
        detail = source_db.get_cut_detail(con, second["cutIds"][0])
        self.assertEqual(detail["description_text"], "corrected")


class TestCommitOwnsOnlyItsOwnTags(unittest.TestCase):
    """A re-commit replaces the tags this pipeline wrote, and nothing else.

    Staging treats a cut's tags as one closed set replaced wholesale, but the
    commit path only ever added, so a tag removed in staging stayed in
    source.db - still searchable. cut_tag.source is what makes the fix
    possible without collateral: writers that do not declare a source (the
    exemplar matcher, reverse image search) leave 'pending', and a commit
    must not clear their work while tidying its own.
    """

    def _commit(self, con, tags):
        return media_ingest.ingest_cuts_to_db(
            con,
            source={"sourceCategory": "anime", "isOfficial": True,
                    "workTitle": "W", "originRef": "E1"},
            media={"mediaType": "video", "filePath": "clip.mp4", "fileHash": "h-1"},
            cuts=[{
                "cutIndex": 0, "startMs": 0, "endMs": 3000,
                "descriptionText": "설명", "tags": tags,
            }],
        )

    def _values(self, con, cut_id):
        return {
            r["value"] for r in con.execute(
                "SELECT t.value AS value FROM cut_tag ct "
                "JOIN tag t ON t.tag_id = ct.tag_id WHERE ct.cut_id = ?", (cut_id,))
        }

    def test_a_tag_removed_in_staging_is_removed_on_recommit(self):
        con = _fresh_connection()
        cut_id = self._commit(con, [
            {"category": "setting", "value": "교실", "source": "session"},
            {"category": "mood", "value": "고요", "source": "session"},
        ])["cutIds"][0]
        self.assertEqual(self._values(con, cut_id), {"교실", "고요"})

        self._commit(con, [{"category": "setting", "value": "교실", "source": "session"}])
        self.assertEqual(self._values(con, cut_id), {"교실"})

    def test_a_tag_from_a_writer_that_declared_no_source_survives(self):
        con = _fresh_connection()
        cut_id = self._commit(
            con, [{"category": "setting", "value": "교실", "source": "session"}]
        )["cutIds"][0]
        # Another writer attaching a tag the way the exemplar matcher does -
        # no source, so the column keeps its 'pending' default.
        source_db.tag_cut_by_value(con, cut_id=cut_id, category="subject", value="긴 머리")

        self._commit(con, [{"category": "setting", "value": "교실", "source": "session"}])
        self.assertEqual(self._values(con, cut_id), {"교실", "긴 머리"})

    def test_the_search_index_follows_the_removal(self):
        """A stale tag that survived in tag_search would still be findable."""
        con = _fresh_connection()
        cut_id = self._commit(con, [
            {"category": "setting", "value": "교실", "source": "session"},
            {"category": "mood", "value": "고요", "source": "session"},
        ])["cutIds"][0]
        self._commit(con, [{"category": "setting", "value": "교실", "source": "session"}])

        hits = con.execute(
            "SELECT rowid FROM tag_search WHERE tag_search MATCH ?", ('"고요"*',)
        ).fetchall()
        self.assertEqual(hits, [])


class TestStandaloneCommitDropsSpeakers(unittest.TestCase):
    """`speaker` is a person's field, and this path has no person in it.

    Transcription leaves speaker NULL (it does not know who spoke) and the
    ingest bridge has no speaker write, so the app's review pane is the only
    place one is legitimately set. A name arriving at this tool could only be
    a session's inference - and it is the worst identity field to infer,
    because a speaker may be off-screen, so the frame cannot contradict it.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util

        source = Path(__file__).resolve().parents[1] / "mcp" / "pr-mcp.py"
        try:
            spec = importlib.util.spec_from_file_location("pr_mcp_speaker_test", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:  # pragma: no cover - environment, not logic
            raise unittest.SkipTest(f"pr-mcp.py could not be imported: {exc}")
        tool = module.commit_ingested_cuts_to_source_db
        # FastMCP may hand back a wrapper rather than the function itself.
        cls.commit = getattr(tool, "fn", tool)

    def _commit(self, dialogue):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "source.db"
            con = sqlite3.connect(db)
            con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            con.commit()
            con.close()

            result = type(self).commit(
                source_category="anime",
                is_official=True,
                media_type="video",
                media_file_path=str(Path(tmp) / "clip.mp4"),
                work_title="W",
                compute_file_hash=False,
                db_path=str(db),
                cuts=[{
                    "cutIndex": 0, "startMs": 0, "endMs": 3000,
                    "descriptionText": "설명", "dialogue": dialogue,
                }],
            )
            con = sqlite3.connect(db)
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute(
                "SELECT speaker, text FROM dialogue ORDER BY dialogue_id")]
            con.close()
            return result, rows

    def test_a_supplied_speaker_is_removed_and_reported(self):
        result, rows = self._commit([
            {"text": "첫 줄", "speaker": "렘", "startMs": 0, "endMs": 1500},
        ])
        self.assertEqual(result["status"], "SUCCESS")
        self.assertIsNone(rows[0]["speaker"])
        self.assertEqual(rows[0]["text"], "첫 줄")
        self.assertTrue(any("speaker" in w for w in result["warnings"]))

    def test_the_rest_of_the_cut_still_lands(self):
        """Dropping, not refusing: the description and text are fine."""
        result, rows = self._commit([
            {"text": "첫 줄", "speaker": "렘", "startMs": 0, "endMs": 1500},
            {"text": "둘째 줄", "startMs": 1500, "endMs": 3000},
        ])
        self.assertEqual(result["cutCount"], 1)
        self.assertEqual([r["text"] for r in rows], ["첫 줄", "둘째 줄"])
        self.assertEqual([r["speaker"] for r in rows], [None, None])

    def test_no_warning_when_nothing_was_supplied(self):
        result, rows = self._commit([{"text": "첫 줄", "startMs": 0, "endMs": 1500}])
        self.assertEqual(result["warnings"], [])


class TestComputeFileHash(unittest.TestCase):
    def test_same_bytes_hash_identically(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.bin", Path(tmp) / "b.bin"
            a.write_bytes(b"hello world")
            b.write_bytes(b"hello world")
            self.assertEqual(media_ingest.compute_file_hash(a), media_ingest.compute_file_hash(b))

    def test_different_bytes_hash_differently(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.bin", Path(tmp) / "b.bin"
            a.write_bytes(b"hello world")
            b.write_bytes(b"goodbye world")
            self.assertNotEqual(media_ingest.compute_file_hash(a), media_ingest.compute_file_hash(b))

    def test_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            media_ingest.compute_file_hash("Z:/does/not/exist.png")


class TestFilterProjectImages(unittest.TestCase):
    def test_keeps_only_image_extensions(self):
        items = [
            {"name": "a", "mediaPath": "C:/proj/a.png", "binPath": ["Work"]},
            {"name": "b", "mediaPath": "C:/proj/b.mp4", "binPath": ["Work"]},
            {"name": "c", "mediaPath": "C:/proj/c.JPG", "binPath": ["Work"]},  # extension match is case-insensitive
            {"name": "d", "mediaPath": None, "binPath": ["Work"]},
        ]
        filtered = media_ingest.filter_project_images(items)
        self.assertEqual([item["name"] for item in filtered], ["a", "c"])


class TestScanLocalImages(unittest.TestCase):
    def test_walks_nested_folders_and_derives_binpath(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "WorkA" / "CharA").mkdir(parents=True)
            (root / "WorkA" / "CharA" / "1.png").write_bytes(b"x")
            (root / "WorkA" / "CharA" / "notes.txt").write_bytes(b"not an image")
            (root / "top.jpg").write_bytes(b"y")

            items = media_ingest.scan_local_images(root)
            self.assertEqual(len(items), 2)
            nested = next(i for i in items if i["name"] == "1.png")
            self.assertEqual(nested["binPath"], ["WorkA", "CharA"])
            top = next(i for i in items if i["name"] == "top.jpg")
            self.assertEqual(top["binPath"], [])

    def test_missing_root_raises(self):
        with self.assertRaises(NotADirectoryError):
            media_ingest.scan_local_images("Z:/does/not/exist")


class TestPlanPathRelink(unittest.TestCase):
    def test_moved_file_is_matched_by_hash_not_path(self):
        db_rows = [{"mediaId": 1, "filePath": "C:/old/a.png", "fileHash": "h1"}]
        disk = [{"name": "a.png", "mediaPath": "C:/new/school/a.png", "binPath": ["school"], "fileHash": "h1"}]
        plan = media_ingest.plan_path_relink(db_rows, disk)
        self.assertEqual(len(plan["moved"]), 1)
        self.assertEqual(plan["moved"][0]["newPath"], "C:/new/school/a.png")
        self.assertEqual(plan["missing"], [])
        self.assertEqual(plan["unregistered"], [])

    def test_unchanged_path_is_not_reported_as_moved(self):
        db_rows = [{"mediaId": 1, "filePath": "C:/a.png", "fileHash": "h1"}]
        disk = [{"name": "a.png", "mediaPath": "C:/a.png", "binPath": [], "fileHash": "h1"}]
        plan = media_ingest.plan_path_relink(db_rows, disk)
        self.assertEqual(plan["moved"], [])

    def test_row_whose_hash_vanished_is_missing(self):
        db_rows = [{"mediaId": 1, "filePath": "C:/a.png", "fileHash": "gone"}]
        plan = media_ingest.plan_path_relink(db_rows, [])
        self.assertEqual([r["mediaId"] for r in plan["missing"]], [1])

    def test_hashless_row_cannot_be_relinked(self):
        # e.g. a directory-backed bundle row - no bytes to match on.
        db_rows = [{"mediaId": 1, "filePath": "C:/bundle", "fileHash": None}]
        disk = [{"name": "a.png", "mediaPath": "C:/a.png", "binPath": [], "fileHash": "h1"}]
        plan = media_ingest.plan_path_relink(db_rows, disk)
        self.assertEqual([r["mediaId"] for r in plan["missing"]], [1])

    def test_new_file_on_disk_is_unregistered(self):
        db_rows = [{"mediaId": 1, "filePath": "C:/a.png", "fileHash": "h1"}]
        disk = [
            {"name": "a.png", "mediaPath": "C:/a.png", "binPath": [], "fileHash": "h1"},
            {"name": "b.png", "mediaPath": "C:/b.png", "binPath": [], "fileHash": "h2"},
        ]
        plan = media_ingest.plan_path_relink(db_rows, disk)
        self.assertEqual([i["name"] for i in plan["unregistered"]], ["b.png"])


class TestFindUnregisteredMedia(unittest.TestCase):
    def test_hash_not_in_registered_set_is_returned(self):
        items = [
            {"name": "a", "fileHash": "hash-a"},
            {"name": "b", "fileHash": "hash-b"},
        ]
        unregistered = media_ingest.find_unregistered_media(items, {"hash-a"})
        self.assertEqual([item["name"] for item in unregistered], ["b"])

    def test_unhashable_item_is_treated_as_unregistered(self):
        items = [{"name": "broken", "fileHash": None}]
        unregistered = media_ingest.find_unregistered_media(items, {"hash-a"})
        self.assertEqual(len(unregistered), 1)


class TestListMediaFileHashes(unittest.TestCase):
    def test_returns_only_non_null_hashes(self):
        con = _fresh_connection()
        source_id = source_db.get_or_create_source(con, source_category="anime", is_official=True, work_title="W", origin_ref="E1")
        source_db.get_or_create_media(con, media_type="image", file_path="a.png", source_id=source_id, file_hash="hash-a")
        source_db.get_or_create_media(con, media_type="image", file_path="b.png", source_id=source_id, file_hash=None)
        self.assertEqual(source_db.list_media_file_hashes(con), {"hash-a"})


if __name__ == "__main__":
    unittest.main()
