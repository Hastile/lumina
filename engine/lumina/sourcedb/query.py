"""Scene search over the source DB, answered in compact text.

The caller is an AI deciding which shot goes under which narration line, and
everything it reads costs tokens on every later turn. So a hit is one line of
facts - work, span, shot, characters, a short description, measurements and
past use - and pixels are looked at only for the final picks.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import db
from ..timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND

# Hangul syllables, Latin words and digits. Korean has no analyzer in the
# stdlib, so matching is per word with particles stripped (see _terms).
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_STOPWORDS = {"그리고", "그런데", "하지만", "그래서", "이제", "저는", "우리는", "있는", "하는", "이런", "그런"}
# Longest first, so 에서 is tried before 서 or 에.
_PARTICLES = (
    "에서", "으로", "에게", "한테", "부터", "까지", "보다", "처럼", "이나", "라도", "이라",
    "가", "이", "은", "는", "을", "를", "의", "에", "로", "와", "과", "도", "만", "야", "아",
)


def _terms(text: str) -> list[str]:
    """Words of two or more characters, each also without a trailing particle.

    Indexed text says 팜술이 웃는다 while a query says 팜술; a prefix query
    finds the first from the second. The other direction (an indexed bare
    noun, a query with a particle) needs the particle stripped from the query.
    """
    out: dict[str, None] = {}
    for t in _TOKEN_RE.findall(text or ""):
        if len(t) < 2 or t in _STOPWORDS:
            continue
        out.setdefault(t, None)
        for p in _PARTICLES:
            if t.endswith(p) and len(t) - len(p) >= 2:
                out.setdefault(t[: -len(p)], None)
                break
    return list(out)


@dataclass
class Hit:
    cut_id: int
    media_id: int
    kind: str
    path: Optional[str]
    start_ticks: int
    end_ticks: int
    score: float = 0.0
    works: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    source_type: Optional[str] = None
    shot: Optional[str] = None
    description: Optional[str] = None
    speech: Optional[str] = None
    luma: Optional[float] = None
    text_overlay: Optional[float] = None
    subject_x: Optional[float] = None
    subject_area: Optional[float] = None
    used: int = 0
    used_captions: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return (self.end_ticks - self.start_ticks) / S

    def line(self) -> str:
        """About 30 tokens: enough to choose, never a picture."""
        span = (f"{self.start_ticks / S:.1f}-{self.end_ticks / S:.1f}s" if self.kind == "video" else "image")
        bits = [f"#{self.cut_id}", "/".join(self.works) or "?", span]
        for v in (self.source_type, self.shot):
            if v:
                bits.append(v)
        if self.characters:
            bits.append("+".join(self.characters))
        if self.description:
            bits.append(f'"{self.description[:40]}"')
        elif self.speech:
            bits.append(f'speech "{self.speech[:30]}"')
        m = []
        if self.luma is not None and self.luma < 0.15:
            m.append("dark")
        if self.text_overlay is not None and self.text_overlay > 0.2:
            m.append("text")
        if self.subject_x is not None and abs(self.subject_x - 0.5) > 0.2:
            m.append(f"x{self.subject_x:.2f}")
        if self.used:
            m.append(f"used{self.used}")
        if m:
            bits.append("[" + ",".join(m) + "]")
        return " ".join(bits)


@dataclass
class Filters:
    work: Optional[str] = None              # name or alias
    character: Optional[str] = None
    media_kind: Optional[str] = None        # video | image
    source_type: Optional[str] = None
    variant: Optional[str] = None           # original | copy | official | fan
    min_seconds: Optional[float] = None     # usable length after the edge margin
    edge_margin: float = 0.3                # seconds kept clear of each scene edge
    min_luma: Optional[float] = None
    max_text_overlay: Optional[float] = None
    unused_only: bool = False               # never used in any project
    used_only: bool = False                 # used before (approved edits first)
    include_staged: bool = False
    exclude_media: tuple[int, ...] = ()


_MEASURES = ("luma", "text_overlay", "subject_x", "subject_area")


def _visible(f: Filters) -> str:
    st = "('live','staged')" if f.include_staged else "('live')"
    return f"m.status IN {st} AND m.dup_of IS NULL"


def _entity_ids(con: sqlite3.Connection, type_: str, name: str) -> list[int]:
    out = []
    for r in con.execute("SELECT entity_id, name, aliases FROM entity WHERE type = ?", (type_,)):
        names = [r["name"], *[a for a in r["aliases"].split("\n") if a]]
        if any(name.lower() in n.lower() for n in names):
            out.append(r["entity_id"])
    return out


def _candidates(con, text, f: Filters, limit: int) -> dict[int, float]:
    """cut_id -> text relevance (higher is better)."""
    where, args = [_visible(f)], []
    if f.media_kind:
        where.append("m.kind = ?")
        args.append(f.media_kind)
    for type_, name in (("work", f.work), ("character", f.character)):
        if name:
            ids = _entity_ids(con, type_, name)
            if not ids:
                return {}
            where.append(
                f"EXISTS (SELECT 1 FROM fact x WHERE x.media_id = c.media_id AND x.kind = '{type_}' "
                f"AND x.status != 'rejected' AND x.ref_id IN ({','.join('?' * len(ids))}) "
                "AND (x.t0_ticks IS NULL OR (x.t0_ticks <= c.end_ticks AND x.t1_ticks >= c.start_ticks)))"
            )
            args += ids
    for kind, value in (("source_type", f.source_type), ("variant", f.variant)):
        if value:
            where.append(
                f"EXISTS (SELECT 1 FROM fact x WHERE x.media_id = c.media_id AND x.kind = '{kind}' "
                "AND x.value = ? AND x.status != 'rejected')"
            )
            args.append(value)
    if f.exclude_media:
        where.append(f"c.media_id NOT IN ({','.join('?' * len(f.exclude_media))})")
        args += list(f.exclude_media)
    terms = _terms(text) if text else []
    if terms:
        match = " OR ".join(f'"{t}"*' for t in terms)
        rows = con.execute(
            "SELECT c.cut_id, bm25(cut_doc) AS rank FROM cut_doc JOIN cut c ON c.cut_id = cut_doc.rowid "
            f"JOIN media m ON m.media_id = c.media_id WHERE cut_doc MATCH ? AND {' AND '.join(where)} "
            "ORDER BY rank LIMIT ?",
            [match, *args, limit],
        ).fetchall()
        return {r["cut_id"]: -r["rank"] for r in rows}
    rows = con.execute(
        f"SELECT c.cut_id FROM cut c JOIN media m ON m.media_id = c.media_id WHERE {' AND '.join(where)} LIMIT ?",
        [*args, limit],
    ).fetchall()
    return {r["cut_id"]: 0.0 for r in rows}


def _hydrate(con, cut_ids: list[int]) -> dict[int, Hit]:
    if not cut_ids:
        return {}
    q = ",".join("?" * len(cut_ids))
    cuts = con.execute(
        f"SELECT c.cut_id, c.media_id, c.start_ticks, c.end_ticks, m.kind FROM cut c "
        f"JOIN media m ON m.media_id = c.media_id WHERE c.cut_id IN ({q})",
        cut_ids,
    ).fetchall()
    hits = {
        r["cut_id"]: Hit(r["cut_id"], r["media_id"], r["kind"], None, r["start_ticks"], r["end_ticks"])
        for r in cuts
    }
    media_ids = sorted({h.media_id for h in hits.values()})
    mq = ",".join("?" * len(media_ids))
    facts = con.execute(
        f"SELECT f.media_id, f.t0_ticks, f.t1_ticks, f.kind, f.value, f.num, f.producer, f.status, e.name "
        f"FROM fact f LEFT JOIN entity e ON e.entity_id = f.ref_id "
        f"WHERE f.media_id IN ({mq}) AND f.status != 'rejected' "
        f"ORDER BY (f.producer = 'human') DESC, f.status = 'accepted' DESC, f.num DESC",
        media_ids,
    ).fetchall()
    usage = con.execute(
        f"SELECT media_id, src_in_ticks, src_out_ticks, caption, approved FROM usage WHERE media_id IN ({mq})",
        media_ids,
    ).fetchall()
    by_media: dict[int, list[Hit]] = {}
    for h in hits.values():
        by_media.setdefault(h.media_id, []).append(h)
    for f in facts:
        for h in by_media[f["media_id"]]:
            if not db._overlaps(f["t0_ticks"], f["t1_ticks"], h.start_ticks, h.end_ticks):
                continue
            k = f["kind"]
            if k == "work" and f["name"]:
                name = f["name"] if f["status"] == "accepted" else f["name"] + "?"
                if f["name"] not in h.works and name not in h.works:
                    h.works.append(name)
            elif k == "character" and f["name"]:
                # Unconfirmed machine guesses carry a "?" so they are never
                # read as fact (a tagger names characters it was never taught).
                name = f["name"] if f["status"] == "accepted" else f["name"] + "?"
                if f["name"] not in h.characters and name not in h.characters:
                    h.characters.append(name)
            elif k == "source_type" and h.source_type is None:
                h.source_type = f["value"]
            elif k == "shot" and h.shot is None:
                h.shot = f["value"]
            elif k == "description" and h.description is None:
                h.description = f["value"]
            elif k == "speech":
                h.speech = ((h.speech + " ") if h.speech else "") + f["value"]
            elif k in _MEASURES and getattr(h, k) is None:
                setattr(h, k, f["num"])
    for u in usage:
        for h in by_media[u["media_id"]]:
            if db._overlaps(u["src_in_ticks"], u["src_out_ticks"], h.start_ticks, h.end_ticks):
                h.used += 1
                if u["caption"]:
                    h.used_captions.append(u["caption"])
    for h in hits.values():
        h.path = db.media_path(con, h.media_id)
    return hits


def search(
    con: sqlite3.Connection, text: Optional[str] = None, filters: Optional[Filters] = None, *,
    limit: int = 8, pool: int = 400,
) -> list[Hit]:
    f = filters or Filters()
    rel = _candidates(con, text, f, pool)
    hits = _hydrate(con, list(rel))
    out = []
    for cid, h in hits.items():
        usable = h.seconds - 2 * f.edge_margin if h.kind == "video" else float("inf")
        if f.min_seconds is not None and usable < f.min_seconds:
            continue
        if f.min_luma is not None and h.luma is not None and h.luma < f.min_luma:
            continue
        if f.max_text_overlay is not None and h.text_overlay is not None and h.text_overlay > f.max_text_overlay:
            continue
        if f.unused_only and h.used:
            continue
        if f.used_only and not h.used:
            continue
        # Text relevance first; a shot the person already chose for similar
        # words is the next best evidence; a clearly visible subject next.
        h.score = rel[cid] + 0.5 * min(h.used, 3) + (h.subject_area or 0)
        out.append(h)
    out.sort(key=lambda h: (-h.score, h.cut_id))
    return out[:limit]


def similar(con: sqlite3.Connection, cut_id: int, model: str, *, limit: int = 8,
            filters: Optional[Filters] = None) -> list[Hit]:
    """Cuts whose keyframe looks like this cut's keyframe."""
    keys, vecs = db.load_embeddings(con, model)
    if not len(keys):
        return []
    row = con.execute("SELECT media_id, keyframe_ticks FROM cut WHERE cut_id = ?", (cut_id,)).fetchone()
    match = np.where((keys[:, 0] == row["media_id"]) & (keys[:, 1] == row["keyframe_ticks"]))[0]
    if not len(match):
        return []
    v = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-6)
    sims = v @ v[match[0]]
    order = np.argsort(-sims)
    f = filters or Filters()
    picked: list[tuple[int, float]] = []
    for i in order:
        media_id, t = int(keys[i, 0]), int(keys[i, 1])
        c = con.execute(
            f"SELECT c.cut_id FROM cut c JOIN media m ON m.media_id = c.media_id "
            f"WHERE c.media_id = ? AND c.keyframe_ticks = ? AND {_visible(f)}",
            (media_id, t),
        ).fetchone()
        if c and c["cut_id"] != cut_id:
            picked.append((c["cut_id"], float(sims[i])))
        if len(picked) >= limit:
            break
    hits = _hydrate(con, [c for c, _ in picked])
    for c, s in picked:
        hits[c].score = s
    return [hits[c] for c, _ in picked]
