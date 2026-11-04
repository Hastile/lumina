"""Edit in Adobe Audition (clip.prepare_audio_edit) and hot reload of media files that change on
disk (media.refresh and the engine's watcher), against real ffmpeg."""

import shutil
import subprocess

import pytest

from lumina.engine import Engine
from lumina.media.jobs import JobRunner
from lumina.model import validate_project

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
SEC = 254_016_000_000


def tone(path, seconds, freq=440):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:d={seconds}",
                    "-ac", "1", "-c:a", "pcm_s16le", str(path)], check=True)


def engine_with(paths):
    e = Engine(run_jobs=False)
    e.dispatch("project.new", {"name": "t"})
    ids = e.dispatch("execute", {"name": "media.import", "params": {"paths": [str(p) for p in paths]}})["result"]["media_ids"]
    run(e, "sequence.create", name="S")
    return e, ids


def run(e, _cmd, **params):
    return e.dispatch("execute", {"name": _cmd, "params": params})["result"]


def audio_clip(e, cid):
    seq = e.session.doc["sequences"][e.session.doc["active_sequence_id"]]
    return next(t["clips"][cid] for t in seq["tracks"].values() if cid in t["clips"])


def test_an_untouched_clip_edits_its_own_file(tmp_path):
    src = tmp_path / "narration.wav"
    tone(src, 2)
    e, (mid,) = engine_with([src])
    cid = run(e, "clip.place", media_id=mid, at=0)["audio_clip_id"]
    undo_steps = len(e.session.undo_stack)
    r = run(e, "clip.prepare_audio_edit", clip_ids=[cid])
    assert r["clips"] == [{"clip_id": cid, "media_id": mid, "path": str(src), "extracted": False}]
    assert len(e.session.undo_stack) == undo_steps and len(e.session.doc["media"]) == 1


def test_a_trimmed_clip_gets_an_extracted_copy_that_undo_keeps_on_disk(tmp_path):
    src = tmp_path / "narration.wav"
    tone(src, 4)
    e, (mid,) = engine_with([src])
    cid = run(e, "clip.place", media_id=mid, at=0, source_in=SEC, source_out=3 * SEC)["audio_clip_id"]
    run(e, "clip.keyframe", clip_id=cid, prop="volume_db", op="add", at=SEC)
    before = dict(audio_clip(e, cid))
    assert [k["t"] for k in before["keyframes"]["volume_db"]] == [2 * SEC]
    r = run(e, "clip.prepare_audio_edit", clip_ids=[cid])["clips"][0]
    dest = tmp_path / "narration Audio Extracted.wav"
    assert r["extracted"] and r["path"] == str(dest) and dest.exists()
    new = e.session.doc["media"][r["media_id"]]
    assert abs(new["duration"] - 2 * SEC) < SEC // 100 and new["bin_id"] == e.session.doc["media"][mid]["bin_id"]
    c = audio_clip(e, cid)
    assert (c["media_id"], c["source_in"], c["start"], c["end"]) == (new["id"], 0, before["start"], before["end"])
    assert c["name"] == new["name"] and [k["t"] for k in c["keyframes"]["volume_db"]] == [SEC]  # was source time 2 s
    validate_project(e.session.doc)
    e.dispatch("undo")
    assert audio_clip(e, cid)["media_id"] == mid and r["media_id"] not in e.session.doc["media"]
    assert dest.exists()  # Premiere leaves the rendered file
    # a second edit does not overwrite the first copy
    r2 = run(e, "clip.prepare_audio_edit", clip_ids=[cid])["clips"][0]
    assert r2["path"] == str(tmp_path / "narration Audio Extracted 1.wav")


def test_speed_and_reverse_keep_their_timing(tmp_path):
    src = tmp_path / "sfx.wav"
    tone(src, 4)
    e, (mid,) = engine_with([src])
    cid = run(e, "clip.place", media_id=mid, at=0, source_in=SEC, source_out=3 * SEC)["audio_clip_id"]
    run(e, "clip.set_speed", clip_id=cid, speed=2.0)
    c0 = dict(audio_clip(e, cid))
    r = run(e, "clip.prepare_audio_edit", clip_ids=[cid])["clips"][0]
    c = audio_clip(e, cid)
    assert r["extracted"] and c["speed"] == c0["speed"] and (c["start"], c["end"]) == (c0["start"], c0["end"])
    assert c["source_in"] == 0 and abs(e.session.doc["media"][r["media_id"]]["duration"] - 2 * SEC) < SEC // 100
    # a whole file played at 100 % but backwards is a changed range too
    full = run(e, "clip.place", media_id=mid, at=10 * SEC)["audio_clip_id"]
    run(e, "clip.set_speed", clip_id=full, speed=1.0, reverse=True)
    rev = run(e, "clip.prepare_audio_edit", clip_ids=[full])["clips"][0]
    c = audio_clip(e, full)
    assert rev["extracted"] and c["speed"] < 0
    assert abs(c["source_in"] - e.session.doc["media"][rev["media_id"]]["duration"]) <= SEC // 30


def test_a_changed_file_is_reloaded_once_it_settles(tmp_path):
    src = tmp_path / "narration.wav"
    tone(src, 2)
    e, (mid,) = engine_with([src])
    run(e, "media.set_derived", media_id=mid, waveform_path=str(tmp_path / "old-waveform.json"))
    seen, pending = {}, {}
    assert e._poll_media(seen, pending) == []  # first look: taken as it is
    tone(src, 3, freq=880)  # saved in Audition
    assert e._poll_media(seen, pending) == []  # changed: wait for the writer to finish
    assert e._poll_media(seen, pending) == [mid]
    m = e.session.doc["media"][mid]
    assert m["revision"] == 1 and abs(m["duration"] - 3 * SEC) < SEC // 100 and m["waveform_path"] is None
    assert e._poll_media(seen, pending) == []
    validate_project(e.session.doc)


def test_generated_files_of_a_reloaded_media_get_new_names(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_CACHE", str(tmp_path / "cache"))
    src = tmp_path / "narration.wav"
    tone(src, 1)
    e, (mid,) = engine_with([src])
    recorded = {}
    runner = JobRunner(record=lambda m, f: recorded.update(f), emit=lambda ev: None)
    item = {**e.session.doc["media"][mid], "revision": 2}
    runner.submit_media(item, {"waveform"})
    runner.pool.shutdown(wait=True)
    assert recorded["waveform_path"].endswith("waveform-r2.json")
