import numpy as np
import pytest

from lumina.commands import audio as audio_cmd
from lumina.commands.registry import CommandError
from lumina.render.export import stereo_pan

from .test_session import clips, make


def _audio_clips():
    s, sid, (a, b, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": 0})
    s.execute("clip.place", {"media_id": b, "at": "20s"})
    return s, sid, [c["id"] for c in clips(s, sid, "audio")]


def test_audio_gain_set_and_adjust_is_one_undo_step():
    s, sid, ids = _audio_clips()
    s.execute("clip.audio_gain", {"clip_ids": ids, "mode": "set", "db": -3})
    s.execute("clip.audio_gain", {"clip_ids": ids, "mode": "adjust", "db": 1.5})
    assert [c["gain_db"] for c in clips(s, sid, "audio")] == [-1.5, -1.5]
    s.undo()
    assert [c["gain_db"] for c in clips(s, sid, "audio")] == [-3, -3]


def test_audio_gain_normalize(monkeypatch):
    s, sid, ids = _audio_clips()
    peaks = {"a.mp4": -12.0, "b.mp4": -6.0}
    monkeypatch.setattr(audio_cmd, "media_path", lambda m: m["path"])
    s.services.audio_peak = lambda path, start, dur, speed: next(v for k, v in peaks.items() if path.endswith(k))
    r = s.execute("clip.audio_gain", {"clip_ids": ids, "mode": "normalize_max", "db": -1})["result"]
    assert sorted(r["gain_db"].values()) == [5.0, 5.0], "one gain for the selection: the loudest peak lands on -1"
    s.execute("clip.audio_gain", {"clip_ids": ids, "mode": "normalize_all", "db": -1})
    assert sorted(c["gain_db"] for c in clips(s, sid, "audio")) == [5.0, 11.0]


def test_audio_gain_needs_audio():
    s, sid, (a, b, c, d) = make()
    vid = s.execute("clip.place", {"media_id": d, "at": 0})["result"]["video_clip_id"]
    with pytest.raises(CommandError):
        s.execute("clip.audio_gain", {"clip_ids": [vid], "mode": "normalize_all", "db": 0})


def test_track_mixer_and_master():
    s, sid, ids = _audio_clips()
    seq = s.doc["sequences"][sid]
    tid = seq["audio_tracks"][0]
    s.execute("track.set", {"track_id": tid, "volume_db": -6, "pan": -50})
    s.execute("sequence.set_master", {"volume_db": -2})
    assert seq["tracks"][tid]["volume_db"] == -6 and seq["tracks"][tid]["pan"] == -50 and seq["master_volume_db"] == -2
    with pytest.raises(CommandError):
        s.execute("track.set", {"track_id": tid, "pan": 150})


def test_stereo_pan_matches_web_audio():
    pcm = np.array([[1.0, 0.5]], dtype=np.float32)
    assert np.allclose(stereo_pan(pcm, 0), pcm)
    assert np.allclose(stereo_pan(pcm, -100), [[1.5, 0.0]], atol=1e-6)  # all right goes left
    assert np.allclose(stereo_pan(pcm, 100), [[0.0, 1.5]], atol=1e-6)
    g = np.pi / 4  # pan -50: x = 0.5
    assert np.allclose(stereo_pan(pcm, -50), [[1 + 0.5 * np.cos(g), 0.5 * np.sin(g)]], atol=1e-6)
