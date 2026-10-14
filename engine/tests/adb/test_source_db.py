from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


import lumina.adb.source_db as source_db
from lumina.adb.source_candidate_finder import (
    expand_query_terms, find_candidates_for_cues, find_candidates_for_text,
    strip_particle, tokenize,
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "lumina" / "adb" / "database" / "schema.sql"


def _fresh_connection() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return con


class TestSourceDbHelpers(unittest.TestCase):
    def test_get_or_create_source_is_idempotent(self):
        con = _fresh_connection()
        first = source_db.get_or_create_source(
            con, source_category="anime", is_official=True,
            work_title="테스트 작품", origin_ref="EP01",
        )
        second = source_db.get_or_create_source(
            con, source_category="anime", is_official=True,
            work_title="테스트 작품", origin_ref="EP01",
        )
        self.assertEqual(first, second)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM source").fetchone()[0], 1)

    def test_get_or_create_media_dedupes_by_file_hash(self):
        con = _fresh_connection()
        source_id = source_db.get_or_create_source(
            con, source_category="anime", is_official=True, work_title="W", origin_ref="E1",
        )
        m1 = source_db.get_or_create_media(
            con, media_type="video", file_path="a.mp4", source_id=source_id,
            file_hash="abc123", duration_ms=5000, fps=24.0,
        )
        m2 = source_db.get_or_create_media(
            con, media_type="video", file_path="a.mp4", source_id=source_id,
            file_hash="abc123", duration_ms=5000, fps=24.0,
        )
        self.assertEqual(m1, m2)

    def test_merge_media_into_resolves_to_canonical(self):
        con = _fresh_connection()
        source_id = source_db.get_or_create_source(
            con, source_category="game", is_official=True, work_title="W", origin_ref="E1",
        )
        png = source_db.get_or_create_media(
            con, media_type="image", file_path="a.png", source_id=source_id, file_hash="h-png",
        )
        webp = source_db.get_or_create_media(
            con, media_type="image", file_path="a.webp", source_id=source_id, file_hash="h-webp",
        )
        # Same picture, different encoding - different hashes, so the hash
        # dedupe in get_or_create_media cannot catch it.
        self.assertNotEqual(png, webp)
        source_db.merge_media_into(con, duplicate_media_id=webp, canonical_media_id=png)
        self.assertEqual(source_db.resolve_media_id(con, webp), png)
        self.assertEqual(source_db.resolve_media_id(con, png), png)

    def test_media_cannot_be_its_own_canonical(self):
        con = _fresh_connection()
        source_id = source_db.get_or_create_source(
            con, source_category="game", is_official=True, work_title="W", origin_ref="E1",
        )
        media_id = source_db.get_or_create_media(
            con, media_type="image", file_path="a.png", source_id=source_id,
        )
        with self.assertRaises(ValueError):
            source_db.merge_media_into(con, duplicate_media_id=media_id, canonical_media_id=media_id)

    def test_availability_and_path_kind_reject_unknown_values(self):
        con = _fresh_connection()
        source_id = source_db.get_or_create_source(
            con, source_category="game", is_official=True, work_title="W", origin_ref="E1",
        )
        with self.assertRaises(ValueError):
            source_db.get_or_create_media(
                con, media_type="image", file_path="a.png", source_id=source_id, availability="maybe",
            )
        with self.assertRaises(ValueError):
            source_db.get_or_create_media(
                con, media_type="image", file_path="a.png", source_id=source_id, path_kind="symlink",
            )

    def test_upsert_cut_overwrites_in_place(self):
        con = _fresh_connection()
        source_id = source_db.get_or_create_source(
            con, source_category="anime", is_official=True, work_title="W", origin_ref="E1",
        )
        media_id = source_db.get_or_create_media(
            con, media_type="video", file_path="a.mp4", source_id=source_id,
        )
        cut_id_1 = source_db.upsert_cut(
            con, media_id=media_id, cut_index=0, start_ms=0, end_ms=1000,
            description_text="초안 설명",
        )
        cut_id_2 = source_db.upsert_cut(
            con, media_id=media_id, cut_index=0, start_ms=0, end_ms=1200,
            description_text="개선된 설명",
        )
        self.assertEqual(cut_id_1, cut_id_2)
        row = con.execute(
            "SELECT description_text, end_ms FROM scene_cut WHERE cut_id = ?", (cut_id_1,)
        ).fetchone()
        self.assertEqual(row["description_text"], "개선된 설명")
        self.assertEqual(row["end_ms"], 1200)

    def test_canonical_tag_chain_resolution(self):
        con = _fresh_connection()
        run_id = source_db.get_or_create_tag(con, category="action", value="달리다")
        synonym_id = source_db.get_or_create_tag(con, category="action", value="뛰다")
        source_db.merge_tag_into(con, synonym_tag_id=synonym_id, canonical_tag_id=run_id)
        self.assertEqual(source_db.resolve_tag_id(con, synonym_id), run_id)
        self.assertEqual(source_db.resolve_tag_id(con, run_id), run_id)


class TestSourceCandidateFinder(unittest.TestCase):
    def _seed(self, con: sqlite3.Connection) -> dict[str, int]:
        source_id = source_db.get_or_create_source(
            con, source_category="anime", is_official=True,
            work_title="비 오는 마을", origin_ref="EP03",
        )
        media_id = source_db.get_or_create_media(
            con, media_type="video", file_path="rain_scene.mp4", source_id=source_id,
            duration_ms=8000, fps=24.0,
        )
        cut_rain = source_db.upsert_cut(
            con, media_id=media_id, cut_index=0, start_ms=0, end_ms=4000,
            shot_type="wide", description_text="주인공이 비 내리는 골목을 혼자 걸어간다",
        )
        source_db.insert_dialogue(
            con, cut_id=cut_rain, text="비가 그치질 않네", speaker="주인공",
            start_ms=500, end_ms=2000,
        )
        source_db.tag_cut_by_value(con, cut_id=cut_rain, category="mood", value="쓸쓸함")
        source_db.tag_cut_by_value(con, cut_id=cut_rain, category="use_case", value="이별_장면")

        cut_sunny = source_db.upsert_cut(
            con, media_id=media_id, cut_index=1, start_ms=4000, end_ms=8000,
            shot_type="close", description_text="해가 뜨자 주인공이 웃으며 뛰어간다",
        )
        source_db.insert_dialogue(
            con, cut_id=cut_sunny, text="드디어 날이 갰다!", speaker="주인공",
            start_ms=4200, end_ms=5000,
        )
        source_db.tag_cut_by_value(con, cut_id=cut_sunny, category="mood", value="희망")
        return {"cut_rain": cut_rain, "cut_sunny": cut_sunny}

    def test_tokenize_drops_short_tokens_and_stopwords(self):
        tokens = tokenize("그리고 비가 계속 내린다")
        self.assertNotIn("그리고", tokens)
        self.assertIn("내린다", tokens)

    def test_finds_matching_cut_by_dialogue_text(self):
        con = _fresh_connection()
        cuts = self._seed(con)
        candidates = find_candidates_for_text(con, query_text="비가 그치질 않네요", limit=5)
        self.assertGreater(len(candidates), 0)
        self.assertEqual(candidates[0]["cutId"], cuts["cut_rain"])
        self.assertIn("dialogue", candidates[0]["matchedFields"])

    def test_finds_matching_cut_by_description_text(self):
        con = _fresh_connection()
        cuts = self._seed(con)
        candidates = find_candidates_for_text(con, query_text="웃으며 뛰어가는 장면", limit=5)
        self.assertGreater(len(candidates), 0)
        self.assertEqual(candidates[0]["cutId"], cuts["cut_sunny"])

    def test_bare_noun_matches_particle_suffixed_occurrence(self):
        # The indexed text holds "주인공이"; FTS5's tokenizer keeps the 조사
        # attached, so exact matching would miss a search for 주인공. Prefix
        # matching is what makes bare-noun search work in Korean at all.
        con = _fresh_connection()
        cuts = self._seed(con)
        candidates = find_candidates_for_text(con, query_text="주인공", limit=5)
        self.assertEqual({c["cutId"] for c in candidates}, {cuts["cut_rain"], cuts["cut_sunny"]})

    def test_duplicate_format_media_is_hidden_from_search(self):
        con = _fresh_connection()
        source_id = source_db.get_or_create_source(
            con, source_category="game", is_official=True, work_title="W", origin_ref="E1",
        )
        png = source_db.get_or_create_media(
            con, media_type="image", file_path="a.png", source_id=source_id, file_hash="h-png",
        )
        webp = source_db.get_or_create_media(
            con, media_type="image", file_path="a.webp", source_id=source_id, file_hash="h-webp",
        )
        for media_id in (png, webp):
            source_db.upsert_cut(
                con, media_id=media_id, cut_index=0, start_ms=0, end_ms=0,
                description_text="눈 내리는 옥상에서 총을 든 주인공",
            )
        self.assertEqual(len(find_candidates_for_text(con, query_text="옥상", limit=10)), 2)
        source_db.merge_media_into(con, duplicate_media_id=webp, canonical_media_id=png)
        hits = find_candidates_for_text(con, query_text="옥상", limit=10)
        self.assertEqual([h["filePath"] for h in hits], ["a.png"])

    def test_pending_media_is_hidden_from_search(self):
        con = _fresh_connection()
        source_id = source_db.get_or_create_source(
            con, source_category="game", is_official=True, work_title="W", origin_ref="E1",
        )
        media_id = source_db.get_or_create_media(
            con, media_type="image", file_path="PENDING://아직 못 구한 장면",
            source_id=source_id, availability="pending",
        )
        source_db.upsert_cut(
            con, media_id=media_id, cut_index=0, start_ms=0, end_ms=0,
            description_text="눈 내리는 옥상에서 총을 든 주인공",
        )
        # Describable but unplaceable: it must never come back as a candidate.
        self.assertEqual(find_candidates_for_text(con, query_text="옥상", limit=10), [])

    def test_no_match_returns_empty_list(self):
        con = _fresh_connection()
        self._seed(con)
        candidates = find_candidates_for_text(con, query_text="완전히 무관한 우주여행 로켓", limit=5)
        self.assertEqual(candidates, [])

    def test_find_candidates_for_cues_preserves_cue_order_and_timing(self):
        con = _fresh_connection()
        self._seed(con)
        cues = [
            {"index": 1, "startMilliseconds": 0, "endMilliseconds": 2000, "text": "비가 그치질 않네"},
            {"index": 2, "startMilliseconds": 2000, "endMilliseconds": 4000, "text": "해가 뜨고 웃는다"},
        ]
        results = find_candidates_for_cues(con, cues, limit_per_cue=3)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["cueIndex"], 1)
        self.assertEqual(results[0]["startMilliseconds"], 0)
        self.assertGreater(results[0]["candidateCount"], 0)



