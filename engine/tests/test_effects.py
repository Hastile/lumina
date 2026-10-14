"""Effects / transitions: commands, undo, pruning, evaluation and rendering."""

import shutil

import numpy as np
import pytest
from PIL import Image

from lumina.commands.registry import CommandError
from lumina.effects import apply, param, transform_effect_at, transition_window, trs
from lumina.engine import Engine
from lumina.model import validate_project

SEC = 254_016_000_000
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture()
def eng(tmp_path):
    red, blue = tmp_path / "red.png", tmp_path / "blue.png"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(red)
    Image.new("RGB", (100, 100), (0, 0, 255)).save(blue)
    e = Engine(run_jobs=False)
    e.dispatch("project.new", {"name": "t"})
    ids = e.dispatch("execute", {"name": "media.import", "params": {"paths": [str(red), str(blue)]}})["result"]["media_ids"]
    seq = run(e, "sequence.create", name="s", settings={"width": 100, "height": 100, "fps_num": 30, "fps_den": 1})["sequence_id"]
    a = run(e, "clip.place", media_id=ids[0], at=0, source_out="2s")["video_clip_id"]
    b = run(e, "clip.place", media_id=ids[1], at="2s", source_out="2s")["video_clip_id"]
    return e, seq, a, b


def run(e, _cmd, **params):
    return e.dispatch("execute", {"name": _cmd, "params": params})["result"]


def clip(e, seq, cid):
    return next(c for t in e.session.doc["sequences"][seq]["tracks"].values() for c in t["clips"].values() if c["id"] == cid)


def video_track(e, seq):
    s = e.session.doc["sequences"][seq]
    return s["tracks"][s["video_tracks"][0]]


def frame(e, t):
    out = e.dispatch("export.frame", {"time": t})
    return np.asarray(Image.open(out["path"]).convert("RGB"), dtype=np.int16)


def test_trs_matches_motion_formula():
    m = trs([50, 60], [10, 20], 2.0, 3.0, 90)
    # (src - anchor) = (1, 0) -> scaled (2, 0) -> rotated 90 cw (y down) -> (0, 2)
    assert apply(m, 11, 20) == pytest.approx((50, 62))


