"""Compositor + export against real ffmpeg on generated media."""

import io
import json
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from lumina.commands.registry import CommandError
from lumina.engine import Engine
from lumina.render.compositor import place
from lumina.render.export import resolve_range

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
SEC = 254_016_000_000


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    video = d / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=3", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:d=3", "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(video)], check=True)
    red = d / "red.png"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(red)
    return {"video": str(video), "red": str(red), "dir": d}


def engine_with(paths):
    e = Engine(run_jobs=False)
    e.dispatch("project.new", {"name": "t"})
    ids = e.dispatch("execute", {"name": "media.import", "params": {"paths": paths}})["result"]["media_ids"]
    return e, ids


def run(e, _cmd, **params):
    return e.dispatch("execute", {"name": _cmd, "params": params})["result"]


def frame(e, t, **kw):
    out = e.dispatch("export.frame", {"time": t, **kw})
    return np.asarray(Image.open(out["path"]).convert("RGB"), dtype=np.int16)


def source_frame(path, n):
    vf = f"select=eq(n\\,{n}),scale=in_color_matrix=bt601:out_range=pc"
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", vf, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                       capture_output=True, check=True)
    return np.asarray(Image.open(io.BytesIO(p.stdout)).convert("RGB"), dtype=np.int16)


def test_frame_exact_against_ffmpeg(media):
    e, (vid,) = engine_with([media["video"]])
    run(e, "sequence.create_from_media", media_id=vid)
    # SD source -> Rec.601, the same matrix the reference decode uses
    for n in (0, 1, 17, 44):
        got, ref = frame(e, f"{n}f"), source_frame(media["video"], n)
        assert np.abs(got - ref).mean() < 1.5, n
        if n:
            assert np.abs(got - source_frame(media["video"], n - 1)).mean() > np.abs(got - ref).mean()


def test_trimmed_and_speed_clip_shows_the_right_source_frame(media):
    e, (vid,) = engine_with([media["video"]])
    seq = run(e, "sequence.create", name="s", settings={"width": 320, "height": 240, "fps_num": 30, "fps_den": 1})["sequence_id"]
    cid = run(e, "clip.place", sequence_id=seq, media_id=vid, at="10f", source_in="20f", source_out="80f")["video_clip_id"]
    run(e, "clip.set_speed", sequence_id=seq, clip_id=cid, speed=2.0)
    # sequence frame 15 = 5 frames into the clip = source frame 20 + 5 * 2
    got = frame(e, "15f", sequence_id=seq)
    assert np.abs(got - source_frame(media["video"], 30)).mean() < 1.5
    assert frame(e, "5f", sequence_id=seq).max() == 0  # before the clip: black


def test_motion_scale_position_and_opacity_keyframes(media):
    e, (img,) = engine_with([media["red"]])
    seq = run(e, "sequence.create", name="s", settings={"width": 320, "height": 240, "fps_num": 30, "fps_den": 1})["sequence_id"]
    cid = run(e, "clip.place", sequence_id=seq, media_id=img, at=0, source_out="2s")["video_clip_id"]
    run(e, "clip.set_property", sequence_id=seq, clip_id=cid, prop="scale", value=50)
    run(e, "clip.set_property", sequence_id=seq, clip_id=cid, prop="position", value=[60, 60])
    f0 = frame(e, 0, sequence_id=seq)
    assert tuple(f0[60, 60]) == (255, 0, 0)
    assert tuple(f0[60, 35]) == (255, 0, 0) and tuple(f0[60, 25]) == (0, 0, 0)  # 50 px wide around x=60
    run(e, "clip.set_keyframes", sequence_id=seq, clip_id=cid, prop="opacity", keyframes=[{"t": 0, "v": 100}, {"t": "1s", "v": 0}])
    half = frame(e, "15f", sequence_id=seq)
    assert abs(half[60, 60, 0] - 128) <= 3
    run(e, "clip.set_property", sequence_id=seq, clip_id=cid, prop="rotation", value=45, at=0)
    rot = frame(e, 0, sequence_id=seq)
    assert tuple(rot[60, 60]) == (255, 0, 0)
    assert rot[60, 92, 0] > 200 and rot[30, 30, 0] < 50  # diamond: wider on the axis, corners empty


def test_crop_and_hidden_track(media):
    e, (img,) = engine_with([media["red"]])
    seq = run(e, "sequence.create", name="s", settings={"width": 100, "height": 100, "fps_num": 30, "fps_den": 1})["sequence_id"]
    cid = run(e, "clip.place", sequence_id=seq, media_id=img, at=0, source_out="1s")["video_clip_id"]
    run(e, "clip.set_property", sequence_id=seq, clip_id=cid, prop="crop_left", value=50)
    f = frame(e, 0, sequence_id=seq)
    assert f[50, 25].max() == 0 and tuple(f[50, 75]) == (255, 0, 0)
    tid = e.session.doc["sequences"][seq]["video_tracks"][0]
    run(e, "track.set", sequence_id=seq, track_id=tid, hidden=True)
    assert frame(e, 0, sequence_id=seq).max() == 0


