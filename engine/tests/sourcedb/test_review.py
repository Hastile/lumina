import copy
from types import SimpleNamespace

import pytest

from lumina.commands.registry import CommandError
from lumina.sourcedb import db
from lumina.sourcedb.review import SourceReviewService, connection, read_draft, write_draft, S


@pytest.fixture
def review(tmp_path):
    src = tmp_path / 'source.mp4'
    src.write_bytes(b'test-video-identity')
    doc = {'media': {'source': {'kind': 'video', 'path': str(src)}}}
    service = SourceReviewService(SimpleNamespace(session=SimpleNamespace(doc=doc)), tmp_path / 'source.db')
    with connection(service.path) as con:
        mid, _ = db.register_file(con, src, 'video', probe={'duration_ticks': 4*S, 'fps_num': 30, 'fps_den': 1})
        db.set_cuts(con, mid, [{'start_ticks':0, 'end_ticks':2*S, 'keyframe_ticks':S}, {'start_ticks':2*S,'end_ticks':4*S,'keyframe_ticks':3*S}])
        db.put_facts(con, mid, 'asr@1', [{'kind':'speech','value':'hello there','t0_ticks':S,'t1_ticks':3*S}])
    return service


def get(s):
    return s.get({'media_id':'source'})


def update(s, action, **kw):
    d = get(s)
    return s.update({'media_id':'source','revision':d['revision'],'cut_id':d['cuts'][0]['id'],'action':action, **kw})


def test_draft_is_persistent_without_changing_search_or_project(review):
    before = copy.deepcopy(review.engine.session.doc)
    d = update(review,'update',fields={'work':'Cyberpunk','characters':['Lucy'],'description':'human correction'})
    assert get(review)['cuts'][0]['description'] == 'human correction'
    assert d['cuts'][0]['dialogue'][0]['speaker'] == ''
    with connection(review.path) as con:
        assert not con.execute("SELECT 1 FROM fact WHERE kind='description'").fetchone()
    assert review.engine.session.doc == before


def test_split_merge_boundary_and_undo_clip_dialogue(review):
    old = get(review)
    d = update(review,'split',time=S)
    assert len(d['cuts']) == 3
    assert d['cuts'][0]['dialogue'] == []
    assert d['cuts'][1]['dialogue'][0]['start'] == S
    d = update(review,'merge')
    assert len(d['cuts']) == 2
    d = update(review,'boundary',time=3*S)
    assert d['cuts'][0]['end'] == d['cuts'][1]['start'] == 3*S
    d = update(review,'undo')
    assert d['cuts'][0]['end'] == old['cuts'][0]['end']


def test_publish_requires_review_and_exact_preview(review):
    d = get(review)
    p = {'media_id':'source','revision':d['revision']}
    preview = review.preview(p)
    with pytest.raises(CommandError):
        review.publish({**p,'token':preview['token']})
    d = update(review,'identity',fields={'work':'Default IP'})
    d = update(review,'update',fields={'work':'Override IP','characters':['Lucy'],'description':'test'})
    for c in d['cuts']:
        d = review.update({'media_id':'source','revision':d['revision'],'action':'review','cut_id':c['id']})
    p['revision'] = d['revision']
    preview = review.preview(p)
    result = review.publish({**p,'token':preview['token']})
    assert result['published_revision'] == result['revision']
    with connection(review.path) as con:
        ips = con.execute("SELECT e.name,f.t0_ticks FROM fact f JOIN entity e ON e.entity_id=f.ref_id WHERE f.kind='work' AND f.status='accepted' ORDER BY f.t0_ticks").fetchall()
        assert [tuple(r) for r in ips] == [('Override IP',0),('Default IP',2*S)]
        assert con.execute("SELECT count(*) FROM fact WHERE kind='speech' AND status='rejected'").fetchone()[0] == 1
    with pytest.raises(CommandError):
        review.publish({**p,'token':preview['token']})


