"""shorts_plan (captions -> shots/effects/sounds), shorts.caption_effects and shorts.place_sfx."""
import json
from collections import Counter

import pytest

from lumina import shorts_plan as sp
from lumina.commands.registry import CommandError
from lumina.engine import Engine
from lumina.session import Session
from lumina.timebase import TICKS_PER_SECOND as SEC

from .test_session import FakeServices


def cues(rows):
    """rows: (seconds, text) back to back from 0."""
    out, t = [], 0.0
    for n, (d, text) in enumerate(rows):
        out.append(sp.Cue(n, round(t * SEC), round((t + d) * SEC), text))
        t += d
    return out


# the approved 사스케 닮은꼴 revenge sentence and its neighbours (timings rounded from the edit)
SHORT = [
    (1.0, "사이버펑크 2077 속"), (1.2, "이 캐릭터를"), (0.9, "아시나요?"),                          # hook question
    (0.4, "그리고"), (1.0, "그의 이름은"), (1.3, "바로 사스케입니다"),                              # lead-in + reveal
    (0.4, "심지어"), (1.2, "자신의 일족을 몰살한"), (1.0, "범인을 찾아"), (1.1, "복수하려"),
    (1.0, "한다는 설정마저"), (1.2, "사스케와 닮았죠"),
    (1.2, "친구라는 관계부터"), (1.3, "적 진영에 합류해"), (1.1, "싸우는 것까지,"), (1.4, "모두 똑같습니다"),
    (1.3, "여러분은"), (1.2, "어떻게 생각하시나요?"), (1.5, "댓글로 알려주세요!"),
]


def test_sentences_and_endings():
    c = cues(SHORT)
    sents = sp.sentences_of(c)
    assert [len(s) for s in sents] == [3, 3, 6, 4, 2, 1]
    assert sp.is_clause_end("범인을 찾아서") and sp.is_clause_end("싸우는 것까지,")
    assert sp.is_bound("자신의 일족을 몰살한") and sp.is_bound("이 캐릭터를") and not sp.is_bound("그의 이름은")
    assert sp.is_question("아시나요?") and sp.is_sentence_end("닮았죠") and not sp.is_sentence_end("합류해")


def test_shots_follow_the_grammar():
    plan = sp.make_plan(cues(SHORT))
    texts = [s.text for s in plan.shots]
    assert all(s.seconds >= 1.1 for s in plan.shots), texts
    assert any(t.startswith("그리고 그의 이름은") for t in texts), "a lead-in under 0.5 s rides with the next caption"
    starts = {s.cues[0].text for s in plan.shots}
    assert "사스케와 닮았죠" not in starts, "never cut onto the sentence tail"
    assert "범인을 찾아" not in starts, "never split an adnominal from its head (몰살한 → 범인을)"
    assert "복수하려" in starts, "a clause end (찾아) after ≥1 s takes the sentence's second shot"
    # every cut is on a caption start and the shots tile the captions in order
    flat = [c.i for s in plan.shots for c in s.cues]
    assert flat == list(range(len(SHORT)))
    assert plan.problems == []


def test_reveal_forces_a_cut():
    c = cues(SHORT)
    plan = sp.make_plan(c, reveal=(5,))
    assert any(s.cues[0].text == "바로 사스케입니다" for s in plan.shots)


def test_effects_and_sounds():
    plan = sp.make_plan(cues(SHORT))
    eff = plan.effects
    by = {c.text: c.i for c in plan.cues}
    assert eff[by["사스케와 닮았죠"]] == "fade" and eff[by["모두 똑같습니다"]] == "fade", "sentence tails fade"
    assert eff[by["바로 사스케입니다"]] == "fade", "a long final predicate closes with a fade"
    assert eff[by["이 캐릭터를"]] == "slam", "the hook question slams its focus phrase"
    slams = sorted(i for i, e in eff.items() if e == "slam")
    assert all(b - a > 1 for a, b in zip(slams, slams[1:])), "never two slams in a row"
    assert not any(eff[c.i] == "slam" for c in plan.sentences[-1]), "no slam in the closing sentence"
    snd = plan.sounds
    assert snd[0] == "W3" and snd[by["이 캐릭터를"]] == "뚜훅" and snd[by["아시나요?"]] == "Bells 2"
    assert snd[by["댓글로 알려주세요!"]] == "티딩"
    whooshes = [snd[i] for i in sorted(snd) if snd[i].startswith("W") and i]
    assert whooshes and whooshes[0] == "W4", "the body opener gets W4"
    assert all(a != b for a, b in zip(whooshes, whooshes[1:])), "the same whoosh never twice running"
    starts = sorted(plan.cues[i].start for i in snd)
    assert all(b - a >= 0.5 * SEC for a, b in zip(starts, starts[1:]))
    text = sp.render(plan, 30, 1)
    assert "┃S1 0f-" in text and "W3" in text


