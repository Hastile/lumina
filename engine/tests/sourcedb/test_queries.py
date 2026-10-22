"""The queries the Shorts editing grammar needs, each answered by the frozen
schema. A query this file cannot express is a reason to change the schema
before it is frozen, not after."""

import pytest

from lumina.sourcedb import db
from lumina.sourcedb.query import Filters, search, similar
from lumina.timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND


@pytest.fixture
def lib(tmp_path):
    con = db.connect(tmp_path / "source.db")
    work = db.entity(con, "work", "퓨키 가디스 샷 트릭", aliases=["PUKEY GODDESS SHOT TRICK", "퓨키"])
    other = db.entity(con, "work", "Escape the Walls")
    palm = db.entity(con, "character", "팜술")

    def media(name, kind, cuts, facts, data=None):
        p = tmp_path / name
        p.write_bytes((data or name).encode())
        mid, _ = db.register_file(con, p, kind)
        db.set_cuts(con, mid, cuts)
        db.put_facts(con, mid, "test@1", facts)
        db.set_status(con, mid, "live")
        return mid

    v = lambda a, b: {"start_ticks": a * S, "end_ticks": b * S, "keyframe_ticks": a * S}
    span = lambda a, b: {"t0_ticks": a * S, "t1_ticks": b * S}
    ids = {}
    ids["trailer"] = media("trailer.mp4", "video", [v(0, 3), v(3, 4), v(4, 9)], [
        {"kind": "work", "ref_id": work},
        {"kind": "source_type", "value": "trailer"},
        {"kind": "character", "ref_id": palm, "num": 0.95, **span(0, 3)},
        {"kind": "description", "value": "팜술이 정면으로 웃는 클로즈업", **span(0, 3)},
        {"kind": "subject_area", "num": 0.4, **span(0, 3)},
        {"kind": "luma", "num": 0.05, **span(3, 4)},
        {"kind": "description", "value": "카드를 고르는 플레이 화면, 손과 UI", **span(4, 9)},
        {"kind": "source_type", "value": "gameplay", **span(4, 9)},
        {"kind": "speech", "value": "체험판 플레이", **span(5, 6)},
    ])
    ids["mv"] = media("mv.mp4", "video", [v(0, 5)], [
        {"kind": "work", "ref_id": work},
        {"kind": "source_type", "value": "mv"},
        {"kind": "character", "ref_id": palm, "num": 0.9, **span(0, 5)},
        {"kind": "text_overlay", "num": 0.5, **span(0, 5)},
        {"kind": "subject_x", "num": 0.8, **span(0, 5)},
    ])
    image = {"start_ticks": 0, "end_ticks": 0, "keyframe_ticks": 0}
    ids["orig"] = media("orig.jpg", "image", [image], [
        {"kind": "work", "ref_id": other}, {"kind": "variant", "value": "original"},
        {"kind": "credit", "value": "pandorioltd"}, {"kind": "source_type", "value": "sns_post"},
    ])
    ids["copy"] = media("copy.jpg", "image", [image], [
        {"kind": "work", "ref_id": other}, {"kind": "variant", "value": "copy"},
        {"kind": "description", "value": "로블록스 모방작 검색 결과"},
    ])
    db.replace_usage(con, "퓨키 데모 취소.lumina", [{
        "media_id": ids["trailer"], "sequence": "s", "src_in_ticks": 4 * S, "src_out_ticks": 6 * S,
        "rec_in_ticks": 0, "caption": "체험판을 공개해 사람들이 직접 해봐야 하는데", "approved": 1,
    }])
    con.commit()
    yield con, ids
    con.close()


def cut_of(con, media_id, idx=0):
    return con.execute("SELECT cut_id FROM cut WHERE media_id = ? AND idx = ?", (media_id, idx)).fetchone()[0]


def test_character_by_name_and_work_alias(lib):
    con, ids = lib
    hits = search(con, "팜술이", Filters(work="PUKEY"))
    assert hits[0].cut_id == cut_of(con, ids["trailer"], 0)  # clearly visible beats the MV shot


def test_dark_shots_are_left_out(lib):
    con, ids = lib
    hits = search(con, None, Filters(work="퓨키", media_kind="video", min_luma=0.15), limit=20)
    assert cut_of(con, ids["trailer"], 1) not in [h.cut_id for h in hits]


def test_lyric_typography_is_left_out(lib):
    con, ids = lib
    hits = search(con, "팜술", Filters(max_text_overlay=0.2), limit=20)
    assert ids["mv"] not in [h.media_id for h in hits]


def test_off_centre_subject_is_reported_for_reframing(lib):
    con, ids = lib
    h = search(con, "팜술", Filters(source_type="mv"))[0]
    assert h.subject_x == 0.8 and "x0.80" in h.line()


def test_gameplay_that_looks_like_real_play(lib):
    con, ids = lib
    hits = search(con, "플레이 손 UI", Filters(source_type="gameplay"))
    assert hits[0].cut_id == cut_of(con, ids["trailer"], 2)


def test_original_versus_copy_evidence(lib):
    con, ids = lib
    assert [h.media_id for h in search(con, None, Filters(work="Escape", variant="original"))] == [ids["orig"]]
    assert [h.media_id for h in search(con, None, Filters(work="Escape", variant="copy"))] == [ids["copy"]]


def test_shot_long_enough_after_edge_margin(lib):
    con, ids = lib
    hits = search(con, None, Filters(work="퓨키", media_kind="video", min_seconds=2.0), limit=20)
    assert {h.cut_id for h in hits} == {cut_of(con, ids["trailer"], 0), cut_of(con, ids["trailer"], 2),
                                        cut_of(con, ids["mv"], 0)}


def test_past_captions_find_the_shot_used_for_a_similar_line(lib):
    con, ids = lib
    hits = search(con, "체험판을 공개하기도 꺼려지는")
    assert hits[0].cut_id == cut_of(con, ids["trailer"], 2) and hits[0].used == 1


def test_unused_only_and_used_only(lib):
    con, ids = lib
    used_cut = cut_of(con, ids["trailer"], 2)
    assert used_cut not in [h.cut_id for h in search(con, None, Filters(work="퓨키", unused_only=True), limit=20)]
    assert [h.cut_id for h in search(con, None, Filters(work="퓨키", used_only=True), limit=20)] == [used_cut]


def test_spoken_words(lib):
    con, ids = lib
    assert search(con, "체험판")[0].media_id == ids["trailer"]


def test_account_that_posted_it(lib):
    con, ids = lib
    assert search(con, "pandorioltd")[0].media_id == ids["orig"]


def test_staged_media_is_not_offered_until_live(lib):
    con, ids = lib
    db.set_status(con, ids["copy"], "staged")
    assert ids["copy"] not in [h.media_id for h in search(con, "모방작")]
    assert ids["copy"] in [h.media_id for h in search(con, "모방작", Filters(include_staged=True))]


def test_similar_keyframes(lib):
    con, ids = lib
    a, b, c = cut_of(con, ids["trailer"], 0), cut_of(con, ids["mv"], 0), cut_of(con, ids["trailer"], 2)
    for cid, vec in ((a, [1, 0, 0]), (b, [0.9, 0.1, 0]), (c, [0, 0, 1])):
        row = con.execute("SELECT media_id, keyframe_ticks FROM cut WHERE cut_id = ?", (cid,)).fetchone()
        db.put_embedding(con, row[0], row[1], "m", vec)
    assert [h.cut_id for h in similar(con, a, "m", limit=2)] == [b, c]


def test_a_hit_line_is_short(lib):
    con, ids = lib
    for h in search(con, "팜술", limit=20):
        assert len(h.line()) < 160
