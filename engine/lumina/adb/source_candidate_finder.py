"""Finds source-DB candidates for subtitle-driven edit planning.

Given already-parsed SRT cues (the shape srt_parser.parse_srt_file / pr-mcp's
parse_srt tool returns: dicts with "text", "startMilliseconds", "endMilliseconds"),
searches the source metadata DB (source_db.py) for scene_cut rows whose
dialogue or description text is relevant to what's being said, ranked by
FTS5 bm25.

This module only ranks candidates - it never picks one. Final selection is
left to whichever AI/session is driving edit planning, matching this repo's
existing plan -> approve -> execute convention (see premiere_edit_planning.py
and build_timeline_edit_plan/approve_timeline_edit_plan in pr-mcp.py): return
a candidate pool, don't auto-commit to a decision.

Takes an already-open sqlite3.Connection (see source_db.connect) so it stays
testable against an in-memory DB without touching the real source.db.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Optional

# Matches runs of Latin letters, digits, or Hangul syllables. Korean has no
# morphological analyzer in stdlib, so this is word-level (어절) matching,
# not morpheme-level - good enough for lexical overlap, not stemming-aware.
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

_STOPWORDS = {"그리고", "그런데", "하지만", "그래서", "이제", "저는", "우리는"}


def tokenize(text: str) -> list[str]:
    tokens = [t for t in _TOKEN_RE.findall(text or "") if len(t) > 1]
    seen: dict[str, None] = {}
    for t in tokens:
        if t not in _STOPWORDS:
            seen.setdefault(t, None)
    return list(seen)


# Korean particles, longest first so "에서" is tried before "서"/"에".
#
# Prefix matching alone only solves half the problem, and which half depends
# on which side carries the 조사. An indexed "호시노가" is found by a query for
# "호시노" because the query is a prefix of it. The reverse - a tag indexed as
# the bare noun "시로코", queried from cue text that says "시로코가" - is not,
# because a prefix query can only grow the indexed token, never shorten it.
#
# That reverse case is the normal one for tags: tags are clean nouns while
# queries come from SRT cue text, which is ordinary Korean full of particles.
# Measured, it missed every tag-only match. Stripping a trailing particle and
# searching both forms covers both directions without a morphological
# analyzer, which the stdlib does not have.
_PARTICLES = (
    "에서", "으로", "에게", "한테", "부터", "까지", "보다", "처럼", "이나", "라도", "이라",
    "가", "이", "은", "는", "을", "를", "의", "에", "로", "와", "과", "도", "만", "야", "아",
)


def strip_particle(token: str) -> Optional[str]:
    """The token with a trailing particle removed, or None if there is
    nothing to strip. Never returns a stem shorter than two characters -
    tokenize() drops one-character tokens for the same reason, since a
    single-syllable prefix matches a large share of the vocabulary."""
    for particle in _PARTICLES:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    return None


def expand_query_terms(tokens: list[str]) -> list[str]:
    """Every token, plus its particle-stripped stem where one exists."""
    expanded: dict[str, None] = {}
    for token in tokens:
        expanded.setdefault(token, None)
        stem = strip_particle(token)
        if stem:
            expanded.setdefault(stem, None)
    return list(expanded)


def _fts_match_expr(tokens: list[str]) -> Optional[str]:
    if not tokens:
        return None
    tokens = expand_query_terms(tokens)
    # Quote every term: FTS5's query syntax treats bare tokens containing
    # certain characters as operators, and Hangul syllables are safe but
    # quoting costs nothing and removes an entire class of MATCH syntax errors.
    #
    # The trailing * makes every term a prefix query, which is what makes this
    # usable in Korean at all. FTS5's default tokenizer splits on whitespace,
    # so an indexed "호시노가 자고 있다" holds the token 호시노가 - with exact
    # matching, a search for 호시노 misses it, and every other 조사-suffixed
    # occurrence (호시노와/호시노를/호시노의) too. Measured against the real
    # source.db that cost ~92% recall on character names. Since 조사 attach as
    # suffixes, prefix matching recovers them without needing a morphological
    # analyzer. tokenize() already drops 1-character tokens, so this never
    # degenerates into a single-syllable prefix that matches everything.
    return " OR ".join(f'"{t}"*' for t in tokens)


# Rows a search must never return, enforced in SQL rather than by filtering
# afterwards so every query path gets it for free:
#   - canonical_media_id IS NOT NULL: the same picture in another format,
#     already represented by the row it points at (see merge_media_into).
#   - availability = 'pending': described but not yet acquired, so there is
#     no file to place if a caller picks it.
_MEDIA_VISIBILITY = "m.canonical_media_id IS NULL AND m.availability = 'available'"


def _run_fts_query(con: sqlite3.Connection, match_expr: str, limit: int) -> list[sqlite3.Row]:
    description_rows = con.execute(
        f"""
        SELECT sc.cut_id, sc.media_id, sc.description_text, sc.shot_type,
               sc.start_ms, sc.end_ms,
               m.file_path, m.media_type, m.path_kind,
               s.work_title, s.source_category, s.is_official,
               bm25(cut_search) AS rank, 'description' AS matched_field
        FROM cut_search
        JOIN scene_cut sc ON sc.cut_id = cut_search.rowid
        JOIN media m ON m.media_id = sc.media_id
        JOIN source s ON s.source_id = m.source_id
        WHERE cut_search MATCH ? AND {_MEDIA_VISIBILITY}
        ORDER BY rank
        LIMIT ?
        """,
        (match_expr, limit),
    ).fetchall()

    dialogue_rows = con.execute(
        f"""
        SELECT sc.cut_id, sc.media_id, sc.description_text, sc.shot_type,
               sc.start_ms, sc.end_ms,
               m.file_path, m.media_type, m.path_kind,
               s.work_title, s.source_category, s.is_official,
               bm25(dialogue_search) AS rank, 'dialogue' AS matched_field
        FROM dialogue_search
        JOIN dialogue d ON d.dialogue_id = dialogue_search.rowid
        JOIN scene_cut sc ON sc.cut_id = d.cut_id
        JOIN media m ON m.media_id = sc.media_id
        JOIN source s ON s.source_id = m.source_id
        WHERE dialogue_search MATCH ? AND {_MEDIA_VISIBILITY}
        ORDER BY rank
        LIMIT ?
        """,
        (match_expr, limit),
    ).fetchall()

    # Tags are the third index, and for one class of query they are the only
    # one that works: character names are attached as `subject` tags - by the
    # WD tagger, by the booru hits reverse search returns, by the exemplar
    # matcher - and never reliably written into the prose. Measured against
    # the real source.db before this query existed, "시로코" and "아리스"
    # matched nothing at all while both were tagged on several cuts.
    tag_rows = con.execute(
        f"""
        SELECT sc.cut_id, sc.media_id, sc.description_text, sc.shot_type,
               sc.start_ms, sc.end_ms,
               m.file_path, m.media_type, m.path_kind,
               s.work_title, s.source_category, s.is_official,
               bm25(tag_search) AS rank, 'tag' AS matched_field
        FROM tag_search
        JOIN scene_cut sc ON sc.cut_id = tag_search.rowid
        JOIN media m ON m.media_id = sc.media_id
        JOIN source s ON s.source_id = m.source_id
        WHERE tag_search MATCH ? AND {_MEDIA_VISIBILITY}
        ORDER BY rank
        LIMIT ?
        """,
        (match_expr, limit),
    ).fetchall()

    # The work the cut belongs to. One source spans many cuts, so this branch
    # is the broadest of the four and the least specific - matching a title
    # says nothing about what is in the frame. It earns its place because the
    # documented way to name a shot starts with the work, and because the
    # combined-evidence bonus below is what separates "this cut is from that
    # show AND shows that character" from "this cut is from that show".
    work_rows = con.execute(
        f"""
        SELECT sc.cut_id, sc.media_id, sc.description_text, sc.shot_type,
               sc.start_ms, sc.end_ms,
               m.file_path, m.media_type, m.path_kind,
               s.work_title, s.source_category, s.is_official,
               bm25(source_search) AS rank, 'work' AS matched_field
        FROM source_search
        JOIN source s ON s.source_id = source_search.rowid
        JOIN media m ON m.source_id = s.source_id
        JOIN scene_cut sc ON sc.media_id = m.media_id
        WHERE source_search MATCH ? AND {_MEDIA_VISIBILITY}
        ORDER BY rank
        LIMIT ?
        """,
        (match_expr, limit),
    ).fetchall()

    return (
        list(description_rows) + list(dialogue_rows) + list(tag_rows) + list(work_rows)
    )


def find_candidates_for_text(
    con: sqlite3.Connection, *, query_text: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Ranked scene_cut candidates for a single piece of text (e.g. one
    subtitle cue). bm25 is lower-is-better in sqlite; a cut matched by more
    than one kind of evidence gets a small combined-evidence bonus."""
    tokens = tokenize(query_text)
    match_expr = _fts_match_expr(tokens)
    if match_expr is None:
        return []

    rows = _run_fts_query(con, match_expr, limit=max(limit * 3, limit))

    merged: dict[int, dict[str, Any]] = {}
    for row in rows:
        cut_id = row["cut_id"]
        field_score = -float(row["rank"])  # flip so higher = better
        existing = merged.get(cut_id)
        if existing is None:
            merged[cut_id] = {
                "cutId": cut_id,
                "mediaId": row["media_id"],
                "filePath": row["file_path"],
                "mediaType": row["media_type"],
                "workTitle": row["work_title"],
                "sourceCategory": row["source_category"],
                "isOfficial": bool(row["is_official"]),
                "shotType": row["shot_type"],
                "startMs": row["start_ms"],
                "endMs": row["end_ms"],
                "pathKind": row["path_kind"],
                "descriptionText": row["description_text"],
                "matchedFields": [row["matched_field"]],
                "score": field_score,
            }
        else:
            existing["matchedFields"].append(row["matched_field"])
            existing["score"] = max(existing["score"], field_score)

    for candidate in merged.values():
        if len(set(candidate["matchedFields"])) > 1:
            # Two different kinds of evidence agreeing (description, dialogue,
            # tags, work) beats one of them scoring well alone.
            candidate["score"] *= 1.2

    ranked = sorted(merged.values(), key=lambda c: c["score"], reverse=True)
    return ranked[:limit]


def find_candidates_for_cues(
    con: sqlite3.Connection, cues: list[dict[str, Any]], *, limit_per_cue: int = 5
) -> list[dict[str, Any]]:
    """Runs find_candidates_for_text per SRT cue. Input cues follow
    srt_parser's shape: {"text", "startMilliseconds", "endMilliseconds", ...}."""
    results = []
    for cue in cues:
        text = cue.get("text", "")
        candidates = find_candidates_for_text(con, query_text=text, limit=limit_per_cue)
        results.append(
            {
                "cueIndex": cue.get("index"),
                "startMilliseconds": cue.get("startMilliseconds"),
                "endMilliseconds": cue.get("endMilliseconds"),
                "text": text,
                "candidateCount": len(candidates),
                "candidates": candidates,
            }
        )
    return results
