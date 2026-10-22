"""The source DB: connection, identity, and the write rules of schema.sql.

Every write goes through here so the rules hold everywhere: a producer
replaces only its own rows, a rejected fact is never re-added, a person's
fact is never replaced, and a cut's search document follows its facts.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import numpy as np

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
LEXICON_PATH = Path(__file__).with_name("lexicon_danbooru.tsv")
SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path.home() / ".lumina" / "source.db"

HUMAN = "human"

# (kind, value_type, searchable, note). Adding a kind means adding a line
# here; connect() inserts the missing ones. Never rename or remove one that
# rows may use.
KINDS: tuple[tuple[str, str, int, str], ...] = (
    # who and what
    ("work", "ref", 1, "the work (game, anime, film) it is from"),
    ("character", "ref", 1, "a character on screen; num = confidence"),
    ("person", "ref", 1, "a real person: voice actor, developer, streamer"),
    ("org", "ref", 1, "a company, studio, publisher or channel"),
    # where it came from
    ("source_type", "text", 1, "trailer, pv, mv, gameplay, key_visual, illustration, fanart, "
                               "design_sheet, screenshot, sns_post, article, store_page, "
                               "search_result, recording, meme, logo"),
    ("variant", "text", 1, "original, copy (an imitation), official, fan"),
    ("title", "text", 1, "the original title of the video, post or file"),
    ("credit", "text", 1, "author or account that made or posted it"),
    ("origin_url", "text", 0, "where it was fetched from"),
    ("episode", "text", 1, "episode, chapter or arc"),
    # what is on screen
    ("description", "text", 1, "one line on what happens in the shot"),
    ("shot", "text", 1, "extreme_close, close, medium, wide, insert, ui"),
    ("subject", "text", 1, "who or what is shown (lexicon category)"),
    ("action", "text", 1, "what they do (lexicon category)"),
    ("mood", "text", 1, "the feeling of the shot (lexicon category)"),
    ("setting", "text", 1, "where it takes place (lexicon category)"),
    ("condition", "text", 1, "state of a subject: wet, injured, ... (lexicon category)"),
    ("time_of_day", "text", 1, "day, night, sunset (lexicon category)"),
    ("use_case", "text", 1, "the kind of narration line it fits"),
    ("speech", "text", 1, "spoken or sung words, with their span"),
    ("ocr", "text", 1, "text visible in the frame"),
    ("note", "text", 1, "a person's memo"),
    # raw model output kept for re-mapping, not searched
    ("wd_tag", "text", 0, "a WD tagger tag; num = score"),
    ("rating", "text", 0, "general, sensitive, questionable, explicit; num = score"),
    # measurements, 0..1
    ("luma", "num", 0, "mean luminance of the keyframe"),
    ("text_overlay", "num", 0, "share of the frame covered by overlaid text"),
    ("subject_x", "num", 0, "horizontal centre of the main subject"),
    ("subject_area", "num", 0, "share of the frame the main subject fills"),
    ("motion", "num", 0, "how much the picture moves within the cut"),
    ("hidden", "num", 0, "1 = do not offer this span"),
)


class SchemaMismatch(RuntimeError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_sql(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"\s+", " ", sql).strip().rstrip(";").strip()


def _schema_statements(sql: str) -> list[str]:
    return [s for s in (_normalize_sql(p) for p in sql.split(";")) if s]


def _fingerprint(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'cut_doc_%' "
        "ORDER BY type, name"
    ).fetchall()
    return sorted(_normalize_sql(r[0]) for r in rows)


def expected_fingerprint() -> list[str]:
    mem = sqlite3.connect(":memory:")
    mem.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        return _fingerprint(mem)
    finally:
        mem.close()


def connect(path: "str | Path" = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the DB, creating it if new; refuse one whose schema has drifted."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        if con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
            raise SchemaMismatch(f"{path} has tables but no version; not a source DB")
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        raise SchemaMismatch(f"{path} is version {version}, expected {SCHEMA_VERSION}")
    if _fingerprint(con) != expected_fingerprint():
        raise SchemaMismatch(f"{path} does not match schema.sql")
    if str(path) != ":memory:":
        con.execute("PRAGMA journal_mode = WAL")
    con.executemany(
        "INSERT OR IGNORE INTO fact_kind (kind, value_type, searchable, note) VALUES (?, ?, ?, ?)",
        KINDS,
    )
    # OR IGNORE: a row someone changed in the DB (re-mapped, rejected) wins
    # over the shipped file.
    con.executemany(
        "INSERT OR IGNORE INTO lexicon (src, src_tag, kind, value, origin) VALUES ('danbooru', ?, ?, ?, ?)",
        _shipped_lexicon(),
    )
    con.commit()
    return con


def _shipped_lexicon() -> list[tuple]:
    rows = []
    for line in LEXICON_PATH.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            tag, kind, value, origin = line.split("\t")
            rows.append((tag, kind or None, value or None, origin))
    return rows


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

def sha256_file(path: "str | Path", chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def register_file(
    con: sqlite3.Connection, path: "str | Path", kind: str, *, probe: Optional[dict] = None
) -> tuple[int, bool]:
    """Return (media_id, is_new_content). Unchanged paths are not re-hashed.

    probe: width, height, duration_ticks, fps_num, fps_den for new media.
    """
    path = str(Path(path).resolve())
    st = os.stat(path)
    mtime = int(st.st_mtime * 1000)
    row = con.execute("SELECT media_id, size, mtime_ms FROM media_file WHERE path = ?", (path,)).fetchone()
    if row and row["size"] == st.st_size and row["mtime_ms"] == mtime:
        con.execute("UPDATE media_file SET seen_ms = ? WHERE path = ?", (now_ms(), path))
        return row["media_id"], False
    digest = sha256_file(path)
    found = con.execute("SELECT media_id FROM media WHERE sha256 = ?", (digest,)).fetchone()
    is_new = found is None
    if is_new:
        p = probe or {}
        media_id = con.execute(
            "INSERT INTO media (sha256, kind, width, height, duration_ticks, fps_num, fps_den, added_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (digest, kind, p.get("width"), p.get("height"), p.get("duration_ticks"),
             p.get("fps_num"), p.get("fps_den"), now_ms()),
        ).lastrowid
    else:
        media_id = found["media_id"]
    con.execute(
        "INSERT INTO media_file (path, media_id, size, mtime_ms, seen_ms) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (path) DO UPDATE SET media_id = excluded.media_id, size = excluded.size, "
        "mtime_ms = excluded.mtime_ms, seen_ms = excluded.seen_ms",
        (path, media_id, st.st_size, mtime, now_ms()),
    )
    return media_id, is_new


def media_path(con: sqlite3.Connection, media_id: int) -> Optional[str]:
    """A path that currently exists for the media, most recently seen first."""
    for r in con.execute(
        "SELECT path FROM media_file WHERE media_id = ? ORDER BY seen_ms DESC", (media_id,)
    ):
        if os.path.exists(r["path"]):
            return r["path"]
    return None


def media_id_for_path(con: sqlite3.Connection, path: "str | Path") -> Optional[int]:
    row = con.execute(
        "SELECT media_id FROM media_file WHERE path = ?", (str(Path(path).resolve()),)
    ).fetchone()
    return row["media_id"] if row else None


def set_status(con: sqlite3.Connection, media_id: int, status: str) -> None:
    con.execute("UPDATE media SET status = ? WHERE media_id = ?", (status, media_id))


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------

def name_key(name: str) -> str:
    """Spacing, case and middle dots do not make another name."""
    return re.sub(r"[\s·・\-_.]+", "", name).casefold()


def rename_entity(con: sqlite3.Connection, entity_id: int, name: str) -> None:
    """A new display name; the old one stays as an alias."""
    old = con.execute("SELECT name, aliases FROM entity WHERE entity_id = ?", (entity_id,)).fetchone()
    aliases = [a for a in dict.fromkeys([old["name"], *old["aliases"].split("\n")]) if a and a != name]
    con.execute("UPDATE entity SET name = ?, aliases = ? WHERE entity_id = ?", (name, "\n".join(aliases), entity_id))
    for (m,) in con.execute("SELECT DISTINCT media_id FROM fact WHERE ref_id = ?", (entity_id,)).fetchall():
        rebuild_docs(con, m)


def set_aliases(con: sqlite3.Connection, entity_id: int, aliases: Iterable[str]) -> None:
    """Replace the alias list (cleaning model-written noise)."""
    name = con.execute("SELECT name FROM entity WHERE entity_id = ?", (entity_id,)).fetchone()[0]
    clean = [a for a in dict.fromkeys(a.strip() for a in aliases) if a and a != name]
    con.execute("UPDATE entity SET aliases = ? WHERE entity_id = ?", ("\n".join(clean), entity_id))


def merge_entity(con: sqlite3.Connection, duplicate: int, keep: int) -> None:
    """Point everything at `keep`; the duplicate's names become its aliases."""
    d = con.execute("SELECT * FROM entity WHERE entity_id = ?", (duplicate,)).fetchone()
    k = con.execute("SELECT * FROM entity WHERE entity_id = ?", (keep,)).fetchone()
    if d is None or k is None or d["type"] != k["type"]:
        raise ValueError("both must exist and be the same type")
    entity(con, k["type"], k["name"], aliases=[d["name"], *d["aliases"].split("\n")], catalog_ref=d["catalog_ref"])
    media = [r[0] for r in con.execute("SELECT DISTINCT media_id FROM fact WHERE ref_id = ?", (duplicate,))]
    con.execute("UPDATE fact SET ref_id = ? WHERE ref_id = ?", (keep, duplicate))
    con.execute("DELETE FROM entity WHERE entity_id = ?", (duplicate,))
    for m in media:
        rebuild_docs(con, m)

