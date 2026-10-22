import base64
import hashlib
import json
import subprocess
import time
import urllib.request

import pytest

from lumina.sourcedb import db, ingest, recorder
from lumina.sourcedb.recorder import Playback, Recorder, Take


def test_player_titles():
    t = "JUJUTSU KAISEN Season 2 - Episode 16 | Re#3AANIME - Chrome"
    assert recorder.clean_title(t) == "JUJUTSU KAISEN Season 2 - Episode 16 | Re#3AANIME"
    assert recorder.work_candidates(t)[0] == "JUJUTSU KAISEN"
    assert ingest.episode_of(recorder.clean_title(t)) == ("S2 E16", None)
    assert ingest.episode_of("Sousou no Frieren Episode 7") == ("E07", None)
    assert recorder.work_candidates("(3) 장송의 프리렌 2기 4화 - 라프텔 - Chrome")[0] == "장송의 프리렌"


def test_clip_names_follow_the_channel():
    assert recorder.clip_name("S2 E16", 571.9, None, "x") == "S2 E16 0931"
    assert recorder.clip_name("E01", 776, "재의 왕을: 쓰러뜨리는?", "x") == "E01 1256 재의 왕을 쓰러뜨리는"
    assert recorder.clip_name(None, 3725, None, "x") == "010205"
    assert recorder.clip_name(None, None, None, "2026-10-22 21-00-00") == "2026-10-22 21-00-00"
    assert recorder.first_line("저 구름 고기만두 같아. 맛있겠다") == "저 구름 고기만두 같아."


def test_obs_auth_is_the_documented_double_hash():
    secret = base64.b64encode(hashlib.sha256(b"pw" + b"salt").digest()).decode()
    expected = base64.b64encode(hashlib.sha256((secret + "chal").encode()).digest()).decode()
    assert recorder.obs_auth("pw", "salt", "chal") == expected


def test_board_prefers_the_fresh_playing_video():
    b = recorder.PlaybackBoard()
    b.report(Playback("1:0", "ad", "u", 3.0, 30, paused=True))
    b.report(Playback("2:5", "Episode 3", "u", 100.0, 1400, paused=False))
    assert b.current().tab == "2:5"
    old = Playback("3:0", "old", "u", 1, 1, paused=False)
    old.received -= 10
    b2 = recorder.PlaybackBoard()
    b2.report(old)
    assert b2.current() is None


