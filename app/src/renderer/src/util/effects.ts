// Twin of engine/lumina/effects.py (checked against shared/test-vectors/effects.json).
import type { Clip, Effect, Track, Transition } from '../engine/document'
import { interpolate, sourceTime, type Kf } from './motion'

export type ParamKind = 'point' | 'number' | 'bool' | 'choice'
export type ParamSpec = { name: string; kind: ParamKind; default: number | boolean | null; label: string; min?: number; max?: number }

export const EFFECTS: Record<Effect['type'], { label: string; category: string; params: ParamSpec[] }> = {
  transform: {
    label: 'Transform',
    category: 'Distort',
    params: [
      { name: 'anchor', kind: 'point', default: null, label: 'Anchor Point' },
      { name: 'position', kind: 'point', default: null, label: 'Position' },
      { name: 'uniform', kind: 'bool', default: true, label: 'Uniform Scale' },
      { name: 'scale', kind: 'number', default: 100, label: 'Scale Height', min: 0, max: 10000 },
      { name: 'scale_width', kind: 'number', default: 100, label: 'Scale Width', min: 0, max: 10000 },
      { name: 'skew', kind: 'number', default: 0, label: 'Skew', min: -85, max: 85 },
      { name: 'skew_axis', kind: 'number', default: 0, label: 'Skew Axis' },
      { name: 'rotation', kind: 'number', default: 0, label: 'Rotation' },
      { name: 'opacity', kind: 'number', default: 100, label: 'Opacity', min: 0, max: 100 }
    ]
  },
  gaussian_blur: {
    label: 'Gaussian Blur',
    category: 'Blur & Sharpen',
    params: [
      { name: 'blurriness', kind: 'number', default: 0, label: 'Blurriness', min: 0, max: 1000 },
      { name: 'dimensions', kind: 'choice', default: 0, label: 'Blur Dimensions' },
      { name: 'repeat_edge', kind: 'bool', default: false, label: 'Repeat Edge Pixels' }
    ]
  }
}

export const TRANSITIONS: Record<Transition['type'], { label: string; kind: 'video' | 'audio'; category: string }> = {
  cross_dissolve: { label: 'Cross Dissolve', kind: 'video', category: 'Dissolve' },
  dip_to_black: { label: 'Dip to Black', kind: 'video', category: 'Dissolve' },
  dip_to_white: { label: 'Dip to White', kind: 'video', category: 'Dissolve' },
  constant_power: { label: 'Constant Power', kind: 'audio', category: 'Crossfade' }
}

export const BLUR_SIGMA_PER_UNIT = 1 / 3

type PV = number | boolean | number[]

export function effectParam(effect: Effect, name: string, clip: Clip, seqTime: number, w = 1920, h = 1080): PV {
  const kfs = (effect.keyframes as Record<string, Kf[] | undefined>)[name]
  if (kfs && kfs.length) return interpolate(kfs, sourceTime(clip, seqTime))
  const v = (effect.params as Record<string, PV | undefined>)[name]
  if (v !== undefined) return v
  const spec = EFFECTS[effect.type].params.find((p) => p.name === name)!
  return spec.kind === 'point' && spec.default === null ? [w / 2, h / 2] : (spec.default as PV)
}

// 2x3 affine, row-major [a b c / d e f]: x' = a x + b y + c, y' = d x + e y + f
export type Affine = [number, number, number, number, number, number]
export const IDENTITY: Affine = [1, 0, 0, 0, 1, 0]

export function mul(m: Affine, n: Affine): Affine {
  const [a, b, c, d, e, f] = m
  const [A, B, C, D, E, F] = n
  return [a * A + b * D, a * B + b * E, a * C + b * F + c, d * A + e * D, d * B + e * E, d * C + e * F + f]
}

export const applyAffine = (m: Affine, x: number, y: number): [number, number] => [m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]]

const rad = (d: number) => (d * Math.PI) / 180

export function trs(position: number[], anchor: number[], sx: number, sy: number, rotation: number, skew = 0, skewAxis = 0): Affine {
  let m: Affine = [1, 0, -anchor[0], 0, 1, -anchor[1]]
  m = mul([sx, 0, 0, 0, sy, 0], m)
  if (skew) {
    const ax = rad(skewAxis)
    const k = Math.tan(rad(Math.max(-85, Math.min(85, skew))))
    const ca = Math.cos(ax)
    const sa = Math.sin(ax)
    m = mul([ca, -sa, 0, sa, ca, 0], mul([1, -k, 0, 0, 1, 0], mul([ca, sa, 0, -sa, ca, 0], m)))
  }
  const cr = Math.cos(rad(rotation))
  const sr = Math.sin(rad(rotation))
  m = mul([cr, -sr, 0, sr, cr, 0], m)
  return mul([1, 0, position[0], 0, 1, position[1]], m)
}

export function transformEffectAt(effect: Effect, clip: Clip, t: number, w: number, h: number): { m: Affine; opacity: number } {
  const v = (n: string) => effectParam(effect, n, clip, t, w, h)
  const sy = (v('scale') as number) / 100
  const sx = v('uniform') ? sy : (v('scale_width') as number) / 100
  const m = trs(v('position') as number[], v('anchor') as number[], sx, sy, v('rotation') as number, v('skew') as number, v('skew_axis') as number)
  return { m, opacity: Math.max(0, Math.min(1, (v('opacity') as number) / 100)) }
}

export const cssMatrix = (m: Affine) => `matrix(${m[0]}, ${m[3]}, ${m[1]}, ${m[4]}, ${m[2]}, ${m[5]})`

/** CSS for one effect wrapper (outer = applied later). */
export function effectCss(effect: Effect, clip: Clip, t: number, w: number, h: number): React.CSSProperties | null {
  if (!effect.enabled) return null
  if (effect.type === 'transform') {
    const { m, opacity } = transformEffectAt(effect, clip, t, w, h)
    return { transform: cssMatrix(m), transformOrigin: '0 0', opacity }
  }
  const s = Math.max(0, effectParam(effect, 'blurriness', clip, t) as number) * BLUR_SIGMA_PER_UNIT
  return s > 0.05 ? { filter: `blur(${s}px)` } : null
}

// ---------------------------------------------------------------- transitions

export type TransitionWindow = { start: number; end: number; tr: Transition; a: Clip | null; b: Clip | null }

export function transitionWindow(track: Track, tr: Transition): TransitionWindow | null {
  const a = tr.clip_a ? track.clips[tr.clip_a] ?? null : null
  const b = tr.clip_b ? track.clips[tr.clip_b] ?? null : null
  if ((tr.clip_a && !a) || (tr.clip_b && !b) || (!a && !b)) return null
  if (a && b && a.end !== b.start) return null
  const cut = a ? a.end : b!.start
  const d = Math.max(1, tr.duration)
  const align = !a ? 'start' : !b ? 'end' : tr.alignment
  if (align === 'center') {
    const s = cut - Math.floor(d / 2)
    return { start: s, end: s + d, tr, a, b }
  }
  return align === 'start' ? { start: cut, end: cut + d, tr, a, b } : { start: cut - d, end: cut, tr, a, b }
}

export function trackTransitions(track: Track): TransitionWindow[] {
  return Object.values(track.transitions ?? {})
    .map((tr) => transitionWindow(track, tr))
    .filter((w): w is TransitionWindow => !!w)
    .sort((x, y) => x.start - y.start)
}

/** Progress at a frame (sampled at the frame centre, like the export). */
export const transitionProgress = (w: TransitionWindow, t: number, tpf: number) => Math.max(0, Math.min(1, (t + tpf / 2 - w.start) / Math.max(1, w.end - w.start)))

