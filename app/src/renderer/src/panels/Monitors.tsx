import { useEffect, useRef, useState } from 'react'
import {
  ArrowDownToLine, ArrowRightToLine, Bookmark, Brackets, ChevronFirst, ChevronLast, Pause, Play, SkipBack, SkipForward,
  StepBack, StepForward, Upload, Import as InsertIcon, Replace
} from 'lucide-react'
import type { Clip, MediaItem, Sequence } from '../engine/document'
import { mediaUrl } from '../engine/client'
import { runAction } from '../actions/actions'
import { DRAG_MIME } from './ProjectPanel'
import { activeSequence, set, sequenceEnd, useStore } from '../state/store'
import { goTo, type Monitor } from '../state/transport'
import { parseTimecode, ticksToSeconds, timecode, TICKS_PER_SECOND, type Rate } from '../util/time'
import { MediaLayer } from './MediaLayer'

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
          const t = parseTimecode(editing, rate)
          if (t != null) onCommit(t)
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
  const pct = (t: number) => `${duration ? (100 * t) / duration : 0}%`
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

function Frame({ width, height, children, onDrop }: { width: number; height: number; children: React.ReactNode; onDrop?: (e: React.DragEvent) => void }) {
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
        {children}
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
      <div className="lm-monitor-title">{media ? `Source: ${media.name}` : 'Source: (no clips)'}</div>
      <Frame width={media?.video?.width || 1920} height={media?.video?.height || 1080} onDrop={onDrop}>
        {media && (media.video || media.kind === 'image') && (
          <MediaLayer url={mediaSrc(media)} kind={media.kind === 'image' ? 'image' : 'video'} time={ticksToSeconds(time)} playing={playing} rate={rate} style={{ width: '100%', height: '100%' }} />
        )}
        {media && !media.video && media.kind !== 'image' && (
          <>
            <MediaLayer url={mediaSrc(media)} kind="audio" time={ticksToSeconds(time)} playing={playing} rate={rate} />
            <div className="lm-audio-only">{media.name}</div>
          </>
        )}
      </Frame>
      <div className="lm-monitor-bar">
        <Timecode value={time} rate={r} onCommit={(t) => goTo('source', t)} />
        <span style={{ flex: 1 }} />
        <span className="lm-tc dim">{media ? timecode(inOutDur, r) : '00:00:00:00'}</span>
      </div>
      <Scrubber monitor="source" duration={duration} time={time} inPoint={media?.in_point} outPoint={media?.out_point} />
      <Transport monitor="source" playing={playing} />
    </div>
  )
}

// ---------------------------------------------------------------- Program

type ActiveLayer = { key: string; clip: Clip; media: MediaItem; kind: 'video' | 'audio' | 'image'; time: number; z: number; volume: number }

export function activeLayers(seq: Sequence, media: Record<string, MediaItem>, t: number): ActiveLayer[] {
  const out: ActiveLayer[] = []
  const anySolo = seq.audio_tracks.some((id) => seq.tracks[id].solo)
  seq.video_tracks.forEach((tid, z) => {
    const track = seq.tracks[tid]
    if (track.hidden) return
    for (const clip of Object.values(track.clips)) {
      if (!clip.enabled || t < clip.start || t >= clip.end) continue
      const m = media[clip.media_id]
      if (!m) continue
      out.push({ key: `v-${tid}-${m.id}`, clip, media: m, kind: m.kind === 'image' ? 'image' : 'video', time: ticksToSeconds(clip.source_in + (t - clip.start) * clip.speed), z, volume: 0 })
    }
  })
  for (const tid of seq.audio_tracks) {
    const track = seq.tracks[tid]
    if (track.muted || (anySolo && !track.solo)) continue
    for (const clip of Object.values(track.clips)) {
      if (!clip.enabled || t < clip.start || t >= clip.end) continue
      const m = media[clip.media_id]
      if (!m || (!m.audio && m.kind !== 'audio')) continue
      out.push({ key: `a-${tid}-${m.id}`, clip, media: m, kind: 'audio', time: ticksToSeconds(clip.source_in + (t - clip.start) * clip.speed), z: -1, volume: dbToGain(clip.volume_db) })
    }
  }
  return out
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
        <div className="lm-monitor-title">Program: (no sequences)</div>
        <Frame width={1920} height={1080}>{null}</Frame>
        <div className="lm-monitor-bar"><span className="lm-tc">00:00:00:00</span></div>
        <Transport monitor="program" playing={false} />
      </div>
    )
  }
  const st = seq.settings
  const layers = activeLayers(seq, mediaMap, time)
  const duration = sequenceEnd(seq)
  return (
    <div className="lm-panel lm-monitor">
      <div className="lm-monitor-title">Program: {seq.name}</div>
      <Frame width={st.width} height={st.height}>
        {layers.map((l) =>
          l.kind === 'audio' ? (
            <MediaLayer key={l.key} url={mediaSrc(l.media)} kind="audio" time={l.time} playing={playing} rate={rate * l.clip.speed} volume={l.volume} />
          ) : (
            <MediaLayer
              key={l.key}
              url={mediaSrc(l.media)}
              kind={l.kind}
              time={l.time}
              playing={playing}
              rate={rate * l.clip.speed}
              muted
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', opacity: l.clip.opacity / 100, zIndex: l.z }}
            />
          )
        )}
      </Frame>
      <div className="lm-monitor-bar">
        <Timecode value={time} rate={st} onCommit={(t) => goTo('program', t)} />
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
  const btn = (title: string, action: string, Icon: React.FC<{ size?: number }>) => (
    <button className="lm-iconbtn" title={title} onMouseDown={focus} onClick={() => runAction(action)}>
      <Icon size={15} />
    </button>
  )
  return (
    <div className="lm-transport">
      {btn('Add Marker (M)', 'markers.add', Bookmark)}
      {btn('Mark In (I)', 'markers.markIn', ChevronFirst)}
      {btn('Mark Out (O)', 'markers.markOut', ChevronLast)}
      {btn('Go to In (Shift+I)', 'markers.goIn', ArrowRightToLine)}
      {btn('Step Back 1 Frame (Left)', 'transport.stepBack', StepBack)}
      <button className="lm-iconbtn play" title="Play-Stop Toggle (Space)" onMouseDown={focus} onClick={() => runAction('transport.playStop')}>
        {playing ? <Pause size={17} /> : <Play size={17} />}
      </button>
      {btn('Step Forward 1 Frame (Right)', 'transport.stepForward', StepForward)}
      {btn('Go to Out (Shift+O)', 'markers.goOut', ArrowDownToLine)}
      {monitor === 'source' ? (
        <>
          {btn('Insert (,)', 'clip.insert', InsertIcon)}
          {btn('Overwrite (.)', 'clip.overwrite', Replace)}
        </>
      ) : (
        <>
          {btn('Lift (;)', 'sequence.lift', Upload)}
          {btn('Extract (\')', 'sequence.extract', Brackets)}
          {btn('Go to Previous Edit Point (Up)', 'transport.prevEdit', SkipBack)}
          {btn('Go to Next Edit Point (Down)', 'transport.nextEdit', SkipForward)}
        </>
      )}
    </div>
  )
}

