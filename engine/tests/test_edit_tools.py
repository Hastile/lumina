import pytest

from lumina.commands.registry import CommandError
from lumina.commands.timeline import check_sequence

from .test_session import SEC, clips, make


def _two_cuts():
    """a.mp4 [0, 10s) from source 5s, b.mp4 [10s, 20s) from source 5s: both have 5 s of handles."""
    s, sid, (a, b, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": 0, "source_in": "5s", "source_out": "15s"})
    s.execute("clip.place", {"media_id": b, "at": "10s", "source_in": "5s", "source_out": "15s"})
    return s, sid, clips(s, sid)


def _ok(s, sid):
    check_sequence(s.doc, s.doc["sequences"][sid])


def test_rolling_edit_moves_the_edit_point_and_nothing_else():
    s, sid, (l, r) = _two_cuts()
    s.execute("clip.roll", {"clip_id": l["id"], "edge": "end", "delta": 2 * SEC})
    l2, r2 = clips(s, sid)
    assert (l2["end"], r2["start"], r2["source_in"], r2["end"]) == (12 * SEC, 12 * SEC, 7 * SEC, 20 * SEC)
    a1, a2 = clips(s, sid, "audio")
    assert a1["end"] == 12 * SEC and a2["start"] == 12 * SEC, "linked audio rolls too"
    _ok(s, sid)
    with pytest.raises(CommandError):
        s.execute("clip.roll", {"clip_id": r2["id"], "edge": "start", "delta": 5 * SEC})  # a.mp4 has 5 s left -> 3 s


def test_slip_keeps_position_and_shifts_source():
    s, sid, (l, _) = _two_cuts()
    s.execute("clip.slip", {"clip_id": l["id"], "delta": 2 * SEC})
    l2 = clips(s, sid)[0]
    assert (l2["start"], l2["end"], l2["source_in"]) == (0, 10 * SEC, 3 * SEC), "dragging right shows earlier frames"
    assert clips(s, sid, "audio")[0]["source_in"] == 3 * SEC
    with pytest.raises(CommandError):
        s.execute("clip.slip", {"clip_id": l["id"], "delta": 4 * SEC})


def test_slide_moves_the_clip_between_its_neighbours():
    s, sid, (a, *_) = make()
    for i, src in enumerate(("5s", "5s", "5s")):
        s.execute("clip.place", {"media_id": a, "at": f"{i * 4}s", "source_in": src, "source_out": "9s"})
    p, m, n = clips(s, sid)
    s.execute("clip.slide", {"clip_id": m["id"], "delta": SEC})
    p2, m2, n2 = clips(s, sid)
    assert (p2["end"], m2["start"], m2["end"], m2["source_in"]) == (5 * SEC, 5 * SEC, 9 * SEC, 5 * SEC)
    assert (n2["start"], n2["source_in"], n2["end"]) == (9 * SEC, 6 * SEC, 12 * SEC)
    _ok(s, sid)


def test_rate_stretch_changes_speed_to_fill_the_new_length():
    s, sid, (l, r) = _two_cuts()
    s.execute("clip.remove", {"clip_ids": [r["id"]]})
    s.execute("clip.rate_stretch", {"clip_id": l["id"], "edge": "end", "delta": 10 * SEC})
    l2 = clips(s, sid)[0]
    assert l2["end"] == 20 * SEC and l2["speed"] == 0.5 and l2["source_in"] == 5 * SEC
    assert clips(s, sid, "audio")[0]["speed"] == 0.5
    _ok(s, sid)


def test_paste_keeps_spacing_links_and_effects_and_inserts():
    s, sid, (l, r) = _two_cuts()
    s.execute("effect.add", {"clip_ids": [l["id"]], "type": "gaussian_blur"})
    seq = s.doc["sequences"][sid]
    v, a = seq["video_tracks"][0], seq["audio_tracks"][0]
    src = [c for c in clips(s, sid) + clips(s, sid, "audio") if c["start"] == 0]
    payload = [{"track_id": v if c in clips(s, sid) else a, "clip": c} for c in src]
    res = s.execute("clip.paste", {"clips": payload, "at": 20 * SEC})["result"]
    new = [c for c in clips(s, sid) if c["id"] in res["clip_ids"]][0]
    assert new["start"] == 20 * SEC and new["effects"][0]["type"] == "gaussian_blur"
    assert new["effects"][0]["id"] != clips(s, sid)[0]["effects"][0]["id"]
    na = [c for c in clips(s, sid, "audio") if c["id"] in res["clip_ids"]][0]
    assert na["link_id"] == new["link_id"] and new["link_id"] != clips(s, sid)[0]["link_id"]
    assert s.doc["sequences"][sid]["playhead"] == 30 * SEC
    s.execute("clip.paste", {"clips": payload, "at": 10 * SEC, "mode": "insert"})
    assert [c["start"] for c in clips(s, sid)] == [0, 10 * SEC, 20 * SEC, 30 * SEC]
    _ok(s, sid)


def test_paste_attributes_copies_motion_effects_and_keyframes():
    s, sid, (l, r) = _two_cuts()
    s.execute("clip.set_property", {"clip_id": l["id"], "prop": "scale", "value": 150})
    s.execute("effect.add", {"clip_ids": [l["id"]], "type": "gaussian_blur"})
    s.execute("clip.toggle_animation", {"clip_id": l["id"], "prop": "opacity"})
    src = clips(s, sid)[0]
    s.execute("clip.paste_attributes", {"source": src, "clip_ids": [r["id"]], "attributes": ["motion", "opacity", "effects"]})
    r2 = clips(s, sid)[1]
    assert r2["motion"]["scale"] == 150 and r2["effects"][0]["type"] == "gaussian_blur"
    k_src = src["keyframes"]["opacity"][0]["t"] - src["source_in"]
    assert r2["keyframes"]["opacity"][0]["t"] - r2["source_in"] == k_src, "keyframes keep their offset from the In point"


def test_out_of_sync_clips_move_or_slip_back_into_sync():
    from lumina.commands.edit_tools import sync_anchor

    s, sid, (l, r) = _two_cuts()
    a = clips(s, sid, "audio")[0]
    # move only the video 1 s later (Alt-drag): linked but 30 frames out of sync
    s.execute("clip.move", {"clip_ids": [l["id"]], "delta": 0, "include_linked": False})
    s.execute("clip.trim", {"clip_id": l["id"], "edge": "start", "delta": "1s", "include_linked": False})
    s.execute("clip.slip", {"clip_id": l["id"], "delta": SEC, "include_linked": False})
    v = clips(s, sid)[0]
    assert sync_anchor(v) - sync_anchor(a) == SEC
    s.execute("clip.resync", {"clip_id": v["id"], "mode": "slip"})
    assert sync_anchor(clips(s, sid)[0]) == sync_anchor(a)
    s.undo()
    s.execute("clip.resync", {"clip_id": v["id"], "mode": "move"})  # into the 1 s gap the trim left
    moved = clips(s, sid)[0]
    assert moved["start"] == 0 and sync_anchor(moved) == sync_anchor(a)
