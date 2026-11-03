"""Health of the source DB, and the upkeep that keeps a frozen schema sustainable.

The schema never changes (schema.sql, user_version 1), so what wears over
time is the data. ``check`` reads the DB and reports what needs upkeep, each
with the command that fixes it; nothing here writes except ``backup`` and
``fix_docs``.

  cuts versions      media cut by an older scene-cut version (search quality
                     differs per media) -> recut --stale
  descriptions       Claude descriptions whose span no longer matches a cut
                     (left by a re-cut; they blur into the new cuts) -> describe
                     --redo on those media
  fact shapes        rows whose value/num/ref does not fit their kind's type,
                     bad JSON, inverted spans -> a bug in whatever wrote them
  entities           names that differ only by spacing/case/dots, or one
                     entity's alias that is another's name -> merge
  search index       cuts without a search document and documents without a
                     cut -> fix_docs (free, rebuilds from facts)
  embeddings         cuts whose keyframe has no image embedding -> GPU embed
  files              media whose files are all gone (optional: stats every path)
  backup             age of the newest backup -> backup

cut_id is a search handle, not an identity: a re-cut replaces a media's cuts
and their ids. Anything kept across sessions names (file, source span), as
usage and facts already do.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from . import db

BACKUP_DIR = db.DEFAULT_DB_PATH.parent / "backups"
KEEP_BACKUPS = 10
DESCRIBE_KIND = "description"


def _one(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]


def cut_versions(con: sqlite3.Connection, current: str) -> dict:
    rows = con.execute(
        "SELECT m.media_id, j.version FROM media m LEFT JOIN job j ON j.media_id = m.media_id AND j.stage = 'cuts' "
        "WHERE m.kind = 'video' AND m.status IN ('staged', 'live')").fetchall()
    by = Counter(r["version"] or "(never)" for r in rows)
    stale = [r["media_id"] for r in rows if r["version"] != current]
    return {"current": current, "by_version": dict(by), "stale_media": stale}


def misaligned_descriptions(con: sqlite3.Connection) -> dict:
    """Description facts on video whose span is not a current cut of their media."""
    spans = defaultdict(set)
    for r in con.execute("SELECT c.media_id, c.start_ticks, c.end_ticks FROM cut c JOIN media m USING (media_id) "
                         "WHERE m.kind = 'video'"):
        spans[r[0]].add((r[1], r[2]))
    media, facts = set(), 0
    for r in con.execute("SELECT f.media_id, f.t0_ticks, f.t1_ticks FROM fact f JOIN media m USING (media_id) "
                         "WHERE f.kind = ? AND f.status != 'rejected' AND f.producer != ? AND m.kind = 'video' "
                         "AND f.t0_ticks IS NOT NULL", (DESCRIBE_KIND, db.HUMAN)):
        if (r[1], r[2]) not in spans[r[0]]:
            media.add(r[0])
            facts += 1
    undescribed = _one(con, "SELECT count(*) FROM cut c WHERE NOT EXISTS (SELECT 1 FROM fact f WHERE f.media_id = c.media_id "
                            "AND f.kind = ? AND f.status != 'rejected' AND (f.t0_ticks IS NULL OR "
                            "(f.t0_ticks < c.end_ticks AND f.t1_ticks > c.start_ticks) OR "
                            "(f.t0_ticks = c.start_ticks AND f.t1_ticks = c.end_ticks)))", (DESCRIBE_KIND,))
    return {"misaligned_facts": facts, "media": sorted(media), "undescribed_cuts": undescribed}


def fact_shapes(con: sqlite3.Connection, sample: int = 5) -> dict:
    types = {r[0]: r[1] for r in con.execute("SELECT kind, value_type FROM fact_kind")}
    problems: dict[str, list[int]] = defaultdict(list)

    def add(name, fid):
        problems[name].append(fid)

    for kind, vt in types.items():
        if vt == "ref":
            q = ("SELECT f.fact_id FROM fact f LEFT JOIN entity e ON e.entity_id = f.ref_id "
                 "WHERE f.kind = ? AND (f.ref_id IS NULL OR e.entity_id IS NULL)")
            for (fid,) in con.execute(q, (kind,)):
                add(f"{kind}: ref missing or dangling", fid)
            q = ("SELECT f.fact_id FROM fact f JOIN entity e ON e.entity_id = f.ref_id WHERE f.kind = ? AND e.type != ?")
            for (fid,) in con.execute(q, (kind, kind)):
                add(f"{kind}: ref points at another entity type", fid)
        elif vt == "num":
            for (fid,) in con.execute("SELECT fact_id FROM fact WHERE kind = ? AND num IS NULL", (kind,)):
                add(f"{kind}: num missing", fid)
        elif vt == "text":
            for (fid,) in con.execute("SELECT fact_id FROM fact WHERE kind = ? AND (value IS NULL OR trim(value) = '')", (kind,)):
                add(f"{kind}: empty text", fid)
        elif vt == "json":
            for fid, value in con.execute("SELECT fact_id, value FROM fact WHERE kind = ?", (kind,)):
                try:
                    json.loads(value)
                except (TypeError, ValueError):
                    add(f"{kind}: not JSON", fid)
    for (fid,) in con.execute("SELECT fact_id FROM fact WHERE t0_ticks > t1_ticks"):
        add("span ends before it starts", fid)
    for (fid,) in con.execute("SELECT f.fact_id FROM fact f JOIN media m USING (media_id) "
                              "WHERE f.t1_ticks > m.duration_ticks + 254016000000 AND m.duration_ticks IS NOT NULL"):
        add("span past the end of the media", fid)
    return {k: {"count": len(v), "fact_ids": v[:sample]} for k, v in sorted(problems.items())}


def duplicate_entities(con: sqlite3.Connection) -> list[dict]:
    """Same-type entities whose names collide by name_key, or whose alias is another's name."""
    rows = con.execute("SELECT entity_id, type, name, aliases FROM entity").fetchall()
    uses = Counter({r[0]: r[1] for r in con.execute("SELECT ref_id, count(*) FROM fact WHERE ref_id IS NOT NULL GROUP BY 1")})
    by_key: dict[tuple, set[int]] = defaultdict(set)
    names = {}
    for r in rows:
        names[r["entity_id"]] = r
        by_key[(r["type"], db.name_key(r["name"]))].add(r["entity_id"])
    groups: list[set[int]] = [ids for ids in by_key.values() if len(ids) > 1]
    for r in rows:
        for a in filter(None, (r["aliases"] or "").split("\n")):
            other = by_key.get((r["type"], db.name_key(a)), set()) - {r["entity_id"]}
            if other:
                groups.append(other | {r["entity_id"]})
    merged: list[set[int]] = []
    for g in groups:  # union overlapping groups
        for m in merged:
            if m & g:
                m |= g
                break
        else:
            merged.append(set(g))
    out = []
    for g in merged:
        ids = sorted(g, key=lambda i: (-uses[i], i))  # keep the most used one
        out.append({"type": names[ids[0]]["type"], "keep": ids[0],
                    "entities": [{"id": i, "name": names[i]["name"], "facts": uses[i]} for i in ids]})
    return sorted(out, key=lambda x: -sum(e["facts"] for e in x["entities"]))