def entity(
    con: sqlite3.Connection, type: str, name: str, *,
    aliases: Iterable[str] = (), catalog_ref: Optional[str] = None,
) -> int:
    """Get or create; new aliases are merged in, never dropped."""
    row = con.execute(
        "SELECT entity_id, aliases, catalog_ref FROM entity WHERE type = ? AND name = ?", (type, name)
    ).fetchone()
    new = [a.strip() for a in aliases if a and a.strip() and a.strip() != name]
    if row is None:
        return con.execute(
            "INSERT INTO entity (type, name, aliases, catalog_ref) VALUES (?, ?, ?, ?)",
            (type, name, "\n".join(dict.fromkeys(new)), catalog_ref),
        ).lastrowid
    have = [a for a in row["aliases"].split("\n") if a]
    merged = list(dict.fromkeys(have + new))
    if merged != have or (catalog_ref is not None and row["catalog_ref"] is None):
        con.execute(
            "UPDATE entity SET aliases = ?, catalog_ref = COALESCE(catalog_ref, ?) WHERE entity_id = ?",
            ("\n".join(merged), catalog_ref, row["entity_id"]),
        )
    return row["entity_id"]


# ---------------------------------------------------------------------------
# cuts
# ---------------------------------------------------------------------------

def set_cuts(con: sqlite3.Connection, media_id: int, cuts: Sequence[dict]) -> list[int]:
    """Replace the media's segmentation. Facts and usage are anchored to
    media time, so they survive; only the search units change.

    cuts: dicts with start_ticks, end_ticks, keyframe_ticks, optional phash.
    """
    for c in cuts:
        if not (c["start_ticks"] <= c["keyframe_ticks"] <= max(c["start_ticks"], c["end_ticks"] - 1)
                or c["start_ticks"] == c["end_ticks"]):
            raise ValueError(f"keyframe outside its cut: {c}")
    ordered = sorted(cuts, key=lambda c: c["start_ticks"])
    for a, b in zip(ordered, ordered[1:]):
        if b["start_ticks"] < a["end_ticks"]:
            raise ValueError(f"cuts overlap: {a} / {b}")
    old = [r[0] for r in con.execute("SELECT cut_id FROM cut WHERE media_id = ?", (media_id,))]
    con.executemany("DELETE FROM cut_doc WHERE rowid = ?", [(i,) for i in old])
    con.execute("DELETE FROM cut WHERE media_id = ?", (media_id,))
    ids = [
        con.execute(
            "INSERT INTO cut (media_id, idx, start_ticks, end_ticks, keyframe_ticks, phash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (media_id, i, c["start_ticks"], c["end_ticks"], c["keyframe_ticks"], _signed64(c.get("phash"))),
        ).lastrowid
        for i, c in enumerate(ordered)
    ]
    rebuild_docs(con, media_id)
    return ids


