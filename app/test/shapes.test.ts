import { describe, expect, it } from 'vitest'
import { bounds, svgPath } from '../src/renderer/src/util/shapes'

describe('shapes twin of engine/lumina/shapes.py', () => {
  it('draws corner vertices as lines and smooth ones as curves', () => {
    const rect = [[0, 0, 0, 0, 0, 0], [10, 0, 10, 0, 10, 0], [10, 5, 10, 5, 10, 5]]
    expect(svgPath(rect, true)).toBe('M0 0 L10 0 L10 5 L0 0 Z')
    expect(svgPath(rect, false)).toBe('M0 0 L10 0 L10 5')
    const curve = [[0, 0, -1, 0, 1, 0], [10, 10, 10, 9, 10, 11]]
    expect(svgPath(curve, false)).toBe('M0 0 C1 0 10 9 10 10')
    expect(bounds(rect)).toEqual([0, 0, 10, 5])
  })
})
