import json
from pathlib import Path

import pytest

from lumina import shapes
from lumina.commands.registry import CommandError
from lumina.commands.timeline import check_sequence
from lumina.model import validate_project
from lumina.motion import interpolate, transform_at, value_at

from lumina.session import Session

from .test_session import SEC, FakeServices, clips, make

VECTORS = Path(__file__).resolve().parents[2] / "shared" / "test-vectors" / "keyframes.json"


@pytest.mark.parametrize("case", json.loads(VECTORS.read_text(encoding="utf-8"))["cases"])
def test_shared_keyframe_vectors(case):
    got = interpolate(case["keyframes"], case["t"])
    if isinstance(case["expect"], list):
        assert got == pytest.approx(case["expect"])
    else:
        assert got == pytest.approx(case["expect"])


def placed():
    s, sid, (a, *_) = make()
    cid = s.execute("clip.place", {"media_id": a, "at": 0, "source_in": "2s", "source_out": "10s"})["result"]["video_clip_id"]
    return s, sid, cid


def clip_of(s, sid, cid):
    return next(c for c in clips(s, sid) if c["id"] == cid)


def test_static_set_and_reset():
    s, sid, cid = placed()
    s.execute("clip.set_property", {"clip_id": cid, "prop": "scale", "value": 150})
    s.execute("clip.set_property", {"clip_id": cid, "prop": "position", "value": [100, 200]})
    c = clip_of(s, sid, cid)
    assert c["motion"]["scale"] == 150 and c["motion"]["position"] == [100, 200]
    with pytest.raises(CommandError):
        s.execute("clip.set_property", {"clip_id": cid, "prop": "position", "value": 5})
    s.execute("clip.reset_motion", {"clip_ids": [cid]})
    assert clip_of(s, sid, cid)["motion"]["scale"] == 100


def test_stopwatch_keyframes_follow_source_time():
    s, sid, cid = placed()
    s.execute("clip.toggle_animation", {"clip_id": cid, "prop": "opacity", "at": 0})
    s.execute("clip.set_property", {"clip_id": cid, "prop": "opacity", "value": 0, "at": "2s"})
    c = clip_of(s, sid, cid)
    seq = s.doc["sequences"][sid]
    assert [k["v"] for k in c["keyframes"]["opacity"]] == [100, 0]
    assert c["keyframes"]["opacity"][0]["t"] == 2 * SEC  # source time, not sequence time
    assert value_at(c, "opacity", SEC, seq) == pytest.approx(50)
    # trimming the head keeps keyframes on the same content
    s.execute("clip.trim", {"clip_id": cid, "edge": "start", "delta": "1s"})
    c = clip_of(s, sid, cid)
    assert c["start"] == SEC and c["source_in"] == 3 * SEC
    assert value_at(c, "opacity", SEC, seq) == pytest.approx(50)  # same content frame, same value
    assert value_at(c, "opacity", 2 * SEC, seq) == pytest.approx(0)
    # stopwatch off keeps the current value
    s.execute("clip.toggle_animation", {"clip_id": cid, "prop": "opacity", "at": SEC})
    c = clip_of(s, sid, cid)
    assert "opacity" not in c["keyframes"] and c["opacity"] == pytest.approx(50)


def test_keyframe_toggle_and_set_keyframes():
    s, sid, cid = placed()
    s.execute("clip.set_keyframes", {"clip_id": cid, "prop": "scale", "keyframes": [
        {"t": 0, "v": 100}, {"t": "1s", "v": 200, "interp": "ease"}]})
    c = clip_of(s, sid, cid)
    assert len(c["keyframes"]["scale"]) == 2
    s.execute("clip.keyframe", {"clip_id": cid, "prop": "scale", "at": "1s"})  # toggle off the 2nd
    assert len(clip_of(s, sid, cid)["keyframes"]["scale"]) == 1
    s.execute("clip.keyframe", {"clip_id": cid, "prop": "scale", "at": 0})  # last one -> stops animating
    c = clip_of(s, sid, cid)
    assert "scale" not in c["keyframes"] and c["motion"]["scale"] == 100


