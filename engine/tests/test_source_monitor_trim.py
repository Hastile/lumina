import copy

import pytest

from lumina.commands.registry import CommandError
from .test_session import SEC, clips, make


def test_source_edge_trim_changes_instance_and_undo_restores():
    s, sid, media = make()
    s.execute('clip.place', {'media_id': media[0], 'at': '5s', 'source_in': '2s', 'source_out': '6s'})
    c = clips(s, sid)[0]
    audio_before = copy.deepcopy(clips(s, sid, 'audio'))
    s.execute('clip.trim', {'clip_id': c['id'], 'edge': 'start', 'delta': '1s', 'ripple': False, 'include_linked': False})
    after = clips(s, sid)[0]
    assert (after['start'], after['source_in'], after['end']) == (6*SEC, 3*SEC, 9*SEC)
    assert clips(s, sid, 'audio') == audio_before
    s.undo()
    assert clips(s, sid)[0] == c


@pytest.mark.parametrize('edge,delta', [('end', '2s'), ('start', '-6s')])
def test_blocked_source_trim_leaves_document_unchanged(edge, delta):
    s, sid, media = make()
    s.execute('clip.place', {'media_id': media[0], 'at': '5s', 'source_in': '2s', 'source_out': '6s', 'take_audio': False})
    cid = clips(s, sid)[0]['id']
    s.execute('clip.place', {'media_id': media[1], 'at': '9s', 'source_out': '2s', 'take_audio': False})
    before = copy.deepcopy(s.doc)
    with pytest.raises(CommandError):
        s.execute('clip.trim', {'clip_id': cid, 'edge': edge, 'delta': delta, 'ripple': False, 'include_linked': False})
    assert s.doc == before
