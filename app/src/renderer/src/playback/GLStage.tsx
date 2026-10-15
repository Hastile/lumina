// The Program monitor's pixels: every visible layer composited on the GPU by
// GLCompositor in the export renderer's order (see GLCompositor.ts). The DOM
// layers of the Stage stay on top, transparent, for selection, dragging and
// the drawing tools.

import { gradeUniforms, isIdentity, LUMETRI_KEYS, type LumetriParams } from '../util/color'
import { useEffect, useLayoutEffect, useRef } from 'react'
import type { Clip, MediaItem, Sequence } from '../engine/document'
import { engine, mediaUrl } from '../engine/client'
import { useStore } from '../state/store'
import { BLUR_SIGMA_PER_UNIT, effectParam, transformEffectAt, trs } from '../util/effects'
import { sourceTime, transformAt, type Resolved } from '../util/motion'
import { ticksPerFrame, ticksToSeconds } from '../util/time'
import { FrameSource, openMedia } from './FrameSource'
import { GLCompositor, IDENTITY, mul, type Affine, type Blend, type Placed, type Target } from './GLCompositor'

export type GLLayer = {
  key: string
  clip: Clip | null
  media: MediaItem | null
  color?: string
  group?: string
  fade?: number
  preroll?: boolean
  /** a layer inside a nested sequence: its sequence and that sequence's time */
  ctx?: { seq: Sequence; at: number }
  /** a nested sequence clip: what it shows */
  nested?: { seq: Sequence; at: number; layers: GLLayer[] }
}

type Props = {
  seq: Sequence
  layers: GLLayer[]
  at: number
  playing: boolean
  rate: number
  /** canvas pixels per sequence pixel */
  scale: number
  resolve: (l: GLLayer) => Resolved
  onFailed: () => void
}

let stats = { videos: 0, exact: 0, frames: [] as number[] }

type VideoSlot = { url: string; src: FrameSource | null; tex: WebGLTexture | null; shown: number | null; disposed: boolean }
type Still = { tex: WebGLTexture | null; w: number; h: number }
type GraphicTex = { key: string; groups: { blend: Blend; x: number; y: number; w: number; h: number; tex: WebGLTexture }[] | null }

const hex = (c: string): [number, number, number] => {
  const v = c.replace('#', '')
  const n = v.length === 3 ? v.split('').map((x) => x + x).join('') : v
  return [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16) / 255) as [number, number, number]
}

