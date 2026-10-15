// Text panel: every text layer of the sequence's graphics in time order, editable in place (Premiere
// makes you double-click each one in the Program monitor). Find / Replace All works across them,
// and Match Style gives the selected clips the look of the first selected one in one undo step.
import { useMemo, useState } from 'react'
import { Replace, Search, Wand2 } from 'lucide-react'
import { t } from '@shared/i18n'
import type { Clip } from '../engine/document'
import { activeSequence, exec, get, set, toast, useStore } from '../state/store'
import { goTo } from '../state/transport'
import { timecode } from '../util/time'

type Row = { clip: Clip; layerId: string; text: string; track: string }

export function TextPanel() {
  const seq = useStore((s) => activeSequence(s))
  const selected = useStore((s) => s.selectedClips)
  const [q, setQ] = useState('')
  const [rep, setRep] = useState('')
  const [withPos, setWithPos] = useState(false)
  const rows = useMemo<Row[]>(() => {
    if (!seq) return []
    const out: Row[] = []
    for (const tid of seq.video_tracks) {
      for (const clip of Object.values(seq.tracks[tid].clips)) {
        for (const l of clip.graphic?.layers ?? []) {
          if ((l.type ?? 'text') === 'text' && 'text' in l) out.push({ clip, layerId: l.id, text: l.text, track: tid })
        }
      }
    }
    return out.sort((a, b) => a.clip.start - b.clip.start)
  }, [seq])
  if (!seq) return <div className="lm-panel"><div className="lm-empty">{t('(no sequences)')}</div></div>
  const ql = q.trim().toLowerCase()
  const shown = ql ? rows.filter((r) => r.text.toLowerCase().includes(ql)) : rows
  const graphicsSelected = selected.filter((id) => rows.some((r) => r.clip.id === id))
  const commit = (r: Row, text: string) => {
    if (text !== r.text) void exec('graphic.set_layer', { sequence_id: seq.id, clip_id: r.clip.id, layer_id: r.layerId, text }, t('Edit Text'))
  }
  const replaceAll = async () => {
    if (!q) return
    const res = await exec<{ replaced: number }>('graphic.replace_text', { sequence_id: seq.id, find: q, replace: rep }, t('Replace All'))
    if (res) toast(t('{n} replaced').replace('{n}', String(res.replaced)), 'info')
  }
  const matchStyle = () => {
    const [src, ...rest] = get().selectedClips.filter((id) => rows.some((r) => r.clip.id === id))
    if (!src || !rest.length) return toast(t('Select two or more text clips: the first one gives its style'), 'info')
    void exec('graphic.match_style', { sequence_id: seq.id, source_clip_id: src, clip_ids: rest, include_position: withPos }, t('Match Style'))
  }
  return (
    <div className="lm-panel lm-textpanel" data-text-panel="">
      <div className="bar">
        <Search size={12} />
        <input placeholder={t('Search')} value={q} onChange={(e) => setQ(e.target.value)} data-find="" />
        <Replace size={12} />
        <input placeholder={t('Replace')} value={rep} onChange={(e) => setRep(e.target.value)} data-replace="" />
        <button className="lm-btn" disabled={!q} onClick={() => void replaceAll()} data-action="replace-all">
          {t('Replace All')}
        </button>
      </div>
      <div className="bar">
        <span className="dim">
          {shown.length} / {rows.length} {t('text layers')}
        </span>
        <label className="dim">
          <input type="checkbox" checked={withPos} onChange={(e) => setWithPos(e.target.checked)} /> {t('with position')}
        </label>
        <button className="lm-btn" disabled={graphicsSelected.length < 2} onClick={matchStyle} data-action="match-style" title={t('Select two or more text clips: the first one gives its style')}>
          <Wand2 size={12} /> {t('Match Style')}
        </button>
      </div>
      <div className="rows">
        {shown.map((r) => (
          <div
            key={`${r.clip.id}:${r.layerId}`}
            className={`row ${selected.includes(r.clip.id) ? 'selected' : ''}`}
            data-text-row={r.clip.id}
            onMouseDown={(e) => {
              if ((e.target as HTMLElement).tagName === 'TEXTAREA') return
              const ids = get().selectedClips
              set({ selectedClips: e.ctrlKey || e.shiftKey ? (ids.includes(r.clip.id) ? ids.filter((x) => x !== r.clip.id) : [...ids, r.clip.id]) : [r.clip.id] })
              goTo('program', r.clip.start)
            }}
          >
            <span className="tc">{timecode(r.clip.start, seq.settings)}</span>
            <textarea
              key={r.text}
              defaultValue={r.text}
              rows={Math.min(4, r.text.split('\n').length)}
              onFocus={() => {
                set({ selectedClips: [r.clip.id] })
                goTo('program', r.clip.start)
              }}
              onBlur={(e) => commit(r, e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  ;(e.target as HTMLTextAreaElement).blur()
                }
                if (e.key === 'Escape') {
                  ;(e.target as HTMLTextAreaElement).value = r.text
                  ;(e.target as HTMLTextAreaElement).blur()
                }
              }}
            />
          </div>
        ))}
        {!rows.length && <div className="lm-empty">{t('No text in this sequence. Add one with the Type tool (T) or Ctrl+T.')}</div>}
      </div>
    </div>
  )
}
