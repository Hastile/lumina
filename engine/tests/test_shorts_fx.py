"""shorts_plan (captions -> shots/effects/sounds), shorts.caption_effects and shorts.place_sfx."""

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
    assert (tid["volume_db"], tid["gain_db"], tid["audio_tag"]) == (-3.0, -7.0, "sfx")
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
