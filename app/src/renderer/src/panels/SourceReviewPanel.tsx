import { useEffect, useRef, useState } from 'react'
import { engine, mediaFileUrl } from '../engine/client'
import { useStore } from '../state/store'
import { goTo, stop } from '../state/transport'
import { FrameCanvas } from '../playback/FrameCanvas'
import { TICKS_PER_SECOND as S, timecode } from '../util/time'

type Line = { start: number; end: number; speaker: string; text: string; origin?: string }
type Cut = { id: string; start: number; end: number; keyframe: number; detected_start: number; detected_end: number;
  description: string; shot: string; work: string; characters: string[]; tags: Record<string, string[]>;
  dialogue: Line[]; note: string; excluded: boolean; reviewed: boolean; reviewer?: string; origin: string; evidence: unknown[] }
type Draft = { revision: number; registered?: boolean; duration: number; fps_num: number; fps_den: number;
  analysis_status?: {state:string; reviewable:boolean; missing:string[]; stages:Record<string,{status:string;basis:string;error?:string}>};
  cuts: Cut[]; identity: Record<string, string>; can_undo: boolean; published_revision: number | null;
  job?: { status: string; stage: string; message: string; error?: string }; path: string; db_path: string }

type Choice = {value:string;ref:string}
type Choices = {available:boolean;works:Choice[];characters:Choice[];source_types:string[];shots:string[]}
type Change = {action:string;cut_id?:string;fields?:Record<string,any>;time?:number}
const tagNames:Record<string,string>={action:'행동',mood:'분위기',setting:'장소',use_case:'활용'}
const sourceNames:Record<string,string>={episode:'에피소드',film:'영화',trailer:'예고편',pv:'PV',mv:'뮤직비디오',gameplay:'게임 플레이',key_visual:'키 비주얼',illustration:'일러스트',fanart:'팬아트',design_sheet:'설정화',screenshot:'스크린샷',sns_post:'SNS 게시물',article:'기사',store_page:'상점 페이지',search_result:'검색 결과',recording:'녹화',meme:'밈',logo:'로고'}
const stages:Record<string,string>={cuts:'컷',describe:'장면·IP·인물',tagger:'태그',speech:'대사'}
const list=(s:string)=>s.split(',').map(x=>x.trim()).filter(Boolean)

