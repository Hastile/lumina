// Panels from Premiere 2025+'s Editing workspace right side and top-left tabs.
// Properties, Metadata and Essential Sound are functional; Lumetri Color, Text
// and Graphic Templates hold their place until their milestones.
import { useState } from 'react'
import { ChevronDown, AudioLines, Circle, MessageSquare, Music, Sparkles, Square, Wind, RotateCcw, Plus, Maximize, Minimize } from 'lucide-react'
import { t } from '@shared/i18n'
import type { MediaItem } from '../engine/document'
import { activeSequence, exec, findClip, get, toast, useStore } from '../state/store'
import { engine } from '../engine/client'
import { effectParam } from '../util/effects'
import type { LumetriParams } from '../util/color'
import { ticksToSeconds } from '../util/time'
import { current, KeyframeRow, NumberScrub, setProp, useSelectedClip } from './PropertyControls'
import { GraphicLayers } from './GraphicLayers'

function Section({ title, children, reset }: { title: string; children: React.ReactNode; reset?: () => void }) {
  return (
    <div className="lm-prop-section">
      <div className="head">
        <ChevronDown size={13} />
        <span>{title}</span>
        <span style={{ flex: 1 }} />
        {reset && (
          <button className="lm-iconbtn" title={t('Reset')} onClick={reset}>
            <RotateCcw size={12} />
          </button>
        )}
      </div>
      {children}
    </div>
  )
}

export function PropertiesPanel() {
  const c = useSelectedClip()
  if (!c) return <div className="lm-panel"><div className="lm-empty">{t('Select a clip to see its properties.')}</div></div>
  const { clip, seq } = c
  const pos = current(c, 'position') as number[]
  const anchor = current(c, 'anchor') as number[]
  const fit = (mode: 'fit' | 'fill') => void exec('clip.fit_to_frame', { sequence_id: seq.id, clip_ids: [clip.id], mode }, t(mode === 'fit' ? 'Fit' : 'Fill'))
  return (
    <div className="lm-panel lm-props">
      <div className="lm-props-title">{clip.name}</div>
      <div className="lm-props-scroll lm-ec">
        {clip.graphic && (
          <Section title={t('Layers')}>
            <GraphicLayers c={c} />
            <div className="lm-glayer-add">
              <button className="lm-btn small" onClick={() => void exec('graphic.add_layer', { sequence_id: seq.id, clip_id: clip.id, text: t('New Text Layer') }, t('Add Text Layer'))}>
                <Plus size={12} /> {t('Text')}
              </button>
              <button className="lm-btn small" data-add-shape="rectangle" onClick={() => void exec('graphic.add_shape', { sequence_id: seq.id, clip_id: clip.id, shape: 'rectangle' }, t('New Layer > Rectangle'))}>
                <Square size={12} /> {t('Rectangle')}
              </button>
              <button className="lm-btn small" onClick={() => void exec('graphic.add_shape', { sequence_id: seq.id, clip_id: clip.id, shape: 'ellipse' }, t('New Layer > Ellipse'))}>
                <Circle size={12} /> {t('Ellipse')}
              </button>
            </div>
          </Section>
        )}
        {c.kind === 'video' ? (
          <>
            <Section title={t('Transform')} reset={() => void exec('clip.reset_motion', { sequence_id: seq.id, clip_ids: [clip.id] }, t('Reset'))}>
              {c.media?.video && (
                <div className="lm-fitrow">
                  <button className="lm-chip" onClick={() => fit('fill')}>
                    <Maximize size={12} /> {t('Fill')}
                  </button>
                  <button className="lm-chip" onClick={() => fit('fit')}>
                    <Minimize size={12} /> {t('Fit')}
                  </button>
                </div>
              )}
              <KeyframeRow c={c} prop="position">
                <NumberScrub value={pos[0]} digits={0} suffix=" X" onChange={(v) => setProp(c, 'position', [v, pos[1]])} />
                <NumberScrub value={pos[1]} digits={0} suffix=" Y" onChange={(v) => setProp(c, 'position', [pos[0], v])} />
              </KeyframeRow>
              <KeyframeRow c={c} prop="anchor">
                <NumberScrub value={anchor[0]} digits={0} suffix=" X" onChange={(v) => setProp(c, 'anchor', [v, anchor[1]])} />
                <NumberScrub value={anchor[1]} digits={0} suffix=" Y" onChange={(v) => setProp(c, 'anchor', [anchor[0], v])} />
              </KeyframeRow>
              <KeyframeRow c={c} prop="scale">
                <NumberScrub value={current(c, 'scale') as number} min={0} max={10000} digits={0} suffix=" %" onChange={(v) => setProp(c, 'scale', v)} />
              </KeyframeRow>
              <KeyframeRow c={c} prop="rotation">
                <NumberScrub value={current(c, 'rotation') as number} digits={0} suffix=" °" onChange={(v) => setProp(c, 'rotation', v)} />
              </KeyframeRow>
              <KeyframeRow c={c} prop="opacity">
                <NumberScrub value={current(c, 'opacity') as number} min={0} max={100} digits={0} suffix=" %" onChange={(v) => setProp(c, 'opacity', v)} />
              </KeyframeRow>
            </Section>
            <Section title={t('Crop')}>
              {(['crop_left', 'crop_top', 'crop_right', 'crop_bottom'] as const).map((p) => (
                <KeyframeRow key={p} c={c} prop={p} label={{ crop_left: 'Left', crop_top: 'Top', crop_right: 'Right', crop_bottom: 'Bottom' }[p]}>
                  <NumberScrub value={current(c, p) as number} min={0} max={100} suffix=" %" onChange={(v) => setProp(c, p, v)} />
                </KeyframeRow>
              ))}
            </Section>
          </>
        ) : (
          <Section title={t('Volume')} reset={() => setProp(c, 'volume_db', 0)}>
            <KeyframeRow c={c} prop="volume_db">
              <NumberScrub value={current(c, 'volume_db') as number} min={-96} max={15} step={0.1} suffix=" dB" onChange={(v) => setProp(c, 'volume_db', v)} />
            </KeyframeRow>
          </Section>
        )}
      </div>
    </div>
  )
}

