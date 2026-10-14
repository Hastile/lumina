import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { interpolate, type Kf } from '../src/renderer/src/util/motion'

const vectors = JSON.parse(readFileSync(new URL('../../shared/test-vectors/keyframes.json', import.meta.url), 'utf8')) as {
  cases: { keyframes: Kf[]; t: number; expect: number | number[] }[]
}

describe('keyframe interpolation matches the engine', () => {
  vectors.cases.forEach((c, i) => {
    it(`case ${i}`, () => {
      const got = interpolate(c.keyframes, c.t)
      if (Array.isArray(c.expect)) (got as number[]).forEach((g, j) => expect(g).toBeCloseTo((c.expect as number[])[j], 9))
      else expect(got as number).toBeCloseTo(c.expect, 9)
    })
  })
})
