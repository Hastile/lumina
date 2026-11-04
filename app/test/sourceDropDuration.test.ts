import { expect, it } from 'vitest'
import type { MediaItem } from '../src/renderer/src/engine/document'
import { sourceDropDuration } from '../src/renderer/src/util/sourceDropDuration'
import { TICKS_PER_SECOND as S } from '../src/renderer/src/util/time'

const rate = { fps_num: 30, fps_den: 1 }
const media = (patch = {}) => ({ kind: 'video', duration: 12 * S, ...patch }) as MediaItem

it('previews the whole source by default and only the marked range when set', () => {
  expect(sourceDropDuration(media(), rate)).toBe(12 * S)
  expect(sourceDropDuration(media({ in_point: 2 * S, out_point: 5 * S }), rate)).toBe(3 * S)
  expect(sourceDropDuration(media({ in_point: 8 * S }), rate)).toBe(4 * S)
})

it('matches placement frame rounding without extending beyond the source', () => {
  expect(sourceDropDuration(media({ duration: 1.02 * S }), rate)).toBe(S)
  expect(sourceDropDuration(media({ in_point: S, out_point: 1.01 * S }), rate)).toBe(S / 30)
  expect(sourceDropDuration(media({ in_point: 12 * S }), rate)).toBe(0)
})

it('uses the placement default for still images', () => {
  expect(sourceDropDuration(media({ kind: 'image', duration: 0 }), rate)).toBe(5 * S)
})
