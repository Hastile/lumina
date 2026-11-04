"""Caption tracks, track effects, reverse speed, frame hold, media relink/scan,
source patching and analysis-driven cuts, against real ffmpeg."""

import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from lumina.commands.registry import CommandError
from lumina.engine import Engine
from lumina.model import validate_project

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
SEC = 254_016_000_000


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    video = d / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=3", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:d=3", "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(video)], check=True)
    # tone 0-1 s, silence 1-2 s, tone 2-3 s
    speech = d / "speech.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=300:sample_rate=48000:d=3,volume='if(between(t,1,2),0,1)':eval=frame",
                    str(speech)], check=True)
    srt = d / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n첫 줄\n\n2\n00:00:01,500 --> 00:00:02,500\n둘째 줄\n", encoding="utf-8")
    return {"video": str(video), "speech": str(speech), "srt": str(srt), "dir": d}


def engine_with(paths, settings=None):
    e = Engine(run_jobs=False)
    e.dispatch("project.new", {"name": "t"})
    ids = e.dispatch("execute", {"name": "media.import", "params": {"paths": paths}})["result"]["media_ids"]
    return e, ids


def run(e, _cmd, **params):
    return e.dispatch("execute", {"name": _cmd, "params": params})["result"]


def frame(e, t, **kw):
    out = e.dispatch("export.frame", {"time": t, **kw})
    return np.asarray(Image.open(out["path"]).convert("RGB"), dtype=np.int16)


def seq(e):
    d = e.session.doc
    return d["sequences"][d["active_sequence_id"]]


def clips(e, kind="video_tracks", tid=None):
    s = seq(e)
    tids = [tid] if tid else s[kind]
    return sorted((c for t in tids for c in s["tracks"][t]["clips"].values()), key=lambda c: c["start"])


