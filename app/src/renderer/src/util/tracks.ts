// Twin of engine/lumina/tracks.py: caption cues drawn as a text graphic in their
// track's style, track-effect timing, and the media time a clip shows (reverse
// speed, Frame Hold).

import type { Clip, Effect, Graphic, Track } from '../engine/document'

export const CAPTION_LAYER_ID = 'caption'
const STYLE_FIELDS = [
  'font_family', 'font_size', 'bold', 'italic', 'color', 'stroke_color', 'stroke_width', 'background', 'align',
  'position', 'box', 'vertical_align', 'tracking', 'shadow', 'scale', 'rotation', 'opacity'
] as const

export const isCaptionTrack = (track: Track | null | undefined): boolean => !!track?.caption_style

/** What a clip draws as a graphic: its own graphic, or a caption cue in its track's style. */
export function clipGraphic(clip: Clip, track: Track | null | undefined): Graphic | null {
  if (clip.graphic) return clip.graphic
  const cap = clip.caption
  if (!cap) return null
  const style = (track?.caption_style ?? {}) as Record<string, unknown>
  const layer: Record<string, unknown> = {}
  for (const k of STYLE_FIELDS) if (k in style) layer[k] = style[k]
  for (const [k, v] of Object.entries(cap.style ?? {})) if ((STYLE_FIELDS as readonly string[]).includes(k)) layer[k] = v
  return {
    layers: [{ ...layer, id: CAPTION_LAYER_ID, type: 'text', text: cap.text, runs: null, visible: true, approximate: false } as Graphic['layers'][number]],
    source: 'caption'
  }
}

/** Clip-like timing whose source time is a track effect's keyframe time (see tracks.py). */
export type Timing = Pick<Clip, 'start' | 'end' | 'source_in' | 'speed'>

export function timingClip(effect: Effect, clip: Timing): Timing {
  const anchor = effect.anchor ?? 'in'
  if (anchor === 'out') return { start: clip.end, end: clip.end + 1, source_in: 0, speed: 1 }
  if (anchor === 'stretch') {
    const len = Math.max(1, clip.end - clip.start)
    return { start: clip.start, end: clip.end, source_in: 0, speed: (effect.ref_duration ?? len) / len }
  }
  return { start: clip.start, end: clip.end, source_in: 0, speed: 1 }
}

export const activeTrackEffects = (track: Track | null | undefined): Effect[] => (track?.effects ?? []).filter((e) => e.enabled)

const REVERSE_EPSILON = 254_016_000 // 1 ms

/** The media time (ticks) a clip shows at a source time: Frame Hold pins it; a reversed clip
 * (covering [source at end, source_in)) shows the frame just before. */
export function mediaTime(clip: Clip, sourceT: number): number {
  if (clip.freeze !== null && clip.freeze !== undefined) return clip.freeze
  return clip.speed < 0 ? Math.max(0, sourceT - REVERSE_EPSILON) : sourceT
}