def test_srt_parsing():
    srt = "1\n00:00:00,000 --> 00:00:01,200\n이 캐릭터를\n\n2\n00:00:01,200 --> 00:00:02,500\n아시나요?\n"
    c = sp.parse_srt(srt)
    assert [(x.text, x.start, x.end) for x in c] == [("이 캐릭터를", 0, round(1.2 * SEC)), ("아시나요?", round(1.2 * SEC), round(2.5 * SEC))]


# ---------------------------------------------------------------- commands on a Shorts sequence

TRACKS = ("자막 - 커졌다 줄어들기", "자막 - 꽝 박히기", "자막 - 밑에서 스무쓰하게 올라오기")


class SfxServices(FakeServices):
    def probe(self, path):
        if path.endswith((".wav", ".mp3")):
            return {"name": path.rsplit("/", 1)[-1], "kind": "audio", "duration": 2 * SEC, "video": None,
                    "audio": {"channels": 2, "sample_rate": 48000, "codec": "pcm"}}
        return super().probe(path)


def shorts_session(s=None):
    s = s or Session(services=SfxServices())
    sid = s.execute("sequence.create", {"name": "Short", "settings": {"width": 1080, "height": 1920},
                                        "video_tracks": 6, "audio_tracks": 5})["result"]["sequence_id"]
    tids = [s.execute("caption.add_track", {"name": n})["result"]["track_id"] for n in TRACKS]
    ids = []
    for n, (d, text) in enumerate(SHORT[:6]):
        r = s.execute("caption.add", {"track_id": tids[0], "text": text, "at": round(sum(x for x, _ in SHORT[:n]) * SEC),
                                      "duration": round(d * SEC)})
        ids.append(r["result"]["clip_id"])
    return s, sid, tids, ids


def cue_track(s, sid, cid):
    seq = s.doc["sequences"][sid]
    return next(t for t in seq["video_tracks"] if cid in seq["tracks"][t]["clips"])


def test_sequence_captions_feed_the_plan():
    e = Engine(services=SfxServices(), run_jobs=False)
    shorts_session(e.session)
    out = e.dispatch("shorts.plan", {"format": "json"})
    assert [c["text"] for c in out["captions"]] == [t for _, t in SHORT[:6]]
    assert all(c["now"] == "pop" for c in out["captions"])
    assert out["shots"][0]["at"] == "0f"


def test_caption_effects_move_and_size_as_one_edit():
    s, sid, tids, ids = shorts_session()
    r = s.execute("shorts.caption_effects", {"items": [{"clip_id": ids[1], "effect": "slam"},
                                                       {"clip_id": ids[2], "effect": "fade"},
                                                       {"clip_id": ids[0], "effect": "pop", "size": 83}]})["result"]
    assert cue_track(s, sid, ids[1]) == tids[1] and cue_track(s, sid, ids[2]) == tids[2] and cue_track(s, sid, ids[0]) == tids[0]
    seq = s.doc["sequences"][sid]
    slam = seq["tracks"][tids[1]]["clips"][ids[1]]
    row = next(x for x in r["captions"] if x["clip_id"] == ids[1])
    assert slam["caption"]["style"]["font_size"] in (120.0,) or "note" in row
    assert seq["tracks"][tids[0]]["clips"][ids[0]]["caption"]["style"]["font_size"] == 83
    s.execute("shorts.caption_effects", {"items": [{"clip_id": ids[1], "effect": "pop"}]})
    back = s.doc["sequences"][sid]["tracks"][tids[0]]["clips"][ids[1]]
    assert not (back["caption"].get("style") or {}).get("font_size"), "back to pop drops the slam size"
    s.undo()
    s.undo()
    assert cue_track(s, sid, ids[1]) == tids[0], "one undo per command"