def test_stale_revision_and_external_database_changes(review):
    d = get(review)
    update(review,'update',fields={'description':'first writer'})
    with pytest.raises(CommandError, match='다른 창'):
        review.update({'media_id':'source','revision':d['revision'],'action':'undo'})
    d = get(review)
    with connection(review.path) as con:
        db.put_facts(con,d['media_id'],'other@1',[{'kind':'description','value':'external'}])
    with pytest.raises(CommandError, match='검색 DB'):
        review.preview({'media_id':'source','revision':d['revision']})


def test_invalid_edit_rolls_back(review):
    d = get(review)
    with pytest.raises(CommandError):
        update(review,'boundary',time=4*S)
    assert get(review)['revision'] == d['revision']
    with pytest.raises(CommandError):
        update(review,'update',fields={'dialogue':[{'start':0,'end':3*S,'text':'out of bounds'}]})
    assert get(review)['revision'] == d['revision']


def test_srt_import_unknown_speaker_and_source_change(review,tmp_path):
    path = tmp_path / 'dialogue.srt'
    path.write_text('1\n00:00:01,000 --> 00:00:03,000\nTest dialogue\n',encoding='utf-8')
    d = get(review)
    d = review.import_srt({'media_id':'source','revision':d['revision'],'path':str(path)})
    assert [c['dialogue'][0]['speaker'] for c in d['cuts']] == ['', '']
    assert d['cuts'][0]['dialogue'][0]['end'] == 2*S
    (tmp_path/'source.mp4').write_bytes(b'replaced')
    with pytest.raises(CommandError, match='파일이 변경'):
        get(review)


def test_detector_preserves_one_frame_flash_and_can_undo(review, monkeypatch):
    from fractions import Fraction
    from lumina.sourcedb import ingest
    monkeypatch.setattr(ingest,'probe',lambda *a: {'duration_ticks':4*S,'fps_num':30,'fps_den':1})
    monkeypatch.setattr(ingest,'stream_timing',lambda *a: (Fraction(0),Fraction(30),True))
    monkeypatch.setattr(ingest,'scene_scores',lambda *a: [(Fraction(1),.9),(Fraction(31,30),.9)])
    monkeypatch.setattr(ingest,'spike_times',lambda *a: [])
    d=get(review);job={'stage':'cuts'}
    m,path=review.media({'media_id':'source'})
    review._worker(m,path,{'revision':d['revision']},job)
    assert job['status']=='done', job
    d=get(review)
    assert len(d['cuts'])==3
    assert d['cuts'][1]['end']-d['cuts'][1]['start']==S//30
    assert len(update(review,'undo')['cuts'])==2


def test_analyzer_keeps_human_correction_and_stages_other_proposals(review,tmp_path,monkeypatch):
    from lumina.sourcedb import ingest,describe
    import lumina.sourcedb.review as module
    monkeypatch.setattr(module.tempfile,'tempdir',str(tmp_path))
    thumb=tmp_path/'thumb.jpg';thumb.write_bytes(b'fake')
    monkeypatch.setattr(ingest,'thumb_path',lambda *a: thumb)
    def analyze(con, mids, **kw):
        db.put_facts(con,mids[0],'test-ai@1',[{'kind':'description','value':'AI proposal'},
            {'kind':'speech','value':'heard','t0_ticks':2*S,'t1_ticks':3*S}])
    monkeypatch.setattr(describe,'run',analyze)
    update(review,'update',fields={'description':'keep this'})
    d=get(review)
    _,path=review.media({'media_id':'source'})
    review._analyze(path,d,'describe')
    d=get(review)
    assert d['cuts'][0]['description']=='keep this'
    assert d['cuts'][1]['description']=='AI proposal'
    assert d['cuts'][1]['dialogue'][0]['speaker']==''
    assert not d['cuts'][1]['reviewed']
    with connection(review.path) as con:
        assert not con.execute("SELECT 1 FROM fact WHERE producer='test-ai@1'").fetchone()


def test_agent_review_is_distinct_from_person(review):
    d=update(review,'review',actor='agent')
    assert d['cuts'][0]['reviewer']=='agent'


