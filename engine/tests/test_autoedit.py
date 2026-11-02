"""Source library indexing/search and building whole edits from a plan (real FFmpeg, mocked Claude)."""

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from lumina import autoedit, library
from lumina.commands.registry import CommandError
from lumina.engine import Engine
from lumina.timebase import TICKS_PER_SECOND as SEC

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def footage(tmp_path_factory):
    """two-shot clip (bright bars, then near-black) with a tone, a second clip, a music track and a still."""
    root = tmp_path_factory.mktemp("footage")
    (root / "cam").mkdir()
    _ff("-f", "lavfi", "-i", "smptebars=size=640x360:rate=30:duration=3",
        "-f", "lavfi", "-i", "color=c=0x080808:size=640x360:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(root / "cam" / "beach sunset.mp4"))
    _ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(root / "cam" / "city.mp4"))
    _ff("-f", "lavfi", "-i", "sine=frequency=220:duration=3", str(root / "music.wav"))
    _ff("-f", "lavfi", "-i", "color=c=red:size=640x360", "-frames:v", "1", str(root / "title.png"))
    (root / "notes.txt").write_text("not media")
    res = library.index(root, transcribe_mode="off")
    assert res["errors"] == []
    return root


def test_index_finds_media_shots_and_quality(footage):
    data = library.load(footage)
    assert sorted(data["files"]) == ["cam/beach sunset.mp4", "cam/city.mp4", "music.wav", "title.png"]
    beach = data["files"]["cam/beach sunset.mp4"]
    assert len(beach["shots"]) == 2, "the cut to black is a scene change"
    bars, dark = beach["shots"]
    assert abs(bars["end"] - 3.0) < 0.2
    assert "dark" in library.shot_flags(dark) and "dark" not in library.shot_flags(bars)
    assert bars["sound"] > 0.9 and "speech/sound" in library.shot_flags(bars)
    assert (library.index_dir(footage) / "frames" / bars["keyframe"]).exists()
    assert data["files"]["music.wav"]["shots"][0]["keyframe"] is None
    again = library.index(footage, transcribe_mode="off")
    assert again["analyzed"] == 0 and again["unchanged"] == 4, "unchanged files are not re-analysed"


def test_annotate_search_overview_and_contact_sheet(footage):
    data = library.load(footage)
    beach = data["files"]["cam/beach sunset.mp4"]
    sid = beach["shots"][0]["id"]
    assert library.annotate(footage, [{"shot": sid, "desc": "해변 일몰 와이드샷", "tags": ["#beach", "sunset"]},
                                      {"shot": "nope-1"}])["missing"] == ["nope-1"]
    hits = library.search(footage, "해변")["results"]
    assert hits[0]["shot"] == sid, "substring match works for Korean words"
    assert library.search(footage, "beach")["results"][0]["tags"] == ["beach", "sunset"]
    assert [h["file"] for h in library.search(footage, "city")["results"]] == ["cam/city.mp4"]
    assert all("dark" not in h["flags"] for h in library.search(footage, "", include_flagged=False)["results"])
    log = library.overview_text(footage)
    assert sid in log and "해변 일몰" in log and "## music.wav" in log
    sheet = library.contact_sheet(footage, file="cam/beach sunset.mp4")
    assert sheet["shots"] == [s["id"] for s in beach["shots"]]
    library.index(footage, transcribe_mode="off", force=True)
    assert library.search(footage, "해변")["results"][0]["desc"] == "해변 일몰 와이드샷", "re-index keeps annotations"


def _engine():
    e = Engine(run_jobs=False)
    e.dispatch("project.new", {"name": "t"})
    return e


def _clips(e, sid, kind="video_tracks", idx=0):
    seq = e.session.doc["sequences"][sid]
    return sorted(seq["tracks"][seq[kind][idx]]["clips"].values(), key=lambda c: c["start"])


