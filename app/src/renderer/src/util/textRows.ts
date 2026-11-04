import type { Clip, Sequence } from '../engine/document'
export type TextRow = { clip: Clip; layerId?: string; text: string; track: string; trackName: string; trackLabel: string; locked: boolean; kind: 'captions' | 'graphics' }
export function sequenceTextRows(seq: Sequence): TextRow[] {
  const rows: TextRow[] = []
  for (const [index, tid] of seq.video_tracks.entries()) {
    const track = seq.tracks[tid]
    if (!track) continue
    const base = { track: tid, trackName: track.name, trackLabel: `V${index + 1}`, locked: track.locked }
    for (const clip of Object.values(track.clips)) {
      if (clip.caption) rows.push({ ...base, clip, text: clip.caption.text, kind: 'captions' })
      for (const layer of clip.graphic?.layers ?? []) {
        if ((layer.type ?? 'text') === 'text' && 'text' in layer) rows.push({ ...base, clip, layerId: layer.id, text: layer.text, kind: 'graphics' })
      }
    }
  }
  return rows.sort((a, b) => a.clip.start - b.clip.start || a.trackLabel.localeCompare(b.trackLabel, undefined, { numeric: true }) || (a.layerId ?? '').localeCompare(b.layerId ?? ''))
}
// DOM selection offsets are UTF-16, Python offsets are Unicode codepoints.
export const textCursorOffset = (text: string, offset: number): number => Array.from(text.slice(0, offset)).length