def test_add_set_keyframe_undo(eng):
    e, seq, a, _ = eng
    fid = run(e, "effect.add", clip_ids=[a], type="transform")["effect_ids"][0]
    fx = clip(e, seq, a)["effects"][0]
    assert fx["params"]["position"] == [50, 50] and fx["params"]["anchor"] == [50, 50]
    run(e, "effect.set_param", clip_id=a, effect_id=fid, param="scale", value=50)
    assert clip(e, seq, a)["effects"][0]["params"]["scale"] == 50
    run(e, "effect.set_keyframes", clip_id=a, effect_id=fid, param="opacity", keyframes=[{"t": 0, "v": 0}, {"t": "1s", "v": 100}])
    c = clip(e, seq, a)
    assert param(c["effects"][0], "opacity", c, SEC // 2) == pytest.approx(50)
    run(e, "effect.set_param", clip_id=a, effect_id=fid, param="opacity", value=80, at="1s")
    assert clip(e, seq, a)["effects"][0]["keyframes"]["opacity"][-1]["v"] == 80
    run(e, "effect.toggle_animation", clip_id=a, effect_id=fid, param="opacity", at="1s")
    c = clip(e, seq, a)
    assert "opacity" not in c["effects"][0]["keyframes"] and c["effects"][0]["params"]["opacity"] == 80
    with pytest.raises(CommandError):
        run(e, "effect.set_param", clip_id=a, effect_id=fid, param="nope", value=1)
    with pytest.raises(CommandError):
        run(e, "effect.toggle_animation", clip_id=a, effect_id=fid, param="uniform")
    validate_project(e.session.doc)
    for _ in range(5):
        e.dispatch("undo")
    assert clip(e, seq, a)["effects"] == []


def test_effect_stack_order_and_bypass(eng):
    e, seq, a, _ = eng
    f1 = run(e, "effect.add", clip_ids=[a], type="transform")["effect_ids"][0]
    f2 = run(e, "effect.add", clip_ids=[a], type="gaussian_blur", params={"blurriness": 10})["effect_ids"][0]
    run(e, "effect.set", clip_id=a, effect_id=f2, index=0)
    assert [x["id"] for x in clip(e, seq, a)["effects"]] == [f2, f1]
    run(e, "effect.set", clip_id=a, effect_id=f1, enabled=False)
    assert clip(e, seq, a)["effects"][1]["enabled"] is False
    run(e, "effect.remove", clip_id=a, effect_id=f2)
    assert [x["id"] for x in clip(e, seq, a)["effects"]] == [f1]


def test_transitions_follow_clips_and_get_pruned(eng):
    e, seq, a, b = eng
    tid = run(e, "transition.add", clip_id=a, edge="end", duration="10f")["transition_id"]
    track = video_track(e, seq)
    tr = track["transitions"][tid]
    assert (tr["clip_a"], tr["clip_b"], tr["alignment"]) == (a, b, "center")
    assert transition_window(track, tr)[:2] == (2 * SEC - 5 * SEC // 30, 2 * SEC + 5 * SEC // 30)
    # single-sided fade at the sequence start
    t2 = run(e, "transition.add", clip_id=a, edge="start", type="dip_to_black")["transition_id"]
    assert video_track(e, seq)["transitions"][t2]["alignment"] == "start"
    # moving b away breaks the edit point -> transition dropped in the same undo step
    run(e, "clip.move", clip_ids=[b], delta="1s")
    assert tid not in video_track(e, seq)["transitions"]
    e.dispatch("undo")
    assert tid in video_track(e, seq)["transitions"]
    run(e, "transition.set", transition_id=tid, duration="20f", alignment="start")
    tr = video_track(e, seq)["transitions"][tid]
    assert tr["duration"] == 20 * SEC // 30 and tr["alignment"] == "start"
    run(e, "transition.remove", transition_id=tid)
    assert tid not in video_track(e, seq)["transitions"]
    with pytest.raises(CommandError):
        run(e, "transition.add", clip_id=a, edge="end", type="constant_power")
    validate_project(e.session.doc)


def test_apply_default_at_playhead(eng):
    e, seq, a, b = eng
    run(e, "sequence.set_playhead", time="58f")
    ids = run(e, "transition.apply_default", kind="video")["transition_ids"]
    tr = video_track(e, seq)["transitions"][ids[0]]
    assert (tr["clip_a"], tr["clip_b"]) == (a, b)
    with pytest.raises(CommandError):  # stills have no audio clips
        run(e, "transition.apply_default", kind="audio")


def test_transform_effect_eval():
    c = {"source_in": 0, "start": 0, "speed": 1.0}
    fx = {"type": "transform", "params": {"anchor": [50, 50], "position": [50, 50], "uniform": False, "scale": 50,
                                           "scale_width": 200, "rotation": 0, "opacity": 40, "skew": 0, "skew_axis": 0}, "keyframes": {}}
    m, op = transform_effect_at(fx, c, 0, 100, 100)
    assert apply(m, 60, 60) == pytest.approx((70, 55)) and op == pytest.approx(0.4)


@needs_ffmpeg
def test_render_cross_dissolve_and_dip(eng):
    e, seq, a, b = eng
    run(e, "transition.add", clip_id=a, edge="end", duration="20f")
    mid = frame(e, 2 * SEC - SEC // 60)  # half-way (frame centre)
    assert abs(mid[50, 50, 0] - 128) < 12 and abs(mid[50, 50, 2] - 128) < 12
    early = frame(e, 2 * SEC - 9 * SEC // 30)
    assert early[50, 50, 0] > 200
    run(e, "transition.add", clip_id=a, edge="start", type="dip_to_black", duration="10f")
    assert frame(e, 0).max() < 40  # starts black
    assert frame(e, "9f")[50, 50, 0] > 200


@needs_ffmpeg
def test_render_transform_effect_and_blur(eng):
    e, seq, a, _ = eng
    fid = run(e, "effect.add", clip_ids=[a], type="transform", params={"scale": 50})["effect_ids"][0]
    f = frame(e, 0)
    assert tuple(f[50, 50]) == (255, 0, 0) and f[10, 10].max() == 0  # 50x50 box in the centre
    run(e, "effect.set_param", clip_id=a, effect_id=fid, param="position", value=[25, 25])
    f = frame(e, 0)
    assert tuple(f[25, 25]) == (255, 0, 0) and f[60, 60].max() == 0
    # Motion is applied after the effect: Motion scale 200% around the frame centre
    run(e, "clip.set_property", clip_id=a, prop="scale", value=200)
    f = frame(e, 0)
    assert tuple(f[5, 5]) == (255, 0, 0) and f[60, 60].max() == 0
    run(e, "effect.remove", clip_id=a, effect_id=fid)
    run(e, "clip.set_property", clip_id=a, prop="scale", value=50)
    run(e, "effect.add", clip_ids=[a], type="gaussian_blur", params={"blurriness": 30})
    f = frame(e, 0)
    assert 30 < f[50, 26, 0] < 220  # soft edge where the box ended sharply
    assert f[50, 50, 0] > 200


def test_shared_effect_vectors():
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).resolve().parents[2] / "shared" / "test-vectors" / "effects.json").read_text(encoding="utf-8"))
    for case in data["trs"]:
        assert trs(*case["trs"]) == pytest.approx(tuple(case["expect"]))
    for case in data["windows"]:
        w = transition_window(data["track"], case["transition"])
        assert (list(w[:2]) if w else None) == case["expect"]
