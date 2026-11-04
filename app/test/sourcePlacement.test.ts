import { expect, it } from 'vitest'
import type { MediaItem, Sequence } from '../src/renderer/src/engine/document'
import { patchedTrack, sourcePlacement } from '../src/renderer/src/util/sourcePlacement'

function sequence(patch: Partial<Sequence> = {}): Sequence {
  return {
    video_tracks: ['v1'], audio_tracks: ['a1', 'a2'],
    tracks: {
      v1: { targeted: true }, a1: { targeted: true }, a2: { targeted: false }
    }, ...patch
  } as unknown as Sequence
}

const music = { audio: {}, video: null } as MediaItem

it('places audio-only files on the dropped track with both source patches disabled', () => {
  const seq = sequence({ source_video_track: '', source_audio_track: '' })
  expect(sourcePlacement(seq, { audio: 'a2' }, music)).toEqual({
    video_track_id: null, audio_track_id: 'a2', take_video: false, take_audio: true
  })
})

it('inserts audio-only files on an available track without requiring a selection', () => {
  const seq = sequence({ source_audio_track: '' })
  seq.tracks.a1.targeted = false
  seq.tracks.a1.locked = true
  expect(sourcePlacement(seq, undefined, music).audio_track_id).toBe('a2')
  seq.tracks.a2.locked = true
  expect(sourcePlacement(seq, undefined, music).take_audio).toBe(false)
})

it('keeps video-file audio disabled when its source patch is off', () => {
  const media = { audio: {}, video: {} } as MediaItem
  expect(sourcePlacement(sequence({ source_audio_track: '' }), { audio: 'a2' }, media).take_audio).toBe(false)
})

it('disabled A1 stays disabled for source edits and explicit drop destinations', () => {
  const seq = sequence({ source_audio_track: '' })
  for (const drop of [undefined, { video: 'v1', audio: 'a1' }]) {
    expect(sourcePlacement(seq, drop)).toEqual({
      video_track_id: 'v1', audio_track_id: null, take_video: true, take_audio: false
    })
  }
})

it('source edits follow the displayed patch instead of another targeted track', () => {
  const seq = sequence({ source_audio_track: 'a2' })
  expect(patchedTrack(seq, 'audio')).toBe('a2')
  expect(sourcePlacement(seq).audio_track_id).toBe('a2')
  expect(sourcePlacement(seq, { audio: 'a1' }).audio_track_id).toBe('a1')
})

it('supports audio only and both patches off', () => {
  expect(sourcePlacement(sequence({ source_video_track: '' }))).toEqual({
    video_track_id: null, audio_track_id: 'a1', take_video: false, take_audio: true
  })
  expect(sourcePlacement(sequence({ source_video_track: '', source_audio_track: '' }))).toEqual({
    video_track_id: null, audio_track_id: null, take_video: false, take_audio: false
  })
})
