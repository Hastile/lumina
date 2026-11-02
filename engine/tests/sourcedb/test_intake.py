import os
import subprocess
import time

import pytest

from lumina.sourcedb import db, describe, ingest, intake, recorder


def _video(path, seconds=2):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=duration={seconds}", "-shortest", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    old = time.time() - 60
    os.utime(path, (old, old))


@pytest.fixture
def channel(tmp_path, monkeypatch):
    root = tmp_path / "유튜브 채널"
    folder = root / "Project" / "주술회전" / "옷코츠 유카"
    folder.mkdir(parents=True)
    monkeypatch.setattr(ingest, "CHANNEL_ROOT", root)
    monkeypatch.setattr(ingest, "THUMB_ROOT", tmp_path / "thumbs")
    obs = tmp_path / "obs"
    obs.mkdir()
    con = db.connect(tmp_path / "s.db")
    db.entity(con, "work", "주술회전", aliases=["JUJUTSU KAISEN"])
    con.commit()
    con.close()
    return tmp_path, folder, obs


def test_plan_skips_what_is_not_ready_and_never_overwrites(tmp_path):
    src, dest = tmp_path / "obs", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    for name in ("a.mp4", "b.mp4", "notes.txt"):
        (src / name).write_bytes(b"x")
    (dest / "a.mp4").write_bytes(b"old")
    (dest / "here.png").write_bytes(b"x")
    now = time.time() + 60
    os.utime(src / "b.mp4", (now, now))  # still being written
    moves, skipped = intake.plan_moves(
        [src / "a.mp4", src / "a.mp4", src / "b.mp4", src / "notes.txt", src / "gone.mp4", dest / "here.png"], dest, now)
    assert [t.name for _, t in moves] == ["a (2).mp4", "a (3).mp4"]
    assert {p.name: why.split(":")[0] for p, why in skipped} == {
        "b.mp4": "written to in the last seconds", "notes.txt": "not a video or picture", "gone.mp4": "no such file"}


def test_recordings_move_in_and_the_whole_folder_becomes_searchable(channel, monkeypatch):
    tmp, folder, obs = channel
    _video(obs / "2026-10-22 21-00-00.mp4")
    _video(obs / "S2 E16 0931 영역전개.mp4", 3)
    _video(folder / "트레일러.mp4", 4)          # dropped into the folder by hand earlier
    (obs / "nested").mkdir()
    _video(obs / "nested" / "other short.mp4", 5)  # subfolders of the OBS folder are not taken

    def speech(db_path, log=print, stages=()):
        con = db.connect(db_path)
        for (mid,) in con.execute("SELECT media_id FROM media").fetchall():
            name = os.path.basename(db.media_path(con, mid))
            if name.startswith("2026-"):
                db.put_facts(con, mid, "whisper@1", [{"kind": "speech", "value": "리카, 와줘. 전부 부숴", "t0_ticks": 0,
                                                      "t1_ticks": 1}])
        con.commit()
        con.close()
        return []

    monkeypatch.setattr(recorder, "run_analyzers", speech)
    described = []
    monkeypatch.setattr(describe, "run", lambda con, ids, log=print: described.append(ids))
    lines = []
    ids = intake.intake(folder, source=obs, db_path=tmp / "s.db", log=lines.append)

    assert len(ids) == 3
    assert sorted(p.name for p in folder.iterdir()) == ["S2 E16 0931 영역전개.mp4", "리카, 와줘.mp4", "트레일러.mp4"]
    assert not (obs / "2026-10-22 21-00-00.mp4").exists()
    assert (obs / "nested" / "other short.mp4").exists()
    assert described == [sorted(ids)]
    con = db.connect(tmp / "s.db")
    for mid in ids:
        assert db.job_version(con, mid, "cuts") == ingest.CUTS_V
        work = con.execute("SELECT e.name FROM fact f JOIN entity e ON e.entity_id = f.ref_id "
                           "WHERE f.media_id = ? AND f.kind = 'work'", (mid,)).fetchone()
        assert work[0] == "주술회전"
    assert db.media_path(con, ids[0]) is not None
    # every new cut is listed, under its media
    assert sum(l.startswith("== media") for l in lines) == 3
    assert any(l.strip().startswith("#") and "주술회전" in l for l in lines)

    # a second run finds nothing to do
    again = intake.intake(folder, source=obs, db_path=tmp / "s.db", log=lambda *_: None)
    assert again == []


def test_descriptions_wait_when_they_would_cost_too_much(channel, monkeypatch):
    tmp, folder, obs = channel
    _video(obs / "clip.mp4")
    monkeypatch.setattr(recorder, "run_analyzers", lambda *a, **k: [])
    monkeypatch.setattr(describe, "run", lambda *a, **k: pytest.fail("described over the limit"))
    lines = []
    intake.intake(folder, source=obs, db_path=tmp / "s.db", max_cost=0.0, log=lines.append)
    assert any(l.startswith("descriptions skipped") for l in lines)


def test_dry_run_touches_nothing(channel):
    tmp, folder, obs = channel
    _video(obs / "clip.mp4")
    lines = []
    assert intake.intake(folder, source=obs, db_path=tmp / "s.db", dry_run=True, log=lines.append) == []
    assert (obs / "clip.mp4").exists() and not (folder / "clip.mp4").exists()
    assert lines[0].startswith("move")
