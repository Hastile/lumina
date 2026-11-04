import type { MediaItem } from '../engine/document'
import { snapToFrame, ticksPerFrame, TICKS_PER_SECOND, type Rate } from './time'

/** Match clip.place's source marks and frame-boundary handling. */
export function sourceDropDuration(media: MediaItem, rate: Rate): number {
  const still = media.kind === 'image'
  const start = still ? 0 : media.in_point || 0
  const end = still ? 5 * TICKS_PER_SECOND : Math.min(media.out_point || media.duration, media.duration)
  let duration = snapToFrame(end - start, rate)
  if (end > start && duration === 0) duration = ticksPerFrame(rate)
  if (!still && start + duration > media.duration) duration -= ticksPerFrame(rate)
  return Math.max(0, duration)
}
