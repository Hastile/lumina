from lumina.sourcedb import db, ingest
from lumina.timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND


def _setup(tmp_path):
    con = db.connect(tmp_path / "s.db")
    p = tmp_path / "v.mp4"
    p.write_bytes(b"v")
    mid, _ = db.register_file(con, p, "video")
    db.set_cuts(con, mid, [{"start_ticks": 0, "end_ticks": 4 * S, "keyframe_ticks": 0},
                           {"start_ticks": 4 * S, "end_ticks": 8 * S, "keyframe_ticks": 4 * S}])
    return con, mid


def _status(con):
    return {(r["producer"], r["t0_ticks"]): r["status"] for r in con.execute("SELECT * FROM fact")}


def test_one_machine_alone_never_confirms(tmp_path):
    con, mid = _setup(tmp_path)
    ch = db.entity(con, "character", "oma_kokichi")
    db.put_facts(con, mid, "wd@1", [{"kind": "character", "ref_id": ch, "num": 0.89, "t0_ticks": 0, "t1_ticks": 4 * S}])
    assert ingest.verify(con, mid) == 0


def test_two_producers_agreeing_confirm(tmp_path):
    con, mid = _setup(tmp_path)
    work = db.entity(con, "work", "퓨키")
    db.put_facts(con, mid, "folder@1", [{"kind": "work", "ref_id": work}])
    db.put_facts(con, mid, "claude@1", [{"kind": "work", "ref_id": work, "t0_ticks": 0, "t1_ticks": 4 * S}])
    assert ingest.verify(con, mid) == 2
    assert set(_status(con).values()) == {"accepted"}


def test_same_producer_twice_is_not_agreement(tmp_path):
    con, mid = _setup(tmp_path)
    work = db.entity(con, "work", "퓨키")
    db.put_facts(con, mid, "claude@1", [{"kind": "work", "ref_id": work, "t0_ticks": 0, "t1_ticks": 4 * S},
                                        {"kind": "work", "ref_id": work, "t0_ticks": 4 * S, "t1_ticks": 8 * S}])
    db.put_facts(con, mid, "claude@2", [{"kind": "work", "ref_id": work}])
    assert ingest.verify(con, mid) == 0


def test_character_named_in_the_caption_it_was_used_under(tmp_path):
    con, mid = _setup(tmp_path)
    ch = db.entity(con, "character", "팜술", aliases=["Femme Soule"])
    db.put_facts(con, mid, "claude@1", [{"kind": "character", "ref_id": ch, "t0_ticks": 0, "t1_ticks": 4 * S},
                                        {"kind": "character", "ref_id": ch, "t0_ticks": 4 * S, "t1_ticks": 8 * S}])
    db.replace_usage(con, "p", [{"media_id": mid, "sequence": "s", "src_in_ticks": S, "src_out_ticks": 3 * S,
                                 "rec_in_ticks": 0, "caption": "팜술이 잘 보이는 구도"}])
    assert ingest.verify(con, mid) == 1
    assert _status(con) == {("claude@1", 0): "accepted", ("claude@1", 4 * S): "proposed"}
