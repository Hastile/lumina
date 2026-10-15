// Panels from Premiere 2025+'s Editing workspace right side and top-left tabs.
// Properties, Metadata and Essential Sound are functional; Lumetri Color, Text
// and Graphic Templates hold their place until their milestones.
import { useState } from 'react'
import { ChevronDown, AudioLines, Circle, MessageSquare, Music, Sparkles, Square, Wind, RotateCcw, Plus, Maximize, Minimize } from 'lucide-react'
import { t } from '@shared/i18n'
import type { MediaItem } from '../engine/document'
import { activeSequence, exec, findClip, get, useStore } from '../state/store'
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

export function LumetriPanel() {
  return <div className="lm-panel"><div className="lm-empty">{t('Lumetri Color: color correction arrives with the GPU compositor (M3).')}</div></div>
}

export function TextPanel() {
  return <div className="lm-panel"><div className="lm-empty">{t('Text / captions arrive with captions and graphics (M4).')}</div></div>
}

export function GraphicTemplatesPanel() {
  return <div className="lm-panel"><div className="lm-empty">{t('Graphic templates (MOGRT-style) arrive with graphics (M4).')}</div></div>
}
