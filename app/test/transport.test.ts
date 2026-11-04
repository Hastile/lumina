import { beforeEach, expect, it, vi } from 'vitest'

const mock = vi.hoisted(() => ({ state: { sourceTime: 0, programTime: 0 } }))
vi.mock('../src/renderer/src/state/store', () => ({
  get: () => mock.state,
  set: (value: object) => Object.assign(mock.state, value),
  sourceMedia: () => ({ duration: 2540160000000 }),
  activeSequence: () => ({}),
  sequenceEnd: () => 2540160000000
}))
vi.mock('../src/renderer/src/playback/AudioEngine', () => ({ audioEngine: {} }))
import { setTime } from '../src/renderer/src/state/transport'

beforeEach(() => { mock.state = { sourceTime: 0, programTime: 0 } })
it('keeps scrub and playback times valid for integer engine mark commands', () => {
  setTime('source', 2540160000000 * (137 / 641))
  expect(Number.isInteger(mock.state.sourceTime)).toBe(true)
  expect(mock.state.sourceTime).toBe(Math.round(2540160000000 * (137 / 641)))
  setTime('program', 123.75)
  expect(mock.state.programTime).toBe(124)
  setTime('source', -0.75)
  expect(mock.state.sourceTime).toBe(0)
  setTime('source', 3e12)
  expect(mock.state.sourceTime).toBe(2540160000000)
})
