import { expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ sample: () => ({
  timestamp: 0, duration: 1 / 30, close: vi.fn(),
  toVideoFrame: () => ({ displayWidth: 1920, displayHeight: 1080, close: vi.fn() })
}) }))
vi.mock('@mediabunny/prores', () => ({ registerProresDecoder: vi.fn() }))
vi.mock('mediabunny', () => ({
  ALL_FORMATS: [], EncodedPacketSink: class {}, Input: class {}, UrlSource: class {},
  VideoSampleSink: class {
    async *samples() { yield mocks.sample() }
    async getSample() { return mocks.sample() }
  }
}))
import { FrameSource, type DecodableMedia } from '../src/renderer/src/playback/FrameSource'

it('replaces a scaled playback frame with native resolution when paused on that same frame', async () => {
  vi.stubGlobal('createImageBitmap', vi.fn(async (_frame, opts) => ({
    width: opts?.resizeWidth ?? 1920, height: opts?.resizeHeight ?? 1080, close: vi.fn()
  })))
  const source = new FrameSource({ width: 1920, height: 1080, frameDuration: 1 / 30 } as DecodableMedia)
  try {
    source.playbackScale = 0.5
    source.update(0, true, 1)
    await vi.waitFor(() => expect(source.frame(0)?.sample.bitmap.width).toBe(960))
    const scaled = source.frame(0)!.sample.bitmap
    source.update(0, false, 0)
    await vi.waitFor(() => expect(source.frame(0)?.sample.bitmap.width).toBe(1920))
    expect(scaled.close).toHaveBeenCalledOnce()
    expect(createImageBitmap).toHaveBeenLastCalledWith(expect.anything(), { premultiplyAlpha: 'premultiply' })
  } finally { source.dispose(); vi.unstubAllGlobals() }
})
