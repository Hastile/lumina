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
    s.services.audio_measure = lambda path, start, dur, speed, ch, filters: _m(-30.0 if path.endswith("a.mp4") else -12.0)
    m = s.execute("audio.auto_match", {"targets": {"dialogue": -16}, "music_bed_db": None})["result"]["clips"]
    assert m[ids[0]]["gain_db"] == 14 and m[ids[1]]["gain_db"] == -13
    s.undo()
    assert [c["gain_db"] for c in clips(s, sid, "audio")] == [0, 0]
    s.execute("clip.set_audio_tag", {"clip_ids": ids, "tag": None})
    with pytest.raises(CommandError):
        s.execute("audio.auto_match", {})


def _m(lufs, dur=20.0, tp=None, noise=None, active=None):
    """A measurement as audio_analysis.measure returns it."""
    return {"duration": dur, "integrated": lufs, "momentary_max": lufs + 4, "short_max": lufs + 2, "lra": 6.0,
            "noise_floor": noise if noise is not None else lufs - 40, "active": dur * 0.9 if active is None else active,
            "sample_peak": tp if tp is not None else lufs + 12, "true_peak": tp if tp is not None else lufs + 12}


def _plan(items, **opts):
    from lumina.audio_analysis import DEFAULT_TARGETS, plan_match

    base = [{"gain_db": 0.0, "bed": False, **it} for it in items]
    return plan_match(base, {**DEFAULT_TARGETS, "dialogue": -16}, opts)


def test_match_cuts_of_one_recording_share_a_gain():
    # a talk recording cut in three: the quiet aside and the loud laugh keep their place in the performance
    r = _plan([{"id": "a", "tag": "dialogue", "group": "rec", "m": _m(-24)},
               {"id": "b", "tag": "dialogue", "group": "rec", "m": _m(-26)},
               {"id": "c", "tag": "dialogue", "group": "rec", "m": _m(-21)}])
    assert r["a"]["gain_db"] == r["b"]["gain_db"] == r["c"]["gain_db"]
    assert r["a"]["group"] == 3 and -17 < r["a"]["result"] < -15


def test_match_pulls_an_outlier_cut_halfway_back():
    r = _plan([{"id": "a", "tag": "dialogue", "group": "rec", "m": _m(-24, dur=60)},
               {"id": "b", "tag": "dialogue", "group": "rec", "m": _m(-40, dur=5)}])
    assert "outlier" in r["b"]["notes"] and r["b"]["gain_db"] > r["a"]["gain_db"]
    assert r["b"]["result"] < r["a"]["result"] - 6  # still quieter: it was a whisper


def test_match_music_under_dialogue_is_a_bed():
    r = _plan([{"id": "m", "tag": "music", "group": "bgm", "bed": True, "m": _m(-14)},
               {"id": "n", "tag": "music", "group": "intro", "m": _m(-14)}])
    assert r["m"]["target"] == -28 and "bed" in r["m"]["notes"]
    assert r["n"]["target"] == -25


def test_match_respects_true_peak_and_boost_limits():
    r = _plan([{"id": "a", "tag": "dialogue", "group": "x", "m": _m(-30, tp=-4)},
               {"id": "b", "tag": "dialogue", "group": "y", "m": _m(-60, tp=-40)}])
    assert r["a"]["gain_db"] == 3 and "peak_limited" in r["a"]["notes"]
    assert r["b"]["gain_db"] == 20 and "boost_capped" in r["b"]["notes"]


def test_match_sfx_on_integrated_loudness_short_hits_on_their_loudest_moment_and_small_changes_are_kept():
    r = _plan([{"id": "s", "tag": "sfx", "group": "s", "m": _m(-30, dur=0.8)},
               {"id": "w", "tag": "sfx", "group": "w", "m": _m(-30, dur=4)},
               {"id": "k", "tag": "ambience", "group": "k", "gain_db": -2.0, "m": _m(-27.8)}])
    assert r["s"]["metric"] == "momentary_max" and r["s"]["gain_db"] == 5  # -26 -> -21
    assert r["w"]["metric"] == "integrated" and r["w"]["gain_db"] == 9  # a 4 s whoosh: -30 -> -21 LUFS
    assert r["k"]["gain_db"] == -2.0 and "kept" in r["k"]["notes"]


def test_match_flags_noisy_boosts_and_silence():
    r = _plan([{"id": "n", "tag": "dialogue", "group": "n", "m": _m(-32, noise=-42)},
               {"id": "z", "tag": "dialogue", "group": "z", "m": {**_m(-30), "integrated": None, "momentary_max": None, "short_max": None}}])
    assert "noisy" in r["n"]["notes"]
    assert r["z"]["lufs"] is None and r["z"]["notes"] == ["silent"]