def _signed64(v: Optional[int]) -> Optional[int]:
    """SQLite integers are signed; store a 64-bit hash in two's complement."""
    if v is None:
        return None
    return v - (1 << 64) if v >= (1 << 63) else v


def unsigned64(v: Optional[int]) -> Optional[int]:
    if v is None:
        return None
    return v + (1 << 64) if v < 0 else v


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------

def _overlaps(a0, a1, b0, b1) -> bool:
    if a0 is None or b0 is None:
        return True
    if a0 == a1 or b0 == b1:  # a point (an image, a single frame)
        return a0 <= b1 and b0 <= a1
    return a0 < b1 and b0 < a1


def put_facts(
    con: sqlite3.Connection, media_id: int, producer: str, rows: Sequence[dict], *,
    span: Optional[tuple[int, int]] = None, rebuild: bool = True,
) -> int:
    """Replace this producer's facts (any version of it) on the media, or
    within `span`, by rows.

    rows: dicts with kind and any of value, num, ref_id, t0_ticks, t1_ticks,
    status ('proposed' default, or 'accepted'), basis. Returns rows written.
    A row matching a rejected fact (same kind, value, ref, overlapping
    span) is skipped.
    """
    if producer == HUMAN:
        raise ValueError("human facts go through add_human_fact")
    # The whole family (name@any version): an upgraded producer replaces its
    # old version's rows instead of leaving them beside the new ones.
    family = producer.split("@")[0]
    where = "media_id = ? AND (producer = ? OR producer LIKE ?) AND status != 'rejected'"
    args = [media_id, family, family + "@%"]
    if span is not None:
        where += " AND t0_ticks IS NOT NULL AND t0_ticks < ? AND t1_ticks > ?"
        args += [span[1], span[0]]
    con.execute(f"DELETE FROM fact WHERE {where}", args)
    rejected = con.execute(
        "SELECT kind, value, ref_id, t0_ticks, t1_ticks FROM fact WHERE media_id = ? AND status = 'rejected'",
        (media_id,),
    ).fetchall()
    written = 0
    stamp = now_ms()
    for r in rows:
        t0, t1 = r.get("t0_ticks"), r.get("t1_ticks")
        if any(
            x["kind"] == r["kind"] and x["value"] == r.get("value") and x["ref_id"] == r.get("ref_id")
            and _overlaps(x["t0_ticks"], x["t1_ticks"], t0, t1)
            for x in rejected
        ):
            continue
        con.execute(
            "INSERT INTO fact (media_id, t0_ticks, t1_ticks, kind, value, num, ref_id, producer, status, basis, created_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (media_id, t0, t1, r["kind"], r.get("value"), r.get("num"), r.get("ref_id"),
             producer, r.get("status", "proposed"), r.get("basis"), stamp),
        )
        written += 1
    if rebuild:
        rebuild_docs(con, media_id)
    return written


