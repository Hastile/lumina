// The yellow volume rubber band on audio clips (Premiere): drag it to change the
// clip's Volume (every keyframe moves by the same dB), drag a keyframe dot to
// change that keyframe, Ctrl+click to add a keyframe.

import { useState } from 'react'
import { t } from '@shared/i18n'
import type { Clip, Sequence } from '../engine/document'
import { exec } from '../state/store'
import { seqTimeOf, valueAt, type Kf } from '../util/motion'
import { floorToFrame } from '../util/time'
import { dbToPos, fmtDb, posToDb } from '../panels/Audio'

const PAD = 4

export function VolumeBand({ clip, seq, width, height }: { clip: Clip; seq: Sequence; width: number; height: number }) {
  const [drag, setDrag] = useState<{ shift: number; kf?: number } | null>(null)
  const kfs = (clip.keyframes.volume_db as Kf[] | undefined) ?? []
  const dur = clip.end - clip.start
  const h = Math.max(1, height - 2 * PAD)
  const yOf = (db: number) => PAD + (1 - dbToPos(Math.min(6, db))) * h
  const xOf = (t: number) => ((t - clip.start) / dur) * width
  const at = (t: number) => valueAt(clip, 'volume_db', t, seq) as number
  const shift = (i: number | undefined, db: number) => (drag && (drag.kf === undefined || drag.kf === i) ? Math.max(-96, Math.min(15, db + drag.shift)) : db)

  const pts: [number, number, number | undefined][] = []
  if (kfs.length) {
    pts.push([0, shift(undefined, at(clip.start)), undefined])
    kfs.forEach((k, i) => {
      const x = seqTimeOf(clip, k.t)
      if (x > clip.start && x < clip.end) pts.push([xOf(x), shift(i, k.v as number), i])
    })
    pts.push([width, shift(undefined, at(clip.end - 1)), undefined])
  } else {
    const v = shift(undefined, clip.volume_db)
    pts.push([0, v, undefined], [width, v, undefined])
  }
  if (drag?.kf !== undefined) {
    // only one keyframe moves: the ends follow the interpolation, approximate with neighbours
    pts[0][1] = at(clip.start)
    pts[pts.length - 1][1] = at(clip.end - 1)
  }
  const line = pts.map(([x, db]) => `${x},${yOf(db)}`).join(' ')

  const begin = (e: React.MouseEvent, kf?: number) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    const svg = (e.currentTarget as SVGElement).ownerSVGElement ?? (e.currentTarget as SVGSVGElement)
    const r = svg.getBoundingClientRect()
    const grabT = clip.start + ((e.clientX - r.left) / width) * dur
    if (e.ctrlKey && kf === undefined) {
      void exec('clip.keyframe', { sequence_id: seq.id, clip_id: clip.id, prop: 'volume_db', at: floorToFrame(grabT, seq.settings), op: 'add' }, t('Add/Remove Keyframe'))
      return
    }
    const base = kf !== undefined ? (kfs[kf].v as number) : kfs.length ? at(grabT) : clip.volume_db
    const y0 = e.clientY
    let d = 0
    const move = (ev: MouseEvent) => {
      const db = Math.round(posToDb(Math.max(0, Math.min(1, dbToPos(Math.min(6, base)) - (ev.clientY - y0) / h))) * 10) / 10
      d = db - base
      setDrag({ shift: d, kf })
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      setDrag(null)
      if (!d) return
      if (kf !== undefined) {
        void exec('clip.set_property', { sequence_id: seq.id, clip_id: clip.id, prop: 'volume_db', value: Math.max(-96, Math.min(15, base + d)), at: Math.round(seqTimeOf(clip, kfs[kf].t)) }, t('Volume'))
      } else if (kfs.length) {
        const moved = kfs.map((k) => ({ ...k, t: Math.round(seqTimeOf(clip, k.t) - clip.start), v: Math.max(-96, Math.min(15, (k.v as number) + d)) }))
        void exec('clip.set_keyframes', { sequence_id: seq.id, clip_id: clip.id, prop: 'volume_db', keyframes: moved }, t('Volume'))
      } else {
        void exec('clip.set', { sequence_id: seq.id, clip_ids: [clip.id], volume_db: Math.max(-96, Math.min(15, base + d)) }, t('Volume'))
      }
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  const label = drag ? fmtDb((drag.kf !== undefined ? (kfs[drag.kf].v as number) : kfs.length ? 0 : clip.volume_db) + drag.shift) : null
  return (
    <svg className="lm-vband" width={width} height={height} data-vband={clip.id}>
      <polyline points={line} className="line" />
      <polyline points={line} className="hit" onMouseDown={(e) => begin(e)}>
        <title>{t('Volume')}: {fmtDb(kfs.length ? at(clip.start) : clip.volume_db)} dB</title>
      </polyline>
      {pts.filter((p) => p[2] !== undefined).map(([x, db, i]) => (
        <circle key={i} cx={x} cy={yOf(db)} r={3} className="kf" data-vkf={i} onMouseDown={(e) => begin(e, i)} />
      ))}
      {label && <text x={4} y={11} className="db">{label} dB</text>}
    </svg>
  )
}
