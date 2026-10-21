"""Sequence and clip markers, and putting a clip marker on a time by speed or by moving the clip."""

import pytest

from lumina.commands.registry import CommandError
from lumina.commands.timeline import clip_markers
from lumina.model import validate_project

from .test_session import SEC, clips, make


def placed(source_in=0):
    s, sid, (a, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": 0, "source_in": source_in, "source_out": source_in + 10 * SEC})
    return s, sid, a, clips(s, sid)[0]


def test_m_on_a_selected_clip_marks_its_media_at_the_source_time():
    s, sid, a, v = placed(source_in=SEC)
    out = s.execute("marker.add", {"clip_id": v["id"], "at": "2s", "name": "고점"})["result"]
    assert out["media_id"] == a and out["source_time"] == 3 * SEC
    mk = s.doc["media"][a]["markers"][out["marker_id"]]
    assert (mk["start"], mk["name"]) == (3 * SEC, "고점")
    assert s.doc["sequences"][sid]["markers"] == {}
    # every clip of that media shows it where its source time plays
    assert [t for _, t in clip_markers(s.doc, clips(s, sid)[0])] == [2 * SEC]
    s.execute("clip.set_speed", {"clip_id": v["id"], "speed": 2})
    assert [t for _, t in clip_markers(s.doc, clips(s, sid)[0])] == [1 * SEC]
    assert validate_project(s.doc).model_dump(mode="json") == s.doc
    # edit and delete it through the media (or the clip)
    s.execute("marker.update", {"media_id": a, "marker_id": out["marker_id"], "color": "red"})
    assert mk["color"] == "red"
    s.execute("marker.remove", {"clip_id": v["id"], "marker_ids": [out["marker_id"]]})
    assert s.doc["media"][a]["markers"] == {}
    with pytest.raises(CommandError):
        s.execute("marker.add", {"clip_id": v["id"], "at": "30s"})


def test_speed_puts_the_marked_moment_on_the_caption_start():
    s, sid, a, v = placed()
    s.execute("marker.add", {"media_id": a, "at": "2s"})
    # the moment 2 s into the footage should land on 2.5 s (a caption start): 80% speed
    out = s.execute("clip.align_marker", {"clip_id": v["id"], "at": "2.5s"})["result"]
    assert out["speed"] == pytest.approx(0.8) and not out["shortened"]
    v, au = clips(s, sid)[0], clips(s, sid, "audio")[0]
    assert [t for _, t in clip_markers(s.doc, v)] == [int(2.5 * SEC)]
    # the clip keeps its slot: same start, first frame and end; the linked audio follows
    assert (v["start"], v["source_in"], v["end"]) == (0, 0, 10 * SEC) and au["speed"] == v["speed"]
    # faster than the footage allows to fill the slot: it ends where the media does
    out = s.execute("clip.align_marker", {"clip_id": v["id"], "at": "0.5s"})["result"]
    assert out["speed"] == pytest.approx(4) and out["shortened"] and clips(s, sid)[0]["end"] == 5 * SEC
    with pytest.raises(CommandError):
        s.execute("clip.align_marker", {"clip_id": v["id"], "at": 0})


def test_move_slides_the_clip_so_the_marker_lands_there():
    s, sid, a, v = placed()
    s.execute("marker.add", {"media_id": a, "at": "2s"})
    s.execute("clip.align_marker", {"clip_id": v["id"], "at": "5s", "mode": "move"})
    v, au = clips(s, sid)[0], clips(s, sid, "audio")[0]
    assert v["start"] == 3 * SEC and au["start"] == 3 * SEC and v["speed"] == 1
    assert [t for _, t in clip_markers(s.doc, v)] == [5 * SEC]


def test_sequence_markers_still_go_on_the_sequence():
    s, sid, a, v = placed()
    mid = s.execute("marker.add", {"at": "4s"})["result"]["marker_id"]
    assert s.doc["sequences"][sid]["markers"][mid]["start"] == 4 * SEC and s.doc["media"][a]["markers"] == {}
