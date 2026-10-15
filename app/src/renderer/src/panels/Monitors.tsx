import { newShapeLayer } from '../actions/actions'
import { useEffect, useRef, useState } from 'react'
import {
  ArrowDownToLine, ArrowRightToLine, Bookmark, Brackets, ChevronFirst, ChevronLast, Pause, Play, SkipBack, SkipForward,
  StepBack, StepForward, Upload, Import as InsertIcon, Replace
} from 'lucide-react'
import { t } from '@shared/i18n'
import type { Clip, MediaItem, Sequence } from '../engine/document'
import { mediaUrl } from '../engine/client'
import { runAction } from '../actions/actions'
import { DRAG_MIME } from './ProjectPanel'
import { activeSequence, exec, get, set, sequenceEnd, useStore } from '../state/store'
import { goTo, type Monitor } from '../state/transport'
import { focusPanel } from '../layout/dock'
import { floorToFrame, parseTimecode, ticksPerFrame, ticksToSeconds, timecode, TICKS_PER_SECOND, type Rate } from '../util/time'
import { sourceTime, transformAt, transformCss, valueAt } from '../util/motion'
import { effectCss, trackTransitions, transitionProgress } from '../util/effects'
import { MediaLayer } from './MediaLayer'
import { FrameCanvas } from '../playback/FrameCanvas'
import { GLStage } from '../playback/GLStage'
import { audioEngine } from '../playback/AudioEngine'
import { GraphicView } from './GraphicView'

export function mediaSrc(m: MediaItem): string {
  return mediaUrl(m.proxy_path ?? m.path)
}

const dbToGain = (db: number) => Math.pow(10, db / 20)

// ---------------------------------------------------------------- shared chrome

function Timecode({ value, rate, onCommit }: { value: number; rate: Rate; onCommit: (t: number) => void }) {
  const [editing, setEditing] = useState<string | null>(null)
  return editing !== null ? (
    <input
      className="lm-tc"
      autoFocus
      value={editing}
      onChange={(e) => setEditing(e.target.value)}
      onBlur={() => setEditing(null)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          const v = parseTimecode(editing, rate)
          if (v != null) onCommit(v)
          setEditing(null)
        } else if (e.key === 'Escape') setEditing(null)
      }}
    />
  ) : (
    <button className="lm-tc" onClick={() => setEditing(timecode(value, rate))}>
      {timecode(value, rate)}
    </button>
  )
}

function Scrubber({ monitor, duration, time, inPoint, outPoint, markers }: {
  monitor: Monitor; duration: number; time: number; inPoint?: number | null; outPoint?: number | null; markers?: number[]
}) {
  const ref = useRef<HTMLDivElement>(null)
  const pct = (v: number) => `${duration ? (100 * v) / duration : 0}%`
  const scrub = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect()
    goTo(monitor, Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * duration)
  }
  return (
    <div
      ref={ref}
      className="lm-scrub"
      onMouseDown={(e) => {
        scrub(e.clientX)
        const move = (ev: MouseEvent) => scrub(ev.clientX)
        const up = () => {
          window.removeEventListener('mousemove', move)
          window.removeEventListener('mouseup', up)
        }
        window.addEventListener('mousemove', move)
        window.addEventListener('mouseup', up)
      }}
    >
      {(inPoint != null || outPoint != null) && (
        <div className="inout" style={{ left: pct(inPoint ?? 0), width: `calc(${pct((outPoint ?? duration) - (inPoint ?? 0))})` }} />
      )}
      {markers?.map((m, i) => <div key={i} className="marker" style={{ left: pct(m) }} />)}
      <div className="head" style={{ left: pct(time) }} />
    </div>
  )
}