def test_caption_effects_refuse_everything_on_a_problem():
    s, sid, tids, ids = shorts_session()
    s.execute("caption.add", {"track_id": tids[1], "text": "막는 자막", "at": 0, "duration": "3s"})
    before = s.version
    with pytest.raises(CommandError) as err:
        s.execute("shorts.caption_effects", {"items": [{"clip_id": ids[3], "effect": "fade"},
                                                       {"clip_id": ids[1], "effect": "slam"}]})
    assert "another cue" in err.value.message
    assert s.version == before and cue_track(s, sid, ids[3]) == tids[0], "nothing moved"


def test_place_sfx_by_name_on_grammar_tracks():
    s, sid, tids, ids = shorts_session()
    s.execute("media.import", {"paths": ["/sfx/KF - Whoosh Dramatic 3.wav", "/sfx/KF - Whoosh Dramatic 4.wav",
                                         "/sfx/Epic Hit 3.wav", "/sfx/티딩! 효과음.mp3", "/sfx/Bells 2.wav",
                                         "/sfx/Kick 2.wav", "/sfx/Kick 25.wav"]})
    r = s.execute("shorts.place_sfx", {"auto_match": False, "end_at": "200f", "sounds": [
        {"at": 0, "sound": "W3"}, {"at": "30f", "sound": "W4"}, {"at": "90f", "sound": "Epic Hit 3"},
        {"at": "60f", "sound": "Bells 2"}, {"at": "190f", "sound": "티딩"}, {"at": "120f", "sound": "Kick 2"}]})["result"]
    rows = {x["sound"]: x for x in r["sounds"]}
    assert rows["W3"]["track"] == rows["W4"]["track"] == "A2"
    assert rows["Bells 2"]["track"] == "A4" and rows["Epic Hit 3"]["track"] == "A3" == rows["티딩"]["track"]
    assert rows["Epic Hit 3"]["at_frame"] == 69 and rows["Epic Hit 3"]["pre_roll_frames"] == 21
    assert rows["W3"]["frames"] == 30 and rows["W3"]["tail_cut"], "W3's tail stops where W4 starts"
    assert rows["티딩"]["frames"] == 10, "tails stop at end_at"
    assert rows["Kick 2"]["file"] == "Kick 2.wav", "Kick 2 is not Kick 25"
    seq = s.doc["sequences"][sid]
    clips = {c["id"]: c for t in seq["audio_tracks"] for c in seq["tracks"][t]["clips"].values()}
    tid = clips[rows["티딩"]["clip_id"]]
    assert (tid["volume_db"], tid["gain_db"], tid["audio_tag"]) == (0.0, 0.0, "sfx"), "no clip volume cut, no fixed gain"
    assert all(clips[x["clip_id"]]["volume_db"] == 0.0 for x in r["sounds"])
    assert all(clips[x["clip_id"]]["audio_tag"] == "sfx" for x in r["sounds"])
    s.undo()
    assert not any(seq["tracks"][t]["clips"] for t in s.doc["sequences"][sid]["audio_tracks"]), "one undo removes all"


def test_place_sfx_refuses_unknown_or_blocked_sounds():
    s, sid, tids, ids = shorts_session()
    s.execute("media.import", {"paths": ["/sfx/Kick 2.wav"]})
    with pytest.raises(CommandError, match="no such sound"):
        s.execute("shorts.place_sfx", {"auto_match": False, "sfx_dirs": ["/nonexistent"],
                                       "sounds": [{"at": 0, "sound": "Kick 2"}, {"at": "30f", "sound": "H:Impactor"}]})
    s.execute("shorts.place_sfx", {"auto_match": False, "sounds": [{"at": "30f", "sound": "Kick 2"}]})
    with pytest.raises(CommandError, match="another clip"):
        s.execute("shorts.place_sfx", {"auto_match": False, "sounds": [{"at": "20f", "sound": "Kick 2"}]})