export function MetadataPanel() {
  const doc = useStore((s) => s.doc)
  const items = useStore((s) => s.selectedItems)
  const clips = useStore((s) => s.selectedClips)
  let media: MediaItem | null = null
  if (doc && items.length) media = doc.media[items[0]] ?? null
  if (!media && doc && clips.length) {
    const seq = activeSequence(get())
    const f = seq ? findClip(seq, clips[0]) : null
    media = f?.clip.media_id ? doc.media[f.clip.media_id] ?? null : null
  }
  if (!media) return <div className="lm-panel"><div className="lm-empty">{t('Select a clip or project item.')}</div></div>
  const rows: [string, string][] = [
    ['Name', media.name],
    ['File Path', media.path],
    ['Media Type', media.kind],
    ['Media Duration', media.kind === 'image' ? '-' : `${ticksToSeconds(media.duration).toFixed(3)} s`],
    ['Video', media.video ? `${media.video.width} x ${media.video.height}, ${(media.video.fps_num / media.video.fps_den).toFixed(3)} fps, ${media.video.codec || '?'} ${media.video.pix_fmt}` : '-'],
    ['Audio', media.audio ? `${media.audio.sample_rate} Hz, ${media.audio.channels} ch, ${media.audio.codec || '?'}` : '-'],
    ['Proxy', media.proxy_path ?? '-'],
    ['Label', media.label]
  ]
  return (
    <div className="lm-panel">
      <div className="lm-meta">
        {rows.map(([k, v]) => (
          <div key={k} className="row">
            <span className="k">{t(k)}</span>
            <span className="v" title={v}>{v}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const SOUND_TAGS: [AudioTag, string, React.FC<{ size?: number }>][] = [
  ['dialogue', 'Dialogue', MessageSquare],
  ['music', 'Music', Music],
  ['sfx', 'SFX', Sparkles],
  ['ambience', 'Ambience', Wind]
]
type AudioTag = 'dialogue' | 'music' | 'sfx' | 'ambience'
// Auto-Match targets (engine/lumina/audio_analysis.py DEFAULT_TARGETS)
const TARGETS: Record<AudioTag, number> = { dialogue: -23, music: -25, sfx: -21, ambience: -30 }

type MatchResult = Record<string, {
  tag: string
  lufs: number | null
  metric?: 'integrated' | 'short_max' | 'momentary_max'
  target?: number
  result?: number
  gain_db: number
  group?: number
  notes: string[]
}>

// why Auto-Match chose a gain (engine audio_analysis.plan_match / commands/audio.py)
const MATCH_NOTES: Record<string, string> = {
  silent: 'silent',
  offline: 'Media Offline',
  bed: 'music bed under dialogue',
  short: 'too short to gate: loudest moment',
  outlier: 'far from the rest of its source: pulled halfway',
  peak_limited: 'held under the true-peak ceiling',
  boost_capped: 'boost limit reached',
  noisy: 'noisy: the boost raises the noise too',
  kept: 'already matched: unchanged',
  compressed: 're-measured through compressor / limiter'
}

/** Essential Sound (Edit): tag audio clips by type (or Auto-Tag them), then Loudness Auto-Match. */
export function EssentialSoundPanel() {
  const seq = useStore((s) => activeSequence(s))
  const selected = useStore((s) => s.selectedClips)
  const [busy, setBusy] = useState<string | null>(null)
  const [targets, setTargets] = useState<Record<AudioTag, number>>(TARGETS)
  const [result, setResult] = useState<MatchResult | null>(null)
  const [bed, setBed] = useState<number | null>(12)
  const [ceiling, setCeiling] = useState(-1)
  if (!seq) return <div className="lm-panel"><div className="lm-empty">{t('(no sequences)')}</div></div>
  // the selected audio clips, plus the audio linked to selected video
  const sel = selected.map((id) => findClip(seq, id)).filter(Boolean).map((f) => f!.clip)
  const links = new Set(sel.map((c) => c.link_id).filter(Boolean))
  const audio = seq.audio_tracks.flatMap((id) => Object.values(seq.tracks[id].clips)).filter((c) => sel.includes(c) || (c.link_id && links.has(c.link_id)))
  const ids = audio.map((c) => c.id)
  const tags = new Set(audio.map((c) => c.audio_tag ?? null))
  const tag = tags.size === 1 ? [...tags][0] : null
  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    try {
      await fn()
    } finally {
      setBusy(null)
    }
  }
  const autoTag = () => run(t('Auto-Tag'), () => exec('audio.auto_tag', { sequence_id: seq.id, clip_ids: ids.length ? ids : null, overwrite: ids.length > 0 }, t('Auto-Tag')))
  const autoMatch = () =>
    run(t('Auto-Match'), async () => {
      const r = await exec<{ clips: MatchResult }>('audio.auto_match', { sequence_id: seq.id, clip_ids: ids.length ? ids : null, targets, music_bed_db: bed, peak_ceiling: ceiling }, t('Auto-Match'))
      if (r) setResult(r.clips)
    })
  return (
    <div className="lm-panel lm-es" data-essential-sound="">
      <div className="lm-subtabs">
        <span>{t('Browse')}</span>
        <span className="active">{t('Edit')}</span>
      </div>
      <div className="lm-es-body">
        <div className="dim">{audio.length ? `${audio.length} ${t('audio clip(s) selected')}` : t('No selection')}</div>
        {!tag && <AudioLines size={34} color="#888" />}
        <div className="title">{tag ? t(SOUND_TAGS.find((x) => x[0] === tag)![1]) : t('Select a clip to tag:')}</div>
        <div className="tags">
          {SOUND_TAGS.map(([key, label, Icon]) => (
            <button
              key={key}
              className={`lm-chip ${tag === key ? 'on' : ''}`}
              data-tag={key}
              disabled={!audio.length || !!busy}
              onClick={() => void exec('clip.set_audio_tag', { sequence_id: seq.id, clip_ids: ids, tag: key }, t('Audio Type'))}
            >
              <Icon size={12} /> {t(label)}
            </button>
          ))}
        </div>
        <div className="row">
          <button className="lm-btn" data-action="auto-tag" disabled={!!busy} onClick={() => void autoTag()}>
            {busy === t('Auto-Tag') ? t('Analyzing...') : audio.length ? t('Auto-Tag') : t('Auto-Tag Sequence')}
          </button>
          {tag && (
            <button className="lm-btn" disabled={!!busy} onClick={() => void exec('clip.set_audio_tag', { sequence_id: seq.id, clip_ids: ids, tag: null }, t('Clear Audio Type'))}>
              {t('Clear Audio Type')}
            </button>
          )}
        </div>
        <div className="lm-es-section">
          <div className="head">{t('Loudness')}</div>
          {SOUND_TAGS.filter(([key]) => !tag || key === tag).map(([key, label]) => (
            <label key={key} className="target">
              <span>{t(label)}</span>
              <input type="number" step={0.5} min={-40} max={-5} value={targets[key]} onChange={(e) => setTargets({ ...targets, [key]: Number(e.target.value) })} />
              <span className="dim">LUFS</span>
            </label>
          ))}
          <label className="target" title={t('Music that plays under dialogue is matched this far below the dialogue target')}>
            <span>
              <input type="checkbox" data-opt="bed" checked={bed !== null} onChange={(e) => setBed(e.target.checked ? 12 : null)} /> {t('Music under dialogue')}
            </span>
            <input type="number" step={1} min={0} max={40} disabled={bed === null} value={bed ?? 12} onChange={(e) => setBed(Number(e.target.value))} />
            <span className="dim">LU {t('lower')}</span>
          </label>
          <label className="target">
            <span>{t('True peak ceiling')}</span>
            <input type="number" step={0.5} min={-20} max={0} value={ceiling} onChange={(e) => setCeiling(Number(e.target.value))} />
            <span className="dim">dBTP</span>
          </label>
          <button className="lm-btn primary" data-action="auto-match" disabled={!!busy} onClick={() => void autoMatch()}>
            {busy === t('Auto-Match') ? t('Analyzing...') : t('Auto-Match')}
          </button>
          {result && (
            <div className="results" data-match-results="">
              {Object.entries(result).slice(0, 40).map(([id, r]) => {
                const c = findClip(seq, id)?.clip
                const metric = r.metric === 'integrated' ? 'LUFS' : t('LUFS (loudest)')
                return (
                  <div key={id} className="res" data-notes={r.notes.join(' ')}>
                    <div className="line">
                      <span className="name">{c?.name ?? id}</span>
                      <span>
                        {r.lufs === null
                          ? t(MATCH_NOTES[r.notes[0]] ?? 'silent')
                          : `${r.lufs.toFixed(1)} → ${(r.result ?? r.target ?? 0).toFixed(1)} ${metric} (${r.gain_db > 0 ? '+' : ''}${r.gain_db.toFixed(1)} dB)`}
                      </span>
                    </div>
                    {r.lufs !== null && (r.notes.length > 0 || (r.group ?? 1) > 1) && (
                      <div className="why">
                        {[...((r.group ?? 1) > 1 ? [`${t('same gain as')} ${r.group} ${t('cuts of this source')}`] : []), ...r.notes.map((n) => t(MATCH_NOTES[n] ?? n))].join(' · ')}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// Lumetri Color panel: Basic Correction and Creative sliders driving the clip's Lumetri Color effect
// (engine render/color.py). A drag previews live inside one transaction, so it is one undo step.
const LUMETRI_SECTIONS: { title: string; groups: { title?: string; rows: [keyof LumetriParams, number, number, number][] }[] }[] = [
  { title: 'Basic Correction', groups: [
    { title: 'White Balance', rows: [['temperature', -100, 100, 1], ['tint', -100, 100, 1]] },
    { title: 'Tone', rows: [['exposure', -5, 5, 0.1], ['contrast', -100, 100, 1], ['highlights', -100, 100, 1], ['shadows', -100, 100, 1], ['whites', -100, 100, 1], ['blacks', -100, 100, 1]] },
    { rows: [['saturation', 0, 200, 1]] }
  ] },
  { title: 'Creative', groups: [{ title: 'Adjustments', rows: [['vibrance', -100, 100, 1]] }] }
]
const LUMETRI_LABEL: Record<keyof LumetriParams, string> = {
  temperature: 'Temperature', tint: 'Tint', exposure: 'Exposure', contrast: 'Contrast', highlights: 'Highlights', shadows: 'Shadows',
  whites: 'Whites', blacks: 'Blacks', saturation: 'Saturation', vibrance: 'Vibrance'
}

export function LumetriPanel() {
  const seq = useStore((s) => activeSequence(s))
  const selected = useStore((s) => s.selectedClips)
  const time = useStore((s) => s.programTime)
  const [live, setLive] = useState<{ key: keyof LumetriParams; v: number } | null>(null)
  const clip = seq ? selected.map((id) => findClip(seq, id)).find((f) => f && f.track.kind === 'video')?.clip : undefined
  if (!seq || !clip) return <div className="lm-panel"><div className="lm-empty">{t('Select a video clip to color correct.')}</div></div>
  const fx = clip.effects?.find((e) => e.type === 'lumetri')
  const value = (k: keyof LumetriParams): number => (live?.key === k ? live.v : fx ? (effectParam(fx, k, clip, time) as number) : k === 'saturation' ? 100 : 0)
  // one transaction per drag: the effect is added on first use, every move updates it
  const begin = async (): Promise<string | null> => {
    await engine.call('transaction.begin', { label: t('Lumetri Color') })
    if (fx) return fx.id
    const r = await engine.exec<{ effect_ids: string[] }>('effect.add', { sequence_id: seq.id, clip_ids: [clip.id], type: 'lumetri' }).catch(() => null)
    return r?.effect_ids?.[0] ?? null
  }
  const drag = async (k: keyof LumetriParams, start: number, compute: (ev: MouseEvent) => number) => {
    const id = await begin()
    if (!id) {
      await engine.call('transaction.rollback').catch(() => undefined)
      return
    }
    let pending: number | null = null
    let busy = false
    const push = async () => {
      if (busy || pending === null) return
      busy = true
      const v = pending
      pending = null
      await engine.exec('effect.set_param', { sequence_id: seq.id, clip_id: clip.id, effect_id: id, param: k, value: v, at: time }).catch(() => undefined)
      busy = false
      void push()
    }
    let last = start
    pending = start // the press itself sets the value
    void push()
    const move = (ev: MouseEvent) => {
      last = compute(ev)
      setLive({ key: k, v: last })
      pending = last
      void push()
    }
    const up = async () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      while (busy) await new Promise((r) => setTimeout(r, 10))
      pending = last
      await push()
      await engine.call('transaction.commit').catch(() => undefined)
      setLive(null)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }
  const setOnce = async (k: keyof LumetriParams, v: number) => {
    const id = await begin()
    if (id) await engine.exec('effect.set_param', { sequence_id: seq.id, clip_id: clip.id, effect_id: id, param: k, value: v, at: time }).catch((e) => toast(String(e)))
    await engine.call(id ? 'transaction.commit' : 'transaction.rollback').catch(() => undefined)
  }
  return (
    <div className="lm-panel lm-lumetri" data-lumetri="">
      <div className="lm-lumetri-head">
        <span className="dim">{clip.name}</span>
        {fx && (
          <button className="lm-btn" title={t('Reset')} onClick={() => void exec('effect.remove', { sequence_id: seq.id, clip_id: clip.id, effect_id: fx.id }, t('Reset'))}>
            <RotateCcw size={12} /> {t('Reset')}
          </button>
        )}
      </div>
      <div className="lm-lumetri-body">
        {LUMETRI_SECTIONS.map((sec) => (
          <div key={sec.title} className="lm-lumetri-section">
            <div className="sec">{t(sec.title)}</div>
            {sec.groups.map((g, gi) => (
              <div key={gi} className="grp">
                {g.title && <div className="gtitle">{t(g.title)}</div>}
                {g.rows.map(([k, min, max, step]) => {
                  const v = value(k)
                  const def = k === 'saturation' ? 100 : 0
                  const pos = (v - min) / (max - min)
                  return (
                    <div key={k} className={`row ${k}`} data-lumetri-param={k}>
                      <span className="label">{t(LUMETRI_LABEL[k])}</span>
                      <div
                        className="track"
                        onDoubleClick={() => void setOnce(k, def)}
                        onMouseDown={(e) => {
                          if (e.button !== 0) return
                          e.preventDefault()
                          const r = e.currentTarget.getBoundingClientRect()
                          const at = (x: number) => Math.round((min + Math.max(0, Math.min(1, (x - r.left) / r.width)) * (max - min)) / step) * step
                          const first = at(e.clientX)
                          setLive({ key: k, v: first })
                          void drag(k, first, (ev) => at(ev.clientX))
                        }}
                      >
                        <div className="fill" style={{ left: `${Math.min(pos, (def - min) / (max - min)) * 100}%`, width: `${Math.abs(pos - (def - min) / (max - min)) * 100}%` }} />
                        <div className="knob" style={{ left: `calc(${pos * 100}% - 5px)` }} />
                      </div>
                      <NumberScrub value={v} min={min} max={max} step={step} digits={step < 1 ? 1 : 0} onChange={(nv) => void setOnce(k, nv)} />
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}


export function GraphicTemplatesPanel() {
  return <div className="lm-panel"><div className="lm-empty">{t('Graphic templates (MOGRT-style) arrive with graphics (M4).')}</div></div>
}
