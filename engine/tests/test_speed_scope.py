from copy import deepcopy

import pytest

from lumina.commands.registry import CommandError
from .test_session import clips, make


@pytest.mark.parametrize("kind", ["video", "audio"])
def test_speed_changes_only_requested_clip_and_undo_restores_it(kind):
    s, sid, (a, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": 0, "source_out": "4s"})
    s.execute("clip.place", {"media_id": a, "at": "4s", "source_out": "4s"})
    before = deepcopy(s.doc["sequences"][sid])
    cid = clips(s, sid, kind)[0]["id"]
    result = s.execute("clip.set_speed", {"clip_id": cid, "speed": 2})["result"]
    assert result["clip_ids"] == [cid]
    for tid, track in before["tracks"].items():
        for other_id, original in track["clips"].items():
            actual = s.doc["sequences"][sid]["tracks"][tid]["clips"][other_id]
            if other_id == cid:
                assert actual["speed"] == 2
                assert actual["end"] - actual["start"] == (original["end"] - original["start"]) // 2
            else:
                assert actual == original
    s.undo()
    assert s.doc["sequences"][sid] == before


def test_slowdown_collision_does_not_move_other_clips():
    s, sid, (a, *_) = make()
    s.execute("clip.place", {"media_id": a, "at": 0, "source_out": "4s"})
    s.execute("clip.place", {"media_id": a, "at": "4s", "source_out": "4s"})
    before = deepcopy(s.doc["sequences"][sid])
    with pytest.raises(CommandError, match="overlaps"):
        s.execute("clip.set_speed", {"clip_id": clips(s, sid)[0]["id"], "speed": 0.5})
    assert s.doc["sequences"][sid] == before