def test_existing_first_pass_is_reviewable_and_old_versions_are_reused(review, monkeypatch):
    with connection(review.path) as con:
        mid=db.media_id_for_path(con, review.engine.session.doc['media']['source']['path'])
        for stage in ('cuts','describe','tagger','speech','embed','filename','probe'):
            db.job_done(con,mid,stage,stage+'@old')
    d=get(review)
    assert d['analysis_status']['state']=='analysed'
    assert d['analysis_status']['missing']==[]
    assert d['analysis_status']['reviewable']
    assert not any(c['reviewed'] for c in d['cuts'])
    assert d['analysis_status']['stages']['embed']['status']=='done'
    monkeypatch.setattr(review,'_worker',lambda *a: pytest.fail('already analysed source must not run'))
    for stage in ('ingest','missing','cuts','describe','tagger','speech'):
        result=review.start({'media_id':'source','revision':d['revision'],'stage':stage})
        assert result['status']=='done'
    assert [c['id'] for c in get(review)['cuts']]==[c['id'] for c in d['cuts']]
    with connection(review.path) as con:
        assert not con.execute("SELECT 1 FROM fact WHERE kind='source_review'").fetchone()


def test_missing_plan_skips_completed_and_inapplicable_stages(review, monkeypatch):
    import lumina.sourcedb.review as module
    review.engine.session.doc['media']['source']['audio']=None
    with connection(review.path) as con:
        db.job_done(con,get(review)['media_id'],'tagger','old')
    # A synchronous thread double inspects the actual scheduled plan.
    plans=[]
    class Thread:
        def __init__(self,target,args,**kw): self.args=args
        def start(self): plans.append(self.args[-1])
    monkeypatch.setattr(module.threading,'Thread',Thread)
    d=get(review)
    assert d['analysis_status']['missing']==['describe']
    review.start({'media_id':'source','revision':d['revision'],'stage':'missing'})
    assert plans==[['describe']]


def test_unanalysed_source_queues_ingest_without_skipping_cuts(review,tmp_path,monkeypatch):
    import lumina.sourcedb.review as module
    src=tmp_path/'new.mp4';src.write_bytes(b'new content')
    review.engine.session.doc['media']['source']['path']=str(src)
    d=get(review)
    assert not d['registered'] and d['analysis_status']['state']=='unanalysed'
    assert d['analysis_status']['missing']==['cuts','describe','tagger','speech']
    plans=[]
    class Thread:
        def __init__(self,target,args,**kw): self.args=args
        def start(self): plans.append(self.args[-1])
    monkeypatch.setattr(module.threading,'Thread',Thread)
    review.start({'media_id':'source','stage':'ingest'})
    assert plans==[['cuts','describe','tagger','speech']]


def test_partial_description_only_sends_missing_cut_to_analyzer(review,tmp_path,monkeypatch):
    from lumina.sourcedb import ingest,describe
    import lumina.sourcedb.review as module
    mid=get(review)['media_id']
    with connection(review.path) as con:
        db.put_facts(con,mid,'claude-sonnet@old',[{'kind':'description','value':'existing description','t0_ticks':0,'t1_ticks':2*S}])
    d=get(review)
    assert d['analysis_status']['stages']['describe']['status']=='partial'
    monkeypatch.setattr(module.tempfile,'tempdir',str(tmp_path))
    thumb=tmp_path/'thumb.jpg';thumb.write_bytes(b'fake')
    monkeypatch.setattr(ingest,'thumb_path',lambda *a: thumb)
    def analyze(con,mids,**kw):
        assert [r[0] for r in con.execute('SELECT start_ticks FROM cut')]==[2*S]
        db.put_facts(con,mids[0],'claude-sonnet@new',[{'kind':'description','value':'new description','t0_ticks':2*S,'t1_ticks':4*S}])
    monkeypatch.setattr(describe,'run',analyze)
    _,path=review.media({'media_id':'source'})
    review._analyze(path,d,'describe')
    d=get(review)
    assert [c['description'] for c in d['cuts']]==['existing description','new description']
    assert d['analysis_status']['stages']['describe']['status']=='done'


def live_save(s, changes):
    d=get(s)
    return s.save({'media_id':'source','revision':d['revision'],'changes':changes})


