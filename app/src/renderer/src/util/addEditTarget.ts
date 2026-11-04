import type { Sequence } from '../engine/document'

/** A selection is authoritative: never fall back to unrelated targeted tracks. */
export function addEditTarget(seq: Sequence, selected: string[], time: number) {
  const tracks = Object.values(seq.tracks).filter(track => !track.locked && (
    selected.length
      ? Object.values(track.clips).some(c => selected.includes(c.id) && c.start < time && time < c.end)
      : track.targeted
  ))
  return tracks.map(track => track.id)
}
