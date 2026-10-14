import json
from pathlib import Path

import pytest

from lumina import shapes
from lumina.commands.registry import CommandError
from lumina.commands.timeline import check_sequence
from lumina.model import validate_project
from lumina.motion import interpolate, transform_at, value_at

from .test_session import SEC, clips, make

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