def test_fit_and_transform_resolution():
    s, sid, cid = placed()
    seq = s.doc["sequences"][sid]
    media = s.doc["media"][clip_of(s, sid, cid)["media_id"]]
    s.execute("clip.fit_to_frame", {"clip_ids": [cid], "mode": "fill"})
    tr = transform_at(clip_of(s, sid, cid), 0, seq, media)
    assert tr["scale_x"] == tr["scale_y"] == 100  # fake media is 1920x1080 in a 1920x1080 sequence
    assert tr["position"] == [960, 540] and tr["anchor"] == [960, 540]


def test_graphics_create_edit_and_undo():
    s, sid, (a, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": 0})
    out = s.execute("graphic.create_text", {"text": "안녕하세요", "at": "1s", "font_size": 80, "color": "#ffcc00"})["result"]
    seq = s.doc["sequences"][sid]
    assert out["track_id"] == seq["video_tracks"][1], "goes on the first free track above the footage"
    s.execute("graphic.set_layer", {"clip_id": out["clip_id"], "text": "제목\n두 번째 줄", "stroke_color": "#000000", "stroke_width": 6})
    s.execute("graphic.add_layer", {"clip_id": out["clip_id"], "text": "부제"})
    c = seq["tracks"][out["track_id"]]["clips"][out["clip_id"]]
    assert c["media_id"] is None and c["name"] == "제목"
    assert [l["text"] for l in c["graphic"]["layers"]] == ["제목\n두 번째 줄", "부제"]
    assert validate_project(s.doc).model_dump(mode="json") == s.doc
    check_sequence(s.doc, seq)
    # graphics take part in normal edits
    s.execute("clip.razor", {"at": "3s"})
    s.execute("clip.trim", {"clip_id": out["clip_id"], "edge": "end", "delta": "-0.5s"})
    for _ in range(5):
        s.undo()
    assert out["clip_id"] not in seq["tracks"][out["track_id"]]["clips"]


def test_shape_layers_create_edit_reorder_and_undo():
    s, sid, _ = make()
    seq = s.doc["sequences"][sid]
    out = s.execute("graphic.create_shape", {"shape": "rectangle", "rect": [100, 200, 500, 400], "at": 0, "fill": "#ff0000"})["result"]
    clip = lambda: seq["tracks"][out["track_id"]]["clips"][out["clip_id"]]
    (rect,) = clip()["graphic"]["layers"]
    assert rect["type"] == "shape" and rect["position"] == [300, 300] and rect["fill"] == "#ff0000"
    assert shapes.bounds(rect["path"]) == (-200, -100, 200, 100)
    ell = s.execute("graphic.add_shape", {"clip_id": out["clip_id"], "shape": "ellipse", "stroke_color": "#000000", "stroke_width": 4})["result"]
    t = s.execute("graphic.add_layer", {"clip_id": out["clip_id"], "text": "제목"})["result"]
    assert [l["type"] for l in clip()["graphic"]["layers"]] == ["shape", "shape", "text"]
    s.execute("graphic.set_shape", {"clip_id": out["clip_id"], "layer_id": rect["id"], "width": 800, "fill_enabled": False, "rotation": 15})
    r = clip()["graphic"]["layers"][0]
    assert r["fill"] is None and r["rotation"] == 15 and shapes.bounds(r["path"]) == (-400, -100, 400, 100)
    with pytest.raises(CommandError) as e:
        s.execute("graphic.set_shape", {"clip_id": out["clip_id"], "layer_id": t["layer_id"], "fill": "#00ff00"})
    assert e.value.code == "NOT_SHAPE"
    with pytest.raises(CommandError):
        s.execute("graphic.set_layer", {"clip_id": out["clip_id"], "layer_id": ell["layer_id"], "text": "x"})
    # set_layer without a layer id targets the first text layer, not a shape
    s.execute("graphic.set_layer", {"clip_id": out["clip_id"], "text": "부제"})
    assert clip()["graphic"]["layers"][2]["text"] == "부제"
    s.execute("graphic.move_layer", {"clip_id": out["clip_id"], "layer_id": t["layer_id"], "index": 0})
    assert [l["type"] for l in clip()["graphic"]["layers"]] == ["text", "shape", "shape"]
    assert validate_project(s.doc).model_dump(mode="json") == s.doc
    for _ in range(6):
        s.undo()
    assert out["clip_id"] not in seq["tracks"][out["track_id"]]["clips"]


def test_keyframe_ease_handles_keep_the_other_side():
    s, sid, (a, *_) = make()
    cid = s.execute("clip.place", {"media_id": a, "at": 0})["result"]["video_clip_id"]
    s.execute("clip.set_keyframes", {"clip_id": cid, "prop": "opacity", "keyframes": [
        {"t": 0, "v": 0, "interp": "linear"}, {"t": "1s", "v": 100, "interp": "linear"}, {"t": "2s", "v": 50, "interp": "linear"}]})
    seq = s.doc["sequences"][sid]
    clip = lambda: next(c for t in seq["tracks"].values() for c in t["clips"].values() if c["id"] == cid)
    k1 = clip()["keyframes"]["opacity"][1]
    before = [interpolate(clip()["keyframes"]["opacity"], k1["t"] + d) for d in (-SEC // 4, SEC // 4)]
    s.execute("clip.set_keyframe_ease", {"clip_id": cid, "prop": "opacity", "t": k1["t"], "side": "out", "speed": 0, "influence": 0.5})
    kfs = clip()["keyframes"]["opacity"]
    assert kfs[1]["interp"] == "bezier" and kfs[1]["ease_out"] == [0, 0.5]
    assert kfs[1]["ease_in"] == pytest.approx([100.0, 1 / 3]), "linear in-side becomes the equivalent handle"
    assert interpolate(kfs, k1["t"] - SEC // 4) == pytest.approx(before[0]), "the incoming segment is unchanged"
    assert interpolate(kfs, k1["t"] + SEC // 4) != pytest.approx(before[1])
    s.undo()
    assert clip()["keyframes"]["opacity"][1]["interp"] == "linear"


def _kf_setup():
    s, sid, (a, *_) = make()
    cid = s.execute("clip.place", {"media_id": a, "at": 0})["result"]["video_clip_id"]
    s.execute("clip.set_keyframes", {"clip_id": cid, "prop": "opacity", "keyframes": [
        {"t": 0, "v": 0, "interp": "linear"}, {"t": "1s", "v": 100, "interp": "linear"}, {"t": "2s", "v": 50, "interp": "linear"}]})
    seq = s.doc["sequences"][sid]
    clip = lambda: next(c for t in seq["tracks"].values() for c in t["clips"].values() if c["id"] == cid)
    return s, cid, clip


def test_auto_bezier_is_smooth_through_the_keyframe():
    kfs = [{"t": 0, "v": 0, "interp": "linear"}, {"t": SEC, "v": 100, "interp": "auto_bezier"}, {"t": 2 * SEC, "v": 100, "interp": "linear"}]
    # slope through the neighbours: (100 - 0) / 2 s = 50 /s on both sides of the middle keyframe
    eps = SEC // 1000
    left = (interpolate(kfs, SEC) - interpolate(kfs, SEC - eps)) / (eps / SEC)
    right = (interpolate(kfs, SEC + eps) - interpolate(kfs, SEC)) / (eps / SEC)
    assert left == pytest.approx(50, rel=0.02) and right == pytest.approx(50, rel=0.02)


def test_keyframes_set_interp_keeps_shape_and_menu_types():
    s, cid, clip = _kf_setup()
    k1 = clip()["keyframes"]["opacity"][1]["t"]
    before = [interpolate(clip()["keyframes"]["opacity"], t) for t in (SEC // 2, 3 * SEC // 2)]
    s.execute("keyframes.set_interp", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [k1]}], "interp": "bezier"})
    kfs = clip()["keyframes"]["opacity"]
    assert kfs[1]["interp"] == "bezier"
    assert [interpolate(kfs, t) for t in (SEC // 2, 3 * SEC // 2)] == pytest.approx(before), "Bezier keeps the linear shape"
    s.execute("keyframes.set_interp", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [k1]}], "interp": "ease_in"})
    k = clip()["keyframes"]["opacity"][1]
    assert k["ease_in"] == pytest.approx([0, 1 / 3]) and k["ease_out"] == pytest.approx([-50, 1 / 3])
    s.execute("keyframes.set_interp", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [k1]}], "interp": "continuous_bezier"})
    k = clip()["keyframes"]["opacity"][1]
    assert k["interp"] == "continuous_bezier" and k["ease_in"][0] == pytest.approx(k["ease_out"][0])
    # dragging one handle of a continuous keyframe moves both speeds
    s.execute("clip.set_keyframe_ease", {"clip_id": cid, "prop": "opacity", "t": k1, "side": "out", "speed": 10, "influence": 0.5})
    k = clip()["keyframes"]["opacity"][1]
    assert k["ease_in"][0] == 10 and k["ease_out"] == [10, 0.5]
    s.execute("keyframes.set_interp", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [k1]}], "interp": "auto_bezier"})
    k = clip()["keyframes"]["opacity"][1]
    assert k["interp"] == "auto_bezier" and "ease_in" not in k
    s.execute("keyframes.set_interp", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [0, k1]}], "interp": "hold"})
    assert [k["interp"] for k in clip()["keyframes"]["opacity"]] == ["hold", "hold", "linear"]


