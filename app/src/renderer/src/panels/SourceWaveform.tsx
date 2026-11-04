import { useEffect, useRef, useState } from 'react'
import { mediaUrl } from '../engine/client'
import { goTo } from '../state/transport'
import { drawWaveform, type Waveform } from '../timeline/waveform'
import { ticksToSeconds } from '../util/time'

export function SourceRangeOverlay({ duration, inPoint, outPoint }: { duration: number; inPoint: number; outPoint: number }) {
  const pct = (time: number) => `${Math.max(0, Math.min(100, duration ? time / duration * 100 : 0))}%`
  return <div className="lm-source-range" aria-label="Source In/Out">
    <div className="excluded before" style={{ width: pct(inPoint) }} />
    <div className="excluded after" style={{ left: pct(outPoint) }} />
    <div className="range-edge in" style={{ left: pct(inPoint) }}><span>I</span></div>
    <div className="range-edge out" style={{ left: pct(outPoint) }}><span>O</span></div>
  </div>
}

export function SourceWaveform({ path, duration, time, inPoint, outPoint }: {
  path: string | null; duration: number; time: number; inPoint: number; outPoint: number
}) {
  const top = useRef<HTMLCanvasElement>(null)
  const bottom = useRef<HTMLCanvasElement>(null)
  const [data, setData] = useState<{ path: string; wave: Waveform | null } | null>(null)
  useEffect(() => {
    if (!path) return
    const controller = new AbortController()
    void fetch(mediaUrl(path), { signal: controller.signal })
      .then((r) => { if (!r.ok) throw new Error('waveform'); return r.json() as Promise<Waveform> })
      .then((wave) => setData({ path, wave }))
      .catch(() => { if (!controller.signal.aborted) setData({ path, wave: null }) })
    return () => controller.abort()
  }, [path])
  const wave = data?.path === path ? data?.wave : null
  useEffect(() => {
    const draw = () => {
      for (const canvas of [top.current, bottom.current]) {
        if (!canvas) continue
        if (wave) drawWaveform(canvas, wave, 0, ticksToSeconds(duration), '#68c9ac')
        else canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height)
      }
    }
    const observer = new ResizeObserver(draw)
    if (top.current) observer.observe(top.current)
    draw()
    return () => observer.disconnect()
  }, [wave, duration])
  const seek = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    goTo('source', Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * duration)
  }
  return <div className="lm-source-waveform" aria-label="Source audio waveform"
    onPointerDown={(e) => { if (e.button !== 0) return; e.currentTarget.setPointerCapture(e.pointerId); seek(e) }}
    onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) seek(e) }}
    onPointerUp={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId) }}>
    <div className="wave-channels"><canvas ref={top} /><canvas ref={bottom} /></div>
    {!wave && <div className="wave-status">{path && data?.path === path ? '파형을 불러올 수 없습니다' : '파형 생성 중…'}</div>}
    <SourceRangeOverlay duration={duration} inPoint={inPoint} outPoint={outPoint} />
    <div className="wave-head" style={{ left: `${duration ? Math.max(0, Math.min(100, time / duration * 100)) : 0}%` }} />
  </div>
}
