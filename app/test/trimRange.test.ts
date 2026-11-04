import { expect, it } from 'vitest'
import type { Clip, Sequence } from '../src/renderer/src/engine/document'
import { trimRange } from '../src/renderer/src/util/trimRange'

const clip = (patch = {}) => ({ id: 'c', start: 50, end: 100, source_in: 20, speed: 1, freeze: null, ...patch } as Clip)
const sequence = (...clips: Clip[]) => ({ tracks: { v: { locked: false, clips: Object.fromEntries(clips.map(c => [c.id, c])) } } } as unknown as Sequence)
it('stops at both source edges and keeps one frame', () => {
  expect(trimRange(sequence(clip()), ['c'], 'start', false, 1, () => 100)).toEqual([-20, 49])
  expect(trimRange(sequence(clip()), ['c'], 'end', false, 1, () => 100)).toEqual([-49, 30])
})
it('accounts for speed and rounds inward to whole timeline frames', () => {
  expect(trimRange(sequence(clip({ speed: 1.5 })), ['c'], 'end', false, 2, () => 100)).toEqual([-48, 2])
})
it('supports reversed source limits', () => {
  expect(trimRange(sequence(clip({ source_in: 80, speed: -1 })), ['c'], 'start', false, 1, () => 100)).toEqual([-20, 49])
  expect(trimRange(sequence(clip({ source_in: 80, speed: -1 })), ['c'], 'end', false, 1, () => 100)).toEqual([-49, 30])
})
it('uses the shortest linked handle and stops at adjacent clips', () => {
  const seq = sequence(clip(), clip({ id: 'a', start: 200, end: 250, source_in: 40 }))
  expect(trimRange(seq, ['c', 'a'], 'end', false, 1, () => 100)[1]).toBe(10)
  expect(trimRange(sequence(clip(), clip({ id: 'next', start: 105, end: 110 })), ['c'], 'end', false, 1, () => 100)[1]).toBe(5)
})
it('allows stills and held frames to extend, and ripple heads to keep their position', () => {
  expect(trimRange(sequence(clip()), ['c'], 'end', false, 1, () => null)[1]).toBe(Infinity)
  expect(trimRange(sequence(clip({ freeze: 20 })), ['c'], 'end', false, 1, () => 100)[1]).toBe(Infinity)
  expect(trimRange(sequence(clip({ start: 0, end: 50 })), ['c'], 'start', true, 1, () => 100)[0]).toBe(-20)
})