def test_extension_reports_reach_the_board():
    b = recorder.PlaybackBoard()
    server = recorder.serve_board(b, port=0)
    try:
        body = json.dumps({"tab": "7:0", "title": "Episode 2", "url": "https://x", "currentTime": 12.5,
                           "duration": 1400, "paused": False}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/playback", body,
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        assert b.current().current == 12.5
    finally:
        server.shutdown()


@pytest.fixture
def channel(tmp_path, monkeypatch):
    root = tmp_path / "유튜브 채널"
    folder = root / "Project" / "주술회전" / "옷코츠 유카"
    folder.mkdir(parents=True)
    monkeypatch.setattr(ingest, "CHANNEL_ROOT", root)
    monkeypatch.setattr(ingest, "THUMB_ROOT", tmp_path / "thumbs")
    monkeypatch.setattr(recorder, "STATE_PATH", tmp_path / "recorder.json")
    recorder.set_target_folder(folder)
    obs = tmp_path / "obs"
    obs.mkdir()
    clip = obs / "2026-10-22 21-00-00.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "-pix_fmt", "yuv420p", str(clip)], check=True)
    con = db.connect(tmp_path / "s.db")
    db.entity(con, "work", "주술회전", aliases=["JUJUTSU KAISEN"])
    con.commit()
    con.close()
    return tmp_path, folder, clip


def _take(title="JUJUTSU KAISEN Season 2 - Episode 16 | Site - Chrome", at=571.2):
    p = Playback("1:0", title, "https://example.test/watch/16", at, 1420, paused=False)
    return Take(time.monotonic(), p, at)


def test_a_recording_lands_named_and_ingested(channel):
    tmp, folder, clip = channel
    rec = Recorder(tmp / "s.db", run_analyzers=False, log=lambda *_: None)
    mid = rec.file(clip, _take())
    dest = folder / "S2 E16 0931.mp4"
    assert dest.exists() and not clip.exists()
    con = db.connect(tmp / "s.db")
    facts = {(r["kind"], r["value"] or r["num"]) for r in con.execute(
        "SELECT f.kind, COALESCE(e.name, f.value) AS value, f.num FROM fact f LEFT JOIN entity e ON e.entity_id = f.ref_id "
        "WHERE f.media_id = ? AND f.status = 'accepted'", (mid,))}
    assert ("episode", "S2 E16") in facts
    assert ("source_time", 571.2) in facts
    assert ("work", "주술회전") in facts          # the tab title names it, and the folder agrees
    assert db.media_path(con, mid) == str(dest.resolve())


def test_the_first_spoken_line_joins_the_name(channel, monkeypatch):
    tmp, folder, clip = channel
    rec = Recorder(tmp / "s.db", run_analyzers=False, log=lambda *_: None)
    mid = rec.file(clip, _take())
    con = db.connect(tmp / "s.db")
    db.put_facts(con, mid, "whisper@1", [{"kind": "speech", "value": "영역전개. 무량공처", "t0_ticks": 0, "t1_ticks": 1}])
    con.commit()
    con.close()
    monkeypatch.setattr(recorder.subprocess, "run", lambda *a, **k: None)
    named = rec._analyze_and_name(mid, folder / "S2 E16 0931.mp4")
    assert named.name == "S2 E16 0931 영역전개.mp4"
    con = db.connect(tmp / "s.db")
    assert db.media_path(con, mid) == str(named.resolve())


def test_without_a_player_the_recording_keeps_its_name(channel):
    tmp, folder, clip = channel
    rec = Recorder(tmp / "s.db", run_analyzers=False, log=lambda *_: None)
    rec.file(clip, Take(time.monotonic(), None, None))
    assert (folder / "2026-10-22 21-00-00.mp4").exists()


def test_plans_read_like_the_channel_writes_times():
    clips = recorder.parse_plan("""
# 고죠 컷들
folder: D:/유튜브 채널/Project/주술회전/고죠 사토루
S2 E16 09:20-09:45 고죠 등장
S2 E16 0931-0940
E04 1:02:03-1:02:10 https://example.test/ep4#player 마지막
12:00 - 12:05
""")
    assert [(c.episode, c.start, c.end) for c in clips] == [
        ("S2 E16", 560, 585), ("S2 E16", 571, 580), ("E04", 3723, 3730), (None, 720, 725)]
    assert clips[0].note == "고죠 등장" and str(clips[0].folder).endswith("고죠 사토루")
    assert clips[2].url == "https://example.test/ep4#player" and clips[2].note == "마지막"
    with pytest.raises(ValueError):
        recorder.parse_plan("S2 E16 09:45-09:20")


class FakePlayer:
    """The extension and a video, in one thread: reports every 20 ms, obeys
    the commands in the answers, and keeps time while playing."""

    def __init__(self, board, title, url="https://example.test/16"):
        import threading
        self.board, self.title, self.url = board, title, url
        self.t, self.playing, self.stop = 0.0, False, threading.Event()
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        last = time.monotonic()
        while not self.stop.is_set():
            now = time.monotonic()
            if self.playing:
                self.t += now - last
            last = now
            cmd = self.board.report(Playback("9:0", self.title, self.url, self.t, 1400, not self.playing))
            for k, v in (cmd or {}).items():
                if k == "seek":
                    self.t = v
                elif k == "play":
                    self.playing = True
                elif k == "pause":
                    self.playing = False
                elif k == "navigate":
                    self.url, self.title = v, "JUJUTSU KAISEN Season 2 - Episode 17 | Site"
            time.sleep(0.02)


class FakeObs:
    def __init__(self, rec, player):
        self.rec, self.player, self.log = rec, player, []

    def request(self, kind):
        self.log.append((kind, round(self.player.t, 2)))
        state = "OBS_WEBSOCKET_OUTPUT_STARTED" if kind == "StartRecord" else "OBS_WEBSOCKET_OUTPUT_STOPPED"
        self.rec.on_record(state, "C:/fake.mp4" if kind == "StopRecord" else None)
        return {}


def test_a_plan_seeks_plays_and_records_each_clip(monkeypatch):
    monkeypatch.setattr(recorder, "PREROLL", 0.3)
    rec = Recorder(run_analyzers=False, log=lambda *_: None)
    player = FakePlayer(rec.board, "JUJUTSU KAISEN Season 2 - Episode 16 | Site - Chrome")
    obs = FakeObs(rec, player)
    try:
        ok = recorder.PlanRunner(rec, obs, log=lambda *_: None, poll=0.02).run(
            [recorder.Clip("S2 E16", 571.0, 571.5), recorder.Clip("S2 E17", 10.0, 10.4, url="https://example.test/17")])
    finally:
        player.stop.set()
    assert ok == [True, True]
    (s1, t1), (e1, u1), (s2, t2), (e2, u2) = obs.log
    assert (s1, e1, s2, e2) == ("StartRecord", "StopRecord", "StartRecord", "StopRecord")
    assert 570.6 <= t1 <= 571.0 and u1 >= 571.7          # starts just before, stops just after the end
    assert 9.6 <= t2 <= 10.0 and u2 >= 10.6
    assert player.url.endswith("/17")                      # it opened the second episode itself
    path, take = rec.jobs.get_nowait()
    assert take.playback.title.startswith("JUJUTSU KAISEN Season 2 - Episode 16")
    assert abs(take.player_at_start - t1) < 0.2