def test_place_sfx_finds_files_in_sfx_folders(tmp_path):
    (tmp_path / "pack").mkdir()
    for n in ("H - Cash.wav", "H - Cash Register.wav", "readme.txt"):
        (tmp_path / "pack" / n).write_bytes(b"")
    s, sid, tids, ids = shorts_session()
    r = s.execute("shorts.place_sfx", {"auto_match": False, "sfx_dirs": [str(tmp_path)],
                                       "sounds": [{"at": "10f", "sound": "H:Cash"}]})["result"]
    assert r["sounds"][0]["file"] == "H - Cash.wav" and r["sounds"][0]["also_matched"] == ["H - Cash Register.wav"]


# ---------------------------------------------------------------- shorts.build and shorts_check

from lumina import shorts_check  # noqa: E402

F30 = SEC // 30


class BuildServices(SfxServices):
    def probe(self, path):
        if "narr" in path:
            return {"name": "narr.wav", "kind": "audio", "duration": round(5.8 * SEC), "video": None,
                    "audio": {"channels": 1, "sample_rate": 48000, "codec": "pcm"}}
        return super().probe(path)


def build_session():
    s, sid, tids, ids = shorts_session(Session(services=BuildServices()))
    mids = s.execute("media.import", {"paths": ["a.mp4", "b.mp4", "/v/narr.wav", "/sfx/KF - Whoosh Dramatic 3.wav",
                                                "/sfx/KF - Whoosh Dramatic 4.wav", "/sfx/뚜훅.wav", "/sfx/Bells 2.wav"]}
                     )["result"]["media_ids"]
    seq = s.doc["sequences"][sid]
    s.execute("clip.place", {"media_id": mids[2], "at": 0, "audio_track_id": seq["audio_tracks"][0], "take_video": False})
    return s, sid, tids, ids, mids


def test_build_does_the_whole_short_in_one_undo():
    s, sid, tids, ids, mids = build_session()
    version = s.version
    r = s.execute("shorts.build", {"auto_match": False, "shots": [
        {"at": 0, "end": "93f", "media_id": mids[0]},
        {"at": "93f", "end": "174f", "media_id": mids[1], "source_in": "5s"}]})["result"]
    seq = s.doc["sequences"][sid]
    end = round(5.8 * SEC) + 6 * F30
    assert r["placed"] == 2 and r["end_frame"] == end // F30
    v1 = sorted(seq["tracks"][seq["video_tracks"][0]]["clips"].values(), key=lambda c: c["start"])
    assert v1[-1]["end"] == end, "the last shot reaches narration + 6 frames"
    cues = [c for t in tids for c in seq["tracks"][t]["clips"].values()]
    assert max(c["end"] for c in cues) == end, "so does the last caption"
    assert cue_track(s, sid, ids[1]) == tids[1], "the draft's hook slam is on the slam track"
    assert any(x.endswith("W3 (A2)") for x in r["sounds"]) and any("뚜훅" in x for x in r["sounds"])
    assert "sounds not found, skipped: 티딩" in " ".join(r["notes"])
    assert "shots" in r["check"]
    assert s.version > version
    s.undo()
    seq = s.doc["sequences"][sid]
    assert not seq["tracks"][seq["video_tracks"][0]]["clips"] and cue_track(s, sid, ids[1]) == tids[0]
    assert not any(seq["tracks"][t]["clips"] for t in seq["audio_tracks"][1:]), "one undo removes the whole build"


def test_build_refusal_changes_nothing():
    s, sid, tids, ids, mids = build_session()
    version = s.version
    with pytest.raises(CommandError):
        s.execute("shorts.build", {"auto_match": False, "shots": [{"at": 0, "end": "93f", "media_id": mids[0]}],
                                   "sounds": [{"at": 0, "sound": "없는 소리"}]})
    assert s.version == version
    seq = s.doc["sequences"][sid]
    assert not seq["tracks"][seq["video_tracks"][0]]["clips"]


