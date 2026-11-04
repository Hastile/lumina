import pytest

from .test_session import SEC, clips, make


@pytest.mark.parametrize("mode", ["insert", "overwrite"])
@pytest.mark.parametrize("media_index,kind", [(0, "video"), (2, "audio")])
@pytest.mark.parametrize("marks,expected_in,expected_out", [
    ({"in_point": 2 * SEC, "out_point": 5 * SEC}, 2 * SEC, 5 * SEC),
    ({"in_point": 2 * SEC}, 2 * SEC, None),
    ({"out_point": 5 * SEC}, 0, 5 * SEC),
    ({}, 0, None),
])
def test_placement_uses_source_marks(mode, media_index, kind, marks, expected_in, expected_out):
    s, sid, media = make()
    mid = media[media_index]
    s.execute("media.set_marks", {"media_id": mid, **marks})
    seq = s.doc["sequences"][sid]
    s.execute("clip.place", {
        "media_id": mid, "mode": mode, "at": SEC,
        "video_track_id": seq["video_tracks"][0],
        "audio_track_id": seq["audio_tracks"][0],
    })
    end = expected_out if expected_out is not None else s.doc["media"][mid]["duration"]
    for stream in (["video", "audio"] if kind == "video" else ["audio"]):
        clip = clips(s, sid, stream)[0]
        assert clip["source_in"] == expected_in
        assert clip["start"] == SEC
        assert clip["end"] == SEC + end - expected_in