def ink_box(img, thresh=200):
    ys, xs = np.nonzero((img.min(axis=2) > thresh))
    return (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None


def black_sequence(e):
    sid = run(e, "sequence.create", name="S", settings={"width": 640, "height": 360, "fps_num": 30, "fps_den": 1, "sample_rate": 48000})["sequence_id"]
    return sid


def roundtrip(e):
    """The document stays valid (and saves/loads)."""
    validate_project(e.session.doc)


# ---------------------------------------------------------------- captions


def test_caption_track_import_style_export(media, tmp_path):
    e, _ = engine_with([media["video"]])
    black_sequence(e)
    r = run(e, "caption.import_srt", path=media["srt"])
    tid = r["track_id"]
    assert r["count"] == 2
    cues = clips(e, tid=tid)
    assert [c["caption"]["text"] for c in cues] == ["첫 줄", "둘째 줄"]
    assert cues[1]["start"] == round(1.5 * 30) * SEC // 30
    assert cues[0]["graphic"] is None
    roundtrip(e)
    # the cue renders in the track style, without being a graphic
    img = frame(e, "0.5s")
    box = ink_box(img)
    assert box is not None and box[1] > 360 * 0.6  # bottom of the frame
    # restyling the track moves every cue
    run(e, "caption.set_style", track_id=tid, style={"position": [320, 60]})
    box2 = ink_box(frame(e, "2s"))
    assert box2[3] < 120
    out = tmp_path / "out.srt"
    res = e.dispatch("caption.export_srt", {"path": str(out)})
    assert res["count"] == 2
    text = out.read_text(encoding="utf-8")
    assert "00:00:01,500 --> 00:00:02,500" in text and "둘째 줄" in text


def test_caption_add_edit_and_upgrade(media):
    e, _ = engine_with([media["video"]])
    black_sequence(e)
    r = run(e, "caption.add", text="hello", at="1s", duration="1s")
    run(e, "caption.set", clip_id=r["clip_id"], text="world", style={"color": "#ff0000"})
    c = clips(e, tid=r["track_id"])[0]
    assert c["caption"] == {"text": "world", "style": {"color": "#ff0000"}, "timing": None}
    img = frame(e, "1.2s")
    red = (img[..., 0] > 180) & (img[..., 1] < 80)
    assert red.sum() > 20
    run(e, "caption.to_graphics", track_id=r["track_id"])
    c = clips(e, tid=r["track_id"])[0]
    assert c["caption"] is None and c["graphic"]["layers"][0]["text"] == "world"
    run(e, "caption.from_graphics", track_id=r["track_id"])
    c = clips(e, tid=r["track_id"])[0]
    assert c["caption"]["text"] == "world" and c["graphic"] is None
    roundtrip(e)


# ---------------------------------------------------------------- track effects


def test_track_effect_scales_every_clip_from_its_own_in_point(media):
    e, _ = engine_with([media["video"]])
    black_sequence(e)
    tid = run(e, "caption.add_track", style={"position": [320, 180], "font_size": 60})["track_id"]
    run(e, "caption.add", track_id=tid, text="AAAA", at=0, duration="1s")
    run(e, "caption.add", track_id=tid, text="AAAA", at="1s", duration="1s")
    fx = run(e, "effect.add", track_ids=[tid], type="transform")["effect_ids"][0]
    # 50 % at each cue's In, 100 % 0.5 s later
    run(e, "effect.set_keyframes", track_id=tid, effect_id=fx, param="scale",
        keyframes=[{"t": 0, "v": 50}, {"t": "0.5s", "v": 100}])
    w_small = [ink_box(frame(e, t)) for t in ("0s", "1s")]
    w_full = [ink_box(frame(e, t)) for t in ("0.8s", "1.8s")]
    for small, full in zip(w_small, w_full):
        ws, wf = small[2] - small[0], full[2] - full[0]
        assert abs(ws / wf - 0.5) < 0.08, (ws, wf)
    # the clips themselves are untouched
    assert all(not c["effects"] for c in clips(e, tid=tid))
    roundtrip(e)


def test_track_preset_and_anchor_out(media, tmp_path, monkeypatch):
    import json

    presets = tmp_path / "presets.json"
    presets.write_text(json.dumps({"version": 1, "presets": [{
        "id": "out", "name": "fade out", "folder": [], "type": "anchor_out", "anchor_in": 0, "anchor_out": SEC, "speed": 1,
        "effects": [{"type": "transform", "enabled": True, "params": {"position": [0.5, 0.5], "anchor": [0.5, 0.5]},
                     "keyframes": {"opacity": [{"t": SEC // 2, "v": 100, "interp": "linear"}, {"t": SEC, "v": 0, "interp": "linear"}]}}]}]}))
    monkeypatch.setenv("LUMINA_PRESETS", str(presets))
    e, _ = engine_with([media["video"]])
    black_sequence(e)
    tid = run(e, "caption.add_track", style={"position": [320, 180]})["track_id"]
    run(e, "caption.add", track_id=tid, text="XXXX", at=0, duration="2s")
    fx = run(e, "effect.apply_preset", track_ids=[tid], preset_id="out")["effect_ids"][0]
    eff = seq(e)["tracks"][tid]["effects"][0]
    assert eff["anchor"] == "out" and [k["t"] for k in eff["keyframes"]["opacity"]] == [-SEC // 2, 0]
    assert ink_box(frame(e, "1s")) is not None      # before the fade
    bright = frame(e, "1.9s").max()
    assert bright < 120                              # nearly faded at the Out point
    run(e, "effect.remove", track_id=tid, effect_id=fx)
    assert not seq(e)["tracks"][tid]["effects"]


def test_track_effect_keyframe_commands(media):
    e, _ = engine_with([media["video"]])
    black_sequence(e)
    tid = run(e, "caption.add_track")["track_id"]
    cid = run(e, "caption.add", track_id=tid, text="k", at="1s", duration="2s")["clip_id"]
    fx = run(e, "effect.add", track_ids=[tid], type="transform")["effect_ids"][0]
    run(e, "effect.toggle_animation", track_id=tid, clip_id=cid, effect_id=fx, param="rotation", at="1.5s")
    kfs = seq(e)["tracks"][tid]["effects"][0]["keyframes"]["rotation"]
    assert kfs[0]["t"] == SEC // 2  # relative to the cue's In
    run(e, "effect.set_param", track_id=tid, clip_id=cid, effect_id=fx, param="rotation", value=30, at="2s")
    run(e, "keyframes.move", clip_id=cid, targets=[{"effect_id": fx, "param": "rotation", "times": [SEC]}], delta=SEC // 10)
    ts = [k["t"] for k in seq(e)["tracks"][tid]["effects"][0]["keyframes"]["rotation"]]
    assert ts == [SEC // 2, SEC + SEC // 10]
    run(e, "effect.set", track_id=tid, clip_id=cid, effect_id=fx, anchor="out")
    ts2 = [k["t"] for k in seq(e)["tracks"][tid]["effects"][0]["keyframes"]["rotation"]]
    assert ts2 == [t - 2 * SEC for t in ts]  # same place on the reference cue
    roundtrip(e)


def test_audio_track_effect_on_bus(media, tmp_path):
    e, (sp,) = engine_with([media["speech"]])
    run(e, "sequence.create_from_media", media_id=sp)
    a1 = seq(e)["audio_tracks"][0]
    run(e, "effect.add", track_ids=[a1], type="parametric_eq", params={"mid_freq": 300, "mid_gain": -30, "mid_q": 2})
    job = e.dispatch("export.start", {"path": str(tmp_path / "a.wav"), "preset": "wav", "wait": True})
    assert job["status"] == "done"
    pcm = np.frombuffer((tmp_path / "a.wav").read_bytes()[-48000 * 4:], dtype=np.int16).astype(float)
    assert np.abs(pcm).max() < 0.2 * 32767  # the 300 Hz tone is cut on the bus


# ---------------------------------------------------------------- reverse / frame hold


def test_reverse_speed_plays_the_same_frames_backwards(media):
    e, (vid,) = engine_with([media["video"]])
    run(e, "sequence.create_from_media", media_id=vid)
    v = clips(e)[0]
    fwd = [frame(e, f"{n}f") for n in (0, 30, 89)]
    run(e, "clip.set_speed", clip_id=v["id"], speed=1.0, reverse=True)
    v2 = clips(e)[0]
    assert v2["speed"] == -1.0 and v2["end"] == v["end"]
    rev = [frame(e, f"{n}f") for n in (89, 59, 0)]
    for a, b in zip(fwd, rev):
        assert np.abs(a - b).mean() < 3
    # and back
    run(e, "clip.set_speed", clip_id=v["id"], speed=1.0, reverse=False)
    assert clips(e)[0]["source_in"] == 0 and clips(e)[0]["speed"] == 1.0
    roundtrip(e)


def test_frame_hold_modes(media):
    e, (vid,) = engine_with([media["video"]])
    run(e, "sequence.create_from_media", media_id=vid)
    v = clips(e)[0]
    ref = frame(e, "1s")
    run(e, "clip.frame_hold", clip_id=v["id"], mode="hold", at="1s")
    assert np.abs(frame(e, "0s") - ref).mean() < 3 and np.abs(frame(e, "2.5s") - ref).mean() < 3
    run(e, "clip.frame_hold", clip_id=v["id"], mode="clear")
    r = run(e, "clip.frame_hold", clip_id=v["id"], mode="add", at="2s")
    held = frame(e, "2s")
    assert np.abs(frame(e, "2.9s") - held).mean() < 3
    assert len(clips(e)) == 2 and clips(e)[1]["freeze"] is not None
    end0 = max(c["end"] for c in clips(e, "audio_tracks"))
    run(e, "clip.frame_hold", clip_id=clips(e)[0]["id"], mode="insert", at="1s", duration="1s")
    assert max(c["end"] for c in clips(e, "audio_tracks")) == end0 + SEC
    roundtrip(e)


# ---------------------------------------------------------------- media


def test_relink_locate_offline_and_scan(media, tmp_path):
    copy = tmp_path / "moved" / "src.mp4"
    copy.parent.mkdir()
    shutil.copy(media["video"], copy)
    gone = tmp_path / "src.mp4"
    shutil.copy(media["video"], gone)
    e, (vid, vid2) = engine_with([str(gone), media["video"]])
    gone.unlink()
    scan = e.dispatch("media.scan")
    assert [o["media_id"] for o in scan["offline"]] == [vid]
    r = run(e, "media.locate", folder=str(tmp_path / "moved"))
    assert r["linked"][0]["media_id"] == vid
    assert e.session.doc["media"][vid]["path"] == str(copy)
    scan = e.dispatch("media.scan")
    assert scan["offline"] == [] and any(set(g["media_ids"]) == {vid, vid2} for g in scan["duplicates"])
    run(e, "media.make_offline", media_ids=[vid2])
    assert [o["reason"] for o in e.dispatch("media.scan")["offline"]] == ["made offline"]
    with pytest.raises(CommandError):
        run(e, "media.relink", media_id=vid, path=media["speech"])  # audio for a video item
    roundtrip(e)


def test_source_patching_is_separate_from_targeting(media):
    e, (vid,) = engine_with([media["video"]])
    run(e, "sequence.create", name="S", video_tracks=3, audio_tracks=2)
    s = seq(e)
    v3, a2 = s["video_tracks"][2], s["audio_tracks"][1]
    run(e, "sequence.set_patch", video_track_id=v3, audio_track_id=a2)
    r = run(e, "clip.place", media_id=vid, at=0)
    assert r["video_clip_id"] in seq(e)["tracks"][v3]["clips"] and r["audio_clip_id"] in seq(e)["tracks"][a2]["clips"]
    run(e, "sequence.set_patch", clear_audio=True)
    r = run(e, "clip.place", media_id=vid, at="5s")
    assert "audio_clip_id" not in r
    roundtrip(e)


# ---------------------------------------------------------------- analysis


def test_silence_plan_and_apply(media):
    e, (sp,) = engine_with([media["speech"]])
    run(e, "sequence.create_from_media", media_id=sp)
    det = e.dispatch("analysis.detect", {"kind": "silence", "media_id": sp})
    assert len(det["intervals"]) == 1 and abs(det["intervals"][0]["startSeconds"] - 1) < 0.05
    plan = e.dispatch("analysis.plan_cuts", {"kind": "silence", "padding_before": 0.1, "padding_after": 0.1})
    (s, e_), = plan["ranges_seconds"]
    assert abs(s - 1.1) < 0.06 and abs(e_ - 1.9) < 0.06
    end0 = max(c["end"] for c in clips(e, "audio_tracks"))
    out = e.dispatch("analysis.plan_cuts", {"kind": "silence", "apply": True})
    assert out["applied"]["removed_ticks"] > 0
    assert max(c["end"] for c in clips(e, "audio_tracks")) == end0 - out["applied"]["removed_ticks"]
    e.dispatch("undo")
    assert max(c["end"] for c in clips(e, "audio_tracks")) == end0


def test_scene_cuts_on_a_two_shot_clip(media, tmp_path):
    two = tmp_path / "two.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=160x120:r=30:d=1", "-f", "lavfi", "-i",
                    "color=blue:s=160x120:r=30:d=1", "-filter_complex", "[0][1]concat=n=2:v=1", "-pix_fmt", "yuv420p", str(two)], check=True)
    e, (vid,) = engine_with([str(two)])
    run(e, "sequence.create_from_media", media_id=vid)
    plan = e.dispatch("analysis.plan_scene_cuts", {"apply": True, "min_shot": 0.3})
    assert plan["count"] == 1 and abs(plan["points"][0]["seconds"] - 1.0) < 0.05
    assert len(clips(e)) == 2


# ---------------------------------------------------------------- adjustment layers / project assets


def test_adjustment_layer_blurs_everything_below(media, tmp_path):
    red = tmp_path / "red.png"
    img = Image.new("RGB", (640, 360), (0, 0, 0))
    img.paste((255, 0, 0), (200, 100, 440, 260))
    img.save(red)
    e, (rid,) = engine_with([str(red)])
    black_sequence(e)
    run(e, "clip.place", media_id=rid, at=0, source_out="2s")
    sharp = frame(e, "0.5s")
    aid = run(e, "asset.add", kind="adjustment")["asset_id"]
    cid = run(e, "asset.place", asset_id=aid, at=0, duration="2s")["clip_id"]
    adj = next(c for c in clips(e) if c["id"] == cid)
    assert adj["adjustment"] and seq(e)["video_tracks"].index(next(t for t in seq(e)["video_tracks"] if cid in seq(e)["tracks"][t]["clips"])) > 0
    unchanged = frame(e, "0.5s")
    assert np.abs(unchanged - sharp).mean() < 1  # no effects yet: the picture is untouched
    run(e, "effect.add", clip_ids=[cid], type="gaussian_blur", params={"blurriness": 60})
    blurred = frame(e, "0.5s")
    edge = lambda im: np.abs(np.diff(im[180, 150:250, 0])).max()  # noqa: E731
    assert edge(sharp) > 200 and edge(blurred) < 60
    run(e, "clip.set", clip_ids=[cid], opacity=50)
    half = frame(e, "0.5s")
    assert abs(half[180, 199, 0] - (sharp[180, 199, 0] + blurred[180, 199, 0]) / 2) < 12
    roundtrip(e)


def test_caption_asset_from_srt_and_place(media):
    e, _ = engine_with([media["video"]])
    black_sequence(e)
    aid = run(e, "asset.add", kind="captions", path=media["srt"])["asset_id"]
    a = e.session.doc["assets"][aid]
    assert a["kind"] == "captions" and [c["text"] for c in a["cues"]] == ["첫 줄", "둘째 줄"]
    r = run(e, "asset.place", asset_id=aid, at="1s")
    cues = clips(e, tid=r["track_id"])
    assert [c["caption"]["text"] for c in cues] == ["첫 줄", "둘째 줄"] and cues[0]["start"] == SEC
    roundtrip(e)


def test_caption_tracks_stay_on_top(media):
    e, (vid,) = engine_with([media["video"]])
    black_sequence(e)
    cap = run(e, "caption.add_track")["track_id"]
    v = run(e, "track.add", kind="video")["track_id"]
    order = seq(e)["video_tracks"]
    assert order[-1] == cap and order.index(v) < order.index(cap)
    with pytest.raises(CommandError):
        run(e, "clip.place", media_id=vid, at=0, video_track_id=cap)
    g = run(e, "graphic.create_text", text="t", at=0, duration="1s", track_id=seq(e)["video_tracks"][0])
    run(e, "caption.from_graphics", track_id=seq(e)["video_tracks"][0])
    assert seq(e)["video_tracks"][-1] == g["track_id"]


def test_new_video_track_numbering_skips_captions(media):
    e, _ = engine_with([media["video"]])
    run(e, "sequence.create", name="S", video_tracks=3, audio_tracks=1)
    run(e, "caption.add_track")
    tid = run(e, "track.add", kind="video")["track_id"]
    assert seq(e)["tracks"][tid]["name"] == "Video 4"


def test_caption_cues_take_the_track_transform(media):
    e, _ = engine_with([media["video"]])
    black_sequence(e)
    r = run(e, "caption.add", text="x", at=0, duration="1s")
    for cmd, params in (("clip.set_property", {"prop": "position", "value": [10, 10]}), ("clip.toggle_animation", {"prop": "scale"}),
                        ("clip.reset_motion", None), ("clip.set", None)):
        args = {"clip_ids": [r["clip_id"]]} if params is None else {"clip_id": r["clip_id"], **params}
        if cmd == "clip.set":
            args["opacity"] = 50
        with pytest.raises(CommandError):
            run(e, cmd, **args)
    with pytest.raises(CommandError):
        run(e, "caption.set", clip_id=r["clip_id"], style={"position": [1, 2]})
    run(e, "caption.set", clip_id=r["clip_id"], style={"color": "#00ff00"})  # formatting stays per cue
    run(e, "caption.set_style", track_id=r["track_id"], style={"position": [100, 100], "scale": 150, "rotation": 10, "opacity": 80})
    st = seq(e)["tracks"][r["track_id"]]["caption_style"]
    assert st["position"] == [100, 100] and st["scale"] == 150 and st["opacity"] == 80


def test_text_styles_import_and_apply(media, tmp_path, monkeypatch):
    import glob
    from lumina import text_styles

    monkeypatch.setenv("LUMINA_TEXT_STYLES", str(tmp_path / "styles.json"))
    files = glob.glob(str(__import__("pathlib").Path(__file__).parent / "fixtures" / "*.prtextstyle"))
    if not files:
        pytest.skip("no .prtextstyle fixture")
    e, _ = engine_with([media["video"]])
    black_sequence(e)
    r = e.dispatch("styles.import_premiere", {"path": files[0]})
    assert r["imported"] == 1
    st = e.dispatch("styles.list")[0]
    again = e.dispatch("styles.import_premiere", {"path": files[0]})
    assert again["imported"] == 0  # same name and look: kept once
    cap = run(e, "caption.add", text="스타일", at=0, duration="1s")
    run(e, "caption.set_style", track_id=cap["track_id"], style={"position": [320, 300]})
    run(e, "caption.apply_style", track_id=cap["track_id"], style_id=st["id"])
    cs = seq(e)["tracks"][cap["track_id"]]["caption_style"]
    assert cs["font_family"] == st["style"]["font_family"] and cs["font_size"] == st["style"]["font_size"]
    assert cs["position"] == [320, 300]  # transform kept
    saved = e.dispatch("styles.save", {"track_id": cap["track_id"], "name": "mine"})
    assert text_styles.get(saved["id"])["style"]["font_family"] == cs["font_family"]


def test_text_style_presets_carry_the_transform_and_a_default(media, tmp_path, monkeypatch):
    from lumina import text_styles

    monkeypatch.setenv("LUMINA_TEXT_STYLES", str(tmp_path / "styles.json"))
    tf = {"position": [None, 1058.0], "scale": 110.0, "rotation": 0.0, "opacity": 90.0}
    rec = text_styles.save_style("s", {"font_family": "Pretendard-Bold", "font_size": 100.0}, tf)
    e, _ = engine_with([media["video"]])
    black_sequence(e)
    cap = run(e, "caption.add", text="y", at=0, duration="1s")
    run(e, "caption.apply_style", track_id=cap["track_id"], style_id=rec["id"])
    cs = seq(e)["tracks"][cap["track_id"]]["caption_style"]
    assert cs["position"] == [320.0, 1058.0] and cs["scale"] == 110 and cs["opacity"] == 90  # x: frame centre
    again = text_styles.save_style("s", {"font_family": "Pretendard-Bold", "font_size": 90.0}, tf, replace=True)
    assert again["id"] == rec["id"] and len(text_styles.load()) == 1
    # the default style: every new caption track starts with it
    e.dispatch("styles.set_default", {"style_id": rec["id"]})
    t2 = run(e, "caption.add_track")["track_id"]
    cs2 = seq(e)["tracks"][t2]["caption_style"]
    assert cs2["font_family"] == "Pretendard-Bold" and cs2["font_size"] == 90 and cs2["position"] == [320.0, 1058.0]
    saved = e.dispatch("styles.save", {"track_id": t2, "name": "copy"})
    assert saved["transform"]["position"] == [None, 1058.0]  # centred x saved as "centre"
    assert text_styles.get_default()["id"] == rec["id"]
