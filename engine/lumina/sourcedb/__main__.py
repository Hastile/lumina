"""python -m lumina.sourcedb <command>

  intake <folder> [file ...] [--from DIR] [--dry-run] [--no-describe] [--max-cost USD]
                                      move new recordings (default: the OBS folder) into a Short's folder,
                                      cut, analyse and describe what the DB lacks there, list the new cuts
  ingest <folder>... [--each] [--workers N]
                                      token-free stages (--each: every subfolder is a work), then verify
  usage-all <folder> [--outputs DIR]  usage from every .lumina/.prproj under folder
  usage <project.lumina> [--approved SEQ ...] [--sequences SEQ ...]
  describe [media_id ...] [--redo]    Claude scene descriptions of media never described, then verify
  describe --misaligned [--max-cost USD]
                                      re-describe media whose descriptions no longer match their cuts
  verify                              re-check agreement on every media
  reprobe                             re-measure media probed by an older version
  recut <media_id>... | --stale [--limit N]
                                      cut again at the current cuts version (tagger and embeddings redo)
  status                              what has run on how much
  health [--files] [--json] [--fix-docs]
                                      read-only upkeep report (cut versions, stale descriptions, fact shapes,
                                      duplicate entities, search index, embeddings, files, backups)
  backup [dest] [--keep N]            consistent copy into ~/.lumina/backups (keeps the newest N)
  merge --keep ID DUP...              merge duplicate entities into ID (their names become aliases)

GPU stages (tagger, embed, speech) run in engine/analyzers:
  uv run --project engine/analyzers python engine/analyzers/run.py [db] [stage ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db, describe, health, ingest, intake

COST_PER_CUT = 0.004  # USD per described cut (docs/shorts-editing/README.md)


def _verify_all(con) -> int:
    n = sum(ingest.verify(con, r[0]) for r in con.execute("SELECT media_id FROM media").fetchall())
    con.commit()
    return n


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m lumina.sourcedb")
    ap.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("intake")
    p.add_argument("folder")
    p.add_argument("files", nargs="*")
    p.add_argument("--from", dest="source", default=str(intake.OBS_FOLDER), help="where OBS writes recordings")
    p.add_argument("--dry-run", action="store_true", help="only say what would move and what is new")
    p.add_argument("--no-describe", action="store_true", help="skip Claude descriptions (no tokens)")
    p.add_argument("--max-cost", type=float, default=2.0, help="skip descriptions above this many USD")
    p = sub.add_parser("ingest")
    p.add_argument("folders", nargs="+")
    p.add_argument("--each", action="store_true", help="each subfolder of the given folders is one work's folder")
    p.add_argument("--workers", type=int, default=6)
    p = sub.add_parser("usage-all")
    p.add_argument("folder")
    p.add_argument("--outputs", default="D:/유튜브 채널/Outputs", help="where finished renders are kept")
    p = sub.add_parser("usage")
    p.add_argument("project")
    p.add_argument("--sequences", nargs="*")
    p.add_argument("--approved", nargs="*", default=[])
    p = sub.add_parser("describe")
    p.add_argument("media", nargs="*", type=int)
    p.add_argument("--redo", action="store_true", help="describe again media an older version already did (costs tokens)")
    p.add_argument("--misaligned", action="store_true", help="the media whose descriptions no longer match their cuts")
    p.add_argument("--max-cost", type=float, default=2.0, help="refuse above this many USD (estimated)")
    sub.add_parser("verify")
    sub.add_parser("reprobe")
    p = sub.add_parser("recut")
    p.add_argument("media", nargs="*", type=int)
    p.add_argument("--stale", action="store_true", help="every video media cut by an older version")
    p.add_argument("--limit", type=int, help="at most this many media")
    sub.add_parser("status")
    p = sub.add_parser("health")
    p.add_argument("--files", action="store_true", help="also check every path on disk (slower)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--fix-docs", action="store_true", help="rebuild missing search documents (free)")
    p = sub.add_parser("backup")
    p.add_argument("dest", nargs="?")
    p.add_argument("--keep", type=int, default=health.KEEP_BACKUPS)
    p = sub.add_parser("merge")
    p.add_argument("--keep", type=int, required=True)
    p.add_argument("duplicates", nargs="+", type=int)
    a = ap.parse_args(argv)

    if a.cmd == "intake":
        intake.intake(Path(a.folder), a.files, source=Path(a.source), db_path=a.db, dry_run=a.dry_run,
                      describe_cuts=not a.no_describe, max_cost=a.max_cost,
                      log=lambda s: print(s, flush=True))  # runs for minutes, often into a log file
        return
    con = db.connect(a.db)
    if a.cmd == "ingest":
        roots = [Path(f) for f in a.folders]
        if a.each:
            roots = [d for f in roots for d in sorted(f.iterdir()) if d.is_dir()]
        print(ingest.ingest_many(con, roots, workers=a.workers))
        print("accepted by agreement:", _verify_all(con))
    elif a.cmd == "usage-all":
        root = Path(a.folder)
        projects = [p for p in sorted(root.rglob("*")) if p.suffix.lower() in (".lumina", ".prproj")
                    and not any("Auto-Save" in part for part in p.parts)]
        rendered = ingest.rendered_names(Path(a.outputs))
        total = 0
        for proj in projects:
            try:
                n = ingest.usage_from_project(con, proj, rendered=rendered | ingest.rendered_names(proj.parent))
                con.commit()
                total += n
                print(f"{n:5d} {proj.relative_to(root)}")
            except Exception as e:
                con.rollback()
                print(f"  FAILED {proj.relative_to(root)}: {type(e).__name__}: {e}")
        print(f"{total} usage rows from {len(projects)} projects; accepted by agreement: {_verify_all(con)}")
    elif a.cmd == "usage":
        n = ingest.usage_from_lumina(con, Path(a.project), sequences=a.sequences, approved=a.approved)
        con.commit()
        print(f"{n} usage rows; accepted by agreement: {_verify_all(con)}")
    elif a.cmd == "describe":
        media, redo = a.media or None, a.redo
        if a.misaligned:
            media, redo = health.misaligned_descriptions(con)["media"], True
            if not media:
                print("no misaligned descriptions")
                con.close()
                return
        if redo and media:
            cuts = con.execute(f"SELECT count(*) FROM cut WHERE media_id IN ({','.join('?' * len(media))})", media).fetchone()[0]
            cost = cuts * COST_PER_CUT
            print(f"{len(media)} media, {cuts} cuts, about ${cost:.2f}")
            if cost > a.max_cost:
                raise SystemExit(f"above --max-cost {a.max_cost}; raise it or pass fewer media")
        describe.run(con, media, redo=redo)
        print("accepted by agreement:", _verify_all(con))
    elif a.cmd == "recut":
        media = list(a.media)
        if a.stale:
            media += health.cut_versions(con, ingest.CUTS_V)["stale_media"]
        media = list(dict.fromkeys(media))[:a.limit] if a.limit else list(dict.fromkeys(media))
        if not media:
            raise SystemExit("give media ids or --stale")
        for m in media:
            ingest.run_cuts(con, m, Path(db.media_path(con, m)))
            db.job_done(con, m, "cuts", ingest.CUTS_V)
            con.commit()
            print(m, con.execute("SELECT count(*) FROM cut WHERE media_id = ?", (m,)).fetchone()[0], "cuts")
    elif a.cmd == "reprobe":
        print("last cuts clipped to the picture's length:", ingest.reprobe(con))
    elif a.cmd == "verify":
        print("accepted by agreement:", _verify_all(con))
    elif a.cmd == "health":
        if a.fix_docs:
            print("search documents rebuilt for", health.fix_docs(con), "media")
        rep = health.check(con, current_cuts=ingest.CUTS_V, stat_files=a.files, db_path=Path(a.db),
                           backup_dir=Path(a.db).parent / "backups")
        print(json.dumps(rep, ensure_ascii=False, indent=1) if a.json else health.render(rep))
    elif a.cmd == "backup":
        dest = Path(a.dest) if a.dest else None
        print(health.backup(con, dest, folder=Path(a.db).parent / "backups", keep=a.keep))
    elif a.cmd == "merge":
        for d in a.duplicates:
            db.merge_entity(con, d, a.keep)
        con.commit()
        r = con.execute("SELECT name, aliases FROM entity WHERE entity_id = ?", (a.keep,)).fetchone()
        print(f"merged {a.duplicates} into #{a.keep} {r['name']} (aliases: {r['aliases'].replace(chr(10), ', ')})")
    elif a.cmd == "status":
        for t in ("media", "cut", "fact", "usage", "entity", "embedding"):
            print(f"{t:10s} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]}")
        for r in con.execute("SELECT stage, version, status, count(*) FROM job GROUP BY 1, 2, 3 ORDER BY 1"):
            print(f"  {r[0]:9s} {r[1]:22s} {r[2]:6s} {r[3]}")
        for r in con.execute("SELECT kind, status, count(*) FROM fact WHERE kind IN ('work', 'character') GROUP BY 1, 2"):
            print(f"  {r[0]:9s} {r[1]:9s} {r[2]}")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
