// Shared controls for keyframable properties (Effect Controls + Properties panels).
import { useState } from 'react'
import { ChevronDown, ChevronLeft, ChevronRight, Diamond, Timer } from 'lucide-react'
import { AngleDial, KeyframeGraphs, ValueSlider } from './KeyframeGraph'
import { t } from '@shared/i18n'
import type { Clip, MediaItem, Sequence } from '../engine/document'
import { activeSequence, exec, findClip, useStore } from '../state/store'
import { goTo } from '../state/transport'
import { keyframesOf, seqTimeOf, sourceTime, valueAt, type Kf, type KeyframeProp } from '../util/motion'
import { ticksPerFrame } from '../util/time'

export type ClipCtx = { seq: Sequence; clip: Clip; kind: 'video' | 'audio'; media: MediaItem | null; time: number }

/** The selected clip (first of the selection) with its sequence, media and the playhead. */
export function useSelectedClip(): ClipCtx | null {
  const seq = useStore((s) => activeSequence(s))
  const selected = useStore((s) => s.selectedClips)
  const media = useStore((s) => s.doc?.media)
  const time = useStore((s) => s.programTime)
  if (!seq || !selected.length) return null
  const found = findClip(seq, selected[0])
  if (!found) return null
  const m = found.clip.media_id ? media?.[found.clip.media_id] ?? null : null
  return { seq, clip: found.clip, kind: found.track.kind, media: m, time }
}

/** The Effect Controls timeline's visible range (sequence ticks): zoomed with Ctrl/Alt+wheel, else the clip. */
export function laneSpan(c: ClipCtx, view: { clipId: string; start: number; end: number } | null): [number, number] {
  return view && view.clipId === c.clip.id ? [view.start, view.end] : [c.clip.start, c.clip.end]
}

/** Value under the playhead, clamped into the clip so panels show something sensible. */
export function clipTime(c: ClipCtx): number {
  return Math.min(Math.max(c.time, c.clip.start), c.clip.end - 1)
}

export function NumberScrub({ value, min = -1e9, max = 1e9, step = 1, digits = 1, suffix = '', onChange, disabled }: {
  value: number; min?: number; max?: number; step?: number; digits?: number; suffix?: string; onChange: (v: number) => void; disabled?: boolean
}) {
  const [edit, setEdit] = useState<string | null>(null)
  const [live, setLive] = useState<number | null>(null)
  if (edit !== null) {
    return (
      <input
        className="lm-value-input"
        autoFocus
        value={edit}
        onChange={(e) => setEdit(e.target.value)}
        onBlur={() => setEdit(null)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            const v = Number(edit)
            if (!Number.isNaN(v)) onChange(Math.max(min, Math.min(max, v)))
            setEdit(null)
          }
          if (e.key === 'Escape') setEdit(null)
        }}
      />
    )
  }
  const shown = live ?? value
  return (
    <span
      className={`lm-value ${disabled ? 'dim' : ''}`}
      onMouseDown={(e) => {
        if (disabled || e.button !== 0) return
        const startX = e.clientX
        let moved = false
        let current = value
        const move = (ev: MouseEvent) => {
          const dx = ev.clientX - startX
          if (Math.abs(dx) > 2) moved = true
          current = Math.max(min, Math.min(max, value + Math.round(dx) * step * (ev.shiftKey ? 10 : 1)))
          setLive(current)
        }
        const up = () => {
          window.removeEventListener('mousemove', move)
          window.removeEventListener('mouseup', up)
          setLive(null)
          if (moved && current !== value) onChange(current)
          else if (!moved) setEdit(value.toFixed(digits))
        }
        window.addEventListener('mousemove', move)
        window.addEventListener('mouseup', up)
      }}
    >
      {shown.toFixed(digits)}
      {suffix}
    </span>
  )
}

export function setProp(c: ClipCtx, prop: KeyframeProp, value: number | number[]): void {
  void exec('clip.set_property', { sequence_id: c.seq.id, clip_id: c.clip.id, prop, value, at: clipTime(c) }, t(LABELS[prop]))
}

export const LABELS: Record<KeyframeProp, string> = {
  position: 'Position', scale: 'Scale', scale_width: 'Scale Width', rotation: 'Rotation', anchor: 'Anchor Point',
  crop_left: 'Crop Left', crop_top: 'Crop Top', crop_right: 'Crop Right', crop_bottom: 'Crop Bottom', opacity: 'Opacity', volume_db: 'Level'
}

/** What an expanded property shows under its row (Premiere's twirl-down). */
export type Expand = {
  control?: 'slider' | 'dial'
  min?: number
  max?: number
  value?: number
  onValue?: (v: number) => void
  onEase?: (t: number, side: 'in' | 'out', speed: number, influence: number) => void
}

/** Slider ranges / dials of the Motion, Opacity and Volume properties (Premiere's). */
const EXPAND: Partial<Record<KeyframeProp, { control: 'slider' | 'dial'; min?: number; max?: number }>> = {
  scale: { control: 'slider', min: 0, max: 100 },
  scale_width: { control: 'slider', min: 0, max: 100 },
  rotation: { control: 'dial' },
  opacity: { control: 'slider', min: 0, max: 100 },
  crop_left: { control: 'slider', min: 0, max: 100 },
  crop_top: { control: 'slider', min: 0, max: 100 },
  crop_right: { control: 'slider', min: 0, max: 100 },
  crop_bottom: { control: 'slider', min: 0, max: 100 },
  volume_db: { control: 'slider', min: -60, max: 15 }
}

