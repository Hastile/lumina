// Effect Controls' expanded property (Premiere's twirl-down): a value slider or
// angle dial on the left, and on the keyframe lane the Value and Velocity graphs
// with draggable Bezier handles (horizontal = influence, vertical = speed).

import { useEffect, useRef, useState } from 'react'
import { liveValue } from '../util/liveValue'
import { t } from '@shared/i18n'
import { autoSpeed, interpolate, isBezier, seqTimeOf, sourceTime, type Kf } from '../util/motion'
import { laneSpan, type ClipCtx } from './PropertyControls'
import { useStore } from '../state/store'

const TPS = 254016000000
const EASY: [number, number] = [0, 1 / 3]

/** [speed, influence] a keyframe side behaves as now (twin of engine commands.motion.effective_handle). */
export function effectiveHandle(kfs: Kf[], i: number, side: 'in' | 'out'): [number, number] {
  const k = kfs[i]
  if (k.interp === 'auto_bezier') return [autoSpeed(kfs, i), 1 / 3]
  if (k.interp === 'bezier' || k.interp === 'ease' || k.interp === 'continuous_bezier') {
    const h = side === 'in' ? k.ease_in : k.ease_out
    if (h) return [h[0], h[1]]
    return k.interp === 'continuous_bezier' ? [autoSpeed(kfs, i), 1 / 3] : [...EASY]
  }
  const j = side === 'out' ? i + 1 : i - 1
  if (j < 0 || j >= kfs.length) return [0, 1 / 3]
  const n = kfs[j]
  const dt = Math.abs(n.t - k.t) / TPS
  if (dt <= 0) return [0, 1 / 3]
  if (Array.isArray(k.v)) return [Math.hypot(...k.v.map((x, d) => x - (n.v as number[])[d])) / dt, 1 / 3]
  const dv = side === 'out' ? (n.v as number) - (k.v as number) : (k.v as number) - (n.v as number)
  return [dv / dt, 1 / 3]
}

/** Local twin of the engine's with_ease, for live previews while dragging. */
function withEase(kfs: Kf[], i: number, side: 'in' | 'out', speed: number, influence: number): Kf[] {
  const out = kfs.map((k) => ({ ...k }))
  const k = out[i]
  k.ease_in = effectiveHandle(kfs, i, 'in')
  k.ease_out = effectiveHandle(kfs, i, 'out')
  if (side === 'in') k.ease_in = [speed, Math.min(1, Math.max(0.01, influence))]
  else k.ease_out = [speed, Math.min(1, Math.max(0.01, influence))]
  if (kfs[i].interp === 'auto_bezier' || kfs[i].interp === 'continuous_bezier') {
    // dragging an Auto/Continuous Bezier handle keeps both sides at one speed (engine twin)
    if (side === 'in') k.ease_out = [speed, k.ease_out[1]]
    else k.ease_in = [speed, k.ease_in[1]]
    k.interp = 'continuous_bezier'
  } else k.interp = 'bezier'
  return out
}

const magnitude = (v: number | number[]) => (Array.isArray(v) ? v : [v])

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setW(el.clientWidth))
    ro.observe(el)
    setW(el.clientWidth)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

type EaseFn = (t: number, side: 'in' | 'out', speed: number, influence: number) => void

const GRAPH_H = 64

