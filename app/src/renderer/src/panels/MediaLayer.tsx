// One <video>/<audio>/<img> following the playback clock. The Program monitor
// stacks one per active clip (CSS-composited opacity) until the WebCodecs/WebGL
// compositor lands (plan M1/M3); the Source monitor uses a single layer.

import { useEffect, useRef } from 'react'

type Props = {
  url: string
  kind: 'video' | 'audio' | 'image'
  time: number // seconds into the media
  playing: boolean
  rate: number
  volume?: number // 0..1
  muted?: boolean
  style?: React.CSSProperties
}

export function MediaLayer({ url, kind, time, playing, rate, volume = 1, muted = false, style }: Props) {
  const ref = useRef<HTMLVideoElement & HTMLAudioElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el || kind === 'image') return
    el.muted = muted
    el.volume = Math.max(0, Math.min(1, volume))
  }, [kind, muted, volume])

  useEffect(() => {
    const el = ref.current
    if (!el || kind === 'image') return
    const forward = playing && rate > 0
    if (forward) {
      // follow the clock without seeking mid-play (a seek shows as a stutter): only a big
      // jump seeks; small drift is absorbed by nudging the playback rate up to +-10 %
      const drift = el.currentTime - time
      if (Math.abs(drift) > 0.5 * Math.max(1, rate) || el.paused) {
        if (Math.abs(drift) > 1 / 60) el.currentTime = time
        el.playbackRate = Math.min(16, rate)
      } else {
        const want = Math.min(16, rate * Math.max(0.9, Math.min(1.1, 1 - drift * 2)))
        if (Math.abs(el.playbackRate - want) > 0.01) el.playbackRate = want
      }
      if (el.paused) void el.play().catch(() => undefined)
    } else {
      if (!el.paused) el.pause()
      if (Math.abs(el.currentTime - time) > 0.004) el.currentTime = time
    }
  }, [kind, time, playing, rate])

  useEffect(() => {
    const el = ref.current
    return () => {
      el?.pause()
    }
  }, [])

  if (kind === 'image') return <img src={url} style={style} draggable={false} />
  if (kind === 'audio') return <audio ref={ref} src={url} preload="auto" />
  return <video ref={ref} src={url} preload="auto" playsInline disablePictureInPicture style={style} />
}
