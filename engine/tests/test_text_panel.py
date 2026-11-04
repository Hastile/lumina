import copy
import pytest
from lumina.session import Session
from lumina.commands.registry import CommandError
from lumina.commands.text_editing import text_fingerprint
from lumina.model import validate_project
from lumina.speech.worker import map_rows

S = 254016000000

def setup():
    s = Session()
    sid = s.execute('sequence.create', {'name': '텍스트 테스트'})['result']['sequence_id']
    out = s.execute('caption.add', {'text': '앞 문구 뒤 문구', 'at': '0s', 'duration': '4s'})['result']
    return s, sid, out['track_id'], out['clip_id']

def test_split_preserves_text_style_and_neighbors_and_undo():
    s, sid, tid, cid = setup()
    s.execute('caption.set', {'clip_id': cid, 'style': {'font_size': 120}})
    s.execute('caption.add', {'track_id': tid, 'text': '다음', 'at': '4s', 'duration': '2s'})
    before = copy.deepcopy(s.doc)
    out = s.execute('caption.split_text', {'clip_id': cid, 'offset': 4, 'at': '2s', 'expected_text': '앞 문구 뒤 문구'})['result']
    clips = s.doc['sequences'][sid]['tracks'][tid]['clips']
    assert clips[cid]['caption']['text'] == '앞 문구'
    right = clips[out['clip_ids'][1]]
    assert right['caption']['text'] == '뒤 문구' and right['start'] == 2*S
    assert right['caption']['style'] == {'font_size': 120}
    assert len(clips) == 3
    validate_project(s.doc)
    s.undo(); assert s.doc == before
    s.redo(); assert len(s.doc['sequences'][sid]['tracks'][tid]['clips']) == 3

@pytest.mark.parametrize('extra, code', [({}, 'NEEDS_SPLIT_TIME'), ({'at': '0s'}, 'BAD_SPLIT'), ({'at': '4s'}, 'BAD_SPLIT'), ({'at': '2s', 'expected_text': '옛 문구'}, 'STALE_TEXT')])
def test_split_refuses_estimated_or_stale_boundaries(extra, code):
    s, sid, tid, cid = setup()
    before = copy.deepcopy(s.doc)
    with pytest.raises(CommandError) as e:
        s.execute('caption.split_text', {'clip_id': cid, 'offset': 4, 'expected_text': '앞 문구 뒤 문구', **extra})
    assert e.value.code == code and s.doc == before

def test_locked_track_and_acoustic_split():
    s, sid, tid, cid = setup()
    clip = s.doc['sequences'][sid]['tracks'][tid]['clips'][cid]
    clip['caption']['timing'] = {'text': clip['caption']['text'], 'start': 0, 'end': 4*S, 'chars': [{'offset': 5, 'start': 2*S, 'end': 3*S}]}
    s.doc['sequences'][sid]['tracks'][tid]['locked'] = True
    with pytest.raises(CommandError):
        s.execute('caption.split_text', {'clip_id': cid, 'offset': 4, 'expected_text': clip['caption']['text']})
    s.doc['sequences'][sid]['tracks'][tid]['locked'] = False
    result = s.execute('caption.split_text', {'clip_id': cid, 'offset': 4, 'expected_text': clip['caption']['text']})['result']
    assert result['at'] == 2*S and result['timing_basis'] == 'acoustic'

def test_merge_is_atomic_and_retains_text():
    s, sid, tid, cid = setup()
    s.execute('caption.split_text', {'clip_id': cid, 'offset': 4, 'expected_text': '앞 문구 뒤 문구', 'at': '2s'})
    s.execute('caption.merge_next', {'clip_id': cid})
    clips = s.doc['sequences'][sid]['tracks'][tid]['clips']
    assert len(clips) == 1 and clips[cid]['caption']['text'] == '앞 문구 뒤 문구' and clips[cid]['end'] == 4*S

