import { exec, findClip, get, sourceMedia, toast } from './store'
import { TICKS_PER_SECOND } from '../util/time'

export function sourceRange(s = get()) {
  const media = sourceMedia(s)
  const binding = s.sourceClip
  const seq = binding && s.doc?.sequences[binding.sequenceId]
  const clip = seq && findClip(seq, binding!.clipId)?.clip
  const bound = clip?.media_id === media?.id ? clip : null
  const duration = media?.duration || 5 * TICKS_PER_SECOND
  const other = bound ? bound.source_in + Math.round((bound.end - bound.start) * bound.speed) : 0
  return {
    clip: bound,
    inPoint: bound ? Math.min(bound.source_in, other) : media?.in_point ?? 0,
    outPoint: bound ? Math.max(bound.source_in, other) : media?.out_point ?? duration
  }
}

/** Timeline source edits trim only the opened instance; rejected trims leave it intact. */
export async function setSourceEdge(edge: 'in' | 'out', value: number) {
  const s = get()
  const media = sourceMedia(s)
  if (!media) return
  const range = sourceRange(s)
  if (s.sourceClip) {
    const c = range.clip
    if (!c || c.speed <= 0 || c.freeze != null) {
      toast('이 클립의 소스 범위 변경은 건너뛰었습니다.', 'info')
      return
    }
    await exec('clip.trim', {
      sequence_id: s.sourceClip.sequenceId, clip_id: c.id,
      edge: edge === 'in' ? 'start' : 'end',
      delta: Math.round((value - (edge === 'in' ? range.inPoint : range.outPoint)) / c.speed),
      ripple: false, include_linked: false
    }, 'Edit Source Range')
  } else {
    await exec('media.set_marks', { media_id: media.id, [edge === 'in' ? 'in_point' : 'out_point']: value }, 'Edit Source Range')
  }
}
