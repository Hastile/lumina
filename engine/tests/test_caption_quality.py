import copy
import pytest
from lumina.speech.caption_quality import clean_text, finish_rows, TPS
from lumina.commands.text_editing import text_fingerprint
from lumina.commands.registry import CommandError
from lumina.session import Session
from lumina.model import validate_project


@pytest.mark.parametrize('text,expected', [('안녕하세요.,','안녕하세요'), ('“반갑습니다.”','“반갑습니다”'), ('버전 2.0입니다.','버전 2.0입니다'), ('왜일까요?','왜일까요?'), ('잠깐...','잠깐...'), ('U.S.','U.S.'), ('https://example.com.','https://example.com.')])
def test_punctuation(text, expected):
    assert clean_text(text) == expected


def aligned(text, **extra):
    return {'id': 'a', 'text': text, 'start': TPS, 'end': 10*TPS, 'chars': [
        {'offset': i, 'start': TPS+i*TPS//4, 'end': TPS+(i+1)*TPS//4} for i,c in enumerate(text) if not c.isspace()], **extra}


def test_split_uses_acoustics_preserves_boundaries_and_closing_marks():
    row=aligned('첫 설명입니다. 다음 이야기를 합니다.', track_id='t')
    out,warnings=finish_rows([row], frame_ticks=TPS//30)
    assert len(out)==2 and out[0]['start']==0
    assert out[0]['end']==out[1]['start']
    boundary=next(c['start'] for c in row['chars'] if c['offset']==row['text'].index('다음'))
    assert out[1]['start']==round(boundary/(TPS//30))*(TPS//30)
    assert [r['text'] for r in out]==['첫 설명입니다','다음 이야기를 합니다']
    assert all(r['source_id']=='a' for r in out)
    assert all(r['text'][c['offset']].strip() for r in out for c in r['chars'])


def test_no_fake_split_without_acoustic_boundary():
    out,warnings=finish_rows([{'id':'a','text':'경계가 없는 아주 긴 문장입니다.', 'start':TPS,'end':9*TPS}], frame_ticks=TPS//30)
    assert len(out)==1 and warnings and out[0]['start']==0


def test_apply_children_preserves_style_and_undo_and_refuses_missing_text():
    s=Session();sid=s.execute('sequence.create', {'name':'test'})['result']['sequence_id']
    text='첫 설명입니다. 다음 이야기를 합니다.'
    added=s.execute('caption.add',{'text':text,'at':'1s','duration':'9s'})['result']
    cid,tid=added['clip_id'],added['track_id']
    s.execute('caption.set',{'clip_id':cid,'style':{'font_size':60}})
    before=copy.deepcopy(s.doc);seq=s.doc['sequences'][sid]
    row=aligned(text,track_id=tid);row['id']=cid
    rows,_=finish_rows([row],frame_ticks=TPS//30)
    params={'sequence_id':sid,'mode':'retime','expected_fingerprint':text_fingerprint(seq,s.doc['media']), 'rows':rows,'audio_track_id':'test'}
    s.execute('text.apply_speech',params)
    clips=list(seq['tracks'][tid]['clips'].values())
    assert len(clips)==2 and all(c['caption']['style']=={'font_size':60} for c in clips)
    assert clips[0]['id']==cid and clips[0]['start']==0
    validate_project(s.doc)
    s.undo();assert s.doc==before
    with pytest.raises(CommandError):s.execute('text.apply_speech',{**params,'rows':rows[:1]})
    assert s.doc==before


@pytest.mark.parametrize('mode', ['script', 'retime', 'transcribe'])
def test_service_finishes_worker_result_before_preview(tmp_path, monkeypatch, mode):
    import json
    import threading
    from types import SimpleNamespace
    from lumina.speech.service import SpeechService
    from lumina.speech import service
    s=Session();sid=s.execute('sequence.create', {'name':'test'})['result']['sequence_id']
    row=aligned('첫 설명입니다. 다음 이야기를 합니다.', **({'track_id':'t'} if mode=='retime' else {}))
    (tmp_path/'result.json').write_text(json.dumps({'rows':[row]}),encoding='utf-8')
    monkeypatch.setattr(service, 'export', lambda *a,**k: {})
    monkeypatch.setattr(service.subprocess, 'run', lambda *a,**k: None)
    monkeypatch.setattr(service.subprocess, 'Popen', lambda *a,**k: SimpleNamespace(wait=lambda:0))
    job={'directory':str(tmp_path),'doc':s.doc,'sequence_id':sid,'cancel':threading.Event(),
         'request':{'groups':[[row]] if mode!='transcribe' else [],'shorts':False}, 'status':'queued'}
    SpeechService(None)._run(job,'unused',10*TPS)
    assert job['status']=='preview',job
    assert job['result']['caption_quality']=='gapless-clean-split-v1'
    rows=job['result']['rows']
    assert len(rows)==2 and rows[0]['start']==0 and rows[0]['end']==rows[1]['start']
    assert all(not r['text'].endswith('.') for r in rows)