def test_auto_match_finds_music_beds_and_remeasures_compressed_clips(monkeypatch):
    s, sid, (a, b, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": 0})
    s.execute("clip.place", {"media_id": b, "at": 0, "take_video": False, "audio_track_id": s.doc["sequences"][sid]["audio_tracks"][1]})
    talk, bgm = clips(s, sid, "audio")[0]["id"], clips(s, sid, "audio", 1)[0]["id"]
    monkeypatch.setattr(audio_cmd, "media_path", lambda m: m["path"])
    s.execute("clip.set_audio_tag", {"clip_ids": [talk], "tag": "dialogue"})
    s.execute("clip.set_audio_tag", {"clip_ids": [bgm], "tag": "music"})
    s.execute("effect.add", {"clip_ids": [talk], "type": "compressor"})
    calls = []

    def fake(path, start, dur, speed, ch, filters):
        calls.append(filters)
        if path.endswith("b.mp4"):
            return _m(-14)
        gain = next((float(f[7:-2]) for f in filters if f.startswith("volume=")), 0.0)
        return _m(-30 + gain * 0.5, tp=-30 + gain * 0.5 + 10)  # a 2:1 compressor: half the gain comes through

    s.services.audio_measure = fake
    r = s.execute("audio.auto_match", {"targets": {"dialogue": -16}})["result"]["clips"]
    assert "bed" in r[bgm]["notes"] and r[bgm]["target"] == -28
    # linear maths says +14 dB; through the compressor that only reaches -23, so it is re-measured up to the cap
    assert "compressed" in r[talk]["notes"] and "boost_capped" in r[talk]["notes"]
    assert r[talk]["gain_db"] == 20 and r[talk]["result"] == -20
    assert any(any(f.startswith("acompressor") for f in fl) for fl in calls), "effects are part of the measurement"


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_measure_agrees_with_ffmpeg_ebur128(tmp_path):
    import subprocess

    from lumina.audio_analysis import loudness, measure

    wav = tmp_path / "noise.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:a=0.2", "-ac", "2", str(wav)], check=True)
    m = measure(str(wav), 0, 8 * 254016000000)
    assert abs(m["integrated"] - loudness(str(wav), 0, 8 * 254016000000)) < 0.15
    assert m["true_peak"] >= m["sample_peak"] and m["lra"] < 1
    m6 = measure(str(wav), 0, 8 * 254016000000, filters=["volume=6dB"])
    assert abs(m6["integrated"] - m["integrated"] - 6) < 0.05


def test_auto_match_reset_restores_the_previous_gain(monkeypatch):
    s, sid, ids = _audio_clips()
    monkeypatch.setattr(audio_cmd, "media_path", lambda m: m["path"])
    s.execute("clip.set_audio_tag", {"clip_ids": ids, "tag": "dialogue"})
    s.execute("clip.set", {"clip_ids": ids[:1], "gain_db": 2.0})
    s.services.audio_measure = lambda path, start, dur, speed, ch, filters: _m(-30.0)
    s.execute("audio.auto_match", {"clip_ids": ids[:1], "targets": {"dialogue": -20}, "music_bed_db": None})
    c = clips(s, sid, "audio")[0]
    assert c["gain_db"] == 10 and c["auto_match"] == {"target": -20, "lufs": -30, "gain_db": 10, "previous_gain_db": 2.0}
    # matching again keeps the original "before"
    s.execute("audio.auto_match", {"clip_ids": ids[:1], "targets": {"dialogue": -18}, "music_bed_db": None})
    assert clips(s, sid, "audio")[0]["auto_match"]["previous_gain_db"] == 2.0
    s.execute("audio.reset_match", {"clip_ids": ids[:1]})
    c = clips(s, sid, "audio")[0]
    assert c["gain_db"] == 2.0 and c["auto_match"] is None


def test_muted_clip_is_silent_in_the_export_mix(monkeypatch):
    from lumina.render import export as ex

    s, sid, ids = _audio_clips()
    s.execute("clip.set", {"clip_ids": ids[:1], "muted": True})
    seen = []
    monkeypatch.setattr(ex, "_clip_pcm", lambda doc, c, *a, **k: seen.append(c["id"]) or None)
    import numpy as np
    buf = np.zeros((48000, 2), np.float32)
    ex._mix_into(s.doc, s.doc["sequences"][sid], 0, 40 * 254016000000, buf, 48000)
    assert ids[0] not in seen and ids[1] in seen
