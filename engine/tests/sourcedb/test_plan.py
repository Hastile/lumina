"""Shot-plan search: text and picture rankings fused, shots too short dropped."""

import numpy as np
import pytest

from lumina.sourcedb import db, plan, textvec
from lumina.sourcedb.plan import Shot
from lumina.sourcedb.query import Filters
from lumina.timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND
AXES = {"녹색": 0, "폭발": 1, "바다": 2, "로봇": 3}


class FakeEncoder:
    """A picture space of four directions, one per word."""

    def encode(self, texts):
        out = np.zeros((len(texts), 4), np.float32)
        for i, t in enumerate(texts):
            for w, axis in AXES.items():
                if w in t:
                    out[i, axis] = 1
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-6)


@pytest.fixture
def lib(tmp_path):
    con = db.connect(tmp_path / "source.db")
    work = db.entity(con, "work", "사이버펑크: 엣지러너 2", aliases=["Edgerunners 2"])
    other = db.entity(con, "work", "다른 작품")
    roman = db.entity(con, "character", "로만 카락스")

    def media(name, cuts, facts, look):
        p = tmp_path / name
        p.write_bytes(name.encode())
        mid, _ = db.register_file(con, p, "video")
        db.set_cuts(con, mid, cuts)
        db.put_facts(con, mid, "test@1", facts)
        db.set_status(con, mid, "live")
        for c, word in zip(cuts, look):
            v = np.zeros(4, np.float32)
            v[AXES[word]] = 1
            db.put_embedding(con, mid, c["keyframe_ticks"], textvec.MODEL, v)
        return mid

    v = lambda a, b: {"start_ticks": a * S, "end_ticks": b * S, "keyframe_ticks": a * S}
    span = lambda a, b: {"t0_ticks": a * S, "t1_ticks": b * S}
    ids = {
        # no description at all: only the picture can find these
        "rec": media("E06 1858.mp4", [v(0, 4), v(4, 5), v(5, 9)], [{"kind": "work", "ref_id": work}],
                     ["녹색", "폭발", "폭발"]),
        "roman": media("E01 2147.mp4", [v(0, 4)], [
            {"kind": "work", "ref_id": work}, {"kind": "character", "ref_id": roman, **span(0, 4)},
            {"kind": "description", "value": "로만이 경찰 통제선 앞에 서 있다", **span(0, 4)}], ["바다"]),
        "other": media("other.mp4", [v(0, 9)], [{"kind": "work", "ref_id": other}], ["녹색"]),
    }
    con.commit()
    yield con, ids
    con.close()


def cut(con, media_id, idx=0):
    return con.execute("SELECT cut_id FROM cut WHERE media_id = ? AND idx = ?", (media_id, idx)).fetchone()[0]


def test_undescribed_cuts_are_found_by_their_look(lib):
    con, ids = lib
    shots = [Shot(text="의외로 인기를 얻은 조연", look="녹색 머리 소녀", seconds=2),
             Shot(text="로만은", look="경찰 앞의 소년", seconds=2)]
    res = plan.plan(con, shots, Filters(work="엣지러너"), encoder=FakeEncoder())
    assert res[0][0].cut_id == cut(con, ids["rec"], 0)          # picture only
    assert cut(con, ids["other"]) not in {h.cut_id for h in res[0]}  # another work
    assert res[1][0].cut_id == cut(con, ids["roman"])            # text only


def test_cuts_too_short_for_the_shot_are_dropped(lib):
    con, ids = lib
    res = plan.plan(con, [Shot(look="폭발", seconds=3)], Filters(work="엣지러너"), encoder=FakeEncoder())
    got = {h.cut_id for h in res[0]}
    assert cut(con, ids["rec"], 2) in got        # 4 s, 3.4 s usable >= 3 x 0.87
    assert cut(con, ids["rec"], 1) not in got    # 1 s


def test_an_unknown_character_is_searched_not_filtered(lib):
    con, ids = lib
    notes = []
    res = plan.plan(con, [Shot(look="녹색", seconds=2)], Filters(work="엣지러너", character="오미"),
                    encoder=FakeEncoder(), notes=notes)
    assert res[0] and "오미" in notes[0]
    res = plan.plan(con, [Shot(look="바다", character="로만")], Filters(work="엣지러너"), encoder=FakeEncoder())
    assert [h.cut_id for h in res[0]] == [cut(con, ids["roman"])]


def test_render_marks_a_cut_offered_twice(lib):
    con, ids = lib
    shots = [Shot(text="a", look="녹색", seconds=2), Shot(text="b", look="녹색", seconds=2)]
    res = plan.plan(con, shots, Filters(work="엣지러너"), encoder=FakeEncoder(), exclude_cuts=(cut(con, ids["roman"]),))
    out = plan.render(shots, res)
    assert out.startswith('S1 2.0s "a" / look: 녹색')
    assert "(also S2)" in out and f"#{cut(con, ids['roman'])}" not in out
