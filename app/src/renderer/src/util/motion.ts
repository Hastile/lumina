// Twin of engine/lumina/motion.py; both are checked against
// shared/test-vectors/keyframes.json. Keyframe times are source ticks.

import type { Clip, MediaItem, Sequence } from '../engine/document'

export type KfValue = number | number[]
export type Kf = { t: number; v: KfValue; interp: 'linear' | 'ease' | 'hold' }

export const KEYFRAME_PROPS = [
  'position', 'scale', 'scale_width', 'rotation', 'anchor',
  'crop_left', 'crop_top', 'crop_right', 'crop_bottom', 'opacity', 'volume_db'
] as const
export type KeyframeProp = (typeof KEYFRAME_PROPS)[number]

export const sourceTime = (clip: Clip, seqTime: number): number => clip.source_in + Math.round((seqTime - clip.start) * clip.speed)

/** Inverse of sourceTime (sequence time where a source-time keyframe sits). */
export const seqTimeOf = (clip: Clip, srcTime: number): number => clip.start + (srcTime - clip.source_in) / clip.speed

const ease = (u: number) => u * u * (3 - 2 * u)

export function interpolate(kfs: Kf[], t: number): KfValue {
  if (t <= kfs[0].t) return kfs[0].v
  const last = kfs[kfs.length - 1]
  if (t >= last.t) return last.v
  for (let i = 0; i < kfs.length - 1; i++) {
    const a = kfs[i]
    const b = kfs[i + 1]
    if (a.t <= t && t < b.t) {
      if (a.interp === 'hold') return a.v
      let u = (t - a.t) / (b.t - a.t)
      if (a.interp === 'ease' || b.interp === 'ease') u = ease(u)
      if (Array.isArray(a.v)) return a.v.map((x, j) => x + ((b.v as number[])[j] - x) * u)
      return (a.v as number) + ((b.v as number) - (a.v as number)) * u
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
