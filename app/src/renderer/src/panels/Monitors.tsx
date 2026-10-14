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
import { parseTimecode, ticksToSeconds, timecode, TICKS_PER_SECOND, type Rate } from '../util/time'
import { sourceTime, transformAt, transformCss, valueAt } from '../util/motion'
import { MediaLayer } from './MediaLayer'
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
              <MediaLayer url={mediaSrc(media)} kind={media.kind === 'image' ? 'image' : 'video'} time={ticksToSeconds(time)} playing={playing} rate={rate} style={{ width: '100%', height: '100%' }} />
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

type VisualLayer = { key: string; clip: Clip; media: MediaItem | null; z: number }
type AudioLayer = { key: string; clip: Clip; media: MediaItem; time: number; volume: number }

export function activeLayers(seq: Sequence, media: Record<string, MediaItem>, at: number): { visual: VisualLayer[]; audio: AudioLayer[] } {
  const visual: VisualLayer[] = []
  const audio: AudioLayer[] = []
  seq.video_tracks.forEach((tid, z) => {
    const track = seq.tracks[tid]
    if (track.hidden) return
    for (const clip of Object.values(track.clips)) {
      if (!clip.enabled || at < clip.start || at >= clip.end) continue
      if (clip.graphic) {
        visual.push({ key: `g-${clip.id}`, clip, media: null, z })
        continue
      }
      const m = clip.media_id ? media[clip.media_id] : undefined
      if (m && (m.video || m.kind === 'image')) visual.push({ key: `v-${tid}-${m.id}`, clip, media: m, z })
    }
  })
  const anySolo = seq.audio_tracks.some((id) => seq.tracks[id].solo)
  for (const tid of seq.audio_tracks) {
    const track = seq.tracks[tid]
    if (track.muted || (anySolo && !track.solo)) continue
    for (const clip of Object.values(track.clips)) {
      if (!clip.enabled || at < clip.start || at >= clip.end) continue
      const m = clip.media_id ? media[clip.media_id] : undefined
      if (!m || (!m.audio && m.kind !== 'audio')) continue
      audio.push({
        key: `a-${tid}-${m.id}`, clip, media: m, time: ticksToSeconds(sourceTime(clip, at)),
        volume: dbToGain(valueAt(clip, 'volume_db', at, seq, m) as number)
      })
    }
  }
  return { visual, audio }
}

function Stage({ seq, media, at, playing, rate, scale }: {
  seq: Sequence; media: Record<string, MediaItem>; at: number; playing: boolean; rate: number; scale: number
}) {
  const { width: W, height: H } = seq.settings
  const tool = useStore((s) => s.tool)
  const selected = useStore((s) => s.selectedClips)
  const [drag, setDrag] = useState<{ clipId: string; dx: number; dy: number } | null>(null)
  const { visual, audio } = activeLayers(seq, media, at)

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
      const layer = visual.find((l) => l.clip.id === clipId)
      if (!layer || (Math.abs(last.dx) < 0.5 && Math.abs(last.dy) < 0.5)) return
      const pos = valueAt(layer.clip, 'position', at, seq, layer.media) as number[]
      void exec('clip.set_property', { sequence_id: seq.id, clip_id: clipId, prop: 'position', value: [pos[0] + last.dx, pos[1] + last.dy], at }, t('Position'))
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  return (
    <div className={`lm-stage ${tool === 'type' ? 'typing' : ''}`} style={{ width: W, height: H, transform: `scale(${scale})` }} onMouseDown={onMouseDown}>
      {visual.map((l) => {
        const r = transformAt(l.clip, at, seq, l.media)
        if (drag?.clipId === l.clip.id) r.position = [r.position[0] + drag.dx, r.position[1] + drag.dy]
        const [w, h] = l.media?.video ? [l.media.video.width, l.media.video.height] : [W, H]
        const isSel = selected.includes(l.clip.id)
        return (
          <div key={l.key} data-clip={l.clip.id} className={`lm-layer ${isSel ? 'selected' : ''}`} style={{ width: w, height: h, zIndex: l.z, ...transformCss(r) }}>
            {l.clip.graphic ? (
              <GraphicView graphic={l.clip.graphic} />
            ) : (
              <MediaLayer
                url={mediaSrc(l.media!)}
                kind={l.media!.kind === 'image' ? 'image' : 'video'}
                time={ticksToSeconds(sourceTime(l.clip, at))}
                playing={playing}
                rate={rate * l.clip.speed}
                muted
                style={{ width: '100%', height: '100%', objectFit: 'fill', display: 'block' }}
              />
            )}
            {isSel && <div className="lm-layer-box" style={{ borderWidth: 1 / scale }} />}
          </div>
        )
      })}
      {audio.map((a) => (
        <MediaLayer key={a.key} url={mediaSrc(a.media)} kind="audio" time={a.time} playing={playing} rate={rate * a.clip.speed} volume={a.volume} />
      ))}
    </div>
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
        {(scale) => <Stage seq={seq} media={mediaMap} at={time} playing={playing} rate={rate} scale={scale} />}
      </Frame>
      <div className="lm-monitor-bar">
        <Timecode value={time} rate={st} onCommit={(v) => goTo('program', v)} />
        <span style={{ flex: 1 }} />
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
