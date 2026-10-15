// A video layer that shows exactly the frame at `time`, decoded with WebCodecs
// (playback/FrameSource). Falls back to a <video> element when the container
// or codec cannot be decoded that way.

import { useEffect, useRef, useState } from 'react'
import { MediaLayer } from '../panels/MediaLayer'
import { useStore } from '../state/store'
import { FrameSource, openMedia } from './FrameSource'

type Props = {
  url: string
  /** tried when ``url`` cannot be decoded (e.g. a proxy for an unsupported original) */
  fallbackUrl?: string
  time: number // seconds into the media
  playing: boolean
  rate: number
  /** Upcoming clip: keep showing (and pre-decoding) its first frame while the transport plays. */
  hold?: boolean
  style?: React.CSSProperties
}

export function FrameCanvas({ url, fallbackUrl, time, playing, rate, hold = false, style }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const source = useRef<FrameSource | null>(null)
  const latest = useRef({ time, playing, rate, hold })
  latest.current = { time, playing, rate, hold }
  const drawn = useRef<number | null>(null)
  const [mode, setMode] = useState<'pending' | 'webcodecs' | 'fallback'>('pending')
  const resolution = useStore((s) => s.playbackResolution)

  const draw = () => {
    const src = source.current
    const el = canvas.current
    if (!src || !el) return
    const f = src.frame(latest.current.time)
    if (!f) return
    el.dataset.exact = f.exact ? '1' : '0'
    el.dataset.debug = src.debug
    if (drawn.current === f.sample.timestamp) return
    const ctx = el.getContext('2d')
    if (!ctx) return
    ctx.drawImage(f.sample.bitmap, 0, 0, el.width, el.height)
    drawn.current = f.sample.timestamp
    el.dataset.frame = String(f.sample.timestamp)
  }

  useEffect(() => {
    let alive = true
    setMode('pending')
    void openMedia(url).then(async (m) => (m || !fallbackUrl ? m : openMedia(fallbackUrl))).then((m) => {
      if (!alive) return
      if (!m) return setMode('fallback')
      const src = new FrameSource(m)
      src.onFrame = draw
      source.current = src
      drawn.current = null
      if (canvas.current) {
        canvas.current.width = m.width
        canvas.current.height = m.height
      }
      setMode('webcodecs')
      const l = latest.current
      src.update(l.time, l.playing, l.hold ? Math.max(l.rate, 1e-3) : l.rate)
      draw()
    })
    return () => {
      alive = false
      source.current?.dispose()
      source.current = null
    }
  }, [url, fallbackUrl])

  useEffect(() => {
    if (source.current) source.current.playbackScale = resolution
  }, [resolution, mode])

  useEffect(() => {
    const src = source.current
    if (!src) return
    // a held clip pre-decodes forward from its first frame while the transport plays
    src.update(time, playing && rate !== 0, playing ? rate : 0)
    draw()
    if (canvas.current) canvas.current.dataset.want = String(time)
  }, [time, playing, rate, hold, mode])

  if (mode === 'fallback') {
    return <MediaLayer url={fallbackUrl ?? url} kind="video" time={time} playing={playing && !hold} rate={rate} muted style={style} />
  }
  return <canvas ref={canvas} data-webcodecs="" style={{ display: 'block', ...style }} />
}