/** Letterboxed frame of the given aspect; children get the display scale factor. */
function Frame({ width, height, children, onDrop }: {
  width: number; height: number; children: (scale: number) => React.ReactNode; onDrop?: (e: React.DragEvent) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  useEffect(() => {
    const el = box.current!
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect()
      const s = Math.min(r.width / width, r.height / height)
      setSize({ w: Math.floor(width * s), h: Math.floor(height * s) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [width, height])
  return (
    <div ref={box} className="lm-frame-box" onDragOver={(e) => onDrop && e.preventDefault()} onDrop={onDrop}>
      <div className="lm-frame" style={{ width: size.w, height: size.h }}>
        {size.w > 0 && children(size.w / width)}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- Source

export function SourceMonitor() {
  const media = useStore((s) => (s.sourceMediaId ? s.doc?.media[s.sourceMediaId] ?? null : null))
  const time = useStore((s) => s.sourceTime)
  const playing = useStore((s) => s.playing === 'source')
  const rate = useStore((s) => s.shuttle)

  const r: Rate = media?.video ? { fps_num: media.video.fps_num, fps_den: media.video.fps_den } : { fps_num: 30, fps_den: 1 }
  const duration = media ? media.duration || 5 * TICKS_PER_SECOND : 0
  const inOutDur = media && (media.in_point != null || media.out_point != null) ? (media.out_point ?? duration) - (media.in_point ?? 0) : duration

  const onDrop = (e: React.DragEvent) => {
    const ids = JSON.parse(e.dataTransfer.getData(DRAG_MIME) || '[]') as string[]
    const doc = useStore.getState().doc
    const id = ids.find((i) => doc?.media[i])
    if (id) set({ sourceMediaId: id, sourceTime: 0 })
  }

  return (
    <div className="lm-panel lm-monitor">
      <div className="lm-monitor-title">{t('Source')}: {media ? media.name : t('(no clips)')}</div>
      <Frame width={media?.video?.width || 1920} height={media?.video?.height || 1080} onDrop={onDrop}>
        {() => (
          <>
            {media && (media.video || media.kind === 'image') && (
              media.kind === 'image' ? (
                <MediaLayer url={mediaSrc(media)} kind="image" time={0} playing={false} rate={rate} style={{ width: '100%', height: '100%' }} />
              ) : (
                <>
                  <FrameCanvas url={mediaUrl(media.path)} fallbackUrl={media.proxy_path ? mediaUrl(media.proxy_path) : undefined} time={ticksToSeconds(time)} playing={playing} rate={rate} style={{ width: '100%', height: '100%' }} />
                  {media.audio && <MediaLayer url={mediaSrc(media)} kind="audio" time={ticksToSeconds(time)} playing={playing} rate={rate} />}
                </>
              )
            )}
            {media && !media.video && media.kind !== 'image' && (
              <>
                <MediaLayer url={mediaSrc(media)} kind="audio" time={ticksToSeconds(time)} playing={playing} rate={rate} />
                <div className="lm-audio-only">{media.name}</div>
              </>
            )}
          </>
        )}
      </Frame>
      <div className="lm-monitor-bar">
        <Timecode value={time} rate={r} onCommit={(v) => goTo('source', v)} />
        <span style={{ flex: 1 }} />
        <span className="lm-tc dim">{media ? timecode(inOutDur, r) : '00:00:00:00'}</span>
      </div>
      <Scrubber monitor="source" duration={duration} time={time} inPoint={media?.in_point} outPoint={media?.out_point} />
      <Transport monitor="source" playing={playing} />
    </div>
  )
}

// ---------------------------------------------------------------- Program: composited stage

type VisualLayer = {
  key: string
  clip: Clip | null // null = solid colour (Dip to Black/White)
  media: MediaItem | null
  z: number
  color?: string
  group?: string // transition group: children are summed (plus-lighter) inside an isolated group
  fade?: number
  preroll?: boolean // upcoming clip: mounted hidden, paused on its first frame
}
type AudioLayer = { key: string; clip: Clip; media: MediaItem; time: number; volume: number; span: [number, number]; preroll?: boolean }

/** Layers visible at ``at``. Each clip gets its own media element (keyed by clip id), so a
 * cut never makes a playing element seek; with ``lookahead`` (ticks) clips starting soon are
 * added as hidden ``preroll`` layers already seeked to their first frame, so playback crosses
 * a cut without loading or seeking. */
export function activeLayers(seq: Sequence, media: Record<string, MediaItem>, at: number, lookahead = 0): { visual: VisualLayer[]; audio: AudioLayer[] } {
  const visual: VisualLayer[] = []
  const audio: AudioLayer[] = []
  const tpf = ticksPerFrame(seq.settings)
  const clipLayer = (_tid: string, clip: Clip, z: number, extra: Partial<VisualLayer> = {}, suffix = ''): VisualLayer | null => {
    if (clip.graphic) return { key: `g-${clip.id}`, clip, media: null, z, ...extra }
    const m = clip.media_id ? media[clip.media_id] : undefined
    if (m && (m.video || m.kind === 'image')) return { key: `v-${clip.id}${suffix}`, clip, media: m, z, ...extra }
    return null
  }
  seq.video_tracks.forEach((tid, z) => {
    const track = seq.tracks[tid]
    if (track.hidden) return
    const tw = trackTransitions(track).find((w) => w.start <= at && at < w.end)
    if (tw) {
      const u = transitionProgress(tw, at, tpf)
      const group = `t-${tw.tr.id}`
      const a = tw.a?.enabled ? tw.a : null
      const b = tw.b?.enabled ? tw.b : null
      const sameMedia = a && b && a.media_id && a.media_id === b.media_id
      const push = (l: VisualLayer | null) => l && visual.push(l)
      if (tw.tr.type === 'dip_to_black' || tw.tr.type === 'dip_to_white') {
        const color = tw.tr.type === 'dip_to_black' ? '#000' : '#fff'
        if (u < 0.5) {
          if (a) push(clipLayer(tid, a, z, { group, fade: 1 - 2 * u }))
          visual.push({ key: `${group}-solid`, clip: null, media: null, z, color, group, fade: 2 * u })
        } else {
          visual.push({ key: `${group}-solid`, clip: null, media: null, z, color, group, fade: 2 - 2 * u })
          if (b) push(clipLayer(tid, b, z, { group, fade: 2 * u - 1 }))
        }
      } else {
        if (a) push(clipLayer(tid, a, z, { group, fade: 1 - u }))
        if (b) push(clipLayer(tid, b, z, { group, fade: u }, sameMedia ? '-in' : ''))
      }
      return
    }
    for (const clip of Object.values(track.clips)) {
      if (!clip.enabled || at < clip.start || at >= clip.end) continue
      const l = clipLayer(tid, clip, z)
      if (l) visual.push(l)
    }
    if (lookahead !== 0) {
      // forward: clips starting soon; reverse (negative lookahead): clips that just ended behind us
      for (const clip of Object.values(track.clips)) {
        if (!clip.enabled || clip.graphic) continue
        const soon = lookahead > 0
          ? clip.start > at && clip.start - at <= lookahead
          : clip.end <= at && at - clip.end <= -lookahead
        if (!soon) continue
        const l = clipLayer(tid, clip, z, { preroll: true })
        if (l && l.media?.video) visual.push(l)
      }
    }
  })
  const anySolo = seq.audio_tracks.some((id) => seq.tracks[id].solo)
  for (const tid of seq.audio_tracks) {
    const track = seq.tracks[tid]
    if (track.muted || (anySolo && !track.solo)) continue
    // Constant Power crossfades extend clips into their handles
    const gains = new Map<string, number>()
    const ext = new Map<string, [number, number]>()
    for (const w of trackTransitions(track)) {
      for (const [c, kind] of [[w.a, 'out'], [w.b, 'in']] as const) {
        if (!c) continue
        const [s0, e0] = ext.get(c.id) ?? [c.start, c.end]
        ext.set(c.id, [Math.min(s0, w.start), Math.max(e0, w.end)])
        if (at >= w.start && at < w.end) {
          const u = (at - w.start) / Math.max(1, w.end - w.start)
          gains.set(c.id, (gains.get(c.id) ?? 1) * (kind === 'in' ? Math.sin((u * Math.PI) / 2) : Math.cos((u * Math.PI) / 2)))
        }
      }
    }
    for (const clip of Object.values(track.clips)) {
      const [s0, e0] = ext.get(clip.id) ?? [clip.start, clip.end]
      if (!clip.enabled || at < s0 || at >= e0) continue
      const m = clip.media_id ? media[clip.media_id] : undefined
      if (!m || (!m.audio && m.kind !== 'audio')) continue
      audio.push({
        key: `a-${clip.id}`, clip, media: m, time: ticksToSeconds(sourceTime(clip, at)), span: [s0, e0],
        volume: dbToGain(valueAt(clip, 'volume_db', at, seq, m) as number) * (gains.get(clip.id) ?? 1)
      })
    }
    if (lookahead > 0) {
      for (const clip of Object.values(track.clips)) {
        const s0 = ext.get(clip.id)?.[0] ?? clip.start
        if (!clip.enabled || s0 <= at || s0 - at > lookahead) continue
        const m = clip.media_id ? media[clip.media_id] : undefined
        if (!m || (!m.audio && m.kind !== 'audio')) continue
        // the gain it starts with: scheduled audio is heard before the picture reaches the cut
        const e0 = ext.get(clip.id)?.[1] ?? clip.end
        const g0 = dbToGain(valueAt(clip, 'volume_db', s0, seq, m) as number) * (s0 < clip.start ? 0 : 1)
        audio.push({ key: `a-${clip.id}`, clip, media: m, time: ticksToSeconds(sourceTime(clip, s0)), span: [s0, e0], volume: g0, preroll: true })
      }
    }
  }
  return { visual, audio }
}

function trackGroups(layers: VisualLayer[]): { z: number; transition: boolean; layers: VisualLayer[] }[] {
  const out = new Map<number, { z: number; transition: boolean; layers: VisualLayer[] }>()
  for (const l of layers) {
    const g = out.get(l.z) ?? { z: l.z, transition: false, layers: [] }
    g.layers.push(l)
    g.transition ||= !!l.group
    out.set(l.z, g)
  }
  return [...out.values()].sort((a, b) => a.z - b.z)
}

const PREROLL = 2 * TICKS_PER_SECOND

function Stage({ seq, media, at, playing, rate, scale }: {
  seq: Sequence; media: Record<string, MediaItem>; at: number; playing: boolean; rate: number; scale: number
}) {
  const { width: W, height: H } = seq.settings
  const tool = useStore((s) => s.tool)
  const selected = useStore((s) => s.selectedClips)
  const [drag, setDrag] = useState<{ clipId: string; dx: number; dy: number } | null>(null)
  const [draw, setDraw] = useState<number[] | null>(null)
  // pixels come from the GPU compositor; the DOM layers stay for selection and dragging
  const [glFailed, setGlFailed] = useState(false)
  const gl = !glFailed
  const { visual, audio } = activeLayers(seq, media, at, playing ? (rate < 0 ? -PREROLL : PREROLL) : 0)
  useEffect(() => {
    audioEngine.sync(
      audio
        .filter((a) => a.clip.speed > 0 && audioEngine.status(mediaSrc(a.media)) === 'ok')
        .map((a) => ({ key: a.key, url: mediaSrc(a.media), start: a.clip.start, sourceIn: a.clip.source_in, speed: a.clip.speed, span: a.span, gain: a.volume }))
    )
  })

  const toSeq = (e: React.MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    return [(e.clientX - r.left) / scale, (e.clientY - r.top) / scale]
  }

  const onMouseDown = async (e: React.MouseEvent) => {
    if (e.button !== 0) return
    if (tool === 'type') {
      const [x, y] = toSeq(e)
      const res = await exec<{ clip_id: string }>('graphic.create_text', { sequence_id: seq.id, at, text: t('New Text Layer'), position: [x, y] }, t('New Layer > Text'))
      if (res) {
        set({ selectedClips: [res.clip_id], tool: 'select' })
        focusPanel('properties')
      }
      return
    }
    if (tool === 'rectangle' || tool === 'ellipse') {
      const [x0, y0] = toSeq(e)
      const stage = e.currentTarget as HTMLElement
      let rect = [x0, y0, x0, y0]
      const move = (ev: MouseEvent) => {
        const r = stage.getBoundingClientRect()
        let x1 = (ev.clientX - r.left) / scale
        let y1 = (ev.clientY - r.top) / scale
        if (ev.shiftKey) {
          const d = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0))
          x1 = x0 + Math.sign(x1 - x0 || 1) * d
          y1 = y0 + Math.sign(y1 - y0 || 1) * d
        }
        rect = [x0, y0, x1, y1]
        setDraw(rect)
      }
      const up = () => {
        window.removeEventListener('mousemove', move)
        window.removeEventListener('mouseup', up)
        setDraw(null)
        const small = Math.abs(rect[2] - rect[0]) < 4 || Math.abs(rect[3] - rect[1]) < 4
        const s = small ? Math.min(W, H) / 5 : 0
        void newShapeLayer(tool, small ? [x0 - s, y0 - s, x0 + s, y0 + s] : rect)
      }
      window.addEventListener('mousemove', move)
      window.addEventListener('mouseup', up)
      return
    }
    const hit = (e.target as HTMLElement).closest('[data-clip]') as HTMLElement | null
    if (!hit) {
      set({ selectedClips: [] })
      return
    }
    const clipId = hit.dataset.clip!
    if (!get().selectedClips.includes(clipId)) set({ selectedClips: [clipId] })
    const start = { x: e.clientX, y: e.clientY }
    let last = { dx: 0, dy: 0 }
    const move = (ev: MouseEvent) => {
      last = { dx: (ev.clientX - start.x) / scale, dy: (ev.clientY - start.y) / scale }
      setDrag({ clipId, ...last })
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      setDrag(null)
      const layer = visual.find((l) => !l.preroll && l.clip?.id === clipId)
      if (!layer || (Math.abs(last.dx) < 0.5 && Math.abs(last.dy) < 0.5)) return
      const pos = valueAt(layer.clip!, 'position', at, seq, layer.media) as number[]
      void exec('clip.set_property', { sequence_id: seq.id, clip_id: clipId, prop: 'position', value: [pos[0] + last.dx, pos[1] + last.dy], at }, t('Position'))
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  const resolved = (l: { clip: Clip | null; media: MediaItem | null }) => {
    const r = transformAt(l.clip!, at, seq, l.media)
    if (drag?.clipId === l.clip!.id) r.position = [r.position[0] + drag.dx, r.position[1] + drag.dy]
    return r
  }

  const renderLayer = (l: VisualLayer) => {
    if (!l.clip) return <div key={l.key} style={{ position: 'absolute', inset: 0, width: W, height: H, background: gl ? undefined : l.color }} />
    const clip = l.clip
    const r = transformAt(clip, at, seq, l.media)
    if (drag?.clipId === clip.id) r.position = [r.position[0] + drag.dx, r.position[1] + drag.dy]
    const [w, h] = l.media?.video ? [l.media.video.width, l.media.video.height] : [W, H]
    const isSel = selected.includes(clip.id)
    let content: React.ReactNode = clip.graphic ? (
      <GraphicView graphic={clip.graphic} />
    ) : gl ? (
      <div style={{ width: '100%', height: '100%' }} />
    ) : l.media!.kind === 'image' ? (
      <MediaLayer url={mediaSrc(l.media!)} kind="image" time={0} playing={false} rate={rate} style={{ width: '100%', height: '100%', objectFit: 'fill', display: 'block' }} />
    ) : (
      <FrameCanvas
        url={mediaUrl(l.media!.path)}
        fallbackUrl={l.media!.proxy_path ? mediaUrl(l.media!.proxy_path) : undefined}
        // a prerolled clip waits on its first frame (forward) or its last frame (reverse)
        time={ticksToSeconds(sourceTime(clip, !l.preroll ? at : rate < 0 ? clip.end - ticksPerFrame(seq.settings) : clip.start))}
        playing={playing}
        hold={l.preroll}
        rate={rate * clip.speed}
        style={{ width: '100%', height: '100%' }}
      />
    )
    // standard effects run before Motion: the first effect is the innermost wrapper
    for (const fx of clip.effects ?? []) {
      const css = effectCss(fx, clip, at, w, h)
      if (css) content = <div className="lm-fx" data-fx={fx.id} style={{ width: w, height: h, ...css }}>{content}</div>
    }
    return (
      <div
        key={l.key}
        data-clip={l.preroll ? undefined : clip.id}
        className={`lm-layer ${isSel && !l.preroll ? 'selected' : ''}`}
        style={{ width: w, height: h, zIndex: l.z, ...transformCss(r), ...(l.preroll ? { opacity: 0, pointerEvents: 'none' } : null) }}
      >
        {content}
        {isSel && <div className="lm-layer-box" style={{ borderWidth: 1 / scale }} />}
      </div>
    )
  }

  return (
    <div className={`lm-stage ${gl ? 'gl' : ''} ${tool === 'type' ? 'typing' : tool === 'rectangle' || tool === 'ellipse' ? 'drawing' : ''}`} style={{ width: W, height: H, transform: `scale(${scale})` }} onMouseDown={onMouseDown}>
      {gl && (
        <GLStage
          seq={seq}
          layers={visual}
          at={at}
          playing={playing}
          rate={rate}
          scale={Math.min(1, scale * (window.devicePixelRatio || 1))}
          resolve={resolved}
          onFailed={() => setGlFailed(true)}
        />
      )}
      {trackGroups(visual).map((g) => (
        // one stable container per track and one slot per clip: a clip keeps its media element
        // across cuts, preroll and the start/end of a transition (a remount would flash)
        <div key={`track-${g.z}`} className={`lm-track ${g.transition ? 'lm-tgroup' : ''}`} style={{ zIndex: g.z }}>
          {g.layers.map((l) => (
            <div key={l.key} className={l.group ? 'lm-tpart' : 'lm-slot'} style={{ width: W, height: H, opacity: l.fade ?? 1 }}>
              {renderLayer(l)}
            </div>
          ))}
        </div>
      ))}
      {draw && (
        <div
          className={`lm-draw ${tool === 'ellipse' ? 'ellipse' : ''}`}
          style={{
            left: Math.min(draw[0], draw[2]), top: Math.min(draw[1], draw[3]),
            width: Math.abs(draw[2] - draw[0]), height: Math.abs(draw[3] - draw[1]), borderWidth: 1 / scale
          }}
        />
      )}
      {audio.filter((a) => audioEngine.status(mediaSrc(a.media)) === 'fail' || a.clip.speed <= 0).map((a) => (
        <MediaLayer key={a.key} url={mediaSrc(a.media)} kind="audio" time={a.time} playing={playing && !a.preroll} rate={rate * a.clip.speed} volume={a.preroll ? 0 : a.volume} />
      ))}
    </div>
  )
}

/** Premiere's Select Playback Resolution dropdown (paused frames are always full resolution). */
function PlaybackResolution() {
  const res = useStore((s) => s.playbackResolution)
  return (
    <select
      className="lm-playres"
      title={t('Select Playback Resolution')}
      value={res}
      onChange={(e) => {
        const v = Number(e.target.value) as 1 | 0.5 | 0.25
        set({ playbackResolution: v })
        try {
          localStorage.setItem('lumina.playbackResolution', String(v))
        } catch {
          // per-viewer convenience only
        }
      }}
    >
      <option value={1}>{t('Full')}</option>
      <option value={0.5}>1/2</option>
      <option value={0.25}>1/4</option>
    </select>
  )
}

export function ProgramMonitor() {
  const seq = useStore((s) => activeSequence(s))
  const mediaMap = useStore((s) => s.doc?.media)
  const time = useStore((s) => s.programTime)
  const playing = useStore((s) => s.playing === 'program')
  const rate = useStore((s) => s.shuttle)

  if (!seq || !mediaMap) {
    return (
      <div className="lm-panel lm-monitor">
        <div className="lm-monitor-title">{t('Program')}: {t('(no sequences)')}</div>
        <Frame width={1920} height={1080}>{() => null}</Frame>
        <div className="lm-monitor-bar"><span className="lm-tc">00:00:00:00</span></div>
        <Transport monitor="program" playing={false} />
      </div>
    )
  }
  const st = seq.settings
  const duration = sequenceEnd(seq)
  return (
    <div className="lm-panel lm-monitor">
      <div className="lm-monitor-title">{t('Program')}: {seq.name}</div>
      <Frame width={st.width} height={st.height}>
        {(scale) => <Stage seq={seq} media={mediaMap} at={floorToFrame(time, st)} playing={playing} rate={rate} scale={scale} />}
      </Frame>
      <div className="lm-monitor-bar">
        <Timecode value={time} rate={st} onCommit={(v) => goTo('program', v)} />
        <span style={{ flex: 1 }} />
        <PlaybackResolution />
        <span className="lm-tc dim">{timecode(seq.in_point != null && seq.out_point != null ? seq.out_point - seq.in_point : duration, st)}</span>
      </div>
      <Scrubber monitor="program" duration={Math.max(duration, 1)} time={time} inPoint={seq.in_point} outPoint={seq.out_point} markers={Object.values(seq.markers).map((m) => m.start)} />
      <Transport monitor="program" playing={playing} />
    </div>
  )
}

// ---------------------------------------------------------------- transport buttons (Premiere layout)

function Transport({ monitor, playing }: { monitor: Monitor; playing: boolean }) {
  const focus = () => set({ focusedPanel: monitor })
  const btn = (title: string, key: string, action: string, Icon: React.FC<{ size?: number }>) => (
    <button className="lm-iconbtn" title={`${t(title)} (${key})`} onMouseDown={focus} onClick={() => runAction(action)}>
      <Icon size={15} />
    </button>
  )
  return (
    <div className="lm-transport">
      {btn('Add Marker', 'M', 'markers.add', Bookmark)}
      {btn('Mark In', 'I', 'markers.markIn', ChevronFirst)}
      {btn('Mark Out', 'O', 'markers.markOut', ChevronLast)}
      {btn('Go to In', 'Shift+I', 'markers.goIn', ArrowRightToLine)}
      {btn('Step Back 1 Frame', '←', 'transport.stepBack', StepBack)}
      <button className="lm-iconbtn play" title={`${t('Play-Stop Toggle')} (Space)`} onMouseDown={focus} onClick={() => runAction('transport.playStop')}>
        {playing ? <Pause size={17} /> : <Play size={17} />}
      </button>
      {btn('Step Forward 1 Frame', '→', 'transport.stepForward', StepForward)}
      {btn('Go to Out', 'Shift+O', 'markers.goOut', ArrowDownToLine)}
      {monitor === 'source' ? (
        <>
          {btn('Insert', ',', 'clip.insert', InsertIcon)}
          {btn('Overwrite', '.', 'clip.overwrite', Replace)}
        </>
      ) : (
        <>
          {btn('Lift', ';', 'sequence.lift', Upload)}
          {btn('Extract', "'", 'sequence.extract', Brackets)}
          {btn('Go to Previous Edit Point', '↑', 'transport.prevEdit', SkipBack)}
          {btn('Go to Next Edit Point', '↓', 'transport.nextEdit', SkipForward)}
        </>
      )}
    </div>
  )
}
