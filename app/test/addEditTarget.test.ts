import { expect, it } from 'vitest'
import type { Sequence } from '../src/renderer/src/engine/document'
import { addEditTarget } from '../src/renderer/src/util/addEditTarget'

const seq = { tracks: {
  v: { id: 'v', targeted: true, locked: false, clips: { v1: { id: 'v1', start: 0, end: 10 } } },
  caption: { id: 'caption', targeted: false, locked: false, clips: { c: { id: 'c', start: 0, end: 4 }, next: { id: 'next', start: 4, end: 10 } } },
  locked: { id: 'locked', targeted: true, locked: true, clips: {} }
} } as unknown as Sequence
it('cuts the selected caption instead of targeted video', () => {
  expect(addEditTarget(seq, ['c'], 2)).toEqual(['caption'])
})
it('does not cut another clip when the selected clip is away from the playhead', () => {
  expect(addEditTarget(seq, ['c'], 6)).toEqual([])
  expect(addEditTarget(seq, ['c'], 4)).toEqual([])
})
it('uses unlocked targeted tracks only when nothing is selected', () => {
  expect(addEditTarget(seq, [], 2)).toEqual(['v'])
})