class TestTagSearch(unittest.TestCase):
    """Tags as a retrieval index.

    The case this exists for: character names are attached as `subject` tags
    - by the WD tagger, by the booru hits reverse search returns, by the
      exemplar matcher - and are not reliably written into the prose. Before
    tag_search, a search for a character matched only the cuts where the name
    happened to reach the description, which on the real source.db meant
    several tagged characters matched nothing at all.
    """

    def _seed(self, con: sqlite3.Connection) -> dict[str, int]:
        source_id = source_db.get_or_create_source(
            con, source_category="anime", is_official=True,
            work_title="어느 학원", origin_ref="EP01",
        )
        media_id = source_db.get_or_create_media(
            con, media_type="video", file_path="school.mp4", source_id=source_id,
            duration_ms=6000, fps=24.0,
        )
        # Deliberately describes the action without naming anyone, which is
        # what the pipeline does whenever the name is not exemplar-confirmed.
        named = source_db.upsert_cut(
            con, media_id=media_id, cut_index=0, start_ms=0, end_ms=3000,
            shot_type="medium", description_text="여학생이 총을 들고 난간 위에 서 있다",
        )
        source_db.tag_cut_by_value(con, cut_id=named, category="subject", value="스나오오카미 시로코")

        other = source_db.upsert_cut(
            con, media_id=media_id, cut_index=1, start_ms=3000, end_ms=6000,
            shot_type="wide", description_text="교실에서 학생들이 떠들고 있다",
        )
        source_db.tag_cut_by_value(con, cut_id=other, category="setting", value="교실")
        return {"named": named, "other": other}

    def test_a_character_only_ever_recorded_as_a_tag_is_findable(self):
        con = _fresh_connection()
        cuts = self._seed(con)
        candidates = find_candidates_for_text(con, query_text="시로코가 나오는 장면", limit=5)
        self.assertEqual(candidates[0]["cutId"], cuts["named"])
        self.assertIn("tag", candidates[0]["matchedFields"])

    def test_the_name_is_matched_by_prefix_like_every_other_korean_term(self):
        """조사 stay attached to the query term, so exact matching would miss
        '시로코가' against the tag '스나오오카미 시로코'."""
        con = _fresh_connection()
        cuts = self._seed(con)
        for query in ("시로코", "시로코가", "시로코는"):
            candidates = find_candidates_for_text(con, query_text=f"{query} 등장", limit=5)
            self.assertEqual(candidates[0]["cutId"], cuts["named"], query)

    def test_a_tag_hit_does_not_drag_in_unrelated_cuts(self):
        con = _fresh_connection()
        cuts = self._seed(con)
        candidates = find_candidates_for_text(con, query_text="시로코", limit=5)
        self.assertEqual([c["cutId"] for c in candidates], [cuts["named"]])

    def test_matching_a_tag_and_the_description_scores_above_either_alone(self):
        con = _fresh_connection()
        cuts = self._seed(con)
        candidates = find_candidates_for_text(con, query_text="교실 학생들", limit=5)
        top = candidates[0]
        self.assertEqual(top["cutId"], cuts["other"])
        self.assertEqual({"description", "tag"}, set(top["matchedFields"]))

    def test_removing_a_tag_removes_it_from_search(self):
        con = _fresh_connection()
        cuts = self._seed(con)
        con.execute("DELETE FROM cut_tag WHERE cut_id = ?", (cuts["named"],))
        con.commit()
        self.assertEqual(find_candidates_for_text(con, query_text="시로코", limit=5), [])

    def test_a_cut_keeps_its_other_tags_when_one_is_removed(self):
        """The trigger rebuilds the whole document for that cut, so a partial
        rebuild would silently drop the survivors."""
        con = _fresh_connection()
        cuts = self._seed(con)
        source_db.tag_cut_by_value(con, cut_id=cuts["named"], category="setting", value="옥상")
        tag_id = source_db.get_or_create_tag(con, category="setting", value="옥상")
        con.execute("DELETE FROM cut_tag WHERE cut_id = ? AND tag_id = ?", (cuts["named"], tag_id))
        con.commit()
        candidates = find_candidates_for_text(con, query_text="시로코", limit=5)
        self.assertEqual(candidates[0]["cutId"], cuts["named"])

    def test_a_merged_synonym_stays_findable_under_both_names(self):
        """merge_tag_into leaves cut_tag pointing at the synonym and resolves
        at read time, so indexing only the stored value would make the
        canonical name miss every cut tagged before the merge."""
        con = _fresh_connection()
        cuts = self._seed(con)
        synonym = source_db.get_or_create_tag(con, category="subject", value="시로")
        canonical = source_db.get_or_create_tag(con, category="subject", value="스나오오카미 시로코")
        source_db.tag_cut(con, cut_id=cuts["other"], tag_id=synonym)
        source_db.merge_tag_into(con, synonym_tag_id=synonym, canonical_tag_id=canonical)
        con.commit()
        found = {c["cutId"] for c in find_candidates_for_text(con, query_text="시로코", limit=5)}
        self.assertIn(cuts["other"], found)

    def test_a_pending_source_is_still_excluded_from_tag_hits(self):
        """The visibility rule has to hold on every query path, not just the
        two that existed before."""
        con = _fresh_connection()
        cuts = self._seed(con)
        con.execute(
            "UPDATE media SET availability = 'pending' WHERE media_id = "
            "(SELECT media_id FROM scene_cut WHERE cut_id = ?)", (cuts["named"],)
        )
        con.commit()
        self.assertEqual(find_candidates_for_text(con, query_text="시로코", limit=5), [])


