import { useEffect, useMemo, useRef, useState } from 'react'
import { Search, Scissors, Merge, Lock, Replace, AudioLines } from 'lucide-react'
import { engine } from '../engine/client'
import type { Sequence } from '../engine/document'
import { activeSequence, set, toast, useStore } from '../state/store'
import { goTo } from '../state/transport'
import { secondsToTicks, ticksToSeconds, timecode } from '../util/time'
import { sequenceTextRows, textCursorOffset, type TextRow } from '../util/textRows'

type Tab = 'script' | 'captions' | 'graphics'
type SpeechRow = { id: string; text: string; start: number; end: number; previous_start?: number }
type SpeechJob = { id: string; sequence_id: string; mode: string; status: string; phase: string; progress: number; error?: string; result?: { rows: SpeechRow[]; quality_warnings?: { id: string; reason: string }[] } }

function focusCaption(id: string, cursor: number) {
  requestAnimationFrame(() => {
    const field = document.querySelector<HTMLTextAreaElement>(`[data-text-row="${id}"] textarea`)
    field?.focus({ preventScroll: true }); field?.setSelectionRange(cursor, cursor)
  })
}
const spokenText = (text: string) => text.replace(/[\s\p{P}]/gu, '')

function EditableRow({ row, seq, selected }: { row: TextRow; seq: Sequence; selected: boolean }) {
  const [draft, setDraft] = useState(row.text)
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const cancelling = useRef(false)
  const latest = useRef(row.text)
  const saved = useRef(row.text)
  const queue = useRef(Promise.resolve(true))
  const composing = useRef(false)
  const playhead = useStore(s => s.programTime)
  const [manualAt, setManualAt] = useState<string | null>(null)
  useEffect(() => {
    if (latest.current === saved.current) { latest.current = row.text; setDraft(row.text) }
    saved.current = row.text
  }, [row.text])
  const save = async () => {
    const value = latest.current
    queue.current = queue.current.then(async () => {
      if (value === saved.current || cancelling.current) return true
      try {
        await engine.exec(row.kind === 'captions' ? 'caption.set' : 'graphic.set_layer', {
          sequence_id: seq.id, clip_id: row.clip.id, ...(row.layerId ? { layer_id: row.layerId } : {}), text: value,
        }, '텍스트 수정')
        saved.current = value
        return true
      } catch (e) { toast(String(e)); return false }
    })
    return queue.current
  }
  useEffect(() => {
    if (draft === saved.current || busy) return
    const timer = setTimeout(() => { if (!composing.current) void save() }, 250)
    return () => clearTimeout(timer)
  }, [draft, busy])
  const merge = async (direction: 'next' | 'previous') => {
    if (busy) return
    setBusy(true)
    try {
      if (!await save()) return
      const result = await engine.exec<{ clip_id: string; cursor_text: string }>('caption.merge_next', { sequence_id: seq.id, clip_id: row.clip.id, direction }, '자막 병합')
      set({ selectedClips: [result.clip_id] }); focusCaption(result.clip_id, result.cursor_text.length)
    } catch (e) { toast(String(e)) } finally { setBusy(false) }
  }
  const split = async () => {
    const field = input.current
    if (!field || busy) return
    const offset = textCursorOffset(draft, field.selectionStart)
    if (!draft.slice(0, field.selectionStart).trim() || !draft.slice(field.selectionStart).trim()) return toast('문구 중간에 커서를 놓아주세요.', 'info')
    const timing = row.clip.caption?.timing
    const acoustic = timing && spokenText(String(timing.text)) === spokenText(draft) && timing.start === row.clip.start && timing.end === row.clip.end
    const at = manualAt !== null ? secondsToTicks(Number(manualAt)) : acoustic ? undefined : playhead
    if (at !== undefined && (!Number.isFinite(at) || at <= row.clip.start || at >= row.clip.end)) return toast('자막 안에 재생헤드를 놓거나 분할 시간을 입력하세요. 글자 수로 시간을 추정하지 않습니다.', 'info')
    setBusy(true)
    try {
      if (!await save()) return
      const result = await engine.exec<{ clip_ids: string[] }>('caption.split_text', { sequence_id: seq.id, clip_id: row.clip.id, offset, expected_text: draft, ...(at !== undefined ? { at } : {}) }, '커서에서 자막 분할')
      set({ selectedClips: [result.clip_ids[1]] })
      focusCaption(result.clip_ids[1], 0)
    } catch (e) { toast(String(e)) } finally { setBusy(false) }
  }
  return <div className={`text-entry ${selected ? 'selected' : ''}`} data-text-row={row.clip.id} data-text-kind={row.kind}>
    <div className="text-entry-meta">
      <span className="text-track" title={row.trackName}>{row.trackLabel} · {row.trackName} {row.locked && <Lock size={10} />}</span>
      <button className="text-time" onClick={() => { set({ selectedClips: [row.clip.id], selectedTrack: null }); goTo('program', row.clip.start) }}>{timecode(row.clip.start, seq.settings)}</button>
      <button className="text-time end" onClick={() => goTo('program', row.clip.end)}>{timecode(row.clip.end, seq.settings)}</button>
    </div>
    <div className="text-entry-body">
      <textarea ref={input} value={draft} aria-label={row.kind === 'captions' ? '자막 문구' : '그래픽 문구'} disabled={row.locked || busy}
        rows={Math.min(6, Math.max(2, draft.split('\n').length))} onChange={e => { latest.current = e.target.value; setDraft(e.target.value) }}
        onCompositionStart={() => { composing.current = true }} onCompositionEnd={() => { composing.current = false; void save() }}
        onFocus={() => { cancelling.current = false; set({ selectedClips: [row.clip.id], selectedLayer: row.layerId ?? null, selectedTrack: null }) }}
        onBlur={() => { if (!busy) void save() }}
        onKeyDown={e => {
          e.stopPropagation()
          if (e.nativeEvent.isComposing) return
          if (e.key === 'Enter' && !e.shiftKey && row.kind === 'captions') { e.preventDefault(); void split() }
          else if (e.key === 'Backspace' && row.kind === 'captions' && e.currentTarget.selectionStart === 0 && e.currentTarget.selectionEnd === 0) { e.preventDefault(); void merge('previous') }
          else if (e.key === 'Escape') { cancelling.current = true; latest.current = row.text; setDraft(row.text); input.current?.blur() }
        }} />
      {selected && row.kind === 'captions' && <div className="text-row-tools">
        <button className="lm-btn" disabled={row.locked || busy} onMouseDown={e => e.preventDefault()} onClick={() => void split()} title="Enter 분할 · Shift+Enter 줄바꿈. 음성 정렬 정보가 없으면 재생헤드/지정 시간을 사용합니다."><Scissors size={12} /> 커서에서 분할</button>
        <label>분할 초 <input aria-label="분할 시간(초)" type="number" step={seq.settings.fps_den / seq.settings.fps_num}
          value={manualAt ?? ticksToSeconds(playhead).toFixed(3)} onChange={e => setManualAt(e.target.value)} /></label>
        <button className="lm-btn" disabled={row.locked || busy} onMouseDown={e => e.preventDefault()} onClick={() => void merge('next')} title="같은 스타일로 이어지는 다음 자막과 합치기"><Merge size={12} /> 병합</button>
      </div>}
    </div>
  </div>
}

