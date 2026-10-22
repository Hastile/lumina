"""python -m lumina.sourcedb <command>

  ingest <folder>... [--each] [--workers N]
                                      token-free stages (--each: every subfolder is a work), then verify
  usage-all <folder> [--outputs DIR]  usage from every .lumina/.prproj under folder
  usage <project.lumina> [--approved SEQ ...] [--sequences SEQ ...]
  describe [media_id ...] [--redo]    Claude scene descriptions of media never described, then verify
  verify                              re-check agreement on every media
  reprobe                             re-measure media probed by an older version
  recut <media_id>...                 cut again at the current cuts version (tagger and embeddings redo)
  status                              what has run on how much

GPU stages (tagger, embed, speech) run in engine/analyzers:
  uv run --project engine/analyzers python engine/analyzers/run.py [db] [stage ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db, describe, ingest


def _verify_all(con) -> int:
    n = sum(ingest.verify(con, r[0]) for r in con.execute("SELECT media_id FROM media").fetchall())
    con.commit()
    return n


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m lumina.sourcedb")
    ap.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    sub = ap.add_subparsers(dest="cmd", required=True)
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
    sub.add_parser("verify")
    sub.add_parser("reprobe")
    p = sub.add_parser("recut")
    p.add_argument("media", nargs="+", type=int)
    sub.add_parser("status")
    a = ap.parse_args(argv)

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
        describe.run(con, a.media or None, redo=a.redo)
        print("accepted by agreement:", _verify_all(con))
    elif a.cmd == "recut":
        for m in a.media:
            ingest.run_cuts(con, m, Path(db.media_path(con, m)))
            db.job_done(con, m, "cuts", ingest.CUTS_V)
            con.commit()
            print(m, con.execute("SELECT count(*) FROM cut WHERE media_id = ?", (m,)).fetchone()[0], "cuts")
    elif a.cmd == "reprobe":
        print("last cuts clipped to the picture's length:", ingest.reprobe(con))
    elif a.cmd == "verify":
        print("accepted by agreement:", _verify_all(con))
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
