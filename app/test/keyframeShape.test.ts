import { expect, it, vi } from 'vitest'
vi.mock('@shared/i18n', () => ({ t: (text: string) => text }))

vi.mock('../src/renderer/src/state/store', () => ({}))
vi.mock('../src/renderer/src/layout/Chrome', () => ({}))
vi.mock('../src/renderer/src/panels/KeyframeGraph', () => ({
  effectiveHandle: (frames: { interp: string; ease_in?: number[]; ease_out?: number[] }[], i: number, side: 'in' | 'out') =>
    frames[i].interp === 'linear' ? [10, 1 / 3] : frames[i][side === 'in' ? 'ease_in' : 'ease_out'] ?? [0, 1 / 3]
}))
import { halves } from '../src/renderer/src/panels/KeyframeSelect'

it('shows Bezier mode even when its handles preserve a straight curve', () => {
  expect(halves([{ t: 0, v: 0, interp: 'bezier', ease_in: [10, 1 / 3], ease_out: [10, 1 / 3] }], 0)).toEqual(['bezier', 'bezier'])
  expect(halves([{ t: 0, v: 0, interp: 'linear' }], 0)).toEqual(['linear', 'linear'])
})

it('keeps the distinct incoming and outgoing ease indicators', () => {
  expect(halves([{ t: 0, v: 0, interp: 'bezier', ease_in: [0, 1 / 3], ease_out: [10, 1 / 3] }], 0)).toEqual(['bezier', 'linear'])
})