def test_keyframes_move_and_delete():
    s, cid, clip = _kf_setup()
    kfs = clip()["keyframes"]["opacity"]
    s.execute("keyframes.move", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [kfs[1]["t"]]}], "delta": SEC // 2})
    assert [k["t"] for k in clip()["keyframes"]["opacity"]] == [kfs[0]["t"], kfs[1]["t"] + SEC // 2, kfs[2]["t"]]
    # landing on another keyframe replaces it
    s.execute("keyframes.move", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [kfs[1]["t"] + SEC // 2]}], "delta": SEC // 2})
    assert [k["v"] for k in clip()["keyframes"]["opacity"]] == [0, 100]
    s.execute("keyframes.delete", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [k["t"] for k in clip()["keyframes"]["opacity"]]}]})
    assert "opacity" not in clip()["keyframes"]
    s.undo()
    assert len(clip()["keyframes"]["opacity"]) == 2
    with pytest.raises(CommandError):
        s.execute("keyframes.delete", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [12345]}]})


def test_keyframes_on_effect_params():
    s, cid, clip = _kf_setup()
    fx = s.execute("effect.add", {"clip_ids": [cid], "type": "gaussian_blur"})["result"]
    eid = clip()["effects"][0]["id"]
    s.execute("effect.set_keyframes", {"clip_id": cid, "effect_id": eid, "param": "blurriness", "keyframes": [{"t": 0, "v": 0}, {"t": "1s", "v": 20}]})
    times = [k["t"] for k in clip()["effects"][0]["keyframes"]["blurriness"]]
    s.execute("keyframes.set_interp", {"clip_id": cid, "interp": "ease_out", "targets": [
        {"effect_id": eid, "param": "blurriness", "times": times[:1]}, {"prop": "opacity", "times": [0]}]})
    assert clip()["effects"][0]["keyframes"]["blurriness"][0]["ease_out"] == pytest.approx([0, 1 / 3])
    assert clip()["keyframes"]["opacity"][0]["ease_out"] == pytest.approx([0, 1 / 3])
    s.execute("keyframes.delete", {"clip_id": cid, "targets": [{"effect_id": eid, "param": "blurriness", "times": times}]})
    e = clip()["effects"][0]
    assert "blurriness" not in e["keyframes"] and isinstance(e["params"]["blurriness"], float | int)
    assert fx is not None


