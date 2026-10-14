import copy
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lumina.commands.registry import CommandError
from lumina.commands.timeline import check_sequence
from lumina.model import validate_project
from lumina.session import Session
from lumina.timebase import TICKS_PER_SECOND, frames_to_ticks

SEC = TICKS_PER_SECOND
F = frames_to_ticks(1, 30, 1)


class FakeServices:
    def probe(self, path):
        if path.endswith(".png"):
            return {"name": path, "kind": "image", "duration": 0,
                    "video": {"width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1, "codec": "png", "pix_fmt": ""},
                    "audio": None}
        if path.endswith(".wav"):
            return {"name": path, "kind": "audio", "duration": 60 * SEC, "video": None,
                    "audio": {"channels": 2, "sample_rate": 48000, "codec": "pcm"}}
        return {"name": path, "kind": "video", "duration": 20 * SEC,
                "video": {"width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1, "codec": "h264", "pix_fmt": "yuv420p"},
                "audio": {"channels": 2, "sample_rate": 48000, "codec": "aac"}}


def make():
    s = Session(services=FakeServices())
    mids = s.execute("media.import", {"paths": ["a.mp4", "b.mp4", "c.wav", "d.png"]})["result"]["media_ids"]
    sid = s.execute("sequence.create", {"name": "Seq"})["result"]["sequence_id"]
    return s, sid, mids


def clips(s, sid, kind="video", idx=0):
    seq = s.doc["sequences"][sid]
    tid = seq["video_tracks" if kind == "video" else "audio_tracks"][idx]
    return sorted(seq["tracks"][tid]["clips"].values(), key=lambda c: c["start"])


def test_place_links_video_and_audio():
    s, sid, (a, *_ ) = make()
    r = s.execute("clip.place", {"media_id": a, "at": 0})["result"]
    v, au = clips(s, sid)[0], clips(s, sid, "audio")[0]
    assert v["link_id"] == au["link_id"] is not None
    assert (r["start"], r["end"]) == (0, 20 * SEC)
    assert s.doc["sequences"][sid]["playhead"] == 20 * SEC


def test_overwrite_splits_underlying_clip():
    s, sid, (a, b, *_ ) = make()
    s.execute("clip.place", {"media_id": a, "at": 0})
    s.execute("clip.place", {"media_id": b, "at": "5s", "source_out": "2s"})
    v = clips(s, sid)
    assert [(c["start"], c["end"]) for c in v] == [(0, 5 * SEC), (5 * SEC, 7 * SEC), (7 * SEC, 20 * SEC)]
    assert v[2]["source_in"] == 7 * SEC


def test_insert_ripples_all_sync_locked_tracks():
    s, sid, (a, b, *_ ) = make()
    s.execute("clip.place", {"media_id": a, "at": 0})
    s.execute("clip.place", {"media_id": b, "at": "5s", "source_out": "2s", "mode": "insert"})
    assert [(c["start"], c["end"]) for c in clips(s, sid)] == [(0, 5 * SEC), (5 * SEC, 7 * SEC), (7 * SEC, 22 * SEC)]
    assert [(c["start"], c["end"]) for c in clips(s, sid, "audio")] == [(0, 5 * SEC), (5 * SEC, 7 * SEC), (7 * SEC, 22 * SEC)]


def test_razor_keeps_right_halves_linked_to_each_other():
    s, sid, (a, *_ ) = make()
    s.execute("clip.place", {"media_id": a, "at": 0})
    s.execute("clip.razor", {"at": "10s"})
    v, au = clips(s, sid), clips(s, sid, "audio")
    assert v[1]["link_id"] == au[1]["link_id"] != v[0]["link_id"]


def test_extract_ranges_like_silence_cut():
    s, sid, (a, *_ ) = make()
    s.execute("clip.place", {"media_id": a, "at": 0})
    r = s.execute("sequence.remove_ranges", {"ranges": [["2s", "3s"], ["10s", "12s"]]})["result"]
    assert r["removed_ticks"] == 3 * SEC
    v = clips(s, sid)
    assert [(c["start"], c["end"], c["source_in"]) for c in v] == [
        (0, 2 * SEC, 0), (2 * SEC, 9 * SEC, 3 * SEC), (9 * SEC, 17 * SEC, 12 * SEC)]
    check_sequence(s.doc, s.doc["sequences"][sid])


def test_ripple_delete_closes_gap():
    s, sid, (a, b, *_ ) = make()
    s.execute("clip.place", {"media_id": a, "at": 0, "source_out": "4s"})
    second = s.execute("clip.place", {"media_id": b, "source_out": "4s"})["result"]["video_clip_id"]
    s.execute("clip.place", {"media_id": a, "source_out": "4s"})
    s.execute("clip.remove", {"clip_ids": [second], "ripple": True})
    assert [(c["start"], c["end"]) for c in clips(s, sid)] == [(0, 4 * SEC), (4 * SEC, 8 * SEC)]


def test_trim_bounds_and_collision():
    s, sid, (a, b, *_ ) = make()
    first = s.execute("clip.place", {"media_id": a, "at": 0, "source_in": "1s", "source_out": "5s"})["result"]["video_clip_id"]
    s.execute("clip.place", {"media_id": b, "source_out": "4s"})
    with pytest.raises(CommandError) as e:
        s.execute("clip.trim", {"clip_id": first, "edge": "end", "delta": "1s"})
    assert e.value.code == "TRIM_COLLISION"
    with pytest.raises(CommandError) as e:
        s.execute("clip.trim", {"clip_id": first, "edge": "start", "delta": "-2s"})
    assert e.value.code in ("TRIM_PAST_MEDIA",)
    s.execute("clip.trim", {"clip_id": first, "edge": "end", "delta": "1s", "ripple": True})
    assert [(c["start"], c["end"]) for c in clips(s, sid)] == [(0, 5 * SEC), (5 * SEC, 9 * SEC)]


def test_failed_command_leaves_document_untouched():
    s, sid, (a, *_ ) = make()
    s.execute("clip.place", {"media_id": a, "at": 0})
    before = copy.deepcopy(s.doc)
    with pytest.raises(CommandError):
        s.execute("clip.move", {"clip_ids": [clips(s, sid)[0]["id"]], "delta": "-1s"})
    assert s.doc == before


def test_transaction_is_one_undo_step_and_rolls_back():
    s, sid, (a, *_ ) = make()
    before = copy.deepcopy(s.doc)
    s.begin("Rough cut")
    s.execute("clip.place", {"media_id": a, "at": 0})
    s.execute("clip.razor", {"at": "5s"})
    s.commit()
    s.undo()
    assert s.doc == before
    s.begin("x")
    s.execute("clip.place", {"media_id": a, "at": 0})
    s.rollback()
    assert s.doc == before


def test_save_open_and_crash_recovery(tmp_path):
    s, sid, (a, *_ ) = make()
    path = tmp_path / "p.lumina"
    s.save(str(path))
    s.execute("clip.place", {"media_id": a, "at": 0})
    expected = copy.deepcopy(s.doc)
    # simulate crash: no save, then reopen
    s2 = Session(services=FakeServices())
    snap = s2.open(str(path))
    assert snap["recovered_patches"] > 0
    assert s2.doc == expected
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == "lumina-project"


# ---------------------------------------------------------------- property test

def _op(draw, s, sid, mids):
    seq = s.doc["sequences"][sid]
    all_clips = [c["id"] for t in seq["tracks"].values() for c in t["clips"].values()]
    kind = draw(st.sampled_from(["place", "insert", "razor", "remove", "ripple", "move", "trim", "extract", "speed", "track", "marker"]))
    t = lambda: draw(st.integers(0, 40)) * 15 * F  # noqa: E731
    if kind in ("place", "insert"):
        return "clip.place", {"media_id": draw(st.sampled_from(mids)), "at": t(), "mode": "insert" if kind == "insert" else "overwrite",
                              "source_out": draw(st.integers(1, 12)) * SEC}
    if kind == "razor":
        return "clip.razor", {"at": t()}
    if kind == "extract":
        a = t()
        return "sequence.remove_ranges", {"ranges": [[a, a + draw(st.integers(1, 60)) * F]], "ripple": draw(st.booleans())}
    if kind == "track":
        return "track.add", {"kind": draw(st.sampled_from(["video", "audio"]))}
    if kind == "marker":
        return "marker.add", {"at": t(), "name": "m"}
    if not all_clips:
        return "clip.razor", {"at": t()}
    cid = draw(st.sampled_from(all_clips))
    if kind == "remove":
        return "clip.remove", {"clip_ids": [cid]}
    if kind == "ripple":
        return "clip.remove", {"clip_ids": [cid], "ripple": True}
    if kind == "move":
        return "clip.move", {"clip_ids": [cid], "delta": draw(st.integers(-20, 20)) * 15 * F, "track_delta": draw(st.integers(-1, 1))}
    if kind == "speed":
        return "clip.set_speed", {"clip_id": cid, "speed": draw(st.sampled_from([0.5, 1.0, 2.0, 1.5]))}
    return "clip.trim", {"clip_id": cid, "edge": draw(st.sampled_from(["start", "end"])),
                         "delta": draw(st.integers(-30, 30)) * F, "ripple": draw(st.booleans())}


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.data())
def test_random_edits_keep_invariants_and_undo_exactly(data):
    s, sid, mids = make()
    snapshots = [copy.deepcopy(s.doc)]
    for _ in range(data.draw(st.integers(1, 25))):
        name, params = _op(data.draw, s, sid, mids)
        try:
            out = s.execute(name, params)
        except CommandError:
            assert s.doc == snapshots[-1]
            continue
        check_sequence(s.doc, s.doc["sequences"][sid])
        validate_project(s.doc)
        if out["changed"]:
            snapshots.append(copy.deepcopy(s.doc))
    while len(snapshots) > 1:
        snapshots.pop()
        s.undo()
        assert s.doc == snapshots[-1]
    # and redo all the way forward again keeps invariants
    while s.redo_stack:
        s.redo()
        check_sequence(s.doc, s.doc["sequences"][sid])


def test_in_out_marks_are_partial_updates():
    s, sid, (a, *_ ) = make()
    s.execute("sequence.set_in_out", {"in_point": "1s"})
    s.execute("sequence.set_in_out", {"out_point": "3s"})
    seq = s.doc["sequences"][sid]
    assert (seq["in_point"], seq["out_point"]) == (SEC, 3 * SEC)
    s.execute("sequence.set_in_out", {"in_point": "4s"})  # In after Out clears Out
    assert (seq["in_point"], seq["out_point"]) == (4 * SEC, None)
    s.execute("sequence.set_in_out", {"clear_in": True})
    assert seq["in_point"] is None
    s.execute("media.set_marks", {"media_id": a, "out_point": 2 * SEC})
    s.execute("media.set_marks", {"media_id": a, "in_point": SEC})
    assert (s.doc["media"][a]["in_point"], s.doc["media"][a]["out_point"]) == (SEC, 2 * SEC)