class TestWorkTitleSearch(unittest.TestCase):
    """The work a cut belongs to lives on its source row, which no index
    reached: a cut from 이세계 콰르텟 was unreachable by searching "콰르텟"
    even though CLAUDE.md's shot-naming rule starts every query with the
    work. See migration_011_source_fts.sql."""

    def _seed(self, con):
        quartet = source_db.get_or_create_source(
            con, source_category="anime", is_official=True,
            work_title="이세계 콰르텟", origin_ref="q",
        )
        q_media = source_db.get_or_create_media(
            con, media_type="video", file_path="q.mp4", source_id=quartet, file_hash="h-q",
        )
        q_cut = source_db.upsert_cut(
            con, media_id=q_media, cut_index=0, start_ms=0, end_ms=3000,
            shot_type="medium", description_text="칠판 앞에서 지시봉을 든 인물",
        )
        source_db.tag_cut_by_value(
            con, cut_id=q_cut, category="character", value="로즈월 L. 메이더스"
        )

        other = source_db.get_or_create_source(
            con, source_category="anime", is_official=True,
            work_title="장송의 프리렌", origin_ref="f",
        )
        f_media = source_db.get_or_create_media(
            con, media_type="video", file_path="f.mp4", source_id=other, file_hash="h-f",
        )
        f_cut = source_db.upsert_cut(
            con, media_id=f_media, cut_index=0, start_ms=0, end_ms=3000,
            shot_type="wide", description_text="숲을 걷는 장면",
        )
        return {"quartet": q_cut, "other": f_cut, "quartet_source": quartet}

    def test_a_cut_is_findable_by_its_work_title_alone(self):
        con = _fresh_connection()
        cuts = self._seed(con)
        candidates = find_candidates_for_text(con, query_text="콰르텟", limit=5)
        self.assertEqual([c["cutId"] for c in candidates], [cuts["quartet"]])
        self.assertIn("work", candidates[0]["matchedFields"])

    def test_a_work_hit_does_not_drag_in_other_works(self):
        con = _fresh_connection()
        cuts = self._seed(con)
        candidates = find_candidates_for_text(con, query_text="프리렌", limit=5)
        self.assertEqual([c["cutId"] for c in candidates], [cuts["other"]])

    def test_the_work_and_a_character_together_count_as_two_kinds(self):
        """Work alone is weak evidence; the combined-evidence bonus is what
        makes "that show AND that character" outrank "that show"."""
        con = _fresh_connection()
        cuts = self._seed(con)
        candidates = find_candidates_for_text(con, query_text="콰르텟 로즈월", limit=5)
        top = candidates[0]
        self.assertEqual(top["cutId"], cuts["quartet"])
        self.assertEqual({"work", "tag"}, set(top["matchedFields"]))

    def test_renaming_a_work_moves_its_cuts_in_the_index(self):
        con = _fresh_connection()
        cuts = self._seed(con)
        con.execute(
            "UPDATE source SET work_title = ? WHERE source_id = ?",
            ("이세계 사중주", cuts["quartet_source"]),
        )
        con.commit()
        self.assertEqual(find_candidates_for_text(con, query_text="콰르텟", limit=5), [])
        found = find_candidates_for_text(con, query_text="사중주", limit=5)
        self.assertEqual([c["cutId"] for c in found], [cuts["quartet"]])


