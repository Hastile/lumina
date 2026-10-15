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


def test_audio_effects_go_on_audio_clips():
    s, sid, ids = _audio_clips()
    r = s.execute("effect.add", {"clip_ids": ids[:1], "type": "parametric_eq", "params": {"mid_gain": 6}})["result"]
    e = clips(s, sid, "audio")[0]["effects"][0]
    assert e["id"] == r["effect_ids"][0] and e["params"]["mid_gain"] == 6 and e["params"]["mid_freq"] == 1000
    with pytest.raises(CommandError):
        s.execute("effect.add", {"clip_ids": [clips(s, sid)[0]["id"]], "type": "compressor"})
    with pytest.raises(CommandError):
        s.execute("effect.add", {"clip_ids": ids[:1], "type": "transform"})
    with pytest.raises(CommandError):
        s.execute("effect.set_param", {"clip_id": ids[0], "effect_id": e["id"], "param": "mid_q", "value": 99})


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_audio_effect_filters_with_ffmpeg(tmp_path):
    import subprocess

    from lumina.audio_fx import compressor_makeup_db
    from lumina.render.export import decode_audio

    wav = tmp_path / "sine.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=1000:d=1:sample_rate=48000", "-af", "volume=12dB", str(wav)], check=True)
    src = 254016000000
    rms = lambda x: float(np.sqrt(np.mean(np.square(x[4800:-4800]))))
    base = rms(decode_audio(str(wav), 0, src, 1.0, 48000))
    from lumina.audio_fx import ffmpeg_filters
    eq = {"type": "parametric_eq", "params": {"low_freq": 100, "low_gain": 0, "mid_freq": 1000, "mid_gain": 6, "mid_q": 1, "high_freq": 8000, "high_gain": 0}}
    boosted = rms(decode_audio(str(wav), 0, src, 1.0, 48000, ffmpeg_filters([eq], 48000)))
    assert 20 * np.log10(boosted / base) == pytest.approx(6, abs=0.1), "peaking +6 dB at its centre frequency"
    lim = {"type": "hard_limiter", "params": {"max_amplitude": -18, "input_boost": 0}}
    peak = float(np.abs(decode_audio(str(wav), 0, src, 1.0, 48000, ffmpeg_filters([lim], 48000))[9600:]).max())
    assert 20 * np.log10(peak) < -17.5
    comp = {"type": "compressor", "params": {"threshold": -30, "ratio": 4, "attack": 5, "release": 50, "output_gain": 0}}
    squashed = rms(decode_audio(str(wav), 0, src, 1.0, 48000, ffmpeg_filters([comp], 48000)))
    assert squashed < base * 0.6, "a -9 dB RMS sine above a -30 dB threshold is reduced"
    assert compressor_makeup_db(-30, 4) == pytest.approx(13.5)


def test_classifier_on_synthetic_sound():
    from lumina.audio_analysis import classify, features, tag_from_name

    rate = 16000
    t = np.arange(rate * 12) / rate
    rng = np.random.default_rng(1)
    # speech-like: voice-band noise gated at syllable rate, phrases separated by pauses
    syll = (np.sin(2 * np.pi * 4.5 * t) > 0.1) * (np.sin(2 * np.pi * 0.35 * t) > -0.4)
    voice = np.convolve(rng.standard_normal(len(t)), np.ones(8) / 8, "same") * 0.3 * syll + 0.3 * syll * np.sin(2 * np.pi * 180 * t)
    assert classify(features(voice, rate))[0] == "dialogue"
    chords = sum(0.1 * np.sin(2 * np.pi * f * t) for f in (220, 277, 330, 440, 554)) * (1 + 0.2 * np.sin(2 * np.pi * 0.5 * t))
    assert classify(features(chords, rate))[0] == "music"
    noise = rng.standard_normal(len(t)) * 0.05
    assert classify(features(noise, rate))[0] == "ambience"
    assert classify(features(noise[: rate * 2], rate))[0] == "sfx"
    assert classify(features(np.zeros(rate * 6), rate))[0] is None
    assert tag_from_name("KF - Whoosh Dramatic - 3.mp3") == "sfx"
    assert tag_from_name("브금_신나는.mp3") == "music" and tag_from_name("인터뷰_원본.wav") == "dialogue"
    assert tag_from_name("팜술 출시일 떴다.mp3") is None


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_loudness_with_ffmpeg(tmp_path):
    import subprocess

    from lumina.audio_analysis import loudness

    wav = tmp_path / "sine.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=1000:d=4:sample_rate=48000", "-af", "volume=12dB",
                    "-ac", "2", str(wav)], check=True)
    lufs = loudness(str(wav), 0, 4 * 254016000000)
    assert lufs is not None and -10 < lufs < -5  # a -9 dB RMS 1 kHz sine on both channels is about -6 LUFS


def test_auto_tag_and_auto_match(monkeypatch):
    s, sid, ids = _audio_clips()
    monkeypatch.setattr(audio_cmd, "media_path", lambda m: m["path"])
    s.services.audio_features = lambda path, start, dur, ch: (
        {"duration": 20, "silent": False, "pause_ratio": 0.3, "syllabic": 0.5, "flatness": 0.1, "centroid": 900, "dynamics": 8}
        if path.endswith("a.mp4") else
        {"duration": 20, "silent": False, "pause_ratio": 0.02, "syllabic": 0.1, "flatness": 0.05, "centroid": 1500, "dynamics": 3})
    r = s.execute("audio.auto_tag", {})["result"]["clips"]
    assert [r[i]["tag"] for i in ids] == ["dialogue", "music"]
    with pytest.raises(CommandError):
        s.execute("clip.set_audio_tag", {"clip_ids": ids, "tag": "speech"})
    s.services.audio_loudness = lambda path, start, dur, ch: -30.0 if path.endswith("a.mp4") else -12.0
    m = s.execute("audio.auto_match", {"targets": {"dialogue": -16}})["result"]["clips"]
    assert m[ids[0]]["gain_db"] == 14 and m[ids[1]]["gain_db"] == -13
    s.undo()
    assert [c["gain_db"] for c in clips(s, sid, "audio")] == [0, 0]
    s.execute("clip.set_audio_tag", {"clip_ids": ids, "tag": None})
    with pytest.raises(CommandError):
        s.execute("audio.auto_match", {})
