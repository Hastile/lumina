"""New sources into a Short's folder and the source DB, in one step.

Recordings made by hand in OBS land in the OBS folder (C:/Users/HETX/Videos),
and pictures or trailers get dropped into the Short's folder. Before this,
every edit began with moving them by hand and reading them with scdet and
contact sheets. Here one call moves the recordings into the folder (a move,
never a copy), cuts and measures everything in the folder the DB does not
know yet, runs the GPU stages (tagger, speech, embeddings), has Claude
describe what is still undescribed, and prints every new cut as one search
line, so the edit starts from a DB that already knows the folder.

    python -m lumina.sourcedb intake <folder> [file ...] [--from DIR] [--dry-run]
                                     [--no-describe] [--max-cost USD]

Without files, every finished video directly in --from (the OBS folder) is
taken. When that folder holds recordings for other Shorts, name the files.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import db, describe, ingest, query, recorder

OBS_FOLDER = Path.home() / "Videos"
OBS_STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}")   # OBS's own file names
SETTLE_SECONDS = 10      # a file written to more recently may still be recording
COST_PER_CUT = 0.004     # Claude descriptions, measured on the pilot (USD)


def recordings(folder: Path) -> list[Path]:
    """Videos directly in the folder; its subfolders are someone else's."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ingest.VIDEO_EXT)


def plan_moves(paths: Iterable[Path], dest: Path, now: Optional[float] = None
               ) -> tuple[list[tuple[Path, Path]], list[tuple[Path, str]]]:
    """(source, target) pairs and (file, why it stays) pairs. Names already
    taken in the folder, or by an earlier file of the same call, get ' (2)'."""
    now = time.time() if now is None else now
    moves, skipped, taken = [], [], set()
    for p in paths:
        if not p.is_file():
            skipped.append((p, "no such file"))
            continue
        if p.suffix.lower() not in ingest.VIDEO_EXT | ingest.IMAGE_EXT:
            skipped.append((p, "not a video or picture"))
            continue
        if now - p.stat().st_mtime < SETTLE_SECONDS:
            skipped.append((p, "written to in the last seconds: still recording?"))
            continue
        if p.resolve().parent == dest.resolve():
            continue  # already in the folder; the folder scan takes it
        target = dest / p.name
        i = 2
        while target.exists() or target in taken:
            target = dest / f"{p.stem} ({i}){p.suffix}"
            i += 1
        taken.add(target)
        moves.append((p, target))
    return moves, skipped


def _folder_media(con, dest: Path) -> list[tuple[Path, str, Optional[int]]]:
    return [(p, kind, db.media_id_for_path(con, p)) for p, kind in ingest.discover(dest)]


def _cut_count(con, media_ids: list[int]) -> int:
    if not media_ids:
        return 0
    q = ",".join("?" * len(media_ids))
    return con.execute(f"SELECT count(*) FROM cut WHERE media_id IN ({q})", media_ids).fetchone()[0]


def report(con, media_ids: list[int]) -> list[str]:
    """Each media's name and length, then one search line per cut."""
    out = []
    for mid in media_ids:
        m = con.execute("SELECT kind, duration_ticks FROM media WHERE media_id = ?", (mid,)).fetchone()
        path = db.media_path(con, mid) or "?"
        cuts = [r[0] for r in con.execute("SELECT cut_id FROM cut WHERE media_id = ? ORDER BY idx", (mid,))]
        length = f"{(m['duration_ticks'] or 0) / query.S:.1f}s" if m["kind"] == "video" else "image"
        out.append(f"== media {mid} {Path(path).name} ({length}, {len(cuts)} cuts)")
        hits = query._hydrate(con, cuts)
        out += ["  " + hits[c].line() for c in cuts if c in hits]
    return out


def intake(dest: Path, files: Iterable[str] = (), *, source: Path = OBS_FOLDER, db_path=db.DEFAULT_DB_PATH,
           describe_cuts: bool = True, max_cost: float = 2.0, analyzers: bool = True, dry_run: bool = False,
           log: Callable[[str], None] = print, now: Optional[float] = None) -> list[int]:
    """Returns the media this call brought into the DB."""
    dest = Path(dest).resolve()
    picked = [Path(f) for f in files] if files else recordings(Path(source))
    moves, skipped = plan_moves(picked, dest, now)
    for p, why in skipped:
        log(f"stays  {p}: {why}")
    for src, dst in moves:
        log(f"move   {src} -> {dst}")

    con = db.connect(db_path)
    try:
        if dry_run:
            stale = [(p, "new  " if mid is None else "recut") for p, _, mid in
                     (_folder_media(con, dest) if dest.is_dir() else [])
                     if mid is None or db.job_version(con, mid, "cuts") != ingest.CUTS_V]
            log(f"{len(moves)} to move; {len(stale)} files in the folder to cut "
                f"(new to the DB, or cut by an older version)")
            for p, why in stale:
                log(f"{why}  {p}")
            return []

        for src, dst in moves:
            recorder.move(src, dst)
        moved = {dst.resolve() for _, dst in moves}

        # Cuts and measures for every file in the folder the DB lacks.
        brought: list[int] = []
        for path, kind, mid in _folder_media(con, dest):
            if mid is not None and db.job_version(con, mid, "cuts") == ingest.CUTS_V:
                continue
            try:
                mid = recorder.register(con, path, kind)
                if mid not in brought:  # the same content under two names is one media
                    brought.append(mid)
                con.commit()
                log(f"cut    {path.name}")
            except Exception as e:  # one bad file must not stop the rest
                con.rollback()
                log(f"FAILED {path.name}: {type(e).__name__}: {e}")
        con.commit()
    finally:
        con.close()

    if analyzers and brought:
        log("tagger, speech, embeddings ...")
        recorder.run_analyzers(db_path, log)

    con = db.connect(db_path)
    try:
        # OBS names a recording by the clock; the channel names it by what is said.
        for mid in brought:
            path = Path(db.media_path(con, mid) or "")
            if path.resolve() in moved and OBS_STAMP.fullmatch(path.stem):
                named = recorder.name_by_speech(con, mid, path, "")
                if named != path:
                    log(f"named  {named.name}")
        con.commit()

        folder_ids = sorted({mid for _, _, mid in _folder_media(con, dest) if mid is not None})
        undescribed = [m for m in folder_ids if db.job_version(con, m, "describe") is None]
        if describe_cuts and undescribed:
            n = _cut_count(con, undescribed)
            cost = n * COST_PER_CUT
            if cost > max_cost:
                log(f"descriptions skipped: {n} cuts, about ${cost:.2f} > --max-cost {max_cost:.2f}")
            else:
                log(f"describing {n} cuts of {len(undescribed)} media, about ${cost:.2f}")
                describe.run(con, undescribed, log=log)
        for mid in folder_ids:
            ingest.verify(con, mid)
        con.commit()
        for line in report(con, brought):
            log(line)
    finally:
        con.close()
    return brought
