"""clip.place_shots: a cut plan laid by the Shorts rules, all or nothing."""

from copy import deepcopy

import pytest

from lumina.commands import shots as shots_mod
from lumina.commands.registry import CommandError
from lumina.session import Session
from lumina.sourcedb import db
from lumina.timebase import TICKS_PER_SECOND, frames_to_ticks

from .test_session import FakeServices

SEC = TICKS_PER_SECOND
F = frames_to_ticks(1, 30, 1)


@pytest.fixture
def short(tmp_path, monkeypatch):
    s = Session(services=FakeServices())
    mids = s.execute("media.import", {"paths": ["a.mp4", "d.png"]})["result"]["media_ids"]
    sid = s.execute("sequence.create", {"name": "Short", "settings": {"width": 1080, "height": 1920}})["result"]["sequence_id"]
    # a source DB whose cuts live in "a.mp4" (FakeServices probes any path)
    con = db.connect(tmp_path / "source.db")
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    mid, _ = db.register_file(con, clip, "video")
    db.set_cuts(con, mid, [{"start_ticks": a * SEC, "end_ticks": b * SEC, "keyframe_ticks": a * SEC}
                           for a, b in ((0, 4), (4, 5), (5, 12))])
    db.put_facts(con, mid, "test@1", [{"kind": "subject_x", "num": 0.8, "t0_ticks": 5 * SEC, "t1_ticks": 12 * SEC},
                                      {"kind": "luma", "num": 0.05, "t0_ticks": 0, "t1_ticks": 4 * SEC}])
    db.set_status(con, mid, "live")
    con.commit()
    cuts = [r[0] for r in con.execute("SELECT cut_id FROM cut ORDER BY idx")]
    con.close()
    monkeypatch.setattr(shots_mod, "SOURCE_DB", tmp_path / "source.db")
    return s, sid, mids, cuts, str(clip)


def video(s, sid):
    seq = s.doc["sequences"][sid]
    return sorted(seq["tracks"][seq["video_tracks"][0]]["clips"].values(), key=lambda c: c["start"])


def test_a_plan_lands_with_margins_speeds_and_framing(short):
    s, sid, (a, png), cuts, path = short
    r = s.execute("clip.place_shots", {"sequence_id": sid, "shots": [
        {"cut_id": cuts[0], "at": 0, "end": "60f"},       # 3.4 s usable >= 2 s: 100%
        {"cut_id": cuts[2], "at": "60f", "end": "246f"},  # 6.4 s usable for 6.2 s: 100%, subject at the side
        {"media_id": png, "at": "246f", "end": "276f"},
    ]})["result"]
    v = video(s, sid)
    assert [(c["start"], c["end"]) for c in v] == [(0, 60 * F), (60 * F, 246 * F), (246 * F, 276 * F)]
    assert v[0]["source_in"] == round(0.3 * SEC / F) * F and v[0]["speed"] == 1.0
    assert v[0]["motion"]["scale"] == 115 and v[0]["motion"]["position"] == [540, 1060]
    assert v[1]["motion"]["scale"] == 130 and v[1]["motion"]["position"][0] < 540  # subject on the right moves left
    assert v[2]["motion"]["scale"] == 100  # pictures keep their own framing
    assert r["placed"][0]["notes"] == ["dark"] and r["covered"] == []
    assert not s.doc["sequences"][sid]["tracks"][s.doc["sequences"][sid]["audio_tracks"][0]]["clips"]
    # the file was imported once, and a second plan reuses that media item
    s.execute("clip.place_shots", {"sequence_id": sid, "shots": [{"cut_id": cuts[2], "at": "300f", "end": "330f"}]})
    assert sum(1 for m in s.doc["media"].values() if m["path"] == path) == 1


def test_a_short_cut_is_slowed_but_never_past_the_limit(short):
    s, sid, _, cuts, _ = short
    s.execute("clip.place_shots", {"sequence_id": sid, "shots": [{"cut_id": cuts[0], "at": 0, "end": "3.8s"}]})
    c = video(s, sid)[0]
    assert c["end"] == round(3.8 * SEC / F) * F
    assert 0.87 <= c["speed"] < 1  # 3.4 s of picture over 3.8 s
    with pytest.raises(CommandError, match="needs 5.00s"):
        s.execute("clip.place_shots", {"sequence_id": sid, "shots": [{"cut_id": cuts[0], "at": "10s", "end": "15s"}]})


def test_a_refused_shot_changes_nothing_and_one_undo_removes_the_plan(short):
    s, sid, (a, png), cuts, _ = short
    before = deepcopy(s.doc)
    with pytest.raises(CommandError) as e:
        s.execute("clip.place_shots", {"sequence_id": sid, "shots": [
            {"cut_id": cuts[2], "at": 0, "end": "2s"}, {"cut_id": cuts[1], "at": "2s", "end": "4s"}]})
    assert e.value.code == "PLAN_REFUSED" and s.doc == before
    s.execute("clip.place_shots", {"sequence_id": sid, "shots": [
        {"cut_id": cuts[2], "at": 0, "end": "2s"}, {"cut_id": cuts[0], "at": "2s", "end": "4s"}]})
    assert len(video(s, sid)) == 2
    s.undo()
    assert s.doc["sequences"] == before["sequences"]


def test_clips_outside_the_plan_are_not_covered_unless_allowed(short):
    s, sid, (a, png), cuts, _ = short
    s.execute("clip.place", {"sequence_id": sid, "media_id": png, "at": "1s", "source_out": "1s"})
    with pytest.raises(CommandError) as e:
        s.execute("clip.place_shots", {"sequence_id": sid, "shots": [{"cut_id": cuts[0], "at": 0, "end": "2s"}]})
    assert e.value.code == "COVERED"
    r = s.execute("clip.place_shots", {"sequence_id": sid, "allow_cover": True,
                                       "shots": [{"cut_id": cuts[0], "at": 0, "end": "2s"}]})["result"]
    assert len(r["covered"]) == 1 and len(video(s, sid)) == 1


def test_explicit_ranges_and_overrides(short):
    s, sid, (a, png), _, _ = short
    s.execute("clip.place_shots", {"sequence_id": sid, "shots": [
        {"media_id": a, "source_in": "1s", "source_out": "3s", "at": 0, "end": "2s", "scale": 140, "position": [600, 900]}]})
    c = video(s, sid)[0]
    assert (c["source_in"], c["speed"], c["motion"]["scale"], c["motion"]["position"]) == (SEC, 1.0, 140, [600, 900])
    with pytest.raises(CommandError, match="outside"):
        s.execute("clip.place_shots", {"sequence_id": sid, "shots": [
            {"media_id": a, "source_in": "1s", "source_out": "4s", "at": "5s", "end": "7s"}]})
