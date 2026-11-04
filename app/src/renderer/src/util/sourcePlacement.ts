import type { MediaItem, Sequence } from '../engine/document'

/** The track source video / audio is patched to (Insert/Overwrite), null = unpatched. */
export function patchedTrack(seq: Sequence, kind: 'video' | 'audio'): string | null {
  const set_ = kind === 'video' ? seq.source_video_track : seq.source_audio_track
  if (set_ === '') return null
  if (set_ && seq.tracks[set_]) return set_
  const order = kind === 'video' ? seq.video_tracks : seq.audio_tracks
  const usable = order.filter((id) => !seq.tracks[id].locked && !seq.tracks[id].caption_style)
  return usable.find((id) => seq.tracks[id].targeted) ?? usable[0] ?? null
}


/** Audio-only files can be placed even when source audio is unpatched. */
export function sourcePlacement(seq: Sequence, drop?: { video?: string; audio?: string }, media?: MediaItem) {
  const audioOnly = !!media?.audio && !media.video
  const video = audioOnly ? null : patchedTrack(seq, 'video')
  const audio = audioOnly
    ? drop?.audio ?? patchedTrack({ ...seq, source_audio_track: seq.source_audio_track || null }, 'audio')
    : patchedTrack(seq, 'audio')
  return {
    video_track_id: video ? (drop?.video ?? video) : null,
    audio_track_id: audio ? (drop?.audio ?? audio) : null,
    take_video: !!video,
    take_audio: !!audio
  }
}
