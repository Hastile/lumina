// Twin of engine/lumina/motion.py; both are checked against
// shared/test-vectors/keyframes.json. Keyframe times are source ticks.

import type { Clip, MediaItem, Sequence } from '../engine/document'

export type KfValue = number | number[]
export type Kf = {
  t: number
  v: KfValue
  interp: 'linear' | 'ease' | 'hold' | 'bezier'
  ease_in?: number[] | null
  ease_out?: number[] | null
  tangent_in?: number[] | null
  tangent_out?: number[] | null
}

export const KEYFRAME_PROPS = [
  'position', 'scale', 'scale_width', 'rotation', 'anchor',
  'crop_left', 'crop_top', 'crop_right', 'crop_bottom', 'opacity', 'volume_db'
] as const
export type KeyframeProp = (typeof KEYFRAME_PROPS)[number]

export const sourceTime = (clip: Clip, seqTime: number): number => clip.source_in + Math.round((seqTime - clip.start) * clip.speed)

/** Inverse of sourceTime (sequence time where a source-time keyframe sits). */
export const seqTimeOf = (clip: Clip, srcTime: number): number => clip.start + (srcTime - clip.source_in) / clip.speed

const TICKS_PER_SECOND = 254016000000
const EASY_EASE: [number, number] = [0, 1 / 3] // Premiere's Easy Ease: speed 0, influence 33.3 %

function handle(k: Kf, side: 'in' | 'out'): [number, number] | null {
  if (k.interp !== 'bezier' && k.interp !== 'ease') return null
  const h = side === 'in' ? k.ease_in : k.ease_out
  if (h) return [h[0], Math.min(1, Math.max(0, h[1]))]
  return EASY_EASE
}

const cubic = (p0: number, p1: number, p2: number, p3: number, u: number) => {
  const v = 1 - u
  return v * v * v * p0 + 3 * v * v * u * p1 + 3 * v * u * u * p2 + u * u * u * p3
}

/** Temporal Bezier (After Effects model): distance covered at time fraction u. */
function progress(a: Kf, b: Kf, u: number, dist: number): number {
  const ha = handle(a, 'out')
  const hb = handle(b, 'in')
  if (!ha && !hb) return u * dist
  const dt = (b.t - a.t) / TICKS_PER_SECOND
  const [x1, y1] = ha ? [ha[1], ha[0] * ha[1] * dt] : [1 / 3, dist / 3]
  const [x2, y2] = hb ? [1 - hb[1], dist - hb[0] * hb[1] * dt] : [2 / 3, (2 * dist) / 3]
  let lo = 0
  let hi = 1
  for (let i = 0; i < 48; i++) {
    const mid = (lo + hi) / 2
    if (cubic(0, x1, x2, 1, mid) < u) lo = mid
    else hi = mid
  }
  return cubic(0, y1, y2, dist, (lo + hi) / 2)
}

const PATH_STEPS = 64
const dist = (p: number[], q: number[]) => Math.hypot(...p.map((x, i) => x - q[i]))

function pointAt(a: Kf, b: Kf, u: number): number[] {
  const pa = a.v as number[]
  const pb = b.v as number[]
  const to = a.tangent_out
  const ti = b.tangent_in
  if (!to && !ti) {
    const d = dist(pa, pb)
    if (d === 0) return [...pa]
    const f = progress(a, b, u, d) / d
    return pa.map((x, i) => x + (pb[i] - x) * f)
  }
  const q = [pa, pa.map((x, i) => x + (to?.[i] ?? 0)), pb.map((x, i) => x + (ti?.[i] ?? 0)), pb]
  const pts: number[][] = []
  for (let i = 0; i <= PATH_STEPS; i++) pts.push(pa.map((_, j) => cubic(q[0][j], q[1][j], q[2][j], q[3][j], i / PATH_STEPS)))
  const cum = [0]
  for (let i = 0; i < PATH_STEPS; i++) cum.push(cum[i] + dist(pts[i], pts[i + 1]))
  const total = cum[PATH_STEPS]
  if (total === 0) return [...pa]
  const d = Math.min(Math.max(progress(a, b, u, total), 0), total)
  for (let i = 0; i < PATH_STEPS; i++) {
    if (cum[i + 1] >= d) {
      const seg = cum[i + 1] - cum[i]
      const f = seg ? (d - cum[i]) / seg : 0
      return pts[i].map((x, j) => x + (pts[i + 1][j] - x) * f)
    }
  }
  return pts[PATH_STEPS]
}