def search_index(con: sqlite3.Connection) -> dict:
    no_doc = [r[0] for r in con.execute("SELECT c.media_id FROM cut c LEFT JOIN cut_doc d ON d.rowid = c.cut_id "
                                        "WHERE d.rowid IS NULL GROUP BY c.media_id")]
    orphan = _one(con, "SELECT count(*) FROM cut_doc d LEFT JOIN cut c ON c.cut_id = d.rowid WHERE c.cut_id IS NULL")
    return {"media_with_cuts_missing_docs": no_doc, "orphan_docs": orphan}


def embeddings(con: sqlite3.Connection, model: str = "siglip2-so400m-384") -> dict:
    missing = _one(con, "SELECT count(*) FROM cut c JOIN media m USING (media_id) WHERE m.status IN ('staged', 'live') AND "
                        "m.kind IN ('video', 'image') AND NOT EXISTS (SELECT 1 FROM embedding e WHERE e.media_id = c.media_id "
                        "AND e.t_ticks = c.keyframe_ticks AND e.model = ?)", (model,))
    orphan = _one(con, "SELECT count(*) FROM embedding e WHERE e.model = ? AND NOT EXISTS (SELECT 1 FROM cut c "
                       "WHERE c.media_id = e.media_id AND c.keyframe_ticks = e.t_ticks)", (model,))
    return {"model": model, "cuts_without": missing, "without_cut": orphan}


def files(con: sqlite3.Connection, stat: bool = False) -> dict:
    no_path = _one(con, "SELECT count(*) FROM media m WHERE m.status IN ('staged', 'live') AND NOT EXISTS "
                        "(SELECT 1 FROM media_file f WHERE f.media_id = m.media_id)")
    out = {"live_without_any_path": no_path}
    if stat:
        gone = defaultdict(int)
        have = defaultdict(int)
        for path, mid in con.execute("SELECT path, media_id FROM media_file"):
            (have if os.path.exists(path) else gone)[mid] += 1
        out["media_all_paths_gone"] = sorted(m for m in gone if not have.get(m))
    return out


def newest_backup(folder: Path = BACKUP_DIR) -> Optional[dict]:
    if not folder.is_dir():
        return None
    found = sorted(folder.glob("source-*.db"), key=lambda p: p.stat().st_mtime)
    if not found:
        return None
    p = found[-1]
    return {"path": str(p), "age_hours": round((time.time() - p.stat().st_mtime) / 3600, 1), "count": len(found)}


