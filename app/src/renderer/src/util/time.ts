// Mirrors engine/lumina/timebase.py
export const TICKS_PER_SECOND = 254_016_000_000

export type Rate = { fps_num: number; fps_den: number }

export const ticksPerFrame = (r: Rate): number => (TICKS_PER_SECOND * r.fps_den) / r.fps_num

export const ticksToSeconds = (t: number): number => t / TICKS_PER_SECOND
export const secondsToTicks = (s: number): number => Math.round(s * TICKS_PER_SECOND)

export const snapToFrame = (t: number, r: Rate): number => {
  const tpf = ticksPerFrame(r)
  return Math.round(t / tpf) * tpf
}

export const floorToFrame = (t: number, r: Rate): number => {
  const tpf = ticksPerFrame(r)
  return Math.floor(t / tpf + 1e-9) * tpf
}

export function timecode(t: number, r: Rate): string {
  const nominal = Math.round(r.fps_num / r.fps_den)
  const frames = Math.floor(t / ticksPerFrame(r) + 1e-9)
  const ff = frames % nominal
  const total = Math.floor(frames / nominal)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}:${pad(ff)}`
}

/** Parse "00:00:05:12" (or fewer fields) typed into a timecode display. */
export function parseTimecode(text: string, r: Rate): number | null {
  const parts = text.trim().split(/[:;.]/).map((p) => Number(p))
  if (!parts.length || parts.some((p) => Number.isNaN(p))) return null
  while (parts.length < 4) parts.unshift(0)
  const [h, m, s, f] = parts.slice(-4)
  const nominal = Math.round(r.fps_num / r.fps_den)
  return (((h * 60 + m) * 60 + s) * nominal + f) * ticksPerFrame(r)
}
