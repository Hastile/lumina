// Shared controls for keyframable properties (Effect Controls + Properties panels).
import { useState } from 'react'
import { ChevronLeft, ChevronRight, Diamond, Timer } from 'lucide-react'
import { t } from '@shared/i18n'
import type { Clip, MediaItem, Sequence } from '../engine/document'
import { activeSequence, exec, findClip, useStore } from '../state/store'
import { goTo } from '../state/transport'
import { keyframesOf, seqTimeOf, sourceTime, valueAt, type KeyframeProp } from '../util/motion'
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

/** Stopwatch + value + keyframe navigator, Premiere style. */
export function KeyframeRow({ c, prop, children, label }: { c: ClipCtx; prop: KeyframeProp; children: React.ReactNode; label?: string }) {
  const kfs = keyframesOf(c.clip, prop)
  const at = clipTime(c)
  const tol = (ticksPerFrame(c.seq.settings) * c.clip.speed) / 2
  const src = sourceTime(c.clip, at)
  const onKey = !!kfs?.some((k) => Math.abs(k.t - src) <= tol)
  const times = (kfs ?? []).map((k) => seqTimeOf(c.clip, k.t))
  const prev = [...times].reverse().find((x) => x < at - 1)
  const next = times.find((x) => x > at + 1)
  return (
    <div className="lm-ec-prop">
      <button
        className={`stopwatch ${kfs ? 'on' : ''}`}
        title={t('Toggle animation')}
        onClick={() => void exec('clip.toggle_animation', { sequence_id: c.seq.id, clip_id: c.clip.id, prop, at }, t('Toggle animation'))}
      >
        <Timer size={12} />
      </button>
      <span className="pname">{t(label ?? LABELS[prop])}</span>
      <span className="pval">{children}</span>
      <span className="kfnav">
        {kfs && (
          <>
            <button disabled={prev === undefined} title={t('Go to Previous Keyframe')} onClick={() => prev !== undefined && goTo('program', Math.round(prev))}>
              <ChevronLeft size={11} />
            </button>
            <button
              className={onKey ? 'on' : ''}
              title={t('Add/Remove Keyframe')}
              onClick={() => void exec('clip.keyframe', { sequence_id: c.seq.id, clip_id: c.clip.id, prop, at }, t('Add/Remove Keyframe'))}
            >
              <Diamond size={10} />
            </button>
            <button disabled={next === undefined} title={t('Go to Next Keyframe')} onClick={() => next !== undefined && goTo('program', Math.round(next))}>
              <ChevronRight size={11} />
            </button>
          </>
        )}
      </span>
      <KeyframeLane c={c} prop={prop} />
    </div>
  )
}

/** Effect Controls' right-hand mini timeline for one property: diamonds + playhead. */
function KeyframeLane({ c, prop }: { c: ClipCtx; prop: KeyframeProp }) {
  const span = c.clip.end - c.clip.start
  const pct = (x: number) => `${(100 * (x - c.clip.start)) / span}%`
  return (
    <span className="lane">
      {(keyframesOf(c.clip, prop) ?? []).map((k, i) => {
        const x = seqTimeOf(c.clip, k.t)
        if (x < c.clip.start || x > c.clip.end) return null
        return (
          <span key={i} className={`kf ${k.interp}`} style={{ left: pct(x) }} onClick={() => goTo('program', Math.round(x))}>
            <Diamond size={9} />
          </span>
        )
      })}
      {c.time >= c.clip.start && c.time < c.clip.end && <span className="head" style={{ left: pct(c.time) }} />}
    </span>
  )
}

export function current(c: ClipCtx, prop: KeyframeProp): number | number[] {
  return valueAt(c.clip, prop, clipTime(c), c.seq, c.media)
}
