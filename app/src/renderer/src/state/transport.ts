// Playback clock for the Source and Program monitors (Space, J/K/L, arrows).
// Monitors render whatever time the clock says; media elements follow it.

import { activeSequence, get, sequenceEnd, set, sourceMedia } from './store'
import { ticksPerFrame, TICKS_PER_SECOND, type Rate } from '../util/time'

let raf = 0
let last = 0

export type Monitor = 'source' | 'program'

export function monitorRate(m: Monitor): Rate {
  if (m === 'program') return activeSequence()?.settings ?? { fps_num: 30, fps_den: 1 }
  const v = sourceMedia()?.video
  return v ? { fps_num: v.fps_num, fps_den: v.fps_den } : activeSequence()?.settings ?? { fps_num: 30, fps_den: 1 }
}

function duration(m: Monitor): number {
  if (m === 'program') {
    const seq = activeSequence()
    return seq ? sequenceEnd(seq) : 0
  }
  const media = sourceMedia()
  return media ? media.duration || 5 * TICKS_PER_SECOND : 0
}

export function getTime(m: Monitor): number {
  return m === 'program' ? get().programTime : get().sourceTime
}

export function setTime(m: Monitor, t: number): void {
  const clamped = Math.max(0, Math.min(t, duration(m)))
  if (m === 'program') set({ programTime: clamped })
  else set({ sourceTime: clamped })
}

function tick(now: number): void {
  const s = get()
  const m = s.playing
  if (!m) return
  const dt = (now - last) / 1000
  last = now
  const t = getTime(m) + dt * s.shuttle * TICKS_PER_SECOND
  const end = duration(m)
  if (t >= end || t <= 0) {
    setTime(m, t)
    stop()
    return
  }
  setTime(m, t)
  raf = requestAnimationFrame(tick)
}

export function play(m: Monitor, rate = 1): void {
  const s = get()
  if (getTime(m) >= duration(m) - ticksPerFrame(monitorRate(m)) && rate > 0) setTime(m, 0)
  set({ playing: m, shuttle: rate })
  if (!s.playing) {
    last = performance.now()
    cancelAnimationFrame(raf)
    raf = requestAnimationFrame(tick)
  }
}

export function stop(): void {
  cancelAnimationFrame(raf)
  const s = get()
  if (s.playing) {
    // land on a frame boundary like Premiere does
    const m = s.playing
    const tpf = ticksPerFrame(monitorRate(m))
    setTime(m, Math.floor(getTime(m) / tpf) * tpf)
  }
  set({ playing: null, shuttle: 0 })
}

export function togglePlay(m: Monitor): void {
  if (get().playing) stop()
  else play(m, 1)
}

/** L: play forward, repeated presses go 2x/4x/8x. J: same in reverse. K: stop. */
export function shuttle(m: Monitor, dir: 1 | -1): void {
  const s = get()
  if (s.playing === m && Math.sign(s.shuttle) === dir) {
    play(m, Math.max(-8, Math.min(8, s.shuttle * 2)))
  } else {
    if (s.playing) stop()
    play(m, dir)
  }
}

export function step(m: Monitor, frames: number): void {
  if (get().playing) stop()
  const tpf = ticksPerFrame(monitorRate(m))
  setTime(m, Math.round(getTime(m) / tpf) * tpf + frames * tpf)
}

export function goTo(m: Monitor, t: number): void {
  if (get().playing) stop()
  setTime(m, t)
}