def test_check_lists_only_problems():
    s, sid, tids, ids, mids = build_session()
    seq = s.doc["sequences"][sid]
    v1, a2 = seq["video_tracks"][0], seq["audio_tracks"][1]
    s.execute("clip.place", {"media_id": mids[0], "at": 0, "source_out": "30f", "video_track_id": v1, "take_audio": False})
    s.execute("clip.place", {"media_id": mids[1], "at": "40f", "source_out": "5s", "video_track_id": v1, "take_audio": False})
    s.execute("clip.place", {"media_id": mids[3], "at": 0, "audio_track_id": a2, "take_video": False, "source_out": "5f"})
    s.execute("clip.place", {"media_id": mids[4], "at": "6f", "audio_track_id": a2, "take_video": False, "source_out": "5f"})
    rep = shorts_check.check(s.doc, s.doc["sequences"][sid], use_db=False)
    kinds = {p["kind"] for p in rep["problems"]}
    assert {"short shot", "gap", "off caption", "ending", "sfx tag", "sfx close"} <= kinds, kinds
    text = shorts_check.render(rep)
    assert "source DB not consulted" in text and "black gap of 10 frames" in text


def test_check_passes_a_clean_build():
    s, sid, tids, ids, mids = build_session()
    s.execute("shorts.build", {"auto_match": False, "sounds": [], "effects": [], "shots": [
        {"at": 0, "end": "93f", "media_id": mids[0]}, {"at": "93f", "end": "174f", "media_id": mids[1], "source_in": "5s"}]})
    rep = shorts_check.check(s.doc, s.doc["sequences"][sid], use_db=False)
    assert rep["ok"], shorts_check.render(rep)


def test_check_uses_the_source_db_for_flashes_and_dark_shots(tmp_path):
    from lumina.sourcedb import db

    clip = tmp_path / "ep1.mp4"
    clip.write_bytes(b"x")
    con = db.connect(tmp_path / "source.db")
    mid, _ = db.register_file(con, clip, "video", probe={"duration_ticks": 20 * SEC, "fps_num": 30, "fps_den": 1})
    db.set_cuts(con, mid, [{"start_ticks": 0, "end_ticks": 5 * SEC, "keyframe_ticks": 0},
                           {"start_ticks": 5 * SEC, "end_ticks": 20 * SEC, "keyframe_ticks": 5 * SEC}])
    db.put_facts(con, mid, "probe@1", [{"kind": "luma", "num": 0.05, "t0_ticks": 5 * SEC, "t1_ticks": 20 * SEC}])
    con.commit()
    con.close()
    s, sid, tids, ids, mids = build_session()
    m = s.execute("media.import", {"paths": [str(clip)]})["result"]["media_ids"][0]
    v1 = s.doc["sequences"][sid]["video_tracks"][0]
    # starts 2 frames before the scene change at 5 s: the previous shot flashes, then the dark shot runs
    s.execute("clip.place", {"media_id": m, "at": 0, "source_in": 5 * SEC - 2 * F30, "source_out": 8 * SEC,
                             "video_track_id": v1, "take_audio": False})
    rep = shorts_check.check(s.doc, s.doc["sequences"][sid], db_path=tmp_path / "source.db")
    kinds = {p["kind"] for p in rep["problems"]}
    assert {"flash", "dark"} <= kinds and rep["summary"]["source_db"]


def test_calibration_against_an_archive_short():
    """One archive row in cut_grammar_extract.py's format: the user cut where the draft does, except one."""
    from lumina import shorts_calibrate as sc

    caps, t = [], 0.0
    effects = ["pop", "slam", "fade", "pop", "pop", "fade"]
    for (d, text), eff in zip(SHORT[:6], effects):
        caps.append({"start": t, "end": t + d, "text": text, "size": 100, "effect": eff})
        t += d
    pics = [{"track": 0, "start": 0.0, "end": 3.1, "name": "a"}, {"track": 0, "start": 3.1, "end": 4.5, "name": "b"},
            {"track": 0, "start": 4.5, "end": 5.8, "name": "c"}]
    row = {"project": "p.prproj", "sequence": "s", "fps": 30, "captions": caps, "pictures": pics}
    rows = sc.load([json.dumps(row, ensure_ascii=False), json.dumps(row, ensure_ascii=False)])
    assert len(rows) == 1, "copies of a Short count once"
    rep = sc.calibrate(rows)
    assert rep["boundaries"] == 5
    assert rep["cuts"]["precision"] == 1.0 and rep["cuts"]["recall"] == 1.0, "the 바로 reveal is the cut the user made"
    no_auto = sc.compare({**rows[0]})  # the same Short without the automatic reveal misses that cut
    from lumina import shorts_plan as spm
    cues = [spm.Cue(n, round(c["start"] * SEC), round(c["end"] * SEC), c["text"]) for n, c in enumerate(caps)]
    assert len(spm.make_plan(cues, auto_reveal=False).shots) == len(spm.make_plan(cues).shots) - 1
    assert no_auto["f1"] == 1.0
    assert rep["by_kind"]["sentence end"]["user_cut"] == 1.0
    assert rep["effect_agreement"] is not None and "pop->pop" in rep["effects"]
    assert "worst Shorts" in sc.render(rep)


