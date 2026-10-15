// The yellow rubber band on clips (Premiere): Volume on audio clips, Opacity on video clips.
// Drag the line to change the value (every keyframe moves by the same amount), drag a
// keyframe dot up/down to change it and left/right to move it in time, Ctrl+click to add one.

import { useState } from 'react'
import { t } from '@shared/i18n'
import type { Clip, Sequence } from '../engine/document'
import { exec } from '../state/store'
import { seqTimeOf, valueAt, type Kf } from '../util/motion'
import { floorToFrame, ticksPerFrame } from '../util/time'
import { dbToPos, fmtDb, posToDb } from '../panels/Audio'

const PAD = 4

type BandProp = 'volume_db' | 'opacity'

// value <-> 0..1 height of the band, the range, the static value and the readout for each property
const SCALE: Record<BandProp, { toPos: (v: number) => number; fromPos: (p: number) => number; min: number; max: number; label: string; fmt: (v: number) => string; unit: string }> = {
  volume_db: { toPos: (v) => dbToPos(Math.min(6, v)), fromPos: (p) => Math.round(posToDb(p) * 10) / 10, min: -96, max: 15, label: 'Volume', fmt: fmtDb, unit: 'dB' },
  opacity: { toPos: (v) => v / 100, fromPos: (p) => Math.round(p * 1000) / 10, min: 0, max: 100, label: 'Opacity', fmt: (v) => v.toFixed(1), unit: '%' }
}

export function VolumeBand({ clip, seq, width, height, prop = 'volume_db' }: { clip: Clip; seq: Sequence; width: number; height: number; prop?: BandProp }) {
  const sc = SCALE[prop]
  const [drag, setDrag] = useState<{ shift: number; kf?: number; dt?: number } | null>(null)
  const kfs = (clip.keyframes[prop] as Kf[] | undefined) ?? []
  const dur = clip.end - clip.start
  const h = Math.max(1, height - 2 * PAD)
  const yOf = (v: number) => PAD + (1 - sc.toPos(v)) * h
  const xOf = (time: number) => ((time - clip.start) / dur) * width
  const at = (time: number) => valueAt(clip, prop, time, seq) as number
  const staticValue = prop === 'opacity' ? clip.opacity : clip.volume_db
  const clampV = (v: number) => Math.max(sc.min, Math.min(sc.max, v))
  const shift = (i: number | undefined, v: number) => (drag && (drag.kf === undefined || drag.kf === i) ? clampV(v + drag.shift) : v)

  const pts: [number, number, number | undefined][] = []
  if (kfs.length) {
    pts.push([0, shift(undefined, at(clip.start)), undefined])
    kfs.forEach((k, i) => {
      const x = seqTimeOf(clip, k.t) + (drag?.kf === i ? drag.dt ?? 0 : 0)
      if (x > clip.start && x < clip.end) pts.push([xOf(x), shift(i, k.v as number), i])
    })
    pts.sort((a, b) => a[0] - b[0])
    pts.push([width, shift(undefined, at(clip.end - 1)), undefined])
  } else {
    const v = shift(undefined, staticValue)
    pts.push([0, v, undefined], [width, v, undefined])
  }
  if (drag?.kf !== undefined) {
    // only one keyframe moves: the ends follow the interpolation, approximate with neighbours
    pts[0][1] = at(clip.start)
    pts[pts.length - 1][1] = at(clip.end - 1)
  }
  const line = pts.map(([x, v]) => `${x},${yOf(v)}`).join(' ')

  const begin = (e: React.MouseEvent, kf?: number) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    const svg = (e.currentTarget as SVGElement).ownerSVGElement ?? (e.currentTarget as SVGSVGElement)
    const r = svg.getBoundingClientRect()
    const grabT = clip.start + ((e.clientX - r.left) / width) * dur
    if (e.ctrlKey && kf === undefined) {
      void exec('clip.keyframe', { sequence_id: seq.id, clip_id: clip.id, prop, at: floorToFrame(grabT, seq.settings), op: 'add' }, t('Add/Remove Keyframe'))
      return
    }
    const base = kf !== undefined ? (kfs[kf].v as number) : kfs.length ? at(grabT) : staticValue
    const tpf = ticksPerFrame(seq.settings)
    const x0 = e.clientX
    const y0 = e.clientY
    let d = 0
    let dt = 0
    const move = (ev: MouseEvent) => {
      d = sc.fromPos(Math.max(0, Math.min(1, sc.toPos(base) - (ev.clientY - y0) / h))) - base
      if (kf !== undefined) {
        // a keyframe also moves in time, frame by frame, staying inside the clip
        const kt = seqTimeOf(clip, kfs[kf].t)
        dt = Math.round((((ev.clientX - x0) / width) * dur) / tpf) * tpf
        dt = Math.max(clip.start - kt, Math.min(clip.end - tpf - kt, dt))
      }
      setDrag({ shift: d, kf, dt })
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      setDrag(null)
      if (!d && !dt) return
      const rel = (k: Kf) => Math.round(seqTimeOf(clip, k.t) - clip.start)
      if (kf !== undefined) {
        const moved = { ...kfs[kf], t: rel(kfs[kf]) + dt, v: clampV(base + d) }
        // the dragged keyframe replaces one it lands on (Premiere)
        const rest = kfs.filter((_, i) => i !== kf).map((k) => ({ ...k, t: rel(k) })).filter((k) => k.t !== moved.t)
        void exec('clip.set_keyframes', { sequence_id: seq.id, clip_id: clip.id, prop, keyframes: [...rest, moved].sort((a, b) => a.t - b.t) }, t(sc.label))
      } else if (kfs.length) {
        const moved = kfs.map((k) => ({ ...k, t: rel(k), v: clampV((k.v as number) + d) }))
        void exec('clip.set_keyframes', { sequence_id: seq.id, clip_id: clip.id, prop, keyframes: moved }, t(sc.label))
      } else {
        void exec('clip.set', { sequence_id: seq.id, clip_ids: [clip.id], [prop]: clampV(base + d) }, t(sc.label))
      }
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  const label = drag ? sc.fmt((drag.kf !== undefined ? (kfs[drag.kf].v as number) : kfs.length ? 0 : staticValue) + drag.shift) : null
  return (
    <svg className={`lm-vband ${prop === 'opacity' ? 'opacity' : ''}`} width={width} height={height} data-vband={clip.id} data-band={prop}>
      <polyline points={line} className="line" />
      <polyline points={line} className="hit" onMouseDown={(e) => begin(e)}>
        <title>
          {t(sc.label)}: {sc.fmt(kfs.length ? at(clip.start) : staticValue)} {sc.unit}
        </title>
      </polyline>
      {pts.filter((p) => p[2] !== undefined).map(([x, v, i]) => (
        <circle key={i} cx={x} cy={yOf(v)} r={3} className="kf" data-vkf={i} onMouseDown={(e) => begin(e, i)} />
      ))}
      {label && (
        <text x={4} y={11} className="db">
          {label} {sc.unit}
        </text>
      )}
    </svg>
  )
}
