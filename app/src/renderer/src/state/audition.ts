// Edit in Adobe Audition (Premiere's Edit > Edit in Adobe Audition). The engine picks the file each clip is
// edited in: the clip's own file when it plays all of it untouched, otherwise a rendered
// "<clip> Audio Extracted.wav" the clip switches to (one undo step; the file stays on disk). Audition opens
// the files, and the engine's hot reload picks up each save.
import { activeSequence, exec, findClip, get, toast } from './store'

/** The selected clips Audition can edit: audio clips of media files. */
export function auditionClips(s = get()): string[] {
  const seq = activeSequence(s)
  if (!seq) return []
  return s.selectedClips.filter((id) => {
    const hit = findClip(seq, id)
    const media = hit?.clip.media_id ? s.doc?.media[hit.clip.media_id] : null
    return hit?.track.kind === 'audio' && !!media?.audio
  })
}

export async function editClipsInAudition(clipIds = auditionClips()): Promise<void> {
  if (!clipIds.length) return
  const r = await exec<{ clips: { path: string }[] }>('clip.prepare_audio_edit', { clip_ids: clipIds }, 'Edit in Adobe Audition')
  if (r) await openInAudition([...new Set(r.clips.map((c) => c.path))])
}

/** A project item: its file, as it is. */
export async function openInAudition(paths: string[]): Promise<void> {
  if (!paths.length) return
  const res = await window.lumina.openInAudition(paths)
  if (!res.ok) toast(res.error ?? 'Adobe Audition을 열 수 없습니다.')
}
