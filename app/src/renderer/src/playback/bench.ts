// Test hook: decode throughput of the WebCodecs path for one media URL.
import { VideoSampleSink } from 'mediabunny'
import { openMedia } from './FrameSource'

export async function decodeBench(url: string, from: number, seconds: number): Promise<Record<string, number | string>> {
  const m = await openMedia(url)
  if (!m) return { error: 'not decodable' }
  const sink = new VideoSampleSink(m.track)
  const t0 = performance.now()
  let n = 0
  let first = 0
  for await (const s of sink.samples(from, from + seconds)) {
    if (!n) first = performance.now() - t0
    n++
    s.close()
  }
  const ms = performance.now() - t0
  return { frames: n, ms: Math.round(ms), firstMs: Math.round(first), fps: Math.round((n / ms) * 1000), codec: m.track.codec ?? '', w: m.width, h: m.height }
}

/** Test hook: drive a FrameSource with a real-time clock and count frames that were not exact. */
export async function sourceBench(url: string, from: number, seconds: number, rate = 1): Promise<Record<string, unknown>> {
  const { FrameSource } = await import('./FrameSource')
  const m = await openMedia(url)
  if (!m) return { error: 'not decodable' }
  const src = new FrameSource(m)
  src.update(from, false, 0)
  await new Promise((r) => setTimeout(r, 600))
  let frames = 0
  let inexact = 0
  const samples: string[] = []
  const t0 = performance.now()
  await new Promise<void>((resolve) => {
    const step = () => {
      const t = from + ((performance.now() - t0) / 1000) * rate
      src.update(t, true, rate)
      const f = src.frame(t)
      frames++
      if (!f?.exact) {
        inexact++
        if (samples.length < 12) samples.push(`${t.toFixed(3)} ${src.debug}`)
      }
      if (performance.now() - t0 < seconds * 1000) requestAnimationFrame(step)
      else resolve()
    }
    requestAnimationFrame(step)
  })
  src.dispose()
  return { frames, inexact, samples }
}

/** Test hook: the first presentation timestamps as mediabunny reports them. */
export async function firstTimestamps(url: string, n = 6): Promise<number[]> {
  const m = await openMedia(url)
  if (!m) return []
  const out: number[] = []
  const sink = new VideoSampleSink(m.track)
  for await (const s of sink.samples(0)) {
    out.push(s.timestamp)
    s.close()
    if (out.length >= n) break
  }
  return out
}