def longform_session(rows):
    s = Session(services=BuildServices())
    sid = s.execute("sequence.create", {"name": "Long", "video_tracks": 3, "audio_tracks": 2})["result"]["sequence_id"]
    tid = s.execute("caption.add_track", {"name": "자막"})["result"]["track_id"]
    t = 0
    for start, d, text in rows:
        s.execute("caption.add", {"track_id": tid, "text": text, "at": round(start * SEC), "duration": round(d * SEC)})
    mids = s.execute("media.import", {"paths": ["a.mp4", "b.mp4"]})["result"]["media_ids"]
    return s, sid, mids


def test_edit_check_longform_rules():
    s, sid, mids = longform_session([
        (0.5, 2.0, "그 문제를 해결하지"), (2.5, 2.0, "못했던 이유는"), (4.5, 6.0, "길게 이어지는 설명입니다. 그리고 다음 문장도"),
        (11.0, 1.0, "끝입니다."), (12.5, 1.5, "핵심 증거를")])
    v1 = s.doc["sequences"][sid]["video_tracks"][0]
    s.execute("clip.place", {"media_id": mids[0], "at": 0, "source_in": "2s", "source_out": "4s", "video_track_id": v1, "take_audio": False})
    s.execute("clip.place", {"media_id": mids[1], "at": "2s", "source_out": "0.7s", "video_track_id": v1, "take_audio": False})
    s.execute("clip.place", {"media_id": mids[0], "at": "2.7s", "source_in": "2.5s", "source_out": "12.5s", "video_track_id": v1, "take_audio": False})
    rep = shorts_check.check(s.doc, s.doc["sequences"][sid], use_db=False, profile="longform")
    kinds = Counter(p["kind"] for p in rep["problems"])
    assert kinds["reuse"] == 1, "a.mp4 2.5-4 s comes back after its first use at 2-4 s"
    assert kinds["caption start"] == 1 and kinds["caption gap"] >= 2 and kinds["caption punctuation"] == 1
    assert kinds["caption split"] == 1, "해결하지 / 못했던"
    assert kinds["caption long"] == 1, "6 s holding two sentences"
    assert "short shot" not in kinds and "ending" not in kinds, "Shorts-only rules are off in long-form"
    assert rep["summary"]["profile"] == "longform"


def test_reuse_counts_stills_and_ignores_other_spans():
    s, sid, mids = longform_session([(0, 10, "설명")])
    img = s.execute("media.import", {"paths": ["d.png"]})["result"]["media_ids"][0]
    v1 = s.doc["sequences"][sid]["video_tracks"][0]
    s.execute("clip.place", {"media_id": mids[0], "at": 0, "source_out": "2s", "video_track_id": v1, "take_audio": False})
    s.execute("clip.place", {"media_id": mids[0], "at": "2s", "source_in": "10s", "source_out": "12s", "video_track_id": v1, "take_audio": False})
    s.execute("clip.place", {"media_id": img, "at": "4s", "source_out": "1s", "video_track_id": v1})
    s.execute("clip.place", {"media_id": img, "at": "5s", "source_out": "1s", "video_track_id": v1})
    rep = shorts_check.check(s.doc, s.doc["sequences"][sid], use_db=False, profile="longform")
    reuse = [p for p in rep["problems"] if p["kind"] == "reuse"]
    assert len(reuse) == 1 and reuse[0]["frame"] == 150, "only the repeated still; another span of a.mp4 is new"


