import { afterEach, expect, it, vi } from 'vitest'
import { liveValue } from '../src/renderer/src/util/liveValue'

afterEach(() => vi.unstubAllGlobals())
it('updates before release, coalesces moves, and flushes the last value once', () => {
  let callback: (() => void) | undefined
  vi.stubGlobal('requestAnimationFrame', (fn: () => void) => { callback = fn; return 1 })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  const commit = vi.fn()
  const drag = liveValue(commit)
  drag.update(110)
  drag.update(120)
  callback!()
  expect(commit).toHaveBeenCalledExactlyOnceWith(120)
  drag.update(130)
  drag.finish(135)
  expect(commit.mock.calls).toEqual([[120], [135]])
  drag.finish(135)
  expect(commit).toHaveBeenCalledTimes(2)
})