export function SourceReviewPanel(){
  const id=useStore(s=>s.sourceMediaId)
  const project=useStore(s=>s.doc?.id)
  return <LiveSourceReview key={`${project}:${id}`} id={id}/>
}
function optimistic(d:Draft, changes:Change[]):Draft {
  const n=structuredClone(d)
  for(const c of changes){
    if(c.action==='identity'){Object.assign(n.identity,c.fields);continue}
    const i=n.cuts?.findIndex(x=>x.id===c.cut_id) ?? -1
    if(i<0)continue
    if(c.action==='update'){const tags=c.fields?.tags;const previousTags=n.cuts[i].tags;Object.assign(n.cuts[i],c.fields);if(tags)n.cuts[i].tags={...previousTags,...tags}}
    if(c.action==='boundary' && n.cuts[i+1] && c.time!==undefined){n.cuts[i].end=c.time;n.cuts[i+1].start=c.time}
  }
  return n
}
function LiveSourceReview({id}:{id:string|null}){
  const media=useStore(s=>id?s.doc?.media[id]:undefined)
  const [d,setD]=useState<Draft|null>(null)
  const current=useRef<Draft|null>(null)
  const [selected,select]=useState('')
  const [error,setError]=useState('')
  const [saving,setSaving]=useState(false)
  const [toolsOpen,setToolsOpen]=useState(false)
  const [listOpen,setListOpen]=useState(false)
  const [query,setQuery]=useState('')
  const [ipQuery,setIpQuery]=useState('')
  const [choices,setChoices]=useState<Choices|null>(null)
  const [choiceError,setChoiceError]=useState('')
  const [lineIndex,setLineIndex]=useState(0)
  const [draftLine,setDraftLine]=useState<Line|null>(null)
  const [showVideo,setShowVideo]=useState(true)
  const [playing,setPlaying]=useState(false)
  const [playTime,setPlayTime]=useState(0)
  const video=useRef<HTMLVideoElement>(null)
  const playEnd=useRef(0)
  const alive=useRef(true)
  const pending=useRef<Change[]>([])
  const inFlight=useRef(false)
  const failed=useRef(false)
  const timer=useRef<ReturnType<typeof setTimeout>|null>(null)
  const panel=useRef<HTMLDivElement>(null)
  const [short,setShort]=useState(false)
  const eligible=!!media && ['video','image'].includes(media.kind)
  const cut=d?.cuts?.find(c=>c.id===selected) ?? d?.cuts?.[0]
  const index=cut && d?d.cuts.indexOf(cut):-1
  const next=d?.cuts?.[index+1]
  const fixed=!next || media?.kind==='image'
  const rate={fps_num:d?.fps_num || 30,fps_den:d?.fps_den || 1}
  const frame=Math.round(S*rate.fps_den/rate.fps_num)
  const tc=(t:number)=>timecode(t,rate)
  const work=cut?.work || d?.identity?.work || ''
  const registeredSpeakers=[...new Set([...(cut?.characters || []),...(cut?.dialogue.map(l=>l.speaker).filter(Boolean) || [])])]
  const sync=(value:Draft)=>{current.current=value;if(alive.current)setD(value)}
  const flush=async()=>{
    if(inFlight.current || !pending.current.length || !current.current)return
    const batch=pending.current.splice(0)
    const revision=current.current.revision
    inFlight.current=true
    if(alive.current){setSaving(true);setError('')}
    try{
      const saved=await engine.call<Draft>('source_review.save',{media_id:id,revision,changes:batch})
      failed.current=false
      sync(optimistic(saved,pending.current))
    }catch(e){
      pending.current=[...batch,...pending.current];failed.current=true
      if(alive.current)setError(`자동 저장 실패 · 입력은 유지됩니다. ${String(e)}`)
    }finally{
      inFlight.current=false
      if(!failed.current && pending.current.length)void flush()
      else if(alive.current)setSaving(false)
    }
  }
  const enqueue=(change:Change)=>{
    // Coalesce one drag/typing burst; already-in-flight writes remain ordered.
    const last=pending.current.findLastIndex(c=>c.action===change.action && c.cut_id===change.cut_id)
    if(last>=0 && ['boundary','update','identity'].includes(change.action)){
      const prev=pending.current[last]
      pending.current[last]=change.fields?{...change,fields:{...prev.fields,...change.fields,...(change.fields.tags?{tags:{...prev.fields?.tags,...change.fields.tags}}:{})}}:change
    }else pending.current.push(change)
    if(current.current)sync(optimistic(current.current,[change]))
    setSaving(true)
    if(timer.current)clearTimeout(timer.current)
    timer.current=setTimeout(()=>{if(!failed.current)void flush()},350)
  }
  useEffect(()=>{
    alive.current=true
    const get=async()=>{
      if(!eligible || inFlight.current || pending.current.length)return
      try{const value=await engine.call<Draft>('source_review.get',{media_id:id});if(alive.current && !inFlight.current && !pending.current.length)sync(value)}catch(e){if(alive.current)setError(String(e))}
    }
    void get();const poll=setInterval(()=>void get(),1800)
    return()=>{alive.current=false;clearInterval(poll);if(timer.current)clearTimeout(timer.current);if(!failed.current)void flush()}
  },[id,eligible])
  useEffect(()=>{
    const el=panel.current;if(!el)return
    const ro=new ResizeObserver(()=>setShort(el.clientHeight<420));ro.observe(el);return()=>ro.disconnect()
  },[])
  useEffect(()=>{
    let active=true
    setChoices(old=>old?{...old,characters:[]}:old)
    const t=setTimeout(()=>void engine.call<Choices>('source_review.choices',{query:ipQuery,work}).then(v=>{if(active){setChoices(v);setChoiceError(v.available?'':'IP DB를 찾지 못했습니다')}}).catch(e=>{if(active)setChoiceError(String(e))}),180)
    return()=>{active=false;clearTimeout(t)}
  },[ipQuery,work])
  const patch=(fields:Record<string,any>)=>cut && enqueue({action:'update',cut_id:cut.id,fields})
  const patchLines=(lines:Line[])=>patch({dialogue:lines.filter(l=>l.text.trim())})
  const changeEnd=(time:number)=>{
    if(!cut || !next || fixed)return
    video.current?.pause()
    const t=Math.max(cut.start+frame,Math.min(Math.round(time/frame)*frame,next.end-frame))
    stop();goTo('source',Math.max(cut.start,t-frame))
    if(t!==cut.end)enqueue({action:'boundary',cut_id:cut.id,time:t})
  }
  const choose=(c:Cut)=>{video.current?.pause();select(c.id);setLineIndex(0);setDraftLine(null);setIpQuery('');setListOpen(false)}
  const play=()=>{
    if(!video.current || !cut)return
    if(playing){video.current.pause();return}
    video.current.currentTime=cut.start/S;playEnd.current=cut.end
    setPlayTime(cut.start)
    void video.current.play().catch(e=>setError(String(e)))
  }
  const run=async(method:string,params:Record<string,unknown>={})=>{
    if(saving || inFlight.current || pending.current.length)return
    setSaving(true);setError('')
    try{await engine.call(`source_review.${method}`,{media_id:id,revision:current.current?.revision,...params});const value=await engine.call<Draft>('source_review.get',{media_id:id});if(alive.current)sync(value)}catch(e){if(alive.current)setError(String(e))}finally{if(alive.current)setSaving(false)}
  }
  const running=d?.job?.status==='running'
  const busy=saving || running
  const line=draftLine ?? cut?.dialogue[Math.min(lineIndex,Math.max(0,cut.dialogue.length-1))]
  const li=cut?Math.min(lineIndex,Math.max(0,cut.dialogue.length-1)):0
  const updateLine=(field:string,value:any)=>{
    if(!cut)return
    if(draftLine){const valueLine={...draftLine,[field]:value};if(valueLine.text.trim()){patchLines([...cut.dialogue,valueLine]);setLineIndex(cut.dialogue.length);setDraftLine(null)}else setDraftLine(valueLine);return}
    if(field==='text' && !String(value).trim())return
    patchLines(cut.dialogue.map((l,i)=>i===li?{...l,[field]:value}:l))
  }
  return <div ref={panel} className="lm-source-review sr-compact sr-live" data-source-review onKeyDown={e=>e.stopPropagation()}>
    <header><span title={media?.path}>{media?.name || '프로젝트에서 소스를 열어 주세요'}</span><button aria-label="미리보기 표시" aria-pressed={showVideo} onClick={()=>setShowVideo(!showVideo)}>영상</button><button aria-label="분석 및 관리" onClick={()=>setToolsOpen(!toolsOpen)}>분석·관리</button></header>
    {!eligible?<p>소스 모니터에 열린 영상·이미지를 검수합니다. 프로젝트에서 소스를 더블클릭하세요.</p>:<>
      <div className="sr-status" data-analysis-state={d?.analysis_status?.state}><span>{d?.cuts?.length || 0}컷</span><span role="status">{error?'저장 오류':saving?'자동 저장 중…':running?'분석 중':d?.published_revision===d?.revision && d?.revision!=null?'DB 자동 저장 완료':'변경 시 DB 자동 저장'}</span></div>
      {error && <div className="sr-error" role="alert">{error}<button onClick={()=>{failed.current=false;void flush()}}>저장 재시도</button></div>}
      {!cut?<div className="sr-empty"><button disabled={busy || !d} onClick={()=>void run('start',{stage:'ingest'})}>소스 ingest 시작</button><button disabled={busy || !d} onClick={()=>void run('start',{stage:'cuts'})}>컷만 검출</button></div>:<>
        <nav className="sr-cut-nav"><button aria-label="이전 컷" disabled={busy || index<=0} onClick={()=>choose(d!.cuts[index-1])}>◀</button><select aria-label="검수할 컷" disabled={busy} value={cut.id} onChange={e=>{const c=d!.cuts.find(c=>c.id===e.target.value);if(c)choose(c)}}>{d!.cuts.map((c,i)=><option key={c.id} value={c.id}>{i+1} / {d!.cuts.length} · {c.description || c.work || tc(c.start)}</option>)}</select><button aria-label="다음 컷" disabled={busy || !next} onClick={()=>next && choose(next)}>▶</button><button aria-label="컷 목록 검색" onClick={()=>setListOpen(true)}>목록</button></nav>
        <div className="sr-preview sr-boundary-preview" hidden={!showVideo || short}>
          <div className="sr-boundary">
            <div data-comparison="reference">{media.kind==='image'?<img src={mediaFileUrl(media)} alt="시작 경계"/>:<BoundaryFrame url={mediaFileUrl(media)} time={cut.start/S}/>}<small>시작 경계 · {tc(cut.start)} · 고정</small></div>
            <div data-comparison="candidate">
              {media.kind==='video' && <video ref={video} src={mediaFileUrl(media)} hidden={!playing} onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onEnded={()=>setPlaying(false)} onTimeUpdate={e=>{const t=Math.round(e.currentTarget.currentTime*S);setPlayTime(t);if(t>=playEnd.current)e.currentTarget.pause()}} onError={()=>setError('원본 영상을 재생할 수 없습니다')}/>}
              {!playing && (media.kind==='image'?<img src={mediaFileUrl(media)} alt="끝 경계"/>:<BoundaryFrame url={mediaFileUrl(media)} time={Math.max(cut.start,cut.end-frame)/S}/>)}
              <small>끝 경계 · {tc(cut.end)}{fixed?' · 고정':playing?' · 재생 중':' · 자동 저장'}</small>
            </div>
          </div>
        </div>
        <div className="sr-playbar" aria-label="끝 경계 조정">
          <div className="sr-playbar-track" aria-hidden="true"><span className="sr-playbar-cut" style={{left:`${cut.start/d!.duration*100}%`,width:`${(cut.end-cut.start)/d!.duration*100}%`}}/>{d!.cuts.slice(1).map(c=><i key={c.id} className="sr-playbar-mark" style={{left:`${c.start/d!.duration*100}%`}}/>)}<i className="sr-playbar-reference" style={{left:`${cut.start/d!.duration*100}%`}}/></div>
          <input type="range" aria-label="끝 경계 위치" min={0} max={d!.duration || 1} step={frame} value={playing?playTime:cut.end} disabled={fixed || running || failed.current} aria-valuetext={tc(playing?playTime:cut.end)} title={fixed?'마지막 끝 경계는 고정입니다':'드래그하면 끝 경계와 다음 컷 시작이 함께 자동 저장됩니다'} onChange={e=>changeEnd(Number(e.target.value))}/>
        </div>
        <div className="sr-transport"><button aria-label="이전 프레임" disabled={fixed || running || failed.current || cut.end-frame<=cut.start} onClick={()=>changeEnd(cut.end-frame)}>← 1프레임</button><button aria-label="다음 프레임" disabled={fixed || running || failed.current || cut.end+frame>=next!.end} onClick={()=>changeEnd(cut.end+frame)}>1프레임 →</button><button aria-label={playing?'일시 정지':'소스 재생'} disabled={media.kind!=='video'} onClick={play}>{playing?'정지':'선택 컷 재생'}</button><button disabled={busy || !d?.can_undo} onClick={()=>enqueue({action:'undo'})}>되돌리기</button></div>
        <div className="sr-live-hint">{fixed?'마지막 끝 경계 고정':`끝 경계 이동 → 컷 ${index+2} 시작도 변경 · 자동 저장`}</div>
        <div className="sr-detail sr-unified" aria-label="컷 정보">
          {choiceError && <small role="alert">{choiceError}</small>}
          <div className="sr-fields-row"><label>IP 검색<input aria-label="IP 검색" value={ipQuery} placeholder="IP DB 검색" onChange={e=>setIpQuery(e.target.value)}/></label><label>IP<EnumSelect label="IP / 작품" value={work} values={choices?.works.map(x=>x.value) || []} onChange={value=>{setIpQuery('');patch({work:value,characters:[],dialogue:cut.dialogue.map(l=>({...l,speaker:''}))})}}/></label></div>
          <div className="sr-fields-row"><label>등장인물<EnumSelect label="등장인물 추가" value="" values={(choices?.characters.map(x=>x.value)||[]).filter(v=>!cut.characters.includes(v))} empty="인물 추가…" onChange={v=>v && patch({characters:[...cut.characters,v]})}/></label><label>소스 종류<EnumSelect label="소스 종류" value={d!.identity.source_type} values={choices?.source_types || []} labels={sourceNames} onChange={v=>enqueue({action:'identity',fields:{source_type:v}})}/></label></div>
          {!!cut.characters.length && <div className="sr-characters">{cut.characters.map(c=><button key={c} aria-label={`${c} 제거`} onClick={()=>patch({characters:cut.characters.filter(v=>v!==c)})}>{c} ×</button>)}</div>}
          <div className="sr-fields-row sr-description"><label>장면<AutoText label="장면 설명" value={cut.description} multiline onChange={v=>patch({description:v})}/></label><label>샷<EnumSelect label="샷 크기" value={cut.shot} values={choices?.shots || []} onChange={v=>patch({shot:v})}/></label></div>
          <div className="sr-dialogue-compact">
            <div className="sr-line-nav"><b>대사</b><button aria-label="이전 대사" disabled={li<=0} onClick={()=>setLineIndex(li-1)}>◀</button><span>{draftLine?'새 대사':`${cut.dialogue.length?li+1:0}/${cut.dialogue.length}`}</span><button aria-label="다음 대사" disabled={li>=cut.dialogue.length-1} onClick={()=>setLineIndex(li+1)}>▶</button><button onClick={()=>{setDraftLine({start:cut.start,end:cut.end,speaker:'',text:''})}}>추가</button>{line && <button onClick={()=>{if(draftLine)setDraftLine(null);else {patchLines(cut.dialogue.filter((_,i)=>i!==li));setLineIndex(Math.max(0,li-1))}}}>삭제</button>}</div>
            {line?<><div className="sr-dialogue-row"><EnumSelect label="화자" value={line.speaker} empty="화자 미확인" values={choices?.characters.map(x=>x.value)||[]} fallbackValues={registeredSpeakers} onChange={v=>updateLine('speaker',v)}/><AutoText label="대사 내용" value={line.text} onChange={v=>updateLine('text',v)}/></div>{!choices?.characters.length && <small>{registeredSpeakers.length?'IP DB 매칭 없음 · 이 컷의 등록 인물에서 화자 선택':'화자 후보 없음 · IP를 검색해 선택하거나 인물 정보를 등록하세요'}</small>}<details><summary>대사 시간 · {tc(line.start)} — {tc(line.end)}</summary><div className="sr-fields-row"><label>시작 초<input aria-label="대사 시작 초" type="number" step="any" value={line.start/S} onChange={e=>updateLine('start',Math.round(Number(e.target.value)*S))}/></label><label>끝 초<input aria-label="대사 끝 초" type="number" step="any" value={line.end/S} onChange={e=>updateLine('end',Math.round(Number(e.target.value)*S))}/></label></div></details></>:<small>대사 없음 · 분석·관리에서 전사 실행</small>}
          </div>
          <div className="sr-tag-row">{Object.entries(cut.tags).map(([k,v])=><label key={k}>{tagNames[k] || k}<AutoText label={tagNames[k] || k} value={v.join(', ')} onChange={text=>patch({tags:{[k]:list(text)}})}/></label>)}</div>
          <details className="sr-source-extra"><summary>소스 공통 정보 · 메모</summary>{['origin_url','episode','note'].map(k=><label key={k}>{{origin_url:'원본 URL',episode:'에피소드',note:'메모'}[k]}<AutoText label={k} value={d!.identity[k]} onChange={v=>enqueue({action:'identity',fields:{[k]:v}})}/></label>)}</details>
        </div>
      </>}
      {(toolsOpen || listOpen) && <section className="sr-overlay" role="dialog" aria-label={listOpen?'컷 목록 검색':'분석 및 관리'}><header><strong>{listOpen?'컷 목록':'분석 및 관리'}</strong><button aria-label="닫기" onClick={()=>{setToolsOpen(false);setListOpen(false)}}>×</button></header><div className="sr-overlay-body">{listOpen?<><input aria-label="컷 검색" value={query} onChange={e=>setQuery(e.target.value)}/><div className="sr-cuts">{d?.cuts.filter(c=>JSON.stringify(c).toLowerCase().includes(query.toLowerCase())).map(c=><button key={c.id} disabled={busy} onClick={()=>choose(c)}>{d.cuts.indexOf(c)+1}. {c.description || c.work || tc(c.start)}</button>)}</div></>:<>
        {Object.entries(d?.analysis_status?.stages || {}).map(([k,v])=><div className="sr-status" key={k}><span>{stages[k] || k}</span><span>{v.status}</span></div>)}
        <button disabled={busy || !d?.analysis_status?.missing.length} onClick={()=>void run('start',{stage:d?.cuts?.length?'missing':'ingest'})}>{d?.cuts?.length?'빠진 분석만 실행':'소스 ingest 시작'}</button>
        {d?.job && <p>{d.job.message} {d.job.error}</p>}
        {cut && <><p>컷 구성</p><button disabled={busy || fixed} onClick={()=>enqueue({action:'merge',cut_id:cut.id})}>다음 컷과 병합</button><button disabled={busy || cut.end-cut.start<2*frame} onClick={()=>enqueue({action:'split',cut_id:cut.id,time:Math.round((cut.start+cut.end)/2/frame)*frame})}>중간에서 분할</button></>}
      </>}</div></section>}
    </>}
  </div>
}
function EnumSelect({label,value,values,onChange,empty='미지정',labels={},fallbackValues=[]}:{label:string;value:string;values:string[];onChange:(v:string)=>void;empty?:string;labels?:Record<string,string>;fallbackValues?:string[]}){
  const options=[...new Set(values)]
  const registered=[...new Set(fallbackValues)].filter(v=>v && !options.includes(v))
  return <select aria-label={label} value={value || ''} onChange={e=>onChange(e.target.value)}><option value="">{empty}</option>{value && !options.includes(value) && !registered.includes(value) && <option value={value} disabled>{value} (기존 값)</option>}{options.map(v=><option key={v} value={v}>{labels[v] || v}</option>)}{!!registered.length && <optgroup label="이 컷의 등록 인물">{registered.map(v=><option key={v} value={v}>{v}</option>)}</optgroup>}</select>
}
function AutoText({label,value,onChange,multiline=false}:{label:string;value:string;onChange:(v:string)=>void;multiline?:boolean}){
  const [local,setLocal]=useState(value)
  const composing=useRef(false)
  const focused=useRef(false)
  useEffect(()=>{if(!composing.current && !focused.current)setLocal(value)},[value])
  const props={ 'aria-label':label,value:local,onFocus:()=>{focused.current=true},onBlur:()=>{focused.current=false;if(!composing.current)setLocal(value)},onCompositionStart:()=>{composing.current=true},onCompositionEnd:(e:React.CompositionEvent<HTMLInputElement|HTMLTextAreaElement>)=>{composing.current=false;onChange(e.currentTarget.value)},onChange:(e:React.ChangeEvent<HTMLInputElement|HTMLTextAreaElement>)=>{setLocal(e.target.value);if(!composing.current)onChange(e.target.value)}}
  return multiline?<textarea {...props} rows={2}/>:<input {...props}/>
}
// Do not show a cached neighbour as if it were the requested boundary frame.
function BoundaryFrame({url,time}:{url:string;time:number}) {
  const root=useRef<HTMLDivElement>(null)
  const [ready,setReady]=useState(false)
  useEffect(()=>{
    setReady(false)
    const el=root.current
    if(!el)return
    const check=()=>{const c=el.querySelector('canvas');setReady(!!c && c.dataset.exact==='1' && Math.abs(Number(c.dataset.want)-time)<0.00001)}
    const observer=new MutationObserver(check)
    observer.observe(el,{subtree:true,attributes:true,childList:true})
    check()
    return ()=>observer.disconnect()
  },[url,time])
  return <div ref={root} className="sr-frame" data-ready={ready}>
    <FrameCanvas url={url} time={time} playing={false} rate={1} style={{width:'100%',height:'100%',objectFit:'contain'}}/>
    {!ready && <span className="sr-frame-pending">프레임 확인 중…<br/>계속 표시되면 원본·코덱을 확인하세요</span>}
  </div>
}