class TestParticleStripping(unittest.TestCase):
    """Prefix matching only grows an indexed token, never shortens it, so it
    solves the 조사 problem in one direction only. These cover the other."""

    def test_a_trailing_particle_is_stripped(self):
        self.assertEqual(strip_particle("시로코가"), "시로코")
        self.assertEqual(strip_particle("교실에서"), "교실")
        self.assertEqual(strip_particle("호시노는"), "호시노")

    def test_the_longest_particle_wins(self):
        """'에서' has to be tried before '서' and '에', or the stem keeps a
        stray syllable."""
        self.assertEqual(strip_particle("복도에서"), "복도")

    def test_a_word_with_nothing_to_strip_is_left_alone(self):
        self.assertIsNone(strip_particle("장면"))
        self.assertIsNone(strip_particle("classroom"))

    def test_stripping_never_produces_a_single_syllable_stem(self):
        """tokenize drops one-character tokens because a single-syllable
        prefix matches a large share of the vocabulary; the stems have to
        respect the same floor."""
        self.assertIsNone(strip_particle("비가"))
        self.assertIsNone(strip_particle("눈은"))

    def test_both_forms_are_searched(self):
        terms = expand_query_terms(["시로코가", "장면"])
        self.assertIn("시로코가", terms)
        self.assertIn("시로코", terms)
        self.assertIn("장면", terms)

    def test_the_original_direction_still_works(self):
        """An indexed '호시노가' found by a query for '호시노' - the case
        prefix matching already covered, which must not regress."""
        con = _fresh_connection()
        source_id = source_db.get_or_create_source(
            con, source_category="anime", is_official=True, work_title="w", origin_ref="EP1")
        media_id = source_db.get_or_create_media(
            con, media_type="video", file_path="a.mp4", source_id=source_id,
            duration_ms=1000, fps=24.0)
        cut = source_db.upsert_cut(
            con, media_id=media_id, cut_index=0, start_ms=0, end_ms=1000,
            shot_type="close", description_text="호시노가 자고 있다")
        found = find_candidates_for_text(con, query_text="호시노", limit=5)
        self.assertEqual(found[0]["cutId"], cut)

if __name__ == "__main__":
    unittest.main()