def add_human_fact(con: sqlite3.Connection, media_id: int, kind: str, *, value=None, num=None,
                   ref_id=None, t0_ticks=None, t1_ticks=None, basis=None) -> int:
    fid = con.execute(
        "INSERT INTO fact (media_id, t0_ticks, t1_ticks, kind, value, num, ref_id, producer, status, basis, created_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?)",
        (media_id, t0_ticks, t1_ticks, kind, value, num, ref_id, HUMAN, basis, now_ms()),
    ).lastrowid
    rebuild_docs(con, media_id)
    return fid


def review_fact(con: sqlite3.Connection, fact_id: int, status: str, basis: Optional[str] = None) -> None:
    """A person accepts or rejects a machine fact. Rejected rows stay as a
    record that keeps the producer from proposing it again."""
    if status not in ("accepted", "rejected"):
        raise ValueError(status)
    row = con.execute("SELECT media_id FROM fact WHERE fact_id = ?", (fact_id,)).fetchone()
    if row is None:
        raise KeyError(fact_id)
    con.execute(
        "UPDATE fact SET status = ?, basis = COALESCE(?, basis) WHERE fact_id = ?", (status, basis, fact_id)
    )
    rebuild_docs(con, row["media_id"])


# ---------------------------------------------------------------------------
# embeddings, usage, jobs, lexicon
# ---------------------------------------------------------------------------

def put_embedding(con: sqlite3.Connection, media_id: int, t_ticks: int, model: str, vec) -> None:
    blob = np.asarray(vec, dtype="<f2").tobytes()
    con.execute(
        "INSERT OR REPLACE INTO embedding (media_id, t_ticks, model, vec) VALUES (?, ?, ?, ?)",
        (media_id, t_ticks, model, blob),
    )


