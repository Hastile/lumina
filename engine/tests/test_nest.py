import pytest

from lumina.commands.registry import CommandError
from lumina.commands.timeline import check_sequence

from .test_session import SEC, clips, make


def test_nest_keeps_tracks_spacing_and_transitions():
    s, sid, (a, b, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": "2s", "source_out": "4s"})
    s.execute("clip.place", {"media_id": b, "at": "6s", "source_out": "4s"})
    ids = [c["id"] for c in clips(s, sid)]
    r = s.execute("sequence.nest", {"clip_ids": ids, "name": "Intro"})["result"]
    outer = s.doc["sequences"][sid]
    v = clips(s, sid)
    assert len(v) == 1 and (v[0]["start"], v[0]["end"], v[0]["name"]) == (2 * SEC, 10 * SEC, "Intro")
    inner = s.doc["sequences"][r["nested_sequence_id"]]
    starts = sorted(c["start"] for c in inner["tracks"][inner["video_tracks"][0]]["clips"].values())
    assert starts == [0, 4 * SEC]
    assert inner["settings"] == outer["settings"]
    check_sequence(s.doc, outer)
    check_sequence(s.doc, inner)
    s.undo()
    assert len(clips(s, sid)) == 2 and r["nested_sequence_id"] not in s.doc["sequences"]


def test_place_sequence_and_cycles():
    s, sid, (a, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": 0, "source_out": "3s"})
    other = s.execute("sequence.create", {"name": "B", "activate": False})["result"]["sequence_id"]
    with pytest.raises(CommandError):
        s.execute("clip.place_sequence", {"sequence_id": other, "nested_id": "nope"})
    with pytest.raises(CommandError):
        s.execute("clip.place_sequence", {"sequence_id": sid, "nested_id": sid})
    r = s.execute("clip.place_sequence", {"sequence_id": other, "nested_id": sid, "at": "1s"})["result"]
    assert len(r["clip_ids"]) == 2 and r["end"] == 4 * SEC
    with pytest.raises(CommandError, match="contain itself"):
        s.execute("clip.place_sequence", {"sequence_id": sid, "nested_id": other})
    # trimming a nested clip is limited by its sequence's length
    nv = clips(s, other)[0]
    with pytest.raises(CommandError):
        s.execute("clip.trim", {"sequence_id": other, "clip_id": nv["id"], "edge": "end", "delta": "1s"})


def test_unnest_puts_the_shown_part_back():
    s, sid, (a, b, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": "2s", "source_out": "4s"})
    s.execute("clip.place", {"media_id": b, "at": "6s", "source_out": "4s"})
    before = sorted((c["start"], c["end"], c["source_in"], c["media_id"]) for c in clips(s, sid))
    r = s.execute("sequence.nest", {"clip_ids": [c["id"] for c in clips(s, sid)]})["result"]
    out = s.execute("sequence.unnest", {"clip_id": r["video_clip_id"]})["result"]
    assert out["warnings"] == []
    assert sorted((c["start"], c["end"], c["source_in"], c["media_id"]) for c in clips(s, sid)) == before
    assert not any(c.get("sequence_id") for t in s.doc["sequences"][sid]["tracks"].values() for c in t["clips"].values())
    assert len(clips(s, sid, "audio")) == 2
    check_sequence(s.doc, s.doc["sequences"][sid])
    # trimmed nested clip: only what it shows comes back; its own Opacity is reported as dropped
    s.undo()
    s.execute("clip.trim", {"clip_id": r["video_clip_id"], "edge": "start", "delta": "3s"})
    s.execute("clip.set", {"clip_ids": [r["video_clip_id"]], "opacity": 50})
    out = s.execute("sequence.unnest", {"clip_id": r["video_clip_id"]})["result"]
    v = clips(s, sid)
    assert (v[0]["start"], v[0]["source_in"]) == (5 * SEC, 3 * SEC) and len(v) == 2
    assert out["warnings"] and "opacity" in out["warnings"][0]


@pytest.mark.parametrize("command", ["clip.place", "clip.place_sequence"])
@pytest.mark.parametrize("mode", ["insert", "overwrite"])
def test_place_disabled_audio_with_explicit_drop_track(command, mode):
    s, sid, (a, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": 0, "source_out": "3s"})
    other = s.execute("sequence.create", {"name": "Destination", "activate": False})["result"]["sequence_id"]
    seq = s.doc["sequences"][other]
    source = {"media_id": a} if command == "clip.place" else {"nested_id": sid}
    s.execute(command, {
        "sequence_id": other, **source, "at": 0, "mode": mode,
        "video_track_id": seq["video_tracks"][0],
        "audio_track_id": seq["audio_tracks"][0], "take_audio": False,
    })
    assert len(clips(s, other)) == 1
    assert clips(s, other, "audio") == []
