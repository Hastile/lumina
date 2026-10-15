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
