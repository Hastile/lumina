import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { gradePixel } from '../src/renderer/src/util/color'

const data = JSON.parse(readFileSync(resolve(__dirname, '../../shared/test-vectors/lumetri.json'), 'utf-8'))

describe('Lumetri Color twin of engine/lumina/render/color.py', () => {
  it('grades every vector like the engine', () => {
    for (const c of data.cases) {
      c.in.forEach((px: number[], i: number) => {
        gradePixel(px, c.params).forEach((v, k) => expect(v).toBeCloseTo(c.out[i][k], 5))
      })
    }
  })
})