/** Stopwatch + value + keyframe navigator, Premiere style. */
export function KeyframeRow({ c, prop, children, label }: { c: ClipCtx; prop: KeyframeProp; children: React.ReactNode; label?: string }) {
  const at = clipTime(c)
  const base = { sequence_id: c.seq.id, clip_id: c.clip.id, at }
  const spec = EXPAND[prop]
  const v = current(c, prop)
  return (
    <KfRow
      c={c}
      kfs={keyframesOf(c.clip, prop)}
      label={t(label ?? LABELS[prop])}
      onToggle={() => void exec('clip.toggle_animation', { ...base, prop }, t('Toggle animation'))}
      onKey={() => void exec('clip.keyframe', { ...base, prop }, t('Add/Remove Keyframe'))}
      expand={{
        ...spec,
        value: typeof v === 'number' ? v : undefined,
        onValue: (x) => setProp(c, prop, x),
        onEase: (tt, side, speed, influence) =>
          void exec('clip.set_keyframe_ease', { sequence_id: c.seq.id, clip_id: c.clip.id, prop, t: tt, side, speed, influence }, t('Keyframe Velocity'))
      }}
    >
      {children}
    </KfRow>
  )
}

/** Generic row: used by Motion/Opacity properties and by effect parameters. */
export function KfRow({ c, kfs, label, children, onToggle, onKey, animatable = true, expand }: {
  c: ClipCtx; kfs: Kf[] | undefined; label: string; children: React.ReactNode; onToggle: () => void; onKey: () => void; animatable?: boolean; expand?: Expand
}) {
  const [open, setOpen] = useState(false)
  const at = clipTime(c)
  const tol = (ticksPerFrame(c.seq.settings) * c.clip.speed) / 2
  const src = sourceTime(c.clip, at)
  const has = !!kfs && kfs.length > 0
  const onKeyNow = !!kfs?.some((k) => Math.abs(k.t - src) <= tol)
  const times = (kfs ?? []).map((k) => seqTimeOf(c.clip, k.t))
  const prev = [...times].reverse().find((x) => x < at - 1)
  const next = times.find((x) => x > at + 1)
  const graphs = !!expand?.onEase && !!kfs && kfs.length >= 2
  const control = expand?.control && expand.value !== undefined && expand.onValue
  const expandable = !!control || graphs
  const row = (
    <div className="lm-ec-prop">
      <span className="lead">
        {expandable ? (
          <button className="twirl" title={t('Show/Hide')} data-twirl={label} onClick={() => setOpen(!open)}>
            {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          </button>
        ) : (
          <span className="twirl" />
        )}
        {animatable ? (
          <button className={`stopwatch ${has ? 'on' : ''}`} title={t('Toggle animation')} onClick={onToggle}>
            <Timer size={12} />
          </button>
        ) : (
          <span />
        )}
      </span>
      <span className="pname">{label}</span>
      <span className="pval">{children}</span>
      <span className="kfnav">
        {has && (
          <>
            <button disabled={prev === undefined} title={t('Go to Previous Keyframe')} onClick={() => prev !== undefined && goTo('program', Math.round(prev))}>
              <ChevronLeft size={11} />
            </button>
            <button className={onKeyNow ? 'on' : ''} title={t('Add/Remove Keyframe')} onClick={onKey}>
              <Diamond size={10} />
            </button>
            <button disabled={next === undefined} title={t('Go to Next Keyframe')} onClick={() => next !== undefined && goTo('program', Math.round(next))}>
              <ChevronRight size={11} />
            </button>
          </>
        )}
      </span>
      <KeyframeLane c={c} kfs={kfs} />
    </div>
  )
  if (!open || !expandable) return row
  return (
    <>
      {row}
      <div className="lm-ec-expand" data-expanded={label}>
        {control && (
          <>
            <span className="controls">
              {expand.control === 'dial' && <AngleDial value={expand.value!} onCommit={expand.onValue!} />}
              {expand.control === 'slider' && <ValueSlider value={expand.value!} min={expand.min ?? 0} max={expand.max ?? 100} onCommit={expand.onValue!} />}
            </span>
            <span className="lm-kcell" />
          </>
        )}
        {graphs && <KeyframeGraphs c={c} kfs={kfs!} onEase={expand!.onEase!} />}
      </div>
    </>
  )
}

/** Effect Controls' right-hand mini timeline for one property: diamonds + playhead. */
function KeyframeLane({ c, kfs }: { c: ClipCtx; kfs: Kf[] | undefined }) {
  const view = useStore((s) => s.ecView)
  const [a, b] = laneSpan(c, view)
  const pct = (x: number) => `${(100 * (x - a)) / Math.max(1, b - a)}%`
  return (
    <span className="lane">
      {(kfs ?? []).map((k, i) => {
        const x = seqTimeOf(c.clip, k.t)
        if (x < a || x > b) return null
        return (
          <span key={i} className={`kf ${k.interp}`} style={{ left: pct(x) }} onClick={() => goTo('program', Math.round(x))}>
            <Diamond size={9} />
          </span>
        )
      })}
      {c.time >= a && c.time < b && <span className="head" style={{ left: pct(c.time) }} />}
    </span>
  )
}

export function current(c: ClipCtx, prop: KeyframeProp): number | number[] {
  return valueAt(c.clip, prop, clipTime(c), c.seq, c.media)
}