def check(con: sqlite3.Connection, *, current_cuts: str, stat_files: bool = False, db_path: Optional[Path] = None,
          backup_dir: Path = BACKUP_DIR) -> dict:
    size = {}
    if db_path is not None:
        for suffix in ("", "-wal"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                size["db" if not suffix else "wal"] = round(p.stat().st_size / 2 ** 20, 1)
    return {
        "rows": {t: _one(con, f"SELECT count(*) FROM {t}") for t in ("media", "cut", "fact", "entity", "embedding", "usage")},
        "size_mb": size,
        "cuts": cut_versions(con, current_cuts),
        "descriptions": misaligned_descriptions(con),
        "fact_shapes": fact_shapes(con),
        "entities": duplicate_entities(con),
        "search_index": search_index(con),
        "embeddings": embeddings(con),
        "files": files(con, stat_files),
        "backup": newest_backup(backup_dir),
    }


def _ids(ids: list[int], n: int = 12) -> str:
    return " ".join(map(str, ids[:n])) + (f" … (+{len(ids) - n})" if len(ids) > n else "")


def render(rep: dict) -> str:
    """The report as lines, each problem followed by what fixes it."""
    L = ["rows: " + ", ".join(f"{k} {v:,}" for k, v in rep["rows"].items())
         + (f"; size {rep['size_mb']}" if rep["size_mb"] else "")]
    c = rep["cuts"]
    stale = c["stale_media"]
    L.append(f"cuts: {c['by_version']} (current {c['current']})")
    if stale:
        L.append(f"  ! {len(stale)} video media behind {c['current']} -> python -m lumina.sourcedb recut --stale "
                 "(then GPU tagger/embed, then describe --misaligned)")
    d = rep["descriptions"]
    L.append(f"descriptions: {d['misaligned_facts']} off the current cuts on {len(d['media'])} media; "
             f"{d['undescribed_cuts']:,} cuts with none")
    if d["media"]:
        L.append(f"  ! re-describe (tokens) -> python -m lumina.sourcedb describe --misaligned   [{_ids(d['media'])}]")
    if rep["fact_shapes"]:
        L.append("fact shapes:")
        for k, v in rep["fact_shapes"].items():
            L.append(f"  ! {k}: {v['count']} (e.g. fact {', '.join(map(str, v['fact_ids']))})")
    else:
        L.append("fact shapes: ok")
    ents = rep["entities"]
    L.append(f"entities: {len(ents)} duplicate groups")
    for g in ents[:15]:
        names = " / ".join(f"{e['name']}#{e['id']}({e['facts']})" for e in g["entities"])
        dups = " ".join(str(e["id"]) for e in g["entities"] if e["id"] != g["keep"])
        L.append(f"  ? {g['type']}: {names} -> python -m lumina.sourcedb merge --keep {g['keep']} {dups}")
    s = rep["search_index"]
    if s["media_with_cuts_missing_docs"] or s["orphan_docs"]:
        L.append(f"  ! search index: {len(s['media_with_cuts_missing_docs'])} media missing docs, {s['orphan_docs']} orphan "
                 "docs -> python -m lumina.sourcedb health --fix-docs")
    else:
        L.append("search index: ok")
    e = rep["embeddings"]
    L.append(f"embeddings ({e['model']}): {e['cuts_without']:,} cuts without, {e['without_cut']:,} left from old cuts")
    f = rep["files"]
    line = f"files: {f['live_without_any_path']} live media with no path"
    if "media_all_paths_gone" in f:
        line += f", {len(f['media_all_paths_gone'])} whose files are all gone [{_ids(f['media_all_paths_gone'])}]"
    L.append(line)
    b = rep["backup"]
    if b is None:
        L.append("backup: none -> python -m lumina.sourcedb backup")
    else:
        L.append(f"backup: {b['count']} kept, newest {b['age_hours']} h old ({b['path']})"
                 + (" -> python -m lumina.sourcedb backup" if b["age_hours"] > 24 * 7 else ""))
    return "\n".join(L)


def fix_docs(con: sqlite3.Connection) -> int:
    """Rebuild the search documents of media whose cuts lack one, and drop documents without a cut."""
    s = search_index(con)
    for m in s["media_with_cuts_missing_docs"]:
        db.rebuild_docs(con, m)
    con.execute("DELETE FROM cut_doc WHERE rowid NOT IN (SELECT cut_id FROM cut)")
    con.commit()
    return len(s["media_with_cuts_missing_docs"])


def backup(con: sqlite3.Connection, dest: Optional[Path] = None, *, folder: Path = BACKUP_DIR, keep: int = KEEP_BACKUPS) -> dict:
    """A consistent copy while the DB stays in use (VACUUM INTO also compacts it); keeps the newest `keep` in folder."""
    if dest is None:
        now = time.time()
        dest = folder / (time.strftime("source-%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}.db")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(dest)
    con.execute("VACUUM INTO ?", (str(dest),))
    check = sqlite3.connect(dest)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        media = check.execute("SELECT count(*) FROM media").fetchone()[0]
    finally:
        check.close()
    removed = []
    if dest.parent == folder:
        old = sorted(folder.glob("source-*.db"), key=lambda p: p.stat().st_mtime)[:-keep]
        for p in old:
            p.unlink()
            removed.append(p.name)
    return {"path": str(dest), "mb": round(dest.stat().st_size / 2 ** 20, 1), "integrity": ok, "media": media,
            "removed": removed}