def test_build_assembles_one_undoable_sequence(footage):
    data = library.load(footage)
    beach = data["files"]["cam/beach sunset.mp4"]["shots"]
    e = _engine()
    plan = {"title": "Trip", "library": str(footage), "format": {"aspect": "9:16"},
            "clips": [{"shot": beach[0]["id"], "in": 0.5, "out": 2.5, "caption": "Day 1"},
                      {"file": "cam/city.mp4", "in": 1, "out": 3, "transition": "dissolve", "mute": True},
                      {"file": "title.png", "duration": 1.5, "transition": "dip_to_black"},
                      {"shot": "missing-0"}],
            "music": {"file": "music.wav", "volume_db": -20}}
    r = e.dispatch("edit.build", {"plan": plan})
    sid = r["sequence_id"]
    assert r["clips"] == 3 and abs(r["duration"] - 5.5) < 0.05
    assert any("missing-0" in w for w in r["warnings"])
    seq = e.session.doc["sequences"][sid]
    assert (seq["settings"]["width"], seq["settings"]["height"]) == (360, 640)
    v = _clips(e, sid)
    assert [round(c["source_in"] / SEC, 2) for c in v[:2]] == [0.5, 1.0]
    assert v[0]["motion"]["scale"] == pytest.approx(177.78, abs=0.01), "16:9 source fills the 9:16 frame"
    trans = seq["tracks"][seq["video_tracks"][0]]["transitions"]
    assert sorted(t["type"] for t in trans.values()) == ["cross_dissolve", "dip_to_black"], r["warnings"]
    a1 = _clips(e, sid, "audio_tracks", 0)
    assert len(a1) == 1, "city.mp4 has no audio; the still has none either"
    music = _clips(e, sid, "audio_tracks", 1)
    assert len(music) == 2 and music[-1]["end"] == v[-1]["end"], "a 3 s track loops to cover 5.5 s"
    assert all(c["volume_db"] == -20 for c in music)
    caps = [c for tid in seq["video_tracks"] for c in seq["tracks"][tid]["clips"].values() if c.get("caption")]
    assert len(caps) == 1
    e.dispatch("undo")
    assert sid not in e.session.doc["sequences"] and not e.session.doc["media"], "the whole build is one undo step"


def test_build_exports(footage, tmp_path):
    e = _engine()
    out = tmp_path / "cut.mp4"
    r = e.dispatch("edit.build", {"plan": {"library": str(footage), "clips": [{"file": "cam/city.mp4", "in": 0, "out": 1}],
                                           "output": str(out), "project": str(tmp_path / "cut.lumina")}})
    assert r["export"]["path"] == str(out) and out.exists()
    assert (tmp_path / "cut.lumina").exists()


def test_build_rejects_plans_without_picture(footage):
    with pytest.raises(CommandError):
        _engine().dispatch("edit.build", {"plan": {"library": str(footage), "clips": [{"file": "music.wav"}]}})


class FakeClaude:
    """Records requests; answers with canned structured output."""

    def __init__(self, answers):
        self.answers, self.requests = answers if callable(answers) else list(answers), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(
                                   self.answers(kw) if callable(self.answers) else self.answers.pop(0)))])


def test_describe_and_plan_with_mocked_claude(footage):
    data = library.load(footage)
    beach = data["files"]["cam/beach sunset.mp4"]["shots"]
    city = data["files"]["cam/city.mp4"]["shots"]
    undescribed = {f["rel"] for f in data["files"].values() if f["kind"] != "audio" and any(not s["desc"] for s in f["shots"])}

    def vision(kw):  # describes city.mp4, and also names a shot that is not on the sheet
        if "cam/city.mp4" not in kw["messages"][0]["content"][1]["text"]:
            return {"shots": []}
        return {"shots": [{"shot": city[0]["id"], "desc": "test pattern", "tags": ["pattern"]},
                          {"shot": beach[1]["id"], "desc": "ignored", "tags": []}]}

    claude = FakeClaude(vision)
    autoedit.describe_shots(footage, client=claude)
    assert len(claude.requests) == len(undescribed), "one vision request per file with undescribed shots"
    req = next(r for r in claude.requests if "cam/city.mp4" in r["messages"][0]["content"][1]["text"])
    assert req["messages"][0]["content"][0]["type"] == "image"
    assert req["output_config"]["format"]["schema"] == autoedit.DESCRIBE_SCHEMA
    lib = library.load(footage)
    assert library.find_shot(lib, city[0]["id"])[1]["desc"] == "test pattern"
    assert not any(s["desc"] == "ignored" for _f, s in library.iter_shots(lib)), "only the sheet's own shots are written"

    answer = {"title": "Short", "clips": [{"shot": beach[0]["id"], "in": 0, "out": 2, "transition": "cut", "caption": "",
                                           "mute": False}],
              "music": "music.wav", "music_volume_db": -18, "subtitles": "none", "notes": "one shot"}
    claude = FakeClaude([answer])
    plan = autoedit.plan_with_claude(footage, "15초 쇼츠", duration=15, aspect="9:16", client=claude)
    req = claude.requests[0]
    assert req["model"] == autoedit.DEFAULT_MODEL and req["fallbacks"] == "default"
    assert beach[0]["id"] in req["messages"][0]["content"][0]["text"], "the footage log goes to the planner"
    assert "15 seconds" in req["messages"][0]["content"][1]["text"]
    assert plan["format"] == {"aspect": "9:16"} and plan["music"]["file"] == "music.wav"
    r = _engine().dispatch("edit.build", {"plan": plan})
    assert r["clips"] == 1 and r["music_clips"] == 1


def test_planner_refusal_is_an_error(footage):
    claude = FakeClaude([])
    claude.beta.messages.create = lambda **kw: SimpleNamespace(stop_reason="refusal", content=[],
                                                               stop_details=SimpleNamespace(explanation="no"))
    with pytest.raises(CommandError, match="declined"):
        autoedit.plan_with_claude(footage, "x", client=claude)
