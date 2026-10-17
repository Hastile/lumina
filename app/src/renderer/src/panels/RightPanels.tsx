// Panels from Premiere 2025+'s Editing workspace right side and top-left tabs.
// Properties, Metadata and Essential Sound are functional; Lumetri Color, Text
// and Graphic Templates hold their place until their milestones.
import { useState } from 'react'
import { ChevronDown, Circle, Square, RotateCcw, Plus, Maximize, Minimize } from 'lucide-react'
import { t } from '@shared/i18n'
import type { MediaItem } from '../engine/document'
import { activeSequence, exec, findClip, get, toast, useStore } from '../state/store'
import { engine } from '../engine/client'
import { effectParam } from '../util/effects'
import type { LumetriParams } from '../util/color'
import { ticksToSeconds } from '../util/time'
import { current, KeyframeRow, NumberScrub, setProp, useSelectedClip } from './PropertyControls'
import { GraphicLayers } from './GraphicLayers'
import { DRAG_MIME, setDraggedItems } from './ProjectPanel'
import { placeAsset } from '../actions/extra'
import { TrackPanel, useSelectedTrack } from './TrackPanel'

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
  const tr = useSelectedTrack()
  const cueTrack = c?.clip.caption ? findClip(c.seq, c.clip.id)?.track : undefined
  if ((!c && tr) || (c && cueTrack?.caption_style)) {
    // a caption cue shows its track: Premiere's captions have one style and transform per track
    const seq = c ? c.seq : tr!.seq
    const track = c ? cueTrack! : tr!.track
    return (
      <div className="lm-panel lm-props">
        <div className="lm-props-title">{track.caption_style ? t('Caption Track') : t('Track')} · {track.name}</div>
        <div className="lm-props-scroll lm-ec">
          <TrackPanel seq={seq} track={track} />
        </div>
      </div>
    )
  }
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


/** Essential Graphics > Browse: the project's Motion Graphics Templates. Double-click (or drag onto the
 * timeline) places one at the playhead as an editable graphic clip. */
export function GraphicTemplatesPanel() {
  const assets = useStore((s) => s.doc?.assets)
  const templates = Object.values(assets ?? {}).filter((a) => a.kind === 'template').sort((a, b) => a.name.localeCompare(b.name))
  const add = async () => {
    const files = await window.lumina.openDialog({ title: t('Install Motion Graphics Template'), properties: ['openFile', 'multiSelections'], filters: [{ name: 'Motion Graphics Templates', extensions: ['mogrt', 'aegraphic'] }] }, 'import')
    for (const path of files) await exec('asset.add', { kind: 'template', path }, t('Install Motion Graphics Template'))
  }
  return (
    <div className="lm-panel lm-templates">
      <div className="lm-toolbar">
        <span className="dim" style={{ flex: 1 }}>{t('Motion Graphics Templates')}</span>
        <button className="lm-iconbtn" title={t('Install Motion Graphics Template')} onClick={() => void add()} data-add-template="">
          +
        </button>
      </div>
      {templates.length ? (
        <div className="lm-tree">
          {templates.map((a) => (
            <div
              key={a.id}
              className="node leaf"
              data-template={a.id}
              draggable
              title={a.path ?? ''}
              onDragStart={(e) => {
                e.dataTransfer.setData(DRAG_MIME, JSON.stringify([a.id]))
                e.dataTransfer.effectAllowed = 'copy'
                setDraggedItems([a.id])
              }}
              onDragEnd={() => setDraggedItems([])}
              onDoubleClick={() => void placeAsset(a.id)}
            >
              {a.name}
            </div>
          ))}
        </div>
      ) : (
        <div className="lm-empty">{t('No templates in this project. Import a .mogrt (+) or a Premiere project that uses one.')}</div>
      )}
    </div>
  )
}