def test_tmi_hangs_on_its_caption():
    s, sid, tids, ids = shorts_session()
    s.execute("shorts.caption_effects", {"items": [{"clip_id": ids[1], "effect": "slam", "size": 120}]})
    r = s.execute("shorts.add_tmi", {"items": [
        {"caption_clip_id": ids[0], "text": "CD 프로젝트 레드"},
        {"caption_clip_id": ids[0], "text": "원작은 TRPG\n사이버펑크 2020"},
        {"caption_clip_id": ids[1], "text": "정확히는 조연"},
        {"caption_clip_id": ids[4], "text": "성우는 같습니다"}]})["result"]
    seq = s.doc["sequences"][sid]
    v4 = seq["video_tracks"][3]
    assert r["track_id"] == v4
    clips = sorted(seq["tracks"][v4]["clips"].values(), key=lambda c: c["start"])
    cues = {c["id"]: c for t in tids for c in seq["tracks"][t]["clips"].values()}
    assert len(clips) == 3, "two notes on one caption share one graphic"
    assert (clips[0]["start"], clips[0]["end"]) == (cues[ids[0]]["start"], cues[ids[0]]["end"])
    ys = {l["text"]: l["position"][1] for c in clips for l in c["graphic"]["layers"]}
    assert ys == {"CD 프로젝트 레드": 680.0, "원작은 TRPG\n사이버펑크 2020": 960.0, "정확히는 조연": 670.0, "성우는 같습니다": 680.0}
    layer = clips[0]["graphic"]["layers"][0]
    assert (layer["font_family"], layer["font_size"], layer["box"]) == ("Pretendard-Medium", 48.0, [810.0, 576.0])
    assert any("polite register" in w for w in r["warnings"])
    with pytest.raises(CommandError, match="already has a clip"):
        s.execute("shorts.add_tmi", {"items": [{"caption_clip_id": ids[0], "text": "다른 메모"}]})
    s.execute("shorts.add_tmi", {"replace": True, "items": [{"caption_clip_id": ids[0], "text": "다른 메모"}]})
    assert len(s.doc["sequences"][sid]["tracks"][v4]["clips"]) == 3


def test_sound_calibration_and_names():
    from lumina import shorts_calibrate as sc

    assert sc.canonical_sound("KF - Whoosh Dramatic 4.wav") == "W4"
    assert sc.canonical_sound("Epic Hit 3.wav") == "Epic Hit 3" and sc.canonical_sound("Kick 2.mp3") == "Kick 2"
    assert sc.canonical_sound("Hit - Guns 01.wav") == "H:Guns" and sc.canonical_sound("티딩! 효과음.mp3") == "티딩"
    assert sc.sound_kind("hit?") == sc.sound_kind("H:Cash") == "hit" and sc.sound_kind("W5") == "whoosh"
    caps, t = [], 0.0
    for d, text in SHORT[:6]:
        caps.append({"start": t, "end": t + d, "text": text, "effect": "pop"})
        t += d
    sounds = [{"track": 1, "start": 0.0, "end": 1.0, "name": "KF - Whoosh Dramatic 3.wav"},          # W3 on frame 0: same
              {"track": 2, "start": 1.0 - 0.7, "end": 2.0, "name": "Epic Hit 3.wav"},              # lands on cue 1 (draft: 뚜훅)
              {"track": 1, "start": 3.1, "end": 4.0, "name": "KF - Whoosh Dramatic 1.wav"}]        # cue 3 (draft: W4)
    row = {"project": "p", "sequence": "s", "fps": 30, "captions": caps,
           "pictures": [{"track": 0, "start": 0.0, "end": 5.8, "name": "a"}], "sounds": sounds}
    rep = sc.calibrate([row])
    snd = rep["sounds"]
    assert snd["user_has"] == 3 and snd["same_sound"] == round(1 / 3, 3) and snd["same_kind"] == 1.0
    assert snd["by_draft_sound"]["Bells 2"] == {"(nothing)": 1, "n": 1}
    assert "what the user had" in sc.render(rep)