def test_match_style_and_replace_text_across_captions():
    s, sid, _ = make()
    a = s.execute("graphic.create_text", {"text": "첫 자막 안녕", "at": "0s", "font_size": 80, "color": "#ffcc00", "stroke_color": "#000000",
                                          "stroke_width": 6, "position": [960, 900]})["result"]
    b = s.execute("graphic.create_text", {"text": "둘째 안녕 안녕", "at": "5s", "font_size": 40, "color": "#ffffff", "position": [100, 100]})["result"]
    s.execute("graphic.match_style", {"source_clip_id": a["clip_id"], "clip_ids": [b["clip_id"]]})
    vt = s.doc["sequences"][sid]["video_tracks"]
    get = lambda cid: next(c for t in vt for c in s.doc["sequences"][sid]["tracks"][t]["clips"].values() if c["id"] == cid)  # noqa: E731
    lb = get(b["clip_id"])["graphic"]["layers"][0]
    assert (lb["font_size"], lb["color"], lb["stroke_width"], lb["text"], lb["position"]) == (80, "#ffcc00", 6, "둘째 안녕 안녕", [100, 100])
    s.execute("graphic.match_style", {"source_clip_id": a["clip_id"], "clip_ids": [b["clip_id"]], "include_position": True})
    assert get(b["clip_id"])["graphic"]["layers"][0]["position"] == [960, 900]
    r = s.execute("graphic.replace_text", {"find": "안녕", "replace": "hello"})["result"]
    assert r["replaced"] == 3
    assert get(b["clip_id"])["graphic"]["layers"][0]["text"] == "둘째 hello hello"
    assert get(a["clip_id"])["name"] == "첫 자막 hello"
    s.undo()
    assert get(b["clip_id"])["graphic"]["layers"][0]["text"] == "둘째 안녕 안녕"


