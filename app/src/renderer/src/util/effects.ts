// Twin of engine/lumina/effects.py (checked against shared/test-vectors/effects.json).
import type { Clip, Effect, Track, Transition } from '../engine/document'
import { interpolate, sourceTime, type Kf } from './motion'

/** 'level': a number that is not keyframed (audio effect settings). */
export type ParamKind = 'point' | 'number' | 'bool' | 'choice' | 'level'
export type ParamSpec = { name: string; kind: ParamKind; default: number | boolean | null; label: string; min?: number; max?: number; unit?: string }

export const EFFECTS: Record<Effect['type'], { label: string; category: string; media?: 'audio'; params: ParamSpec[] }> = {
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
  },
  parametric_eq: {
    label: 'Parametric Equalizer',
    category: 'Filter and EQ',
    media: 'audio',
    params: [
      { name: 'low_freq', kind: 'level', default: 100, label: 'Low Frequency', min: 20, max: 2000, unit: 'Hz' },
      { name: 'low_gain', kind: 'level', default: 0, label: 'Low Gain', min: -30, max: 30, unit: 'dB' },
      { name: 'mid_freq', kind: 'level', default: 1000, label: 'Mid Frequency', min: 40, max: 16000, unit: 'Hz' },
      { name: 'mid_gain', kind: 'level', default: 0, label: 'Mid Gain', min: -30, max: 30, unit: 'dB' },
      { name: 'mid_q', kind: 'level', default: 1, label: 'Mid Q', min: 0.1, max: 30, unit: '' },
      { name: 'high_freq', kind: 'level', default: 8000, label: 'High Frequency', min: 1000, max: 20000, unit: 'Hz' },
      { name: 'high_gain', kind: 'level', default: 0, label: 'High Gain', min: -30, max: 30, unit: 'dB' }
    ]
  },
  compressor: {
    label: 'Single-band Compressor',
    category: 'Amplitude and Compression',
    media: 'audio',
    params: [
      { name: 'threshold', kind: 'level', default: -20, label: 'Threshold', min: -60, max: 0, unit: 'dB' },
      { name: 'ratio', kind: 'level', default: 4, label: 'Ratio', min: 1, max: 20, unit: ':1' },
      { name: 'attack', kind: 'level', default: 10, label: 'Attack', min: 0, max: 1000, unit: 'ms' },
      { name: 'release', kind: 'level', default: 100, label: 'Release', min: 0, max: 3000, unit: 'ms' },
      { name: 'output_gain', kind: 'level', default: 0, label: 'Output Gain', min: -30, max: 30, unit: 'dB' }
    ]
  },
  hard_limiter: {
    label: 'Hard Limiter',
    category: 'Amplitude and Compression',
    media: 'audio',
    params: [
      { name: 'max_amplitude', kind: 'level', default: -0.1, label: 'Maximum Amplitude', min: -30, max: 0, unit: 'dB' },
      { name: 'input_boost', kind: 'level', default: 0, label: 'Input Boost', min: 0, max: 30, unit: 'dB' }
    ]
  }
}

export const effectMedia = (type: Effect['type']): 'audio' | 'video' => EFFECTS[type].media ?? 'video'

/** Web Audio's automatic compressor makeup (spec: (1 / fullRangeGain) ^ 0.6, knee 0); the preview
 * cancels it so it matches the export (engine/lumina/audio_fx.py). */
export const compressorMakeupDb = (threshold: number, ratio: number): number => -0.6 * threshold * (1 - 1 / ratio)

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
  const spec = EFFECTS[effect.type].params.find((p) => p.name === name)
  if (!spec) return 0 // not a parameter of this effect
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