export function TextPanel() {
  const seq = useStore(s => activeSequence(s))
  const projectKey = useStore(s => s.projects.find(p => p.active)?.key)
  const selected = useStore(s => s.selectedClips)
  const playing = useStore(s => s.playing)
  const playhead = useStore(s => s.programTime)
  const rowList = useRef<HTMLDivElement>(null)
  const [tab, setTab] = useState<Tab>('captions')
  const [q, setQ] = useState('')
  const [track, setTrack] = useState('all')
  const [replacement, setReplacement] = useState('')
  const [showReplace, setShowReplace] = useState(false)
  const [script, setScript] = useState('')
  const scriptDraft = useRef('')
  const scriptSaved = useRef('')
  const scriptQueue = useRef(Promise.resolve())
  const scriptComposing = useRef(false)
  const [audioTrack, setAudioTrack] = useState('')
  const [shorts, setShorts] = useState(false)
  const [job, setJob] = useState<SpeechJob | null>(null)
  const [working, setWorking] = useState(false)
  const [available, setAvailable] = useState<boolean | null>(null)
  const rows = useMemo(() => seq ? sequenceTextRows(seq) : [], [seq])
  const currentRow = rows.find(r => r.kind === tab && (track === 'all' || r.track === track) && r.text.toLowerCase().includes(q.toLowerCase()) && r.clip.start <= playhead && playhead < r.clip.end)?.clip.id
  useEffect(() => {
    if (playing !== 'program' || !currentRow || !rowList.current) return
    const list = rowList.current
    const row = list.querySelector<HTMLElement>(`[data-text-row="${currentRow}"]`)
    if (row) list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top
  }, [playing, currentRow, tab, track])
  useEffect(() => {
    setTrack('all'); setJob(null); setAudioTrack(seq?.audio_tracks[0] ?? '')
    setShorts(!!seq && seq.settings.height > seq.settings.width)
  }, [seq?.id, projectKey])
  useEffect(() => {
    const text = String(seq?.transcript?.text ?? '')
    scriptDraft.current = text; scriptSaved.current = text; setScript(text)
  }, [seq?.id, projectKey])
  useEffect(() => {
    const text = String(seq?.transcript?.text ?? '')
    if (scriptDraft.current === scriptSaved.current) { scriptDraft.current = text; setScript(text) }
    scriptSaved.current = text
  }, [seq?.transcript?.text])
  useEffect(() => {
    if (!seq || script === String(seq.transcript?.text ?? '')) return
    const timer = setTimeout(() => { if (!scriptComposing.current) void saveScript().catch(e => toast(String(e))) }, 350)
    return () => clearTimeout(timer)
  }, [script, seq?.id, seq?.transcript?.text])
  useEffect(() => {
    let live = true
    engine.call<{ available: boolean }>('speech.status').then(r => { if (live) setAvailable(r.available) }).catch(() => { if (live) setAvailable(false) })
    engine.call<SpeechJob[]>('speech.list').then(r => { if (live) setJob(r.filter(j => j.sequence_id === seq?.id && ['queued', 'running', 'preview', 'failed'].includes(j.status)).at(-1) ?? null) }).catch(() => {})
    return () => { live = false }
  }, [seq?.id, projectKey])
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return
    let live = true
    const timer = setInterval(() => { void engine.call<SpeechJob>('speech.job', { job_id: job.id }).then(r => { if (live) setJob(r) }).catch(e => { if (live) setJob(j => j ? { ...j, status: 'failed', error: String(e) } : null) }) }, 1500)
    return () => { live = false; clearInterval(timer) }
  }, [job?.id, job?.status])
  if (!seq) return <div className="lm-panel"><div className="lm-empty">시퀀스를 열어주세요.</div></div>
  const relevant = rows.filter(r => r.kind === tab)
  const shown = relevant.filter(r => (track === 'all' || r.track === track) && r.text.toLowerCase().includes(q.toLowerCase()))
  const running = working || !!job && ['queued', 'running'].includes(job.status)
  const saveScript = async () => {
    const value = scriptDraft.current
    scriptQueue.current = scriptQueue.current.catch(() => {}).then(async () => {
      if (value !== scriptSaved.current) {
        await engine.exec('text.set_script', { sequence_id: seq.id, text: value }, '대본 수정')
        scriptSaved.current = value
      }
    })
    return scriptQueue.current
  }
  const startSpeech = async (mode: string) => {
    setWorking(true)
    try {
      if (tab === 'script') await saveScript()
      setJob(await engine.call<SpeechJob>('speech.start', { sequence_id: seq.id, audio_track_id: audioTrack, mode, shorts,
        ...(mode === 'retime' && track !== 'all' ? { caption_track_ids: [track] } : {}) }))
    } catch (e) { toast(String(e)) } finally { setWorking(false) }
  }
  const apply = async (transcriptOnly: boolean) => {
    if (!job) return
    setWorking(true)
    try { await engine.call('speech.apply', { job_id: job.id, transcript_only: transcriptOnly }); setJob({ ...job, status: 'applied' }) }
    catch (e) { toast(String(e)) } finally { setWorking(false) }
  }
  const replaceVisible = async () => {
    if (!q || !shown.length) return
    setWorking(true)
    let begun = false
    try {
      await engine.call('transaction.begin', { label: '표시된 텍스트 바꾸기' }); begun = true
      for (const row of shown.filter(r => !r.locked)) await engine.exec(row.kind === 'captions' ? 'caption.set' : 'graphic.set_layer', {
        sequence_id: seq.id, clip_id: row.clip.id, ...(row.layerId ? { layer_id: row.layerId } : {}), text: row.text.split(q).join(replacement),
      })
      await engine.call('transaction.commit'); begun = false
    } catch (e) { if (begun) await engine.call('transaction.rollback').catch(() => {}); toast(String(e)) } finally { setWorking(false) }
  }
  return <div className="lm-panel lm-textpanel" data-text-panel="">
    <div className="text-tabs" role="tablist" aria-label="텍스트 종류">
      {([['script', '대본'], ['captions', '캡션'], ['graphics', '그래픽']] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => { setTab(id); setTrack('all') }}>{label}{id !== 'script' && <span>{rows.filter(r => r.kind === id).length}</span>}</button>)}
      <span className="text-sequence" title={seq.name}>{seq.name}</span>
    </div>
    <div className="text-toolbar">
      <label className="text-search"><Search size={13} /><input placeholder="검색" value={q} onChange={e => setQ(e.target.value)} data-find="" /></label>
      {tab !== 'script' && <><select aria-label="텍스트 트랙" value={track} onChange={e => setTrack(e.target.value)}><option value="all">모든 트랙</option>{[...new Set(relevant.map(r => r.track))].map(tid => { const r = relevant.find(r => r.track === tid)!; return <option key={tid} value={tid}>{r.trackLabel} · {r.trackName}</option> })}</select>
      <button className="lm-btn" title="찾아 바꾸기" onClick={() => setShowReplace(v => !v)}><Replace size={13} /></button></>}
    </div>
    {showReplace && tab !== 'script' && <div className="text-toolbar"><input placeholder="바꿀 문구" value={replacement} onChange={e => setReplacement(e.target.value)} data-replace="" /><button className="lm-btn" disabled={!q || running} onClick={() => void replaceVisible()} data-action="replace-all">표시된 트랙에서 바꾸기</button></div>}
    {tab !== 'graphics' && <div className="speech-toolbar">
      <AudioLines size={14} /><select aria-label="내레이션 트랙" value={audioTrack} onChange={e => setAudioTrack(e.target.value)}><option value="">내레이션 트랙 선택</option>{seq.audio_tracks.map((tid, i) => <option key={tid} value={tid}>A{i + 1} · {seq.tracks[tid].name}</option>)}</select>
      <select aria-label="자막 분할 모드" value={shorts ? 'shorts' : 'longform'} onChange={e => setShorts(e.target.value === 'shorts')}><option value="longform">롱폼</option><option value="shorts">쇼츠</option></select>
      <button className="lm-btn" disabled={!audioTrack || running || available === false || job?.status === 'preview'} onClick={() => void startSpeech(tab === 'captions' ? 'retime' : script.trim() ? 'script' : 'transcribe')}>{tab === 'captions' ? '음성에 재정렬' : script.trim() ? '대본을 음성에 맞추기' : '받아쓰기'}</button>
    </div>}
    {available === false && tab !== 'graphics' && <div className="text-notice">음성 런타임이 필요합니다. 설치 방법: docs/text-panel.md</div>}
    {job && <div className="speech-job" data-speech-status={job.status}>
      <div className="speech-job-heading"><strong>{job.status === 'failed' ? '음성 작업 실패' : job.status === 'applied' ? '적용 완료' : job.phase}</strong><span>{job.status === 'running' ? `${job.progress}%` : ''}</span>
        {['queued', 'running', 'preview'].includes(job.status) && <button className="lm-btn" onClick={() => { void engine.call<SpeechJob>('speech.cancel', { job_id: job.id }).then(setJob).catch(e => toast(String(e))) }}>취소</button>}</div>
      {job.error && <p>{job.error}</p>}
      {job.status === 'preview' && <><p>{job.result?.rows.length}개 · 문구와 시간을 확인한 뒤 적용하세요. 아직 타임라인은 변경되지 않았습니다.</p>
        <p>첫 자막 0프레임 · 끝 쉼표/마침표 정리 · 긴 자막 음성 경계 분할</p>
        {!!job.result?.quality_warnings?.length && <p role="alert">긴 자막 {job.result.quality_warnings.length}개는 안전한 분할 경계가 부족하거나 무음을 포함합니다. 문구와 음성을 확인하세요.</p>}
        <div className="speech-preview">{job.result?.rows.map((r, i) => <div key={r.id}><button onClick={() => goTo('program', r.start)}>{i + 1} · {timecode(r.start, seq.settings)} – {timecode(r.end, seq.settings)}</button>{r.previous_start !== undefined && <small>이전 {timecode(r.previous_start, seq.settings)} · 이동 {((r.start - r.previous_start) / 254016000000).toFixed(2)}초</small>}<span>{r.text}</span></div>)}</div>
        <div className="text-toolbar">{job.mode !== 'retime' && <button className="lm-btn" disabled={working} onClick={() => void apply(true)}>대본만 적용</button>}<button className="lm-btn primary" disabled={working} onClick={() => void apply(false)}>{job.mode === 'retime' ? '기존 자막 시간에 적용' : '대본 + 새 캡션 트랙 만들기'}</button></div></>}
    </div>}
    {tab === 'script' ? <div className="script-content">
      <div className="text-notice">대본은 캡션·그래픽과 별도로 저장됩니다. 한 줄을 한 자막으로 정렬하며 자막 사이 빈 구간을 없앱니다.</div>
      <div className="text-toolbar"><button className="lm-btn" disabled={!!script.trim() || !rows.some(r => r.kind === 'captions')} onClick={() => {
        const text = rows.filter(r => r.kind === 'captions').map(r => r.text).join('\n')
        scriptDraft.current = text; setScript(text); void saveScript().catch(e => toast(String(e)))
      }}>모든 캡션에서 대본 가져오기</button><span className="text-notice">기존 대본이 비어 있을 때 사용</span></div>
      <textarea aria-label="대본" placeholder="대본을 붙여 넣거나, 비워 둔 채 받아쓰기를 시작하세요." value={script} onChange={e => { scriptDraft.current = e.target.value; setScript(e.target.value) }} onCompositionStart={() => { scriptComposing.current = true }} onCompositionEnd={() => { scriptComposing.current = false; void saveScript().catch(e => toast(String(e))) }} onBlur={() => { void saveScript().catch(e => toast(String(e))) }} />
      {q && <div className="script-matches">{script.split('\n').filter(line => line.toLowerCase().includes(q.toLowerCase())).map((line, i) => <p key={i}>{line}</p>)}</div>}
    </div> : <div className="rows" ref={rowList} data-playing-caption={playing === 'program' ? currentRow : undefined}>{shown.map(row => <EditableRow key={`${seq.id}:${row.clip.id}:${row.layerId ?? 'caption'}`} row={row} seq={seq} selected={selected.includes(row.clip.id) || playing === 'program' && row.clip.id === currentRow} />)}
      {!shown.length && <div className="lm-empty">{q ? '검색 결과가 없습니다.' : tab === 'captions' ? '캡션이 없습니다. 대본 탭에서 받아쓰거나 SRT를 가져오세요.' : '텍스트 그래픽이 없습니다.'}</div>}</div>}
    <footer className="text-footer">{tab === 'script' ? '대본 · 영상에 직접 표시되지 않음' : `${shown.length} / ${relevant.length} · ${track === 'all' ? '모든 트랙' : seq.tracks[track]?.name}`}{tab === 'captions' && <span>Enter 분할 · Shift+Enter 줄바꿈 · 맨 앞 ⌫ 병합</span>}</footer>
  </div>
}