def test_apply_retime_and_stale_guard():
    s, sid, tid, cid = setup()
    seq = s.doc['sequences'][sid]
    before = copy.deepcopy(s.doc)
    params = {'sequence_id': sid, 'expected_fingerprint': text_fingerprint(seq, s.doc['media']), 'mode': 'retime', 'audio_track_id': 'test',
              'rows': [{'id': cid, 'text': '앞 문구 뒤 문구', 'start': S, 'end': 3*S}]}
    s.execute('text.apply_speech', params)
    assert seq['tracks'][tid]['clips'][cid]['start'] == S
    s.undo(); assert s.doc == before
    s.execute('caption.set', {'clip_id': cid, 'text': '사용자 수정'})
    with pytest.raises(CommandError) as e: s.execute('text.apply_speech', params)
    assert e.value.code == 'STALE_RESULT'

def test_script_is_separate_and_persists(tmp_path):
    s, sid, tid, cid = setup()
    s.execute('text.set_script', {'text': '새 대본\n다음 줄'})
    assert s.doc['sequences'][sid]['tracks'][tid]['clips'][cid]['caption']['text'] == '앞 문구 뒤 문구'
    path = str(tmp_path / 'script.lumina')
    s.save(path)
    from lumina.session import _read_project
    assert _read_project(path)[0]['sequences'][sid]['transcript']['text'] == '새 대본\n다음 줄'

def test_mapping_requires_acoustic_characters_and_preserves_ids():
    rows = [{'id': 'a', 'text': '가 나'}, {'id': 'b', 'text': '다'}]
    words = [{'word': '가 나 다', 'chars': [{'char': c, 'start': i, 'end': i+.2, 'score': .9} for i, c in enumerate('가나다')]}]
    result = map_rows(rows, words)
    assert [r['text'] for r in result] == ['가 나', '다']
    assert result[0]['chars'][1]['offset'] == 2 and result[1]['start'] == 2*S
    with pytest.raises(ValueError): map_rows(rows, [{'word': '가 나 다', 'start': 0, 'end': 3}])


def test_gapless_alignment_extends_end_without_moving_spoken_start():
    rows = [{'id': 'a', 'text': '가'}, {'id': 'b', 'text': '나'}]
    words = [{'word': '가나', 'chars': [{'char': '가', 'start': .3, 'end': 1}, {'char': '나', 'start': 2, 'end': 3}]}]
    result = map_rows(rows, words, close_gaps=True)
    assert result[0]['end'] == result[1]['start'] == 2*S
    assert result[0]['start'] == round(.3*S) and result[1]['end'] == 3*S


def test_punctuation_space_edit_preserves_alignment_and_merge_previous():
    s, sid, tid, cid = setup()
    clip = s.doc['sequences'][sid]['tracks'][tid]['clips'][cid]
    clip['caption']['timing'] = {'text': clip['caption']['text'], 'start': 0, 'end': 4*S, 'chars': [
        {'offset': i, 'start': round(n*.5*S), 'end': round((n*.5+.3)*S)} for n, (i, c) in enumerate((i, c) for i, c in enumerate(clip['caption']['text']) if not c.isspace())]}
    text = '앞 문구.\n뒤  문구'
    s.execute('caption.set', {'clip_id': cid, 'text': text})
    timing = s.doc['sequences'][sid]['tracks'][tid]['clips'][cid]['caption']['timing']
    assert timing['text'] == text
    right = s.execute('caption.split_text', {'clip_id': cid, 'offset': 6, 'expected_text': text})['result']['clip_ids'][1]
    assert s.doc['sequences'][sid]['tracks'][tid]['clips'][right]['caption']['text'] == '뒤  문구'
    out = s.execute('caption.merge_next', {'clip_id': right, 'direction': 'previous'})['result']
    assert out['clip_id'] == cid and out['cursor_text'] == '앞 문구.'
    merged = s.doc['sequences'][sid]['tracks'][tid]['clips'][cid]
    assert merged['caption']['timing']['text'] == '앞 문구. 뒤  문구'
    s.execute('caption.set', {'clip_id': cid, 'text': '다른 발음'})
    assert s.doc['sequences'][sid]['tracks'][tid]['clips'][cid]['caption']['timing'] is None