export function GLStage({ seq, layers, at, playing, rate, scale, resolve, onFailed }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const comp = useRef<GLCompositor | null>(null)
  const videos = useRef(new Map<string, VideoSlot>())
  const stills = useRef(new Map<string, Still>())
  const graphics = useRef(new Map<string, GraphicTex>())
  const props = useRef<Props>(null as unknown as Props)
  props.current = { seq, layers, at, playing, rate, scale, resolve, onFailed }
  const resolution = useStore((s) => s.playbackResolution)
  const raf = useRef(0)

  const requestDraw = () => {
    if (raf.current) return
    raf.current = requestAnimationFrame(() => {
      raf.current = 0
      draw()
    })
  }

  useEffect(() => {
    try {
      comp.current = new GLCompositor(canvas.current!)
    } catch (err) {
      console.warn('[lumina] WebGL2 compositor unavailable, using DOM compositing', err)
      onFailed()
    }
    const vids = videos.current
    return () => {
      cancelAnimationFrame(raf.current)
      for (const v of vids.values()) {
        v.disposed = true
        v.src?.dispose()
      }
      vids.clear()
      comp.current?.dispose()
      comp.current = null
    }
  }, [])

  // ---- sources

  const videoSlot = (key: string, m: MediaItem): VideoSlot => {
    const url = mediaUrl(m.path)
    let v = videos.current.get(key)
    if (v && v.url !== url) {
      v.disposed = true
      v.src?.dispose()
      v = undefined
    }
    if (!v) {
      const slot: VideoSlot = { url, src: null, tex: null, shown: null, disposed: false }
      videos.current.set(key, slot)
      const fallback = m.proxy_path ? mediaUrl(m.proxy_path) : null
      void openMedia(url)
        .then((d) => (d || !fallback ? d : openMedia(fallback)))
        .then((d) => {
          if (!d || slot.disposed) return
          slot.src = new FrameSource(d)
          slot.src.playbackScale = useStore.getState().playbackResolution
          slot.src.onFrame = requestDraw
          requestDraw()
        })
      v = slot
    }
    return v
  }

  const still = (m: MediaItem): Still => {
    const url = mediaUrl(m.proxy_path ?? m.path)
    let s = stills.current.get(url)
    if (!s) {
      const slot: Still = { tex: null, w: 0, h: 0 }
      stills.current.set(url, slot)
      void fetch(url)
        .then((r) => r.blob())
        .then((b) => createImageBitmap(b, { premultiplyAlpha: 'premultiply' }))
        .then((bmp) => {
          const c = comp.current
          if (!c) return
          slot.tex = c.texture()
          c.upload(slot.tex, bmp)
          slot.w = bmp.width
          slot.h = bmp.height
          bmp.close()
          requestDraw()
        })
        .catch((err) => console.warn('[lumina] image texture failed', url, err))
      s = slot
    }
    return s
  }

  const graphic = (clip: Clip, seqOf?: Sequence): GraphicTex => {
    const key = JSON.stringify(clip.graphic)
    let g = graphics.current.get(clip.id)
    if (!g || g.key !== key) {
      const old = g
      const slot: GraphicTex = { key, groups: old?.groups ?? null }
      graphics.current.set(clip.id, slot)
      const { width, height } = (seqOf ?? props.current.seq).settings
      void engine
        .call<{ groups: { blend: Blend; x: number; y: number; width: number; height: number; png: string }[] }>('render.graphic', { graphic: clip.graphic, width, height })
        .then(async (r) => {
          const c = comp.current
          if (!c || graphics.current.get(clip.id) !== slot) return
          const groups = []
          for (const gr of r.groups) {
            const bytes = Uint8Array.from(atob(gr.png), (ch) => ch.charCodeAt(0))
            const bmp = await createImageBitmap(new Blob([bytes], { type: 'image/png' }), { premultiplyAlpha: 'premultiply' })
            const tex = c.texture()
            c.upload(tex, bmp)
            bmp.close()
            groups.push({ blend: gr.blend, x: gr.x, y: gr.y, w: gr.width, h: gr.height, tex })
          }
          for (const gr of old?.groups ?? []) c.gl.deleteTexture(gr.tex)
          slot.groups = groups
          requestDraw()
        })
        .catch((err) => console.warn('[lumina] graphic render failed', err))
      g = slot
    }
    return g
  }

  // ---- drawing

  const drawClip = (c: GLCompositor, target: Target, l: GLLayer, fade: number, blend: Blend) => {
    const clip = l.clip!
    const { seq: sq, at: t } = l.ctx ?? props.current
    const [W, H] = [sq.settings.width, sq.settings.height]
    let sources: { placed: Placed; ps: number; blend: Blend }[] = []
    let w = W
    let h = H
    if (l.nested) {
      // the nested sequence composited on its own, transparent where empty, shown at this sequence's size
      const { seq: inner } = l.nested
      c.beginSub(inner.settings.width, inner.settings.height)
      drawLayers(c, l.nested.layers)
      const placed = c.endSub()
      const k = c.seqToFrame[0]
      sources = [{ placed: { ...placed, w: W, h: H }, ps: (inner.settings.width * k) / W, blend }]
    } else if (clip.graphic) {
      const g = graphic(clip, sq)
      sources = (g.groups ?? []).map((gr) => ({ placed: { tex: gr.tex, x: gr.x, y: gr.y, w: gr.w, h: gr.h, flip: true }, ps: 1, blend: gr.blend === 'multiply' ? 'multiply' : blend }))
    } else if (l.media?.kind === 'image') {
      const s = still(l.media)
      w = l.media.video?.width ?? s.w
      h = l.media.video?.height ?? s.h
      if (s.tex) sources = [{ placed: { tex: s.tex, x: 0, y: 0, w, h, flip: true }, ps: s.w / Math.max(1, w), blend }]
    } else if (l.media?.video) {
      w = l.media.video.width
      h = l.media.video.height
      const v = videos.current.get(l.key)
      const f = v?.src?.frame(ticksToSeconds(sourceTime(clip, t)))
      stats.videos++
      if (f?.exact) stats.exact++
      if (f) stats.frames.push(f.sample.timestamp)
      if (v && f) {
        if (!v.tex) v.tex = c.texture()
        if (v.shown !== f.sample.timestamp || v.tex === null) {
          c.upload(v.tex, f.sample.bitmap)
          v.shown = f.sample.timestamp
        }
        sources = [{ placed: { tex: v.tex, x: 0, y: 0, w, h, flip: true }, ps: f.sample.bitmap.width / w, blend }]
      }
    }
    if (!sources.length) return
    const r = l.ctx ? transformAt(clip, t, sq, l.media) : props.current.resolve(l)
    const motion = trs(r.position, r.anchor, r.scaleX / 100, r.scaleY / 100, r.rotation) as Affine
    const opacity = Math.max(0, Math.min(1, r.opacity / 100)) * fade
    const [cl, ct, cr, cb] = r.crop
    const crop: [number, number, number, number] | null = cl || ct || cr || cb ? [(cl / 100) * w, (ct / 100) * h, w - (cr / 100) * w, h - (cb / 100) * h] : null
    for (const s of sources) {
      // standard effects in source space, in stack order (compositor.apply_effects)
      let placed = s.placed
      let pre: Affine = IDENTITY
      let preOp = 1
      for (const fx of clip.effects ?? []) {
        if (!fx.enabled) continue
        if (fx.type === 'transform') {
          const { m, opacity: op } = transformEffectAt(fx, clip, t, w, h)
          pre = mul(m as Affine, pre)
          preOp *= op
        } else if (fx.type === 'lumetri') {
          const vals = Object.fromEntries(LUMETRI_KEYS.map((k) => [k, effectParam(fx, k, clip, t) as number])) as LumetriParams
          if (!isIdentity(vals)) placed = c.grade(placed, Math.round(placed.w * s.ps), Math.round(placed.h * s.ps), gradeUniforms(vals))
        } else if (fx.type === 'gaussian_blur') {
          const sig = Math.max(0, effectParam(fx, 'blurriness', clip, t) as number) * BLUR_SIGMA_PER_UNIT
          const dims = Math.round(effectParam(fx, 'dimensions', clip, t) as number)
          const sx = (dims === 2 ? 0 : sig) * s.ps
          const sy = (dims === 1 ? 0 : sig) * s.ps
          if (sx < 0.05 && sy < 0.05) continue
          const repeat = !!effectParam(fx, 'repeat_edge', clip, t)
          const pad = repeat ? 0 : Math.ceil(3 * Math.max(sx, sy)) + 1
          // materialize the whole source frame with the pending affine and opacity, padded
          // so the blur can spread past its edges (compositor._materialize + padding)
          const m = c.materialize(placed, pre, preOp, w, h, s.ps, pad)
          placed = c.blur(m, Math.round(w * s.ps + 2 * pad), Math.round(h * s.ps + 2 * pad), sx, sy)
          pre = IDENTITY
          preOp = 1
        }
      }
      c.draw(target, placed, mul(c.seqToFrame, mul(motion, pre)), {
        tint: [opacity * preOp, opacity * preOp, opacity * preOp, opacity * preOp],
        blend: s.blend,
        toCrop: pre,
        crop
      })
    }
  }

  /** Composite a sequence's layers, bottom to top, into the current frame target. */
  const drawLayers = (c: GLCompositor, layers: GLLayer[]) => {
    let group: string | undefined
    for (const l of layers) {
      if (l.preroll) continue
      if (l.group !== group) {
        if (group) c.endGroup()
        group = l.group
        if (group) c.beginGroup()
      }
      // read the targets per layer: a nested clip swaps them while it draws
      const target = group ? c.groupTarget : c.frameTarget
      const blend: Blend = group ? 'add' : 'normal'
      const fade = l.fade ?? 1
      if (!l.clip) {
        const [r, g, b] = hex(l.color ?? '#000')
        c.solid(target, [r, g, b, fade], blend)
      } else drawClip(c, target, l, fade, blend)
    }
    if (group) c.endGroup()
  }

  const draw = () => {
    const c = comp.current
    const p = props.current
    if (!c) return
    const t0 = performance.now()
    stats = { videos: 0, exact: 0, frames: [] }
    const { width: W, height: H } = p.seq.settings
    const playingNow = p.playing && p.rate !== 0
    // tests force full resolution with window.__luminaGLScale = 1
    c.resize(W, H, (window as unknown as { __luminaGLScale?: number }).__luminaGLScale ?? p.scale)
    // drive decoders (visible clips and prerolled ones)
    const live = new Set<string>()
    const drive = (layers: GLLayer[], sq: Sequence, at: number, rate: number) => {
      const tpf = ticksPerFrame(sq.settings)
      for (const l of layers) {
        if (l.nested && l.clip) drive(l.nested.layers, l.nested.seq, l.nested.at, rate * l.clip.speed)
        if (!l.clip || l.clip.graphic || !l.media?.video || l.media.kind === 'image') continue
        live.add(l.key)
        const v = videoSlot(l.key, l.media)
        const t = l.preroll ? (rate < 0 ? l.clip.end - tpf : l.clip.start) : at
        v.src?.update(ticksToSeconds(sourceTime(l.clip, t)), playingNow, playingNow ? rate * l.clip.speed : 0)
      }
    }
    drive(p.layers, p.seq, p.at, p.rate)
    for (const [k, v] of videos.current) {
      if (live.has(k)) continue
      v.disposed = true
      v.src?.dispose()
      if (v.tex) c.gl.deleteTexture(v.tex)
      videos.current.delete(k)
    }
    c.begin()
    drawLayers(c, p.layers)
    c.end()
    // for tests and diagnostics: were all visible video layers on their exact frame
    const el = canvas.current!
    el.dataset.exact = stats.exact === stats.videos ? '1' : '0'
    el.dataset.frames = stats.frames.map((x) => x.toFixed(4)).join(',')
    el.dataset.ms = (performance.now() - t0).toFixed(2)
  }

  useEffect(() => {
    for (const v of videos.current.values()) if (v.src) v.src.playbackScale = resolution
  }, [resolution])

  useLayoutEffect(() => {
    draw()
  })

  const { width: W, height: H } = seq.settings
  return <canvas ref={canvas} className="lm-glstage" data-gl="" style={{ position: 'absolute', left: 0, top: 0, width: W, height: H }} />
}