/** Value graph (scalars) + Velocity graph for one animated property. */
export function KeyframeGraphs({ c, kfs, onEase }: { c: ClipCtx; kfs: Kf[]; onEase: EaseFn }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [live, setLive] = useState<Kf[] | null>(null)
  const frames = live ?? kfs
  const isPoint = Array.isArray(frames[0]?.v)
  const { clip } = c
  const view = useStore((s) => s.ecView)
  const [v0, v1] = laneSpan(c, view)
  const span = Math.max(1, v1 - v0)
  const xOfSeq = (seqT: number) => ((seqT - v0) / span) * width
  const xOfSrc = (src: number) => xOfSeq(seqTimeOf(clip, src))
  const srcOfX = (x: number) => sourceTime(clip, v0 + (x / Math.max(1, width)) * span)
  const step = Math.max(1, Math.round((span / Math.max(1, width)) * clip.speed))

  // sample value and velocity along the lane
  const n = Math.max(2, Math.floor(width / 2))
  const xs: number[] = []
  const vals: number[] = []
  const vels: number[] = []
  for (let i = 0; i <= n; i++) {
    const x = (i / n) * width
    const src = srcOfX(x)
    const a = interpolate(frames, src - step)
    const b = interpolate(frames, src + step)
    const v = interpolate(frames, src)
    xs.push(x)
    vals.push(isPoint ? 0 : (v as number))
    const da = magnitude(a)
    const db = magnitude(b)
    const d = isPoint ? Math.hypot(...db.map((y, k) => y - da[k])) : (db[0] - da[0])
    vels.push(d / ((2 * step) / TPS))
  }
  // handles in (time, value) and (time, speed) space
  type H = { i: number; side: 'in' | 'out'; t: number; speed: number; infl: number; dt: number; dim: boolean }
  const handles: H[] = []
  frames.forEach((k, i) => {
    for (const side of ['in', 'out'] as const) {
      const j = side === 'in' ? i - 1 : i + 1
      if (j < 0 || j >= frames.length) continue
      const [speed, infl] = effectiveHandle(frames, i, side)
      handles.push({ i, side, t: k.t, speed, infl, dt: Math.abs(frames[j].t - k.t), dim: !isBezier(k.interp) })
    }
  })
  const vRange = (arr: number[], extra: number[]) => {
    let lo = Math.min(...arr, ...extra)
    let hi = Math.max(...arr, ...extra)
    if (hi - lo < 1e-6) {
      lo -= 1
      hi += 1
    }
    const pad = (hi - lo) * 0.12
    return [lo - pad, hi + pad] as const
  }
  const valueHandleY = (h: H) => (frames[h.i].v as number) + (h.side === 'out' ? 1 : -1) * h.speed * h.infl * (h.dt / TPS)
  const [vlo, vhi] = vRange(vals, isPoint ? [] : handles.map(valueHandleY))
  const velMax = Math.max(1e-6, ...vels.map(Math.abs), ...handles.map((h) => Math.abs(h.speed))) * 1.15
  const yVal = (v: number) => GRAPH_H - ((v - vlo) / (vhi - vlo)) * GRAPH_H
  const yVel = (s: number) => GRAPH_H / 2 - (s / velMax) * (GRAPH_H / 2)
  const handleTime = (h: H) => h.t + (h.side === 'out' ? 1 : -1) * h.infl * h.dt

  const playX = xOfSeq(c.time)
  const playSrc = sourceTime(clip, Math.min(Math.max(c.time, clip.start), clip.end - 1))
  const speedNow = (() => {
    const a = magnitude(interpolate(frames, playSrc - step))
    const b = magnitude(interpolate(frames, playSrc + step))
    const d = isPoint ? Math.hypot(...b.map((y, k) => y - a[k])) : b[0] - a[0]
    return d / ((2 * step) / TPS)
  })()

  /** Drag a handle end; ``graph`` tells how the pointer maps back to speed. */
  const drag = (e: React.PointerEvent, h: H, graph: 'value' | 'velocity') => {
    e.stopPropagation()
    e.preventDefault()
    const svg = (e.currentTarget as SVGElement).ownerSVGElement!
    const rect = svg.getBoundingClientRect()
    let last: [number, number] = [h.speed, h.infl]
    const move = (ev: PointerEvent) => {
      const x = ev.clientX - rect.left
      const y = ev.clientY - rect.top
      const tt = srcOfX(Math.max(0, Math.min(width, x)))
      const infl = Math.min(1, Math.max(0.01, Math.abs(tt - h.t) / h.dt))
      let speed: number
      if (graph === 'velocity') speed = ((GRAPH_H / 2 - y) / (GRAPH_H / 2)) * velMax
      else {
        const v = vlo + ((GRAPH_H - y) / GRAPH_H) * (vhi - vlo)
        speed = ((v - (frames[h.i].v as number)) * (h.side === 'out' ? 1 : -1)) / (infl * (h.dt / TPS))
      }
      if (isPoint) speed = Math.abs(speed)
      last = [speed, infl]
      setLive(withEase(kfs, h.i, h.side, speed, infl))
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setLive(null)
      onEase(h.t, h.side, last[0], last[1])
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const path = (ys: number[]) => xs.map((x, i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${ys[i].toFixed(1)}`).join(' ')
  const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2))

  // one grid row per graph: labels on the left (Premiere shows the ranges and the speed there),
  // the curve on the keyframe lane
  const sizer = <div className="lm-ksizer" ref={ref} />
  return (
    <>
      {!isPoint && (
        <>
          <span className="lm-klabels">
            <span>{fmt(vhi)}</span>
            <span>{fmt(vlo)}</span>
          </span>
          <span className="lm-kcell">
            {sizer}
            {width > 0 && (
              <svg className="lm-kgraph value" width={width} height={GRAPH_H} data-graph="value">
                <path d={path(vals.map(yVal))} className="curve" />
                {handles.map((h, idx) => {
                  const kx = xOfSrc(h.t)
                  const ky = yVal(frames[h.i].v as number)
                  const hx = xOfSrc(handleTime(h))
                  const hy = yVal(valueHandleY(h))
                  return (
                    <g key={idx} className={h.dim ? 'dim' : ''}>
                      <line x1={kx} y1={ky} x2={hx} y2={hy} className="arm" />
                      <circle cx={hx} cy={hy} r={3.5} className="grip" data-handle={`${h.i}-${h.side}`} onPointerDown={(e) => drag(e, h, 'value')} />
                    </g>
                  )
                })}
                {frames.map((k, i) => <rect key={i} x={xOfSrc(k.t) - 3} y={yVal(k.v as number) - 3} width={6} height={6} className="key" />)}
                {playX >= 0 && playX <= width && <line x1={playX} x2={playX} y1={0} y2={GRAPH_H} className="head" />}
              </svg>
            )}
          </span>
        </>
      )}
      <span className="lm-klabels">
        <span>{fmt(velMax)}</span>
        <span className="speed">
          {t('Velocity')}: {fmt(speedNow)} /{t('sec')}
        </span>
        <span>{fmt(isPoint ? 0 : -velMax)}</span>
      </span>
      <span className="lm-kcell">
        {isPoint && sizer}
        {width > 0 && (
          <svg className="lm-kgraph velocity" width={width} height={GRAPH_H} data-graph="velocity">
            <line x1={0} x2={width} y1={GRAPH_H / 2} y2={GRAPH_H / 2} className="zero" />
            <path d={path(vels.map(yVel))} className="curve" />
            {handles.map((h, idx) => {
              const kx = xOfSrc(h.t)
              const hx = xOfSrc(handleTime(h))
              const y = yVel(h.speed)
              return (
                <g key={idx} className={h.dim ? 'dim' : ''}>
                  <line x1={kx} y1={y} x2={hx} y2={y} className="arm" />
                  <circle cx={hx} cy={y} r={3.5} className="grip" data-handle={`${h.i}-${h.side}`} onPointerDown={(e) => drag(e, h, 'velocity')} />
                </g>
              )
            })}
            {frames.map((k, i) => {
              const [si] = effectiveHandle(frames, i, i ? 'in' : 'out')
              return <rect key={i} x={xOfSrc(k.t) - 3} y={yVel(si) - 3} width={6} height={6} className="key" />
            })}
            {playX >= 0 && playX <= width && <line x1={playX} x2={playX} y1={0} y2={GRAPH_H} className="head" />}
          </svg>
        )}
      </span>
    </>
  )
}

/** Premiere's value slider under an expanded property. */
export function ValueSlider({ value, min, max, onCommit }: { value: number; min: number; max: number; onCommit: (v: number) => void }) {
  const [live, setLive] = useState<number | null>(null)
  const shown = live ?? value
  const fmt = (v: number) => (Number.isInteger(v) ? v.toFixed(1) : String(v))
  return (
    <div className="lm-vslider">
      <span className="lo">{fmt(min)}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={(max - min) / 1000}
        value={Math.max(min, Math.min(max, shown))}
        onChange={(e) => {
          const v = Math.round(Number(e.target.value) * 100) / 100
          setLive(v)
          onCommit(v)
        }}
        onPointerUp={() => {
          if (live !== null) onCommit(Math.round(live * 100) / 100)
          setLive(null)
        }}
        onKeyUp={() => {
          if (live !== null) onCommit(Math.round(live * 100) / 100)
          setLive(null)
        }}
      />
      <span className="hi">{fmt(max)}</span>
    </div>
  )
}

/** Premiere's angle dial (rotation, skew axis): drag around it; whole turns accumulate. */
export function AngleDial({ value, onCommit }: { value: number; onCommit: (v: number) => void }) {
  const [live, setLive] = useState<number | null>(null)
  const shown = live ?? value
  const a = ((shown - 90) * Math.PI) / 180
  return (
    <svg
      className="lm-dial"
      width={30}
      height={30}
      onPointerDown={(e) => {
        const r = (e.currentTarget as SVGElement).getBoundingClientRect()
        const cx = r.left + r.width / 2
        const cy = r.top + r.height / 2
        const ang = (ev: { clientX: number; clientY: number }) => (Math.atan2(ev.clientY - cy, ev.clientX - cx) * 180) / Math.PI
        let prev = ang(e)
        let acc = value
        const preview = liveValue(onCommit)
        const move = (ev: PointerEvent) => {
          const cur = ang(ev)
          let d = cur - prev
          if (d > 180) d -= 360
          if (d < -180) d += 360
          prev = cur
          acc += d
          setLive(Math.round(acc * 10) / 10)
          preview.update(Math.round(acc * 10) / 10)
        }
        const up = () => {
          window.removeEventListener('pointermove', move)
          window.removeEventListener('pointerup', up)
          setLive(null)
          preview.finish(Math.round(acc * 10) / 10)
        }
        window.addEventListener('pointermove', move)
        window.addEventListener('pointerup', up)
      }}
    >
      <circle cx={15} cy={15} r={12} className="ring" />
      <line x1={15} y1={15} x2={15 + 11 * Math.cos(a)} y2={15 + 11 * Math.sin(a)} className="needle" />
    </svg>
  )
}
