import pytest

from lumina import text_fit
from lumina.commands.registry import CommandError
from lumina.render import fonts
from lumina.render.compositor import render_graphic
from lumina.session import Session

from .test_session import FakeServices

SUB = {"font_family": "S-CoreDream-5Medium", "font_size": 100, "tracking": -18, "color": "#ffffff",
       "stroke_color": "#000000", "stroke_width": 3.5, "position": [538.2, 169.5],
       "shadow": {"color": "#000000", "opacity": 100, "angle": 135, "distance": 5, "blur": 0}}
MAIN = dict(SUB, font_family="S-CoreDream-9Black", font_size=115, color="#3a6fb3", stroke_color="#ffffff",
            stroke_width=6, position=[539.3, 312.5])


def title():
    """A 1080x1920 sequence with the channel's title graphic (sub above, main below), moved like the template."""
    s = Session(services=FakeServices())
    sid = s.execute("sequence.create", {"name": "s", "settings": {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1}})["result"]["sequence_id"]
    out = s.execute("graphic.create_text", {"text": "술 마시기 내기 하는", "at": 0, "duration": "10s", **SUB})["result"]
    cid = out["clip_id"]
    s.execute("graphic.add_layer", {"clip_id": cid, "text": "귀여운 얀데레 악마", **MAIN})
    s.execute("clip.set", {"clip_ids": [cid], "name": "쇼츠 타이틀"})
    s.execute("clip.set_property", {"clip_id": cid, "prop": "position", "value": [540, 1000]})
    s.execute("clip.set_property", {"clip_id": cid, "prop": "anchor", "value": [540, 960]})
    seq = s.doc["sequences"][sid]
    return s, seq, seq["tracks"][out["track_id"]]["clips"][cid]


def layers(clip):
    return [l["text"] for l in clip["graphic"]["layers"]]


def test_bounds_match_the_rendered_layer():
    s, seq, clip = title()
    for layer in clip["graphic"]["layers"]:
        alone = render_graphic({"layers": [layer]}, 1080, 1920).getchannel("A").getbbox()
        x0, y0, x1, y1 = text_fit.layer_bounds(seq, clip, layer)
        # the clip's Motion moves the whole graphic 40px down
        assert abs(x0 - alone[0]) <= 1 and abs(x1 - alone[2]) <= 1 and abs(y0 - (alone[1] + 40)) <= 1


def test_dry_run_measures_without_editing():
    s, seq, clip = title()
    out = s.execute("shorts.set_copy", {"sub": "가" * 5, "main": "나" * 5, "dry_run": True})
    assert out["changed"] is False and layers(clip) == ["술 마시기 내기 하는", "귀여운 얀데레 악마"]
    r = out["result"]
    assert r["sub"]["fits"] and r["main"]["fits"] and r["sub"]["max_hangul"] > r["main"]["max_hangul"] >= 5


def test_copy_up_to_the_limit_is_set_and_the_title_keeps_its_name():
    s, seq, clip = title()
    limits = s.execute("shorts.set_copy", {"dry_run": True})["result"]
    sub, main = "가" * limits["sub"]["max_hangul"], "나" * limits["main"]["max_hangul"]
    s.execute("shorts.set_copy", {"sub": sub, "main": main})
    assert layers(clip) == [sub, main] and clip["name"] == "쇼츠 타이틀"
    s.undo()
    assert layers(clip) == ["술 마시기 내기 하는", "귀여운 얀데레 악마"]


def test_one_syllable_past_the_limit_is_refused_and_nothing_changes():
    s, seq, clip = title()
    limit = s.execute("shorts.set_copy", {"dry_run": True})["result"]["main"]["max_hangul"]
    with pytest.raises(CommandError) as e:
        s.execute("shorts.set_copy", {"sub": "짧은 서브", "main": "나" * (limit + 1)})
    assert e.value.code == "COPY_DOES_NOT_FIT"
    assert e.value.details["main"]["overflow_px"] > 0 and e.value.details["sub"]["fits"]
    assert layers(clip) == ["술 마시기 내기 하는", "귀여운 얀데레 악마"]
    # a margin narrows the limit
    assert s.execute("shorts.set_copy", {"dry_run": True, "margin": 60})["result"]["main"]["max_hangul"] < limit


def test_line_breaks_are_refused():
    s, seq, clip = title()
    with pytest.raises(CommandError):
        s.execute("shorts.set_copy", {"main": "두 줄\n메인"})


def test_characters_the_font_cannot_draw_are_refused():
    if not fonts.find("S-CoreDream-9Black")[1]:
        pytest.skip("needs S-CoreDream installed (KS X 1001 Hangul only)")
    s, seq, clip = title()
    with pytest.raises(CommandError) as e:
        s.execute("shorts.set_copy", {"main": "똠방각하 뷁"})
    assert e.value.details["main"]["missing_glyphs"] == ["똠", "뷁"]  # both outside KS X 1001


def test_without_a_title_graphic_the_error_says_so():
    s = Session(services=FakeServices())
    s.execute("sequence.create", {"name": "s"})
    with pytest.raises(CommandError) as e:
        s.execute("shorts.set_copy", {"main": "x"})
    assert e.value.code == "NO_TITLE"


def test_hidden_words_show_as_question_marks_until_the_reveal():
    s, seq, clip = title()
    out = s.execute("shorts.set_copy", {"sub": "에반게리온을 파쿠리한", "main": "옷코츠 유타", "hide": ["에반게리온"], "reveal_at": "3.5s"})
    titles = out["result"]["titles"]
    assert [(t["sub"], t["main"]) for t in titles] == [("?????을 파쿠리한", "옷코츠 유타"), ("에반게리온을 파쿠리한", "옷코츠 유타")]
    clips = clips_of(seq)
    assert [layers(c) for c in clips] == [["?????을 파쿠리한", "옷코츠 유타"], ["에반게리온을 파쿠리한", "옷코츠 유타"]]
    assert all(c["name"] == "쇼츠 타이틀" for c in clips) and clips[0]["end"] == clips[1]["start"]
    # setting it again moves the reveal onto the existing cut's clips; without hide the mask is gone
    s.execute("shorts.set_copy", {"main": "옷코츠 유타", "hide": ["유타"], "reveal_at": "3.5s"})
    assert [layers(c) for c in clips_of(seq)] == [["에반게리온을 파쿠리한", "옷코츠 ??"], ["에반게리온을 파쿠리한", "옷코츠 유타"]]
    s.execute("shorts.set_copy", {})
    assert [layers(c) for c in clips_of(seq)] == [["에반게리온을 파쿠리한", "옷코츠 유타"]] * 2
    s.undo(); s.undo(); s.undo()
    assert [layers(c) for c in clips_of(seq)] == [["술 마시기 내기 하는", "귀여운 얀데레 악마"]]


def clips_of(seq):
    tracks = [t for t in seq["tracks"].values() if any(c.get("graphic") for c in t["clips"].values())]
    return sorted(tracks[0]["clips"].values(), key=lambda c: c["start"])


def test_a_hidden_word_must_be_in_the_copy_and_needs_a_reveal_time():
    s, seq, clip = title()
    with pytest.raises(CommandError) as e:
        s.execute("shorts.set_copy", {"hide": ["없는말"], "reveal_at": "1s"})
    assert "not in the copy" in e.value.message
    with pytest.raises(CommandError) as e:
        s.execute("shorts.set_copy", {"hide": ["악마"]})
    assert "reveal_at" in e.value.message
    with pytest.raises(CommandError):
        s.execute("shorts.set_copy", {"hide": ["악마"], "reveal_at": "20s"})
    assert len(clips_of(seq)) == 1
