import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { transitionWindow, trs } from '../src/renderer/src/util/effects'

const data = JSON.parse(readFileSync(resolve(__dirname, '../../shared/test-vectors/effects.json'), 'utf-8'))

describe('effects twin of engine/lumina/effects.py', () => {
  it('trs matches', () => {
    for (const c of data.trs) {
      const got = trs(c.trs[0], c.trs[1], c.trs[2], c.trs[3], c.trs[4], c.trs[5], c.trs[6])
      got.forEach((v: number, i: number) => expect(v).toBeCloseTo(c.expect[i], 6))
    }
  })
  it('transition windows match', () => {
    for (const c of data.windows) {
      const w = transitionWindow(data.track, { id: 'x', type: 'cross_dissolve', ...c.transition })
      expect(w ? [w.start, w.end] : null).toEqual(c.expect)
    }
  })
})
