import { mediaUrl } from '../engine/client'

export type Waveform = { rate: number; peaks: number[] }

const cache = new Map<string, Promise<Waveform | null>>()
const ready = new Map<string, Waveform>()

export function loadWaveform(path: string, onReady: () => void): Waveform | null {
  const hit = ready.get(path)
  if (hit) return hit
  if (!cache.has(path)) {
    cache.set(
      path,
      fetch(mediaUrl(path))
        .then((r) => (r.ok ? r.json() : null))
        .then((w: Waveform | null) => {
          if (w) ready.set(path, w)
          return w
        })
        .catch(() => null)
    )
  }
  void cache.get(path)!.then((w) => w && onReady())
  return null
}

/** Draw the clip's source range [srcStart, srcEnd) seconds into the canvas. */
export function drawWaveform(canvas: HTMLCanvasElement, w: Waveform, srcStart: number, srcEnd: number, color: string): void {
  const dpr = window.devicePixelRatio || 1
  const width = canvas.clientWidth
  const height = canvas.clientHeight
  if (!width || !height) return
  canvas.width = Math.round(width * dpr)
  canvas.height = Math.round(height * dpr)
  const ctx = canvas.getContext('2d')!
  ctx.scale(dpr, dpr)
  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = color
  const mid = height / 2
  const perPx = ((srcEnd - srcStart) * w.rate) / width
  for (let x = 0; x < width; x++) {
    const a = Math.floor(srcStart * w.rate + x * perPx)
    const b = Math.max(a + 1, Math.floor(srcStart * w.rate + (x + 1) * perPx))
    let peak = 0
    for (let i = a; i < b && i < w.peaks.length; i++) if (w.peaks[i] > peak) peak = w.peaks[i]
    const h = (peak / 255) * (height / 2 - 1)
    if (h > 0.3) ctx.fillRect(x, mid - h, 1, h * 2)
  }
}

// ---------------------------------------------------------------- filmstrips (video clip thumbnails)

export type Filmstrip = { image: string; interval: number; count: number; width: number; height: number }

const strips = new Map<string, Promise<Filmstrip | null>>()
const stripReady = new Map<string, Filmstrip>()

export function loadFilmstrip(path: string, onReady: () => void): Filmstrip | null {
  const hit = stripReady.get(path)
  if (hit) return hit
  if (!strips.has(path)) {
    strips.set(
      path,
      fetch(mediaUrl(path))
        .then((r) => (r.ok ? r.json() : null))
        .then((f: Filmstrip | null) => {
          if (f) stripReady.set(path, f)
          return f
        })
        .catch(() => null)
    )
  }
  void strips.get(path)!.then((f) => f && onReady())
  return null
}
