import copy
from .test_session import clips, make


def test_empty_razor_tracks_never_means_all_tracks():
    s, sid, media = make()
    s.execute('clip.place', {'media_id': media[0], 'at': 0})
    before = copy.deepcopy(s.doc)
    result = s.execute('clip.razor', {'at': '1s', 'track_ids': []})
    assert result['result']['created_clip_ids'] == []
    assert s.doc == before
    s.execute('clip.razor', {'at': '1s'})
    assert len(clips(s, sid)) == 2
    assert len(clips(s, sid, 'audio')) == 2