def test_keyframes_scale_keeps_curve_shape_and_snaps_to_frames():
    s, cid, clip = _kf_setup()
    kfs = clip()["keyframes"]["opacity"]
    s.execute("clip.set_keyframe_ease", {"clip_id": cid, "prop": "opacity", "t": kfs[1]["t"], "side": "out", "speed": 30, "influence": 0.4})
    times = [k["t"] for k in clip()["keyframes"]["opacity"]]
    # Alt-drag the last keyframe: twice as long, pivot on the first
    s.execute("keyframes.scale", {"clip_id": cid, "targets": [{"prop": "opacity", "times": times}], "pivot": 0, "factor": 2})
    new = clip()["keyframes"]["opacity"]
    assert [k["t"] for k in new] == [0, 2 * SEC, 4 * SEC]
    assert new[1]["ease_out"] == [15, 0.4], "handle speed halves when the spacing doubles"
    # 1/3: 2 s -> 0.6667 s lands on a frame
    s.execute("keyframes.scale", {"clip_id": cid, "targets": [{"prop": "opacity", "times": [k["t"] for k in new]}], "pivot": 0, "factor": 1 / 3})
    tpf = SEC // 30
    assert all(k["t"] % tpf == 0 for k in clip()["keyframes"]["opacity"])


def test_keyframes_paste_at_the_playhead_onto_another_clip():
    s, cid, clip = _kf_setup()
    sid = s.doc["active_sequence_id"]
    b = s.execute("clip.place", {"media_id": next(iter(s.doc["media"])), "at": "30s"})["result"]["video_clip_id"]
    s.execute("effect.add", {"clip_ids": [b], "type": "gaussian_blur"})
    copied = [{"prop": "opacity", "keyframes": [{"t": 0, "v": 0}, {"t": SEC, "v": 100}]},
              {"effect_type": "gaussian_blur", "param": "blurriness", "keyframes": [{"t": 0, "v": 20}]},
              {"effect_type": "transform", "param": "scale", "keyframes": [{"t": 0, "v": 50}]}]
    r = s.execute("keyframes.paste", {"clip_id": b, "at": 32 * SEC, "properties": copied})["result"]
    assert r["count"] == 3 and r["skipped"] == ["transform.scale"]
    c = next(c for t in s.doc["sequences"][sid]["tracks"].values() for c in t["clips"].values() if c["id"] == b)
    assert [k["t"] - c["source_in"] for k in c["keyframes"]["opacity"]] == [2 * SEC, 3 * SEC]
    assert c["effects"][0]["keyframes"]["blurriness"][0]["v"] == 20


def test_new_graphic_is_empty_and_never_lands_on_a_caption_track():
    s = Session(services=FakeServices())
    sid = s.execute("sequence.create", {"name": "s"})["result"]["sequence_id"]
    cap = s.execute("caption.add", {"text": "자막", "at": 0, "duration": "5s"})["result"]["track_id"]
    out = s.execute("graphic.create", {"at": 0})["result"]
    seq = s.doc["sequences"][sid]
    clip = seq["tracks"][out["track_id"]]["clips"][out["clip_id"]]
    assert clip["graphic"]["layers"] == [] and out["track_id"] != cap
    assert not seq["tracks"][out["track_id"]].get("caption_style")
    # layers are added to it like any graphic; the Type tool also skips the caption track
    s.execute("graphic.add_layer", {"clip_id": out["clip_id"], "text": "TMI"})
    assert [l["text"] for l in clip["graphic"]["layers"]] == ["TMI"]
    assert s.execute("graphic.create_text", {"at": "1s", "text": "x"})["result"]["track_id"] != cap
    with pytest.raises(CommandError):
        s.execute("graphic.create", {"at": 0, "track_id": cap})