def test_tool_profiles():
    from lumina.mcp_server import TOOL_PROFILES, build_tools, selected

    names = [t.name for t in build_tools()]
    shorts = selected(names, "shorts")
    assert {"shorts_build", "source_plan", "clip_place_shots", "get_frame_strip", "marker_add"} <= shorts
    assert "graphic_place_mogrt" not in shorts and len(shorts) < len(names) / 2
    assert selected(names, "shorts,clip_slip") == shorts | {"clip_slip"}
    assert selected(names, None) == selected(names, "all") == set(names)
    assert {t.name for t in build_tools("core")} == selected(names, "core")
    for prof in TOOL_PROFILES.values():  # every entry of a profile names something real
        for pat in prof:
            assert any(n.startswith(pat[:-1]) if pat.endswith("*") else n == pat for n in names), pat


def test_set_bgm_lands_the_meter_median_on_target(tmp_path):
    import shutil
    import subprocess

    import numpy as np

    from lumina import audio_analysis

    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    # the meter: a steady sine of peak 0.5 reads -6 dBFS; after silence it falls 24 dB/s
    rate = 24000
    t = np.arange(rate) / rate
    pcm = np.stack([0.5 * np.sin(2 * np.pi * 440 * t)] * 2, axis=1).astype(np.float32)
    m = audio_analysis.meter_db(np.concatenate([pcm, np.zeros_like(pcm)]), rate)
    assert abs(float(np.median(m[10:90])) + 6.02) < 0.1
    assert abs((m[100] - m[150]) - 12.0) < 0.6, "falls 24 dB per second"

    wav = tmp_path / "bgm.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-af", "volume=0.5", str(wav)], check=True)  # lavfi sine peaks at 1/8; x0.5 -> about -24 dBFS
    e = Engine(run_jobs=False)
    sid = e.dispatch("execute", {"name": "sequence.create", "params": {"name": "S", "audio_tracks": 5}})["result"]["sequence_id"]
    seq = e.session.doc["sequences"][sid]
    a5 = seq["audio_tracks"][4]
    mid = e.dispatch("execute", {"name": "media.import", "params": {"paths": [str(wav)]}})["result"]["media_ids"][0]
    e.dispatch("execute", {"name": "track.set", "params": {"track_id": a5, "volume_db": -6.0}})
    e.dispatch("execute", {"name": "clip.place", "params": {"media_id": mid, "at": 0, "audio_track_id": a5, "take_video": False}})
    dry = e.dispatch("execute", {"name": "shorts.set_bgm", "params": {"dry_run": True}})["result"]
    assert abs(dry["before"][1] - (-24.1 - 6.0)) < 0.6 and all(v == 0.0 for v in dry["gain_db"].values())
    r = e.dispatch("execute", {"name": "shorts.set_bgm", "params": {}})["result"]
    assert abs(r["after"][1] - (-25.5)) < 0.1 and abs(list(r["gain_db"].values())[0] - r["change_db"]) < 0.11
    again = e.dispatch("execute", {"name": "shorts.set_bgm", "params": {"dry_run": True}})["result"]
    assert abs(again["before"][1] + 25.5) < 0.15, "measured after the change, the median is on target"


def test_reveal_detection_and_name_hints():
    plan = sp.make_plan(cues(SHORT), names=("사이버펑크", "닮은꼴", "사스케"))
    starts = {s.cues[0].text: s.reason for s in plan.shots}
    assert starts.get("바로 사스케입니다") == "reveal (바로…)"
    by = {c.text: c.i for c in plan.cues}
    assert plan.hints == {by["바로 사스케입니다"]: "사스케"}, "only a name's first mention, mid-sentence"
    assert "reveal?" in sp.render(plan, 30, 1)
    assert "사이버펑크" not in plan.hints.values(), "a name at a sentence start is already a cut"


def test_levels_come_from_auto_match_only():
    """User, 2026-11-03: the level is Auto-Match's, never a lowered clip volume. A2/A3 sounds (티딩, Kick 5 and
    Epic Hit 2 included) go to Auto-Match; A4 (Bells 2, 물방울) stays out of it."""
    from lumina.commands.shorts_fx import sound_rule

    for name in ("티딩", "Kick 5", "Epic Hit 2", "W4", "뚜훅"):
        r = sound_rule(name)
        assert r["match"] and "volume_db" not in r and "gain_db" not in r, name
    assert not sound_rule("Bells 2")["match"] and sound_rule("Bells 2")["track"] == 4