def test_text_graphic_renders_at_its_position(media):
    e, _ = engine_with([media["red"]])
    seq = run(e, "sequence.create", name="s", settings={"width": 640, "height": 360, "fps_num": 30, "fps_den": 1})["sequence_id"]
    run(e, "graphic.create_text", sequence_id=seq, text="TEST", at=0, font_size=60, color="#00ff00", position=[320, 180])
    f = frame(e, 0, sequence_id=seq)
    green = np.argwhere((f[:, :, 1] > 200) & (f[:, :, 0] < 80))
    assert len(green) > 300
    cy, cx = green.mean(axis=0)
    assert abs(cx - 320) < 20 and abs(cy - 180) < 20


def test_place_rotation_maps_corners():
    canvas = Image.new("RGBA", (200, 200), (0, 0, 0, 255))
    img = Image.new("RGBA", (100, 20), (255, 255, 255, 255))
    tr = {"src_w": 100, "src_h": 20, "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0,
          "position": [100, 100], "anchor": [50, 10], "scale_x": 100, "scale_y": 100, "rotation": 90, "opacity": 100}
    place(canvas, img, (0, 0), tr)
    a = np.asarray(canvas)
    assert a[60, 100, 0] == 255 and a[140, 100, 0] == 255  # vertical bar after 90 deg
    assert a[100, 60, 0] == 0 and a[100, 140, 0] == 0


def test_export_h264_verified_with_audio_gain(media):
    e, (vid,) = engine_with([media["video"]])
    r = run(e, "sequence.create_from_media", media_id=vid)
    out = media["dir"] / "out.mp4"
    job = e.dispatch("export.start", {"path": str(out), "wait": True, "hardware": "off"})
    assert job["status"] == "done", job["error"]
    res = job["result"]
    assert res["frames"] == 90 and abs(res["verification"]["durationSeconds"] - 3.0) < 0.05
    assert res["verification"]["video"]["width"] == 320 and res["verification"]["audioStreamCount"] == 1

    def rms(path):
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ss", "0.5", "-t", "2", "-ac", "1", "-f", "f32le", "-"],
                           capture_output=True, check=True)
        return float(np.sqrt(np.mean(np.frombuffer(p.stdout, dtype=np.float32) ** 2)))

    loud = rms(out)
    assert loud > 0.05  # lavfi sine is 1/8 amplitude
    run(e, "clip.set", clip_ids=[r["audio_clip_id"]], volume_db=-6.0)
    out2 = media["dir"] / "quiet.wav"
    job = e.dispatch("export.start", {"path": str(out2), "preset": "wav", "wait": True})
    assert job["status"] == "done", job["error"]
    assert rms(out2) / loud == pytest.approx(10 ** (-6 / 20), rel=0.05)


def test_export_in_out_range_and_errors(media):
    e, (vid,) = engine_with([media["video"]])
    run(e, "sequence.create_from_media", media_id=vid)
    run(e, "sequence.set_in_out", in_point="1s", out_point="2s")
    out = media["dir"] / "inout.mp4"
    job = e.dispatch("export.start", {"path": str(out), "range": "in_out", "wait": True, "hardware": "off"})
    assert job["status"] == "done", job["error"]
    assert job["result"]["frames"] == 30
    seq = e.session.doc["sequences"][e.session.doc["active_sequence_id"]]
    assert resolve_range(seq, None) == (0, 3 * SEC)
    with pytest.raises(CommandError):
        e.dispatch("export.start", {"path": str(out), "start": "2s", "end": "2s"})
    job = e.dispatch("export.start", {"path": str(out), "preset": "nope", "wait": True})
    assert job["status"] == "failed" and "preset" in job["error"]


def test_export_cancel_leaves_no_partial_file(media):
    e, (vid,) = engine_with([media["video"]])
    run(e, "sequence.create_from_media", media_id=vid)
    out = media["dir"] / "cancel.mp4"
    events = []
    e.subscribe(lambda ev: events.append(ev))
    job = e.dispatch("export.start", {"path": str(out), "hardware": "off"})
    e.dispatch("export.cancel", {"job_id": job["id"]})
    for _ in range(200):
        j = next(j for j in e.dispatch("jobs.list") if j["id"] == job["id"])
        if j["status"] in ("cancelled", "done", "failed"):
            break
        import time

        time.sleep(0.05)
    assert j["status"] == "cancelled"
    assert not out.exists() and not list(media["dir"].glob("cancel.partial*"))
    assert any(ev.get("type") == "job" and ev["job"]["id"] == job["id"] for ev in events)
    json.dumps(e.dispatch("jobs.list"))  # serializable for the RPC/MCP layers


def test_audio_constant_power_fade_out(media):
    e, (vid,) = engine_with([media["video"]])
    r = run(e, "sequence.create_from_media", media_id=vid)
    run(e, "transition.add", clip_id=r["audio_clip_id"], edge="end", duration="1s")
    out = media["dir"] / "fade.wav"
    job = e.dispatch("export.start", {"path": str(out), "preset": "wav", "wait": True})
    assert job["status"] == "done", job["error"]
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-ac", "1", "-f", "f32le", "-"], capture_output=True, check=True)
    pcm = np.frombuffer(p.stdout, dtype=np.float32)
    rms = lambda x: float(np.sqrt(np.mean(x ** 2)))  # noqa: E731
    sr = 48000
    full, mid, end = rms(pcm[sr // 2:sr]), rms(pcm[int(2.45 * sr):int(2.55 * sr)]), rms(pcm[-sr // 50:])
    assert mid / full == pytest.approx(np.cos(np.pi / 4), rel=0.08)  # equal power at the midpoint
    assert end / full < 0.1
