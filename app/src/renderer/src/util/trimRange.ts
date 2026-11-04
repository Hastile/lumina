import type { Clip, Sequence } from '../engine/document'

/** Legal timeline delta, rounded inward so previews cannot exceed source handles. */
export function trimRange(seq: Sequence, ids: string[], edge: 'start' | 'end', ripple: boolean,
  frame: number, limitOf: (clip: Clip) => number | null): [number, number] {
  let lo = -Infinity
  let hi = Infinity
  for (const track of Object.values(seq.tracks)) {
    for (const clip of Object.values(track.clips)) {
      if (!ids.includes(clip.id)) continue
      if (track.locked) return [0, 0]
      const length = clip.end - clip.start
      if (edge === 'start') {
        hi = Math.min(hi, length - frame)
        if (!ripple) lo = Math.max(lo, -clip.start)
      } else lo = Math.max(lo, frame - length)
      if (clip.freeze == null) {
        const limit = limitOf(clip)
        const source = edge === 'start' ? clip.source_in : clip.source_in + Math.round(length * clip.speed)
        if (clip.speed > 0) {
          lo = Math.max(lo, -source / clip.speed)
          if (limit != null) hi = Math.min(hi, (limit - source) / clip.speed)
        } else if (clip.speed < 0) {
          hi = Math.min(hi, -source / clip.speed)
          if (limit != null) lo = Math.max(lo, (limit - source) / clip.speed)
        }
      }
      if (!ripple) for (const other of Object.values(track.clips)) {
        if (ids.includes(other.id)) continue
        if (edge === 'start' && other.end <= clip.start) lo = Math.max(lo, other.end - clip.start)
        if (edge === 'end' && other.start >= clip.end) hi = Math.min(hi, other.start - clip.end)
      }
    }
  }
  return [Math.ceil(lo / frame) * frame, Math.floor(hi / frame) * frame]
}
