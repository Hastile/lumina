import { beforeEach, expect, it, vi } from 'vitest'

const mock = vi.hoisted(() => ({ state: {} as any, exec: vi.fn(), toast: vi.fn() }))
vi.mock('../src/renderer/src/state/store', () => ({
  get: () => mock.state,
  sourceMedia: (s = mock.state) => s.doc.media[s.sourceMediaId],
  findClip: (seq: any, id: string) => seq.clips[id] ? { clip: seq.clips[id] } : null,
  exec: mock.exec, toast: mock.toast
}))
import { sourceRange, setSourceEdge } from '../src/renderer/src/state/sourceRange'

beforeEach(() => {
  vi.clearAllMocks()
  mock.state = { sourceMediaId: 'm', sourceClip: null, doc: {
    media: { m: { id: 'm', duration: 1000, in_point: null, out_point: null } },
    sequences: { s: { clips: { c: { id: 'c', media_id: 'm', start: 100, end: 300, source_in: 400, speed: 2 } } } }
  } }
})
it('shows the full source range without explicit marks', () => {
  expect(sourceRange()).toMatchObject({ inPoint: 0, outPoint: 1000 })
})
it('uses the opened instance range instead of shared media marks', async () => {
  mock.state.sourceClip = { sequenceId: 's', clipId: 'c' }
  expect(sourceRange()).toMatchObject({ inPoint: 400, outPoint: 800 })
  await setSourceEdge('out', 900)
  expect(mock.exec).toHaveBeenCalledWith('clip.trim', {
    sequence_id: 's', clip_id: 'c', edge: 'end', delta: 50, ripple: false, include_linked: false
  }, 'Edit Source Range')
  await setSourceEdge('in', 500)
  expect(mock.exec.mock.calls[1][1]).toMatchObject({ edge: 'start', delta: 50 })
})
it('edits media marks when opened from the project bin', async () => {
  await setSourceEdge('in', 200)
  expect(mock.exec).toHaveBeenCalledWith('media.set_marks', { media_id: 'm', in_point: 200 }, 'Edit Source Range')
})
it('does not edit shared media when the bound clip has been deleted', async () => {
  mock.state.sourceClip = { sequenceId: 's', clipId: 'missing' }
  await setSourceEdge('in', 200)
  expect(mock.exec).not.toHaveBeenCalled()
})
it('skips reversed and held clips', async () => {
  mock.state.sourceClip = { sequenceId: 's', clipId: 'c' }
  mock.state.doc.sequences.s.clips.c.speed = -1
  await setSourceEdge('out', 900)
  expect(mock.exec).not.toHaveBeenCalled()
})