def load_embeddings(con: sqlite3.Connection, model: str) -> tuple[np.ndarray, np.ndarray]:
    """(keys [n, 2] of media_id, t_ticks; vectors [n, d] float32)."""
    rows = con.execute("SELECT media_id, t_ticks, vec FROM embedding WHERE model = ?", (model,)).fetchall()
    if not rows:
        return np.zeros((0, 2), np.int64), np.zeros((0, 0), np.float32)
    keys = np.array([(r[0], r[1]) for r in rows], dtype=np.int64)
    vecs = np.stack([np.frombuffer(r[2], dtype="<f2") for r in rows]).astype(np.float32)
    return keys, vecs


def replace_usage(con: sqlite3.Connection, project: str, rows: Sequence[dict]) -> int:
    """Replace every usage row of a project (re-importing an edited project)."""
    touched = {r[0] for r in con.execute("SELECT DISTINCT media_id FROM usage WHERE project = ?", (project,))}
    con.execute("DELETE FROM usage WHERE project = ?", (project,))
    stamp = now_ms()
    for r in rows:
        con.execute(
            "INSERT INTO usage (media_id, project, sequence, track, src_in_ticks, src_out_ticks, rec_in_ticks, "
            "speed, scale, pos_x, pos_y, caption, props, approved, imported_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (r["media_id"], project, r["sequence"], r.get("track"), r["src_in_ticks"], r["src_out_ticks"],
             r["rec_in_ticks"], r.get("speed", 1.0), r.get("scale"), r.get("pos_x"), r.get("pos_y"),
             r.get("caption"), r.get("props"), int(bool(r.get("approved"))), stamp),
        )
        touched.add(r["media_id"])
    for m in touched:
        rebuild_docs(con, m)
    return len(rows)


def job_version(con: sqlite3.Connection, media_id: int, stage: str) -> Optional[str]:
    row = con.execute(
        "SELECT version FROM job WHERE media_id = ? AND stage = ? AND status = 'done'", (media_id, stage)
    ).fetchone()
    return row["version"] if row else None


def job_done(con: sqlite3.Connection, media_id: int, stage: str, version: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO job (media_id, stage, version, status, error, updated_ms) VALUES (?, ?, ?, 'done', NULL, ?)",
        (media_id, stage, version, now_ms()),
    )


def job_failed(con: sqlite3.Connection, media_id: int, stage: str, version: str, error: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO job (media_id, stage, version, status, error, updated_ms) VALUES (?, ?, ?, 'failed', ?, ?)",
        (media_id, stage, version, error[:2000], now_ms()),
    )


def lexicon_lookup(con: sqlite3.Connection, src: str) -> dict[str, tuple[Optional[str], Optional[str]]]:
    return {
        r["src_tag"]: (r["kind"], r["value"])
        for r in con.execute("SELECT src_tag, kind, value FROM lexicon WHERE src = ? AND origin != 'rejected'", (src,))
    }


# ---------------------------------------------------------------------------
# search documents
# ---------------------------------------------------------------------------

def rebuild_docs(con: sqlite3.Connection, media_id: int) -> None:
    cuts = con.execute(
        "SELECT cut_id, start_ticks, end_ticks FROM cut WHERE media_id = ?", (media_id,)
    ).fetchall()
    if not cuts:
        return
    facts = con.execute(
        "SELECT f.t0_ticks, f.t1_ticks, f.value, e.name, e.aliases FROM fact f "
        "JOIN fact_kind k ON k.kind = f.kind AND k.searchable = 1 "
        "LEFT JOIN entity e ON e.entity_id = f.ref_id "
        "WHERE f.media_id = ? AND f.status != 'rejected'",
        (media_id,),
    ).fetchall()
    usage = con.execute(
        "SELECT src_in_ticks, src_out_ticks, caption FROM usage WHERE media_id = ? AND caption IS NOT NULL",
        (media_id,),
    ).fetchall()
    for c in cuts:
        parts: list[str] = []
        for f in facts:
            if _overlaps(f["t0_ticks"], f["t1_ticks"], c["start_ticks"], c["end_ticks"]):
                for text in (f["value"], f["name"], f["aliases"]):
                    if text:
                        parts.append(text)
        for u in usage:
            if _overlaps(u["src_in_ticks"], u["src_out_ticks"], c["start_ticks"], c["end_ticks"]):
                parts.append(u["caption"])
        con.execute("DELETE FROM cut_doc WHERE rowid = ?", (c["cut_id"],))
        con.execute(
            "INSERT INTO cut_doc (rowid, doc) VALUES (?, ?)",
            (c["cut_id"], "\n".join(dict.fromkeys(parts))),
        )
