"""python -m lumina.sourcedb <command>

  ingest <folder>...                  token-free stages, then verify
  usage <project.lumina> [--approved SEQ ...] [--sequences SEQ ...]
  describe [media_id ...] [--redo]    Claude scene descriptions of media never described, then verify
  verify                              re-check agreement on every media
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
    p = sub.add_parser("usage")
    p.add_argument("project")
    p.add_argument("--sequences", nargs="*")
    p.add_argument("--approved", nargs="*", default=[])
    p = sub.add_parser("describe")
    p.add_argument("media", nargs="*", type=int)
    p.add_argument("--redo", action="store_true", help="describe again media an older version already did (costs tokens)")
    sub.add_parser("verify")
    sub.add_parser("status")
    a = ap.parse_args(argv)

    con = db.connect(a.db)
    if a.cmd == "ingest":
        for f in a.folders:
            ids = ingest.ingest_folder(con, Path(f))
            for m in ids:
                db.set_status(con, m, "live")
            con.commit()
            print(f"{f}: {len(ids)} media")
        print("accepted by agreement:", _verify_all(con))
    elif a.cmd == "usage":
        n = ingest.usage_from_lumina(con, Path(a.project), sequences=a.sequences, approved=a.approved)
        con.commit()
        print(f"{n} usage rows; accepted by agreement: {_verify_all(con)}")
    elif a.cmd == "describe":
        describe.run(con, a.media or None, redo=a.redo)
        print("accepted by agreement:", _verify_all(con))
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