def test_live_save_atomic_search_sync_without_approving_other_facts(review):
    d=get(review); cid=d['cuts'][0]['id']
    result=live_save(review,[{'action':'update','cut_id':cid,'fields':{'description':'live correction'}}])
    assert result['published_revision']==result['revision']
    assert not any(c['reviewed'] for c in result['cuts'])
    with connection(review.path) as con:
        assert con.execute("SELECT status FROM fact WHERE kind='description' AND value='live correction' AND status!='rejected'").fetchone()[0]=='accepted'
        rows=con.execute("SELECT status,producer FROM fact WHERE kind='speech' AND status!='rejected'").fetchall()
        assert rows and all(r['status']=='proposed' and r['producer']=='asr@1' for r in rows)
    before=copy.deepcopy(result)
    with pytest.raises(CommandError):
        live_save(review,[{'action':'update','cut_id':cid,'fields':{'description':'must rollback'}},{'action':'boundary','cut_id':cid,'time':4*S}])
    assert get(review)['revision']==before['revision']
    assert get(review)['cuts'][0]['description']=='live correction'


def test_live_boundary_moves_next_start_and_undo_syncs_db(review):
    d=get(review); cid=d['cuts'][0]['id']
    result=live_save(review,[{'action':'boundary','cut_id':cid,'time':3*S}])
    assert result['cuts'][0]['end']==result['cuts'][1]['start']==3*S
    with connection(review.path) as con:
        rows=[dict(r) for r in con.execute('SELECT * FROM cut WHERE media_id=? ORDER BY start_ticks',(result['media_id'],))]
        assert rows[0]['end_ticks']==rows[1]['start_ticks']==3*S
    result=live_save(review,[{'action':'undo'}])
    assert result['cuts'][0]['end']==2*S
    with pytest.raises(CommandError):
        live_save(review,[{'action':'boundary','cut_id':result['cuts'][-1]['id'],'time':3*S}])


def test_catalog_enums_and_validation(review,monkeypatch):
    from lumina.sourcedb import catalog
    cat=catalog.Catalog.__new__(catalog.Catalog)
    cat.ok=True;cat._rows={};cat._refs={};cat._by_key={}
    cat._add(catalog.Match('work',1,'test:ip','테스트 작품',['테스트 작품','Test Work']))
    cat._add(catalog.Match('character',2,'test:char','테스트 인물',['테스트 인물'],ip_id=1))
    cat._add(catalog.Match('character',3,'other:char','다른 인물',['다른 인물'],ip_id=99))
    monkeypatch.setattr(catalog,'_CATALOG',cat)
    choices=review.choices({'query':'테스트','work':'테스트 작품'})
    assert [x['value'] for x in choices['works']]==['테스트 작품']
    assert [x['value'] for x in choices['characters']]==['테스트 인물']
    d=get(review);cid=d['cuts'][0]['id']
    d=live_save(review,[{'action':'update','cut_id':cid,'fields':{'work':'테스트 작품','characters':['테스트 인물']}}])
    for fields in [{'work':'없는 작품'},{'characters':['다른 인물']},{'shot':'invalid'}]:
        with pytest.raises(CommandError):live_save(review,[{'action':'update','cut_id':cid,'fields':fields}])
    with pytest.raises(CommandError):live_save(review,[{'action':'identity','fields':{'source_type':'invalid'}}])
    d=live_save(review,[{'action':'identity','fields':{'source_type':'episode'}}])
    assert d['identity']['source_type']=='episode'


def test_live_stale_revision_and_external_db_guard(review):
    d=get(review);cid=d['cuts'][0]['id']
    live_save(review,[{'action':'update','cut_id':cid,'fields':{'description':'first'}}])
    with pytest.raises(CommandError):
        review.save({'media_id':'source','revision':d['revision'],'changes':[{'action':'update','cut_id':cid,'fields':{'description':'stale'}}]})
    d=get(review)
    with connection(review.path) as con:db.put_facts(con,d['media_id'],'other@1',[{'kind':'description','value':'external'}])
    with pytest.raises(CommandError):live_save(review,[{'action':'update','cut_id':cid,'fields':{'description':'overwrite'}}])
