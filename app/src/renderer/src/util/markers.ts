// Clip markers live on the media (Premiere's master clip markers, in source time): every clip of the
// media shows the ones inside it, where their source time plays (engine: timeline.clip_markers).
import type { Clip, Marker, MediaItem } from '../engine/document'

export type PlacedMarker = { marker: Marker; t: number }

export function clipMarkers(clip: Clip, media: MediaItem | undefined): PlacedMarker[] {
  if (!media?.markers || !clip.speed) return []
  const out: PlacedMarker[] = []
  for (const marker of Object.values(media.markers)) {
    const t = clip.start + Math.round((marker.start - clip.source_in) / clip.speed)
    if (clip.start <= t && t < clip.end) out.push({ marker, t })
  }
  return out.sort((a, b) => a.t - b.t)
}

/** The clip's marker nearest to a time (the one an "align to playhead" means). */
export function nearestClipMarker(clip: Clip, media: MediaItem | undefined, at: number): PlacedMarker | null {
  let best: PlacedMarker | null = null
  for (const pm of clipMarkers(clip, media)) if (!best || Math.abs(pm.t - at) < Math.abs(best.t - at)) best = pm
  return best
}