export function interpolate(kfs: Kf[], t: number): KfValue {
  if (t <= kfs[0].t) return kfs[0].v
  const last = kfs[kfs.length - 1]
  if (t >= last.t) return last.v
  for (let i = 0; i < kfs.length - 1; i++) {
    const a = kfs[i]
    const b = kfs[i + 1]
    if (a.t <= t && t < b.t) {
      if (a.interp === 'hold') return a.v
      const u = (t - a.t) / (b.t - a.t)
      if (Array.isArray(a.v)) return pointAt(a, b, u)
      const dv = (b.v as number) - (a.v as number)
      if (!handle(a, 'out') && !handle(b, 'in')) return (a.v as number) + dv * u
      return (a.v as number) + progress(a, b, u, dv)
    }
  }
  return last.v
}

export function sourceSize(seq: Sequence, media: MediaItem | null | undefined): [number, number] {
  if (media?.video) return [media.video.width, media.video.height]
  return [seq.settings.width, seq.settings.height]
}

export function staticValue(clip: Clip, prop: KeyframeProp, seq: Sequence, media?: MediaItem | null): KfValue {
  const m = clip.motion
  switch (prop) {
    case 'opacity':
      return clip.opacity
    case 'volume_db':
      return clip.volume_db
    case 'position':
      return m.position ?? [seq.settings.width / 2, seq.settings.height / 2]
    case 'anchor': {
      if (m.anchor) return m.anchor
      const [w, h] = sourceSize(seq, media)
      return [w / 2, h / 2]
    }
    default:
      return m[prop]
  }
}

export function keyframesOf(clip: Clip, prop: KeyframeProp): Kf[] | undefined {
  const k = (clip.keyframes as Record<string, Kf[] | undefined>)[prop]
  return k && k.length ? k : undefined
}

export function valueAt(clip: Clip, prop: KeyframeProp, seqTime: number, seq: Sequence, media?: MediaItem | null): KfValue {
  const kfs = keyframesOf(clip, prop)
  return kfs ? interpolate(kfs, sourceTime(clip, seqTime)) : staticValue(clip, prop, seq, media)
}

export type Resolved = {
  position: number[]
  anchor: number[]
  scaleX: number
  scaleY: number
  rotation: number
  opacity: number
  crop: [number, number, number, number] // left, top, right, bottom %
}

export function transformAt(clip: Clip, seqTime: number, seq: Sequence, media?: MediaItem | null): Resolved {
  const v = (p: KeyframeProp) => valueAt(clip, p, seqTime, seq, media)
  const scale = v('scale') as number
  const uniform = clip.motion.uniform
  return {
    position: v('position') as number[],
    anchor: v('anchor') as number[],
    // with Uniform Scale off, Premiere's "Scale" is the height and "Scale Width" the width
    scaleX: uniform ? scale : (v('scale_width') as number),
    scaleY: scale,
    rotation: v('rotation') as number,
    opacity: v('opacity') as number,
    crop: [v('crop_left') as number, v('crop_top') as number, v('crop_right') as number, v('crop_bottom') as number]
  }
}

/** CSS for an element of the source's natural pixel size placed in sequence pixel space. */
export function transformCss(r: Resolved): React.CSSProperties {
  const [px, py] = r.position
  const [ax, ay] = r.anchor
  const [cl, ct, cr, cb] = r.crop
  return {
    transformOrigin: `${ax}px ${ay}px`,
    transform: `translate(${px - ax}px, ${py - ay}px) rotate(${r.rotation}deg) scale(${r.scaleX / 100}, ${r.scaleY / 100})`,
    opacity: Math.max(0, Math.min(1, r.opacity / 100)),
    clipPath: cl || ct || cr || cb ? `inset(${ct}% ${cr}% ${cb}% ${cl}%)` : undefined
  }
}
