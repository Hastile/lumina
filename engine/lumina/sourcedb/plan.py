"""Candidates for a whole shot plan in one call.

Searching caption by caption cost a tool call, and a fresh look at results,
per line. Here the cut plan goes in at once - per shot the narration it
covers, what should be on screen, and how long it lasts - and each shot gets
its few best cuts back as one line each.

Two rankings are fused per shot (reciprocal rank, k=60):
  - text: BM25 over what the DB says about a cut (descriptions, speech,
    names, file titles, captions it was used under), from narration + look;
  - picture: SigLIP similarity of the look to the cut's keyframe, which
    every cut has, described or not (see textvec for how well it works).
Cuts too short for the shot (below SPEED_MIN of its length after the edge
margins) are dropped: a shot may be slowed to 87% at most, never stretched
further to fit.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import query, textvec
from .query import Filters, Hit

RRF_K = 60
TEXT_POOL = 400     # BM25 ranks kept per shot
FUSED_POOL = 80     # fused candidates hydrated per shot
SPEED_MIN = 0.87    # the slowest a placed shot plays (docs/shorts-editing: 87-105%)
DEFAULT_MIN_LUMA = 0.08


@dataclass
class Shot:
    text: str = ""                     # the narration the shot covers
    look: str = ""                     # what should be on screen, as a picture description
    seconds: Optional[float] = None    # how long the shot lasts on the timeline
    character: Optional[str] = None    # who must be in it (overrides the plan's)
    media_kind: Optional[str] = None   # video | image


def _chunks(seq, n=900):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


class Pool:
    """Every cut the filters allow, with its keyframe vector when it has one."""

    def __init__(self, con, f: Filters):
        ids = list(query._candidates(con, None, f, 10 ** 9))
        rows = []
        for part in _chunks(ids):
            rows += con.execute(f"SELECT cut_id, media_id, keyframe_ticks FROM cut WHERE cut_id IN "
                                f"({','.join('?' * len(part))})", part).fetchall()
        media = sorted({r["media_id"] for r in rows})
        vec = {}
        for part in _chunks(media):
            for e in con.execute(f"SELECT media_id, t_ticks, vec FROM embedding WHERE model = ? AND media_id IN "
                                 f"({','.join('?' * len(part))})", [textvec.MODEL, *part]):
                vec[(e["media_id"], e["t_ticks"])] = e["vec"]
        have = [(r["cut_id"], vec[(r["media_id"], r["keyframe_ticks"])]) for r in rows
                if (r["media_id"], r["keyframe_ticks"]) in vec]
        self.size = len(ids)
        self.cut_ids = np.array([c for c, _ in have], dtype=np.int64)
        self.vecs = (np.stack([np.frombuffer(b, dtype="<f2") for _, b in have]).astype(np.float32)
                     if have else np.zeros((0, 0), np.float32))
        if len(self.vecs):
            self.vecs /= np.maximum(np.linalg.norm(self.vecs, axis=1, keepdims=True), 1e-6)

    def ranked(self, v: np.ndarray, n: int) -> list[int]:
        if not len(self.vecs):
            return []
        sims = self.vecs @ v
        top = np.argsort(-sims)[:n]
        return [int(self.cut_ids[i]) for i in top]


def plan(con, shots: list[Shot], filters: Optional[Filters] = None, *, per_shot: int = 5,
         encoder: Optional[textvec.TextEncoder] = None, exclude_cuts: tuple[int, ...] = (),
         notes: Optional[list[str]] = None) -> list[list[Hit]]:
    base = filters or Filters()
    if base.min_luma is None:
        base = dataclasses.replace(base, min_luma=DEFAULT_MIN_LUMA)
    # A character the DB has no entity for (a new work's cast stays unnamed
    # until a person confirms it) would filter everything out; its name is
    # still in descriptions, so it is searched as text instead.
    unknown: set[str] = set()

    def known(name: str) -> bool:
        if query._entity_ids(con, "character", name):
            return True
        unknown.add(name)
        return False

    everyone = ""   # the plan's character, when it can only be searched as text
    if base.character and not known(base.character):
        everyone, base = base.character, dataclasses.replace(base, character=None)
    shots = [dataclasses.replace(s, character=None, text=f"{s.text} {s.character}".strip())
             if s.character and not known(s.character) else s for s in shots]
    if notes is not None and unknown:
        notes.append(f"no character entity for {', '.join(sorted(unknown))}: searched as text, not filtered")
    looks = [s.look or s.text for s in shots]
    vecs = (encoder or textvec.shared()).encode(looks) if any(looks) else None
    pools: dict[tuple, Pool] = {}
    out = []
    for i, s in enumerate(shots):
        f = dataclasses.replace(
            base, character=s.character or base.character, media_kind=s.media_kind or base.media_kind,
            min_seconds=base.min_seconds if s.seconds is None else max(base.min_seconds or 0, s.seconds * SPEED_MIN))
        key = (f.character, f.media_kind)
        if key not in pools:
            pools[key] = Pool(con, f)
        fused: dict[int, float] = {}
        text = " ".join(t for t in (s.text, s.look, everyone) if t)
        if text:
            by_text = sorted(query._candidates(con, text, f, TEXT_POOL).items(), key=lambda kv: -kv[1])
            for r, (cid, _) in enumerate(by_text):
                fused[cid] = fused.get(cid, 0) + 1 / (RRF_K + r)
        if vecs is not None and looks[i]:
            for r, cid in enumerate(pools[key].ranked(vecs[i], TEXT_POOL)):
                fused[cid] = fused.get(cid, 0) + 1 / (RRF_K + r)
        for cid in exclude_cuts:
            fused.pop(cid, None)
        best = sorted(fused, key=lambda c: -fused[c])[:FUSED_POOL]
        hits = query._hydrate(con, best)
        kept = []
        for cid in best:
            h = hits.get(cid)
            if h is None or not query.passes(h, f):
                continue
            # A cut the person chose before, and a subject that fills the frame,
            # break near-ties; they never outrank a clearly better match.
            h.score = fused[cid] + 0.001 * min(h.used, 3) + 0.002 * (h.subject_area or 0)
            kept.append(h)
        kept.sort(key=lambda h: (-h.score, h.cut_id))
        out.append(kept[:per_shot])
    return out


def render(shots: list[Shot], results: list[list[Hit]], notes: tuple[str, ...] = ()) -> str:
    """S1 2.3s "narration" / look: ... then one line per candidate; a cut
    offered for more than one shot says where else."""
    where: dict[int, list[str]] = {}
    for i, hits in enumerate(results):
        for h in hits:
            where.setdefault(h.cut_id, []).append(f"S{i + 1}")
    lines = list(notes)
    for i, (s, hits) in enumerate(zip(shots, results)):
        head = f"S{i + 1}" + (f" {s.seconds:.1f}s" if s.seconds else "")
        if s.text:
            head += f' "{s.text}"'
        if s.look and s.look != s.text:
            head += f" / look: {s.look}"
        lines.append(head)
        for h in hits:
            also = [w for w in where[h.cut_id] if w != f"S{i + 1}"]
            lines.append("  " + h.line() + (f" (also {','.join(also)})" if also else ""))
        if not hits:
            lines.append("  (no candidates: loosen the filters or record the shot)")
    return "\n".join(lines)


SHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "the narration (captions) this shot covers"},
        "look": {"type": "string", "description": "what should be on screen, written as a Korean picture description: "
                                                  "who, doing what, shot size (e.g. '녹색 머리에 야구모자를 쓴 소녀 클로즈업'). "
                                                  "This drives the picture search; abstract narration finds little."},
        "seconds": {"type": "number", "description": "shot length on the timeline; shorter cuts are dropped"},
        "character": {"type": "string"},
        "media_kind": {"type": "string", "enum": ["video", "image"]},
    },
}


def sheet_tiles(con, results: list[list[Hit]], top: int) -> list[tuple[str, "Path"]]:
    """(label, thumbnail) of each shot's first `top` candidates, labelled S2#123."""
    from .ingest import thumb_path

    tiles = []
    for i, hits in enumerate(results):
        for h in hits[:top]:
            r = con.execute("SELECT c.keyframe_ticks, m.sha256 FROM cut c JOIN media m USING (media_id) WHERE cut_id = ?",
                            (h.cut_id,)).fetchone()
            if r:
                tiles.append((f"S{i + 1}#{h.cut_id}", thumb_path(r["sha256"], r["keyframe_ticks"])))
    return tiles


TOOL_DESCRIPTION = (
    "Candidates for a whole cut plan in one call: write the plan first (shots spanning captions), then pass every "
    "shot with the narration it covers, a Korean picture description of what should be on screen (look), and its "
    "length. Each shot gets per_shot lines (#cut_id, work, span, type, shot, cast, description, [dark/text/x/usedN]); "
    "cuts too short for the shot at 87% speed are dropped. Ranking fuses text search with SigLIP picture search, so "
    "undescribed cuts are found by their look. sheet_top=N adds contact sheets of each shot's first N candidates. "
    "The first call starts the picture encoder (about 15 s).")
