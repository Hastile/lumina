"""Source DB upkeep: the health report, backups, stale re-cuts and entity merges."""

import sqlite3

import pytest

from lumina.sourcedb import __main__ as cli
from lumina.sourcedb import db, health, ingest
from lumina.timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND


@pytest.fixture
def con(tmp_path):
    c = db.connect(tmp_path / "source.db")
    yield c
    c.close()


def _video(con, tmp_path, name="v.mp4", cuts=((0, 4), (4, 8)), version=ingest.CUTS_V):
    p = tmp_path / name
    p.write_bytes(name.encode())
    mid, _ = db.register_file(con, p, "video", probe={"duration_ticks": 8 * S, "fps_num": 30, "fps_den": 1})
    db.set_cuts(con, mid, [{"start_ticks": a * S, "end_ticks": b * S, "keyframe_ticks": a * S} for a, b in cuts])
    db.job_done(con, mid, "cuts", version)
    return mid


def test_clean_db_reports_nothing_to_fix(con, tmp_path):
    mid = _video(con, tmp_path)
    db.put_facts(con, mid, "claude-sonnet@4", [{"kind": "description", "value": "달리는 소녀", "t0_ticks": 0, "t1_ticks": 4 * S},
                                               {"kind": "description", "value": "웃는 얼굴", "t0_ticks": 4 * S, "t1_ticks": 8 * S}])
    rep = health.check(con, current_cuts=ingest.CUTS_V)
    assert rep["cuts"]["stale_media"] == []
    assert rep["descriptions"] == {"misaligned_facts": 0, "media": [], "undescribed_cuts": 0}
    assert rep["fact_shapes"] == {} and rep["entities"] == []
    assert rep["search_index"] == {"media_with_cuts_missing_docs": [], "orphan_docs": 0}
    text = health.render(rep)
    assert "fact shapes: ok" in text and "search index: ok" in text and "backup: none" in text


def test_recut_leaves_stale_versions_and_misaligned_descriptions(con, tmp_path):
    old = _video(con, tmp_path, "old.mp4", version="cuts@2")
    db.put_facts(con, old, "claude-sonnet@4", [{"kind": "description", "value": "긴 샷", "t0_ticks": 0, "t1_ticks": 8 * S}])
    rep = health.check(con, current_cuts=ingest.CUTS_V)
    assert rep["cuts"]["stale_media"] == [old] and rep["cuts"]["by_version"] == {"cuts@2": 1}
    assert rep["descriptions"]["media"] == [old], "a description spanning two of the current cuts is off the cuts"
    assert "recut --stale" in health.render(rep) and "describe --misaligned" in health.render(rep)


def test_fact_shapes_catch_what_bypassed_put_facts(con, tmp_path):
    mid = _video(con, tmp_path)
    ch = db.entity(con, "character", "오미")
    raw = [("character", None, None, None), ("luma", None, None, None), ("description", "  ", None, None),
           ("work", None, None, ch)]
    for kind, value, num, ref in raw:
        con.execute("INSERT INTO fact (media_id, kind, value, num, ref_id, producer, created_ms) VALUES (?, ?, ?, ?, ?, 'bug@1', 0)",
                    (mid, kind, value, num, ref))
    con.execute("INSERT INTO fact (media_id, t0_ticks, t1_ticks, kind, value, producer, created_ms) "
                "VALUES (?, ?, ?, 'note', 'x', 'bug@1', 0)", (mid, 5 * S, 2 * S))
    shapes = health.fact_shapes(con)
    assert set(shapes) == {"character: ref missing or dangling", "luma: num missing", "description: empty text",
                           "work: ref points at another entity type", "span ends before it starts"}


def test_duplicate_entities_and_merge(con, tmp_path, capsys):
    mid = _video(con, tmp_path)
    a = db.entity(con, "character", "타카나시 호시노")
    b = db.entity(con, "character", "타카나시호시노")
    c = db.entity(con, "character", "호시노", aliases=["小鳥遊ホシノ"])
    d = db.entity(con, "character", "小鳥遊ホシノ")
    db.entity(con, "work", "호시노")  # another type: not a duplicate
    db.put_facts(con, mid, "wd@1", [{"kind": "character", "ref_id": b, "num": 0.9},
                                    {"kind": "character", "ref_id": b, "num": 0.8, "t0_ticks": 0, "t1_ticks": 4 * S}])
    groups = health.duplicate_entities(con)
    keys = sorted(sorted(e["id"] for e in g["entities"]) for g in groups)
    assert keys == [sorted([a, b]), sorted([c, d])]
    g = next(g for g in groups if a in [e["id"] for e in g["entities"]])
    assert g["keep"] == b, "the most used name is kept"
    con.commit()
    cli.main(["--db", str(tmp_path / "source.db"), "merge", "--keep", str(b), str(a)])
    assert "merged" in capsys.readouterr().out
    con2 = db.connect(tmp_path / "source.db")
    assert con2.execute("SELECT count(*) FROM entity WHERE entity_id = ?", (a,)).fetchone()[0] == 0
    assert "타카나시 호시노" in con2.execute("SELECT aliases FROM entity WHERE entity_id = ?", (b,)).fetchone()[0]
    con2.close()


def test_fix_docs_rebuilds_the_search_index(con, tmp_path):
    mid = _video(con, tmp_path)
    con.execute("DELETE FROM cut_doc")
    con.execute("INSERT INTO cut_doc (rowid, doc) VALUES (999999, 'stale')")
    rep = health.search_index(con)
    assert rep == {"media_with_cuts_missing_docs": [mid], "orphan_docs": 1}
    assert health.fix_docs(con) == 1
    assert health.search_index(con) == {"media_with_cuts_missing_docs": [], "orphan_docs": 0}


def test_backup_is_consistent_and_rotates(con, tmp_path):
    _video(con, tmp_path)
    con.commit()
    folder = tmp_path / "backups"
    made = []
    for n in range(3):
        r = health.backup(con, folder / f"source-2026010{n}-000000.db") if n == 0 else health.backup(con, folder=folder, keep=2)
        made.append(r)
    assert made[0]["integrity"] == "ok" and made[0]["media"] == 1
    assert len(list(folder.glob("source-*.db"))) == 2, "only the newest `keep` stay"
    raw = sqlite3.connect(made[-1]["path"])
    assert raw.execute("SELECT count(*) FROM cut").fetchone()[0] == 2
    raw.close()
    assert health.newest_backup(folder)["count"] == 2
    with pytest.raises(FileExistsError):
        health.backup(con, made[-1]["path"])


def test_health_cli_runs_read_only(con, tmp_path, capsys):
    _video(con, tmp_path, version="cuts@1")
    con.commit()
    before = con.execute("SELECT total_changes()").fetchone()[0]
    cli.main(["--db", str(tmp_path / "source.db"), "health", "--files"])
    out = capsys.readouterr().out
    assert "behind" in out and "files: 0 live media with no path, 0 whose files are all gone" in out
    assert con.execute("SELECT total_changes()").fetchone()[0] == before
