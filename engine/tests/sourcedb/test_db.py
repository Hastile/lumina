import hashlib
import sqlite3

import numpy as np
import pytest

from lumina.sourcedb import db
from lumina.timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND


@pytest.fixture
def con(tmp_path):
    c = db.connect(tmp_path / "source.db")
    yield c
    c.close()


def _media(con, tmp_path, name="a.mp4", data=b"video"):
    p = tmp_path / name
    p.write_bytes(data)
    mid, _ = db.register_file(con, p, "video", probe={"duration_ticks": 10 * S, "fps_num": 30000, "fps_den": 1001})
    return mid


def _doc(con, cut_id):
    return con.execute("SELECT doc FROM cut_doc WHERE rowid = ?", (cut_id,)).fetchone()[0]


def test_schema_is_frozen(tmp_path):
    """schema.sql is version 1 for good: a DB built from it reopens, and one
    that drifted is refused instead of silently used."""
    path = tmp_path / "s.db"
    db.connect(path).close()
    db.connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE media ADD COLUMN extra TEXT")
    raw.commit()
    raw.close()
    with pytest.raises(db.SchemaMismatch):
        db.connect(path)


def test_schema_sql_is_unchanged():
    """Version 1 is frozen (2026-10-22). If this fails, schema.sql was edited:
    put the change in data (a fact kind, a lexicon row) instead. Comments and
    whitespace do not count."""
    digest = hashlib.sha256("\n".join(db.expected_fingerprint()).encode()).hexdigest()
    assert digest == "753957cf2be24520e4c31337ffc7fa1a338ee134650eaa5ad8c1867520713fcc"


def test_lexicon_ships_with_the_code(con):
    assert con.execute("SELECT count(*) FROM lexicon").fetchone()[0] == 429
    assert db.lexicon_lookup(con, "danbooru")["1girl"] == ("subject", "여성 1인")


def test_unknown_kind_is_refused(con, tmp_path):
    mid = _media(con, tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.put_facts(con, mid, "x@1", [{"kind": "descripton", "value": "typo"}])


def test_same_content_is_one_media(con, tmp_path):
    a = _media(con, tmp_path, "a.mp4", b"same")
    b = _media(con, tmp_path, "copy.mp4", b"same")
    assert a == b
    assert con.execute("SELECT count(*) FROM media_file").fetchone()[0] == 2


def test_rejected_fact_is_not_proposed_again(con, tmp_path):
    mid = _media(con, tmp_path)
    db.set_cuts(con, mid, [{"start_ticks": 0, "end_ticks": 10 * S, "keyframe_ticks": S}])
    ch = db.entity(con, "character", "팜술")
    db.put_facts(con, mid, "wd@1", [{"kind": "character", "ref_id": ch, "num": 0.4, "t0_ticks": 0, "t1_ticks": 10 * S}])
    fid = con.execute("SELECT fact_id FROM fact").fetchone()[0]
    db.review_fact(con, fid, "rejected", "not her")
    db.put_facts(con, mid, "wd@1", [{"kind": "character", "ref_id": ch, "num": 0.5, "t0_ticks": 0, "t1_ticks": 10 * S}])
    rows = con.execute("SELECT status FROM fact").fetchall()
    assert [r[0] for r in rows] == ["rejected"]
    cut_id = con.execute("SELECT cut_id FROM cut").fetchone()[0]
    assert "팜술" not in _doc(con, cut_id)


def test_human_fact_survives_producer_rerun(con, tmp_path):
    mid = _media(con, tmp_path)
    db.add_human_fact(con, mid, "description", value="토하는 장면", t0_ticks=0, t1_ticks=S)
    db.put_facts(con, mid, "vlm@1", [{"kind": "description", "value": "웃는 장면"}])
    db.put_facts(con, mid, "vlm@1", [])
    assert [r[0] for r in con.execute("SELECT producer FROM fact")] == ["human"]


def test_recut_keeps_facts_and_usage(con, tmp_path):
    mid = _media(con, tmp_path)
    db.set_cuts(con, mid, [{"start_ticks": 0, "end_ticks": 10 * S, "keyframe_ticks": S}])
    db.put_facts(con, mid, "whisper@1", [{"kind": "speech", "value": "출시일", "t0_ticks": 6 * S, "t1_ticks": 7 * S}])
    db.replace_usage(con, "p.lumina", [{"media_id": mid, "sequence": "s", "src_in_ticks": 1 * S,
                                         "src_out_ticks": 2 * S, "rec_in_ticks": 0, "caption": "데모 취소"}])
    a, b = db.set_cuts(con, mid, [
        {"start_ticks": 0, "end_ticks": 5 * S, "keyframe_ticks": S},
        {"start_ticks": 5 * S, "end_ticks": 10 * S, "keyframe_ticks": 6 * S},
    ])
    assert "데모 취소" in _doc(con, a) and "출시일" not in _doc(con, a)
    assert "출시일" in _doc(con, b) and "데모 취소" not in _doc(con, b)


def test_overlapping_cuts_are_refused(con, tmp_path):
    mid = _media(con, tmp_path)
    with pytest.raises(ValueError):
        db.set_cuts(con, mid, [
            {"start_ticks": 0, "end_ticks": 5 * S, "keyframe_ticks": 0},
            {"start_ticks": 4 * S, "end_ticks": 9 * S, "keyframe_ticks": 5 * S},
        ])


def test_doc_carries_entity_aliases_and_whole_media_facts(con, tmp_path):
    mid = _media(con, tmp_path)
    db.set_cuts(con, mid, [{"start_ticks": 0, "end_ticks": 2 * S, "keyframe_ticks": 0}])
    work = db.entity(con, "work", "퓨키 가디스 샷 트릭", aliases=["PUKEY GODDESS SHOT TRICK"])
    db.put_facts(con, mid, "folder@1", [{"kind": "work", "ref_id": work}, {"kind": "source_type", "value": "trailer"}])
    hit = con.execute("SELECT rowid FROM cut_doc WHERE cut_doc MATCH ?", ('"PUKEY"',)).fetchall()
    assert len(hit) == 1
    assert "trailer" in _doc(con, hit[0][0])


def test_phash_round_trips_as_unsigned(con, tmp_path):
    mid = _media(con, tmp_path)
    h = 0xF0F0_0000_0000_0001
    db.set_cuts(con, mid, [{"start_ticks": 0, "end_ticks": S, "keyframe_ticks": 0, "phash": h}])
    assert db.unsigned64(con.execute("SELECT phash FROM cut").fetchone()[0]) == h


def test_embeddings_round_trip(con, tmp_path):
    mid = _media(con, tmp_path)
    v = np.linspace(-1, 1, 8)
    db.put_embedding(con, mid, S, "m", v)
    keys, vecs = db.load_embeddings(con, "m")
    assert keys.tolist() == [[mid, S]]
    assert np.allclose(vecs[0], v, atol=1e-3)
