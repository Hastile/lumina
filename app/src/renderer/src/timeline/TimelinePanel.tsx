import { EFFECT_MIME, type EffectDrag } from '../panels/EffectsPanel'
import { VolumeBand } from './VolumeBand'
import { EFFECTS, TRANSITIONS, trackTransitions, type TransitionWindow } from '../util/effects'
import { transitionDurationDialog } from './transitionDialog'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { t as tx } from '@shared/i18n'
import { Lock, LockOpen, Eye, EyeOff, Magnet, Link2, Bookmark, Wrench, RefreshCcw, X } from 'lucide-react'
import type { Clip, MediaItem, Sequence, Track } from '../engine/document'
import { engine } from '../engine/client'
import { runAction } from '../actions/actions'
import { openContextMenu } from '../layout/Chrome'
import { openDialog } from '../state/dialog'
import { DRAG_MIME } from '../panels/ProjectPanel'
import { activeSequence, closeSequence, exec, findClip, get, openSequence, sequenceEnd, set, toast, useStore } from '../state/store'
import { goTo } from '../state/transport'
import { floorToFrame, parseTimecode, snapToFrame, ticksPerFrame, timecode, TICKS_PER_SECOND } from '../util/time'
import { drawWaveform, loadFilmstrip, loadWaveform } from './waveform'
import { mediaUrl } from '../engine/client'
import { addCaptionTrack, addTrackEffect, applyPresetToTrack, captionStyleDialog, editCaptionText, exportCaptions, graphicsToCaptions, importCaptions, upgradeCaptions } from '../actions/extra'
import { effectMedia } from '../util/effects'
import { isCaptionTrack } from '../util/tracks'

const HEADER_W = 190
const RULER_H = 30
const VIDEO_H = 42
const AUDIO_H = 46
const SECTION_GAP = 6
const MIN_TRACK_H = 22
const MAX_TRACK_H = 320
const VBAR_W = 10

function loadJson<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key)
    return v ? (JSON.parse(v) as T) : fallback
  } catch {
    return fallback
  }
}
function saveJson(key: string, v: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(v))
  } catch {
    /* storage unavailable */
  }
}
const EDGE_PX = 6
const SNAP_PX = 8

type Row = { track: Track; label: string; kind: 'video' | 'audio'; index: number; top: number; height: number }

type Drag =
  | { kind: 'move'; ids: string[]; anchorRow: number; startX: number; startY: number; dt: number; dRow: number; moved: boolean; base: Clip }
  | { kind: 'trim'; clipId: string; edge: 'start' | 'end'; ripple: boolean; startX: number; dt: number }
  | { kind: 'hand'; startX: number; startScroll: number }
  | { kind: 'marquee'; x0: number; y0: number; x1: number; y1: number; base: string[] }
  | { kind: 'tool'; tool: EditTool; clipId: string; ids: string[]; edge: 'start' | 'end' | null; startX: number; dt: number }

type EditTool = 'rolling' | 'rate' | 'slip' | 'slide'
const EDIT_TOOLS: Record<EditTool, string> = { rolling: 'clip.roll', rate: 'clip.rate_stretch', slip: 'clip.slip', slide: 'clip.slide' }
const EDGE_TOOLS = new Set(['select', 'ripple', 'rolling', 'rate'])

type Shown = { start: number; end: number; srcIn: number; speed: number }

/** What a Rolling / Rate Stretch / Slip / Slide drag will do (the engine's clip.roll etc.), for the live preview. */
function previewTool(seq: Sequence, d: Extract<Drag, { kind: 'tool' }>): Map<string, Shown> {
  const out = new Map<string, Shown>()
  const dt = d.dt
  if (!dt) return out
  const shown = (c: Clip): Shown => out.get(c.id) ?? { start: c.start, end: c.end, srcIn: c.source_in, speed: c.speed }
  for (const tr of Object.values(seq.tracks)) {
    const clips = Object.values(tr.clips)
    for (const c of clips) {
      if (!d.ids.includes(c.id)) continue
      const before = clips.find((o) => o.end === c.start)
      const after = clips.find((o) => o.start === c.end)
      const v = shown(c)
      if (d.tool === 'slip') out.set(c.id, { ...v, srcIn: v.srcIn - dt * c.speed })
      else if (d.tool === 'rate') {
        const len = c.end - c.start
        const next = d.edge === 'end' ? { ...v, end: c.end + dt } : { ...v, start: c.start + dt }
        out.set(c.id, { ...next, speed: (len * c.speed) / Math.max(1, next.end - next.start) })
      } else if (d.tool === 'rolling') {
        const [l, r] = d.edge === 'end' ? [c, after] : [before, c]
        if (l) out.set(l.id, { ...shown(l), end: l.end + dt })
        if (r) out.set(r.id, { ...shown(r), start: r.start + dt, srcIn: r.source_in + dt * r.speed })
      } else {
        out.set(c.id, { ...v, start: c.start + dt, end: c.end + dt })
        if (before) out.set(before.id, { ...shown(before), end: before.end + dt })
        if (after) out.set(after.id, { ...shown(after), start: after.start + dt, srcIn: after.source_in + dt * after.speed })
      }
    }
  }
  return out
}

/** Frames a linked clip is out of sync with its partner of the same source (engine edit_tools.sync_anchor),
 * or 0. Positive: this clip plays its source later than the partner. */
export function syncOffset(seq: Sequence, clip: Clip): number {
  if (!clip.link_id) return 0
  const src = clip.media_id ?? clip.sequence_id
  const kind = Object.values(seq.tracks).find((t) => t.clips[clip.id])?.kind
  for (const t of Object.values(seq.tracks)) {
    if (t.kind === kind) continue
    for (const o of Object.values(t.clips)) {
      if (o.link_id !== clip.link_id || (o.media_id ?? o.sequence_id) !== src || o.speed !== clip.speed) continue
      const anchor = (c: Clip) => c.start - c.source_in / c.speed
      return Math.round((anchor(clip) - anchor(o)) / ticksPerFrame(seq.settings))
    }
  }
  return 0
}

/** Every animated property's keyframes on a clip, as sequence times inside it (the rubber band's own
 * property excluded - the band shows those). */
function clipKeyframeTimes(clip: Clip, kind: 'video' | 'audio'): { t: number; props: string[] }[] {
  const at = new Map<number, string[]>()
  const add = (name: string, ts: number[]) => {
    for (const x of ts) {
      const s = Math.round(clip.start + (x - clip.source_in) / clip.speed)
      if (s < clip.start || s >= clip.end) continue
      at.set(s, [...(at.get(s) ?? []), name])
    }
  }
  for (const [prop, kfs] of Object.entries(clip.keyframes)) {
    if (prop === (kind === 'audio' ? 'volume_db' : 'opacity') || !kfs) continue
    add(prop, (kfs as { t: number }[]).map((k) => k.t))
  }
  for (const fx of clip.effects ?? []) for (const [param, kfs] of Object.entries(fx.keyframes ?? {})) add(`${fx.type}.${param}`, (kfs as { t: number }[]).map((k) => k.t))
  return [...at].map(([t, props]) => ({ t, props })).sort((a, b) => a.t - b.t)
}

type Layout = { vContent: number; aContent: number; rows: Row[]; videoH: number; audioTop: number; audioH: number; vMax: number; aMax: number; vScroll: number; aScroll: number }

/** Premiere's two track areas: video above the divider with V1 against it (scrolling up reveals the
 * higher tracks), audio below it with A1 against it; each scrolls on its own. Row tops are in the
 * body's coordinates. */
type Kind = 'video' | 'audio'
const clampH = (h: number) => Math.round(Math.max(MIN_TRACK_H, Math.min(MAX_TRACK_H, h)))

function buildRows(seq: Sequence, heights: Record<string, number>, zoom: Record<Kind, number>, bodyH: number, split: number, vScroll: number, aScroll: number): Layout {
  const videoH = Math.max(0, Math.min(bodyH - SECTION_GAP, Math.round(bodyH * split)))
  const audioTop = videoH + SECTION_GAP
  const audioH = Math.max(0, bodyH - audioTop)
  // a track's own height times its area's zoom (the vertical zoom bar's knobs)
  const hOf = (id: string, kind: Kind) => clampH((heights[id] ?? (kind === 'video' ? VIDEO_H : AUDIO_H)) * zoom[kind])
  const vContent = seq.video_tracks.reduce((a, id) => a + hOf(id, 'video'), 0)
  const aContent = seq.audio_tracks.reduce((a, id) => a + hOf(id, 'audio'), 0)
  // an area scrolls only as far as its tracks overflow it (zoom in with the vertical bar's knobs)
  const vMax = Math.max(0, vContent - videoH)
  const aMax = Math.max(0, aContent - audioH)
  const vs = Math.max(0, Math.min(vMax, vScroll))
  const as = Math.max(0, Math.min(aMax, aScroll))
  const vrows: Row[] = []
  let y = videoH + vs
  seq.video_tracks.forEach((id, index) => {
    const h = hOf(id, 'video')
    y -= h
    vrows.push({ track: seq.tracks[id], label: `V${index + 1}`, kind: 'video', index, top: y, height: h })
  })
  const rows = vrows.reverse()
  y = audioTop - as
  seq.audio_tracks.forEach((id, index) => {
    const h = hOf(id, 'audio')
    rows.push({ track: seq.tracks[id], label: `A${index + 1}`, kind: 'audio', index, top: y, height: h })
    y += h
  })
  return { vContent, aContent, rows, videoH, audioTop, audioH, vMax, aMax, vScroll: vs, aScroll: as }
}

function niceStep(pxPerSecond: number): number {
  // seconds between major ruler ticks, aiming for ~110px
  const target = 110 / pxPerSecond
  const steps = [1 / 30, 2 / 30, 5 / 30, 10 / 30, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]
  return steps.find((s) => s >= target) ?? 3600
}

export function TimelinePanel() {
  const seq = useStore((s) => activeSequence(s))
  const media = useStore((s) => s.doc?.media)
  if (!seq || !media) {
    return (
      <div className="lm-panel lm-timeline" onDragOver={(e) => e.preventDefault()} onDrop={(e) => void dropOnEmpty(e)}>
        <div className="lm-empty" style={{ marginTop: 40 }}>
          {tx('Drop media here to create a sequence.')}
        </div>
      </div>
    )
  }
  return <TimelineView seq={seq} media={media} />
}

async function dropOnEmpty(e: React.DragEvent) {
  e.preventDefault()
  const ids = JSON.parse(e.dataTransfer.getData(DRAG_MIME) || '[]') as string[]
  const id = ids.find((i) => get().doc?.media[i])
  if (id) await exec('sequence.create_from_media', { media_id: id })
}

function TimelineView({ seq, media }: { seq: Sequence; media: Record<string, MediaItem> }) {
  const sequences = useStore((s) => s.doc?.sequences)
  const activeId = useStore((s) => s.doc?.active_sequence_id)
  const openIds = useStore((s) => s.openSequences)
  const pps = useStore((s) => s.pxPerSecond)
  const scrollX = useStore((s) => s.scrollX)
  const time = useStore((s) => s.programTime)
  const tool = useStore((s) => s.tool)
  const snapOn = useStore((s) => s.snap)
  const selected = useStore((s) => s.selectedClips)
  const selectedTransition = useStore((s) => s.selectedTransition)
  const playing = useStore((s) => s.playing === 'program')
  const flash = useStore((s) => s.flashTracks)
  const showKfs = useStore((s) => s.showClipKeyframes)

  const lanesRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)
  const [bodyH, setBodyH] = useState(300)
  const [heights, setHeights] = useState<Record<string, number>>(() => loadJson('lumina.trackHeights', {}))
  const [split, setSplit] = useState<number>(() => loadJson('lumina.tlSplit', 0.5))
  const [zoom, setZoom] = useState<Record<Kind, number>>(() => loadJson('lumina.tlVZoom', { video: 1, audio: 1 }))
  const [vScroll, setVScroll] = useState(0)
  const [aScroll, setAScroll] = useState(0)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [hover, setHover] = useState<{ clipId: string; edge: 'start' | 'end' | null } | null>(null)
  const [dropTarget, setDropTarget] = useState<{ row: number; t: number } | null>(null)

  useLayoutEffect(() => {
    const el = lanesRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setWidth(el.clientWidth)
      setBodyH(el.clientHeight)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [seq.id])

  const layout = useMemo(() => buildRows(seq, heights, zoom, bodyH, split, vScroll, aScroll), [seq, heights, zoom, bodyH, split, vScroll, aScroll])
  const end = sequenceEnd(seq)
  const contentW = Math.max(width, ((end / TICKS_PER_SECOND) + 30) * pps)
  const toX = useCallback((t: number) => (t / TICKS_PER_SECOND) * pps - scrollX, [pps, scrollX])
  const toT = useCallback((x: number) => Math.max(0, ((x + scrollX) / pps) * TICKS_PER_SECOND), [pps, scrollX])

  // keep playhead visible during playback (page like Premiere)
  useEffect(() => {
    if (!playing) return
    const x = toX(time)
    if (x > width - 20 || x < 0) set({ scrollX: Math.max(0, (time / TICKS_PER_SECOND) * pps - 40) })
  }, [playing, time, toX, width, pps])

  useEffect(() => {
    const fit = () => {
      const s = activeSequence()
      if (!s) return
      const e = sequenceEnd(s) || 10 * TICKS_PER_SECOND
      set({ pxPerSecond: Math.max(0.5, (width - 40) / (e / TICKS_PER_SECOND)), scrollX: 0 })
    }
    window.addEventListener('timeline:zoomFit', fit)
    return () => window.removeEventListener('timeline:zoomFit', fit)
  }, [width])

  // h is the height on screen; the stored height is before the area's zoom
  const resizeTrack = (id: string, kind: Kind, h: number, commit: boolean) => {
    const next = { ...heights, [id]: Math.round(clampH(h) / zoom[kind]) }
    setHeights(next)
    if (commit) saveJson('lumina.trackHeights', next)
  }
  const zoomArea = (kind: Kind, z: number, commit: boolean) => {
    const next = { ...zoom, [kind]: Math.max(0.3, Math.min(6, z)) }
    setZoom(next)
    if (commit) saveJson('lumina.tlVZoom', next)
  }
  const dragDivider = (e: React.MouseEvent) => {
    e.preventDefault()
    const top = bodyRef.current!.getBoundingClientRect().top
    let v = split
    const move = (ev: MouseEvent) => {
      v = Math.max(0.08, Math.min(0.92, (ev.clientY - top - SECTION_GAP / 2) / Math.max(1, bodyH)))
      setSplit(v)
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      saveJson('lumina.tlSplit', v)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  const st = seq.settings
  const tpf = ticksPerFrame(st)
  const { rows } = layout

  // ------------------------------------------------------------ snapping
  const snapPoints = (exclude: Set<string>): number[] => {
    const pts = [time, 0]
    if (seq.in_point != null) pts.push(seq.in_point)
    if (seq.out_point != null) pts.push(seq.out_point)
    for (const m of Object.values(seq.markers)) pts.push(m.start)
    for (const t of Object.values(seq.tracks)) for (const c of Object.values(t.clips)) if (!exclude.has(c.id)) pts.push(c.start, c.end)
    return pts
  }
  const snapDelta = (edges: number[], exclude: Set<string>): number => {
    if (!snapOn) return 0
    const thr = (SNAP_PX / pps) * TICKS_PER_SECOND
    let best = 0
    let bestDist = thr
    for (const p of snapPoints(exclude)) {
      for (const e of edges) {
        const d = Math.abs(p - e)
        if (d < bestDist) {
          bestDist = d
          best = p - e
        }
      }
    }
    return best
  }

  // the part of a row inside its own track area
  const shown = (r: Row): [number, number] =>
    r.kind === 'video' ? [Math.max(0, r.top), Math.min(layout.videoH, r.top + r.height)] : [Math.max(layout.audioTop, r.top), Math.min(bodyH, r.top + r.height)]
  const rowAt = (y: number): number => rows.findIndex((r) => {
    const [a, b] = shown(r)
    return y >= a && y < b
  })
  const laneXY = (e: { clientX: number; clientY: number }) => {
    const r = lanesRef.current!.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }

  // ------------------------------------------------------------ ruler

  const step = niceStep(pps)
  const firstTick = Math.floor(scrollX / pps / step) * step
  const ticks: number[] = []
  for (let s = firstTick; s * pps - scrollX < width; s += step) ticks.push(s)

  const scrubRuler = (e: React.MouseEvent) => {
    const r = e.currentTarget.getBoundingClientRect()
    const go = (cx: number, shift: boolean) => {
      let t = floorToFrame(toT(cx - r.left), st)
      if (shift) {
        // Shift: the playhead snaps to edit points, markers and In/Out (Premiere)
        const thr = (SNAP_PX / pps) * TICKS_PER_SECOND
        let best = thr
        for (const p of snapPoints(new Set()).slice(1)) {
          if (Math.abs(p - t) < best) {
            best = Math.abs(p - t)
            t = p
          }
        }
      }
      goTo('program', Math.min(t, Math.max(end, t)))
    }
    go(e.clientX, e.shiftKey)
    const move = (ev: MouseEvent) => go(ev.clientX, ev.shiftKey)
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  // ------------------------------------------------------------ clip interactions

  const clipMouseDown = (e: React.MouseEvent, clip: Clip, rowIdx: number) => {
    if (e.button !== 0) return
    e.stopPropagation()
    const { x } = laneXY(e)
    const t = floorToFrame(toT(x), st)
    if (tool === 'razor') {
      const at = snapToFrame(t, st)
      // like Premiere: razor cuts linked clips too (Alt = this clip only, Shift = all tracks)
      const trackIds = new Set([rows[rowIdx].track.id])
      if (clip.link_id && !e.altKey)
        for (const tr of Object.values(seq.tracks)) if (Object.values(tr.clips).some((c) => c.link_id === clip.link_id)) trackIds.add(tr.id)
      void exec('clip.razor', { sequence_id: seq.id, at, track_ids: e.shiftKey ? undefined : [...trackIds] }, 'Razor')
      return
    }
    if (tool === 'trackSelect') {
      const tracks = e.shiftKey ? Object.values(seq.tracks) : [rows[rowIdx].track]
      set({ selectedClips: tracks.flatMap((tr) => Object.values(tr.clips).filter((c) => c.end > t).map((c) => c.id)) })
      return
    }
    if (tool === 'hand' || tool === 'zoom') return
    // selection / ripple tools
    const linked = e.altKey || !clip.link_id ? [clip.id] : Object.values(seq.tracks).flatMap((tr) => Object.values(tr.clips).filter((c) => c.link_id === clip.link_id).map((c) => c.id))
    let sel = get().selectedClips
    if (e.ctrlKey || e.shiftKey) {
      sel = sel.includes(clip.id) ? sel.filter((id) => !linked.includes(id)) : [...new Set([...sel, ...linked])]
    } else if (!sel.includes(clip.id)) {
      sel = linked
    }
    set({ selectedClips: sel })
    // the edge under the pointer now (the hover state can lag a press that follows a quick move)
    const cr = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const edge = !EDGE_TOOLS.has(tool) ? null : e.clientX - cr.left < EDGE_PX ? 'start' : cr.right - e.clientX < EDGE_PX ? 'end' : null
    if (tool === 'rolling' || tool === 'rate' || tool === 'slip' || tool === 'slide') {
      if ((tool === 'rolling' || tool === 'rate') && !edge) return
      setDrag({ kind: 'tool', tool, clipId: clip.id, ids: linked, edge, startX: e.clientX, dt: 0 })
      return
    }
    if (edge) {
      setDrag({ kind: 'trim', clipId: clip.id, edge, ripple: tool === 'ripple', startX: e.clientX, dt: 0 })
    } else if (tool === 'select' || tool === 'ripple') {
      setDrag({ kind: 'move', ids: sel.includes(clip.id) ? sel : linked, anchorRow: rowIdx, startX: e.clientX, startY: e.clientY, dt: 0, dRow: 0, moved: false, base: clip })
    }
  }

  const laneMouseMove = (e: React.MouseEvent) => {
    if (drag) return
    const target = (e.target as HTMLElement).closest('[data-clip]') as HTMLElement | null
    if (!target || !EDGE_TOOLS.has(tool)) {
      if (hover) setHover(null)
      return
    }
    const r = target.getBoundingClientRect()
    const edge = e.clientX - r.left < EDGE_PX ? 'start' : r.right - e.clientX < EDGE_PX ? 'end' : null
    const clipId = target.dataset.clip!
    if (hover?.clipId !== clipId || hover.edge !== edge) setHover({ clipId, edge })
  }

  useEffect(() => {
    if (!drag) return
    const move = (e: MouseEvent) => {
      if (drag.kind === 'hand') {
        set({ scrollX: Math.max(0, drag.startScroll - (e.clientX - drag.startX)) })
        return
      }
      if (drag.kind === 'marquee') {
        // rubber-band selection: every clip the rectangle touches, and the clips linked to them
        const { x, y } = laneXY(e)
        const [l, r] = [Math.min(drag.x0, x), Math.max(drag.x0, x)]
        const [top, bot] = [Math.min(drag.y0, y), Math.max(drag.y0, y)]
        const t0 = toT(l)
        const t1 = toT(r)
        const hit = rows.filter((row) => {
          const [a, b] = shown(row)
          return a < b && a < bot && b > top && !row.track.locked
        })
          .flatMap((row) => Object.values(row.track.clips).filter((c) => c.start < t1 && c.end > t0))
        const links = new Set(hit.map((c) => c.link_id).filter(Boolean))
        const linked = e.altKey ? [] : Object.values(seq.tracks).flatMap((tr) => Object.values(tr.clips).filter((c) => c.link_id && links.has(c.link_id)))
        set({ selectedClips: [...new Set([...drag.base, ...hit.map((c) => c.id), ...linked.map((c) => c.id)])] })
        setDrag({ ...drag, x1: x, y1: y })
        return
      }
      const raw = ((e.clientX - drag.startX) / pps) * TICKS_PER_SECOND
      if (drag.kind === 'move') {
        const moved = drag.moved || Math.abs(e.clientX - drag.startX) > 3 || Math.abs(e.clientY - drag.startY) > 3
        let dt = Math.round(raw / tpf) * tpf
        const ex = new Set(drag.ids)
        const clips = drag.ids.map((id) => findClip(seq, id)?.clip).filter((c): c is Clip => !!c)
        const edges = clips.flatMap((c) => [c.start + dt, c.end + dt])
        dt += snapDelta(edges, ex)
        dt = Math.max(dt, -Math.min(...clips.map((c) => c.start)))
        const { y } = laneXY(e)
        const r = rowAt(y)
        const dRow = r >= 0 && rows[r].kind === rows[drag.anchorRow].kind ? r - drag.anchorRow : drag.dRow
        setDrag({ ...drag, dt, dRow, moved })
      } else if (drag.kind === 'trim') {
        const found = findClip(seq, drag.clipId)
        if (!found) return
        let dt = Math.round(raw / tpf) * tpf
        const edgeT = (drag.edge === 'start' ? found.clip.start : found.clip.end) + dt
        dt += snapDelta([edgeT], new Set([drag.clipId]))
        setDrag({ ...drag, dt })
      } else if (drag.kind === 'tool') {
        const found = findClip(seq, drag.clipId)
        if (!found) return
        const c = found.clip
        let dt = Math.round(raw / tpf) * tpf
        if (drag.tool === 'slide') dt += snapDelta([c.start + dt, c.end + dt], new Set(drag.ids))
        else if (drag.edge) dt += snapDelta([(drag.edge === 'start' ? c.start : c.end) + dt], new Set(drag.ids))
        setDrag({ ...drag, dt })
      }
    }
    const up = async (e: MouseEvent) => {
      const d = drag
      setDrag(null)
      if (d.kind === 'move' && d.moved && (d.dt !== 0 || d.dRow !== 0)) {
        const row = rows[d.anchorRow]
        const trackDelta = row.kind === 'video' ? -d.dRow : d.dRow
        await exec('clip.move', { sequence_id: seq.id, clip_ids: d.ids, delta: d.dt, track_delta: trackDelta, include_linked: false, mode: e.ctrlKey ? 'insert' : 'overwrite' }, 'Move')
      } else if (d.kind === 'tool' && d.dt !== 0) {
        const label = { rolling: tx('Rolling Edit'), rate: tx('Rate Stretch'), slip: tx('Slip'), slide: tx('Slide') }[d.tool]
        await exec(EDIT_TOOLS[d.tool], { sequence_id: seq.id, clip_id: d.clipId, delta: d.dt, include_linked: d.ids.length > 1, ...(d.edge && d.tool !== 'slip' && d.tool !== 'slide' ? { edge: d.edge } : {}) }, label)
      } else if (d.kind === 'trim' && d.dt !== 0) {
        await exec('clip.trim', { sequence_id: seq.id, clip_id: d.clipId, edge: d.edge, delta: d.dt, ripple: d.ripple, include_linked: !e.altKey }, d.ripple ? 'Ripple Trim' : 'Trim')
      }
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up, { once: true })
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  })

  const laneMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return
    if (tool === 'hand') {
      setDrag({ kind: 'hand', startX: e.clientX, startScroll: scrollX })
      return
    }
    if (tool === 'zoom') {
      const { x } = laneXY(e)
      const tSec = (x + scrollX) / pps
      const next = e.altKey ? pps / 2 : pps * 2
      set({ pxPerSecond: next, scrollX: Math.max(0, tSec * next - x) })
      return
    }
    // empty lane: rubber-band selection (Shift adds to the selection)
    const { x, y } = laneXY(e)
    const base = e.shiftKey || e.ctrlKey ? get().selectedClips : []
    set({ selectedClips: base, selectedTransition: null })
    if (tool === 'select' || tool === 'ripple' || tool === 'rolling' || tool === 'rate' || tool === 'slip' || tool === 'slide')
      setDrag({ kind: 'marquee', x0: x, y0: y, x1: x, y1: y, base })
  }

  const onWheel = (e: React.WheelEvent) => {
    const y = e.clientY - (bodyRef.current?.getBoundingClientRect().top ?? 0)
    const overHeaders = !!(e.target as HTMLElement).closest('.lm-tl-headers')
    if (e.altKey && overHeaders) {
      // Alt+wheel over a track header: that track's height (Premiere)
      const r = rows[rowAt(y)]
      if (r) resizeTrack(r.track.id, r.kind, r.height + (e.deltaY < 0 ? 6 : -6), true)
    } else if (e.altKey || e.ctrlKey) {
      const { x } = laneXY(e)
      const tSec = (x + scrollX) / pps
      const next = Math.max(0.5, Math.min(4000, pps * (e.deltaY < 0 ? 1.25 : 0.8)))
      set({ pxPerSecond: next, scrollX: Math.max(0, tSec * next - x) })
    } else if (e.shiftKey) {
      set({ scrollX: Math.max(0, Math.min(contentW - width, scrollX + e.deltaY)) })
    } else if (y < layout.videoH) {
      setVScroll(Math.max(0, Math.min(layout.vMax, layout.vScroll - e.deltaY)))
    } else if (y >= layout.audioTop) {
      setAScroll(Math.max(0, Math.min(layout.aMax, layout.aScroll + e.deltaY)))
    }
  }

  // ------------------------------------------------------------ drop from project panel

  const dropOnLanes = async (e: React.DragEvent) => {
    e.preventDefault()
    setDropTarget(null)
    const fxRaw = e.dataTransfer.getData(EFFECT_MIME)
    if (fxRaw) {
      const item = JSON.parse(fxRaw) as EffectDrag
      const { x, y } = laneXY(e)
      const r = rows[rowAt(y)]
      const at = toT(x)
      const clip = r && Object.values(r.track.clips).find((c) => c.start <= at && at < c.end)
      if (!r || !clip) return toast(tx('Drop onto a clip'), 'info')
      if (item.kind === 'effect' || item.kind === 'preset') {
        if (r.kind !== 'video') return toast(tx('Video effects go on video clips'), 'info')
        if (item.kind === 'preset') await exec('effect.apply_preset', { sequence_id: seq.id, clip_ids: [clip.id], preset_id: item.type }, tx('Apply Preset'))
        else await exec('effect.add', { sequence_id: seq.id, clip_ids: [clip.id], type: item.type }, tx('Apply Effect'))
      } else {
        const edge = at - clip.start < clip.end - at ? 'start' : 'end'
        await exec('transition.add', { sequence_id: seq.id, clip_id: clip.id, edge, type: item.type }, tx('Apply Transition'))
      }
      return
    }
    const ids = JSON.parse(e.dataTransfer.getData(DRAG_MIME) || '[]') as string[]
    const doc = get().doc!
    // media, and sequences (dropped as nested sequence clips)
    const items = ids.map((id): MediaItem | Sequence | undefined => doc.media[id] ?? doc.sequences[id]).filter((m): m is MediaItem | Sequence => !!m && m.id !== seq.id)
    if (!items.length) return
    const { x, y } = laneXY(e)
    let at = snapToFrame(toT(x), st)
    at += snapDelta([at], new Set())
    const r = rows[rowAt(y)]
    const vIdx = r ? (r.kind === 'video' ? r.index : Math.min(r.index, seq.video_tracks.length - 1)) : 0
    const aIdx = r ? (r.kind === 'audio' ? r.index : Math.min(r.index, seq.audio_tracks.length - 1)) : 0
    const mode = e.ctrlKey ? 'insert' : 'overwrite'
    await engine.call('transaction.begin', { label: mode === 'insert' ? 'Insert' : 'Overwrite' })
    try {
      for (const m of items) {
        const tracks = { video_track_id: seq.video_tracks[vIdx], audio_track_id: seq.audio_tracks[aIdx] }
        const res = 'settings' in m
          ? await engine.exec<{ end: number }>('clip.place_sequence', { sequence_id: seq.id, nested_id: m.id, at, mode, ...tracks })
          : await engine.exec<{ end: number }>('clip.place', {
            sequence_id: seq.id, media_id: m.id, at, mode, ...tracks, source_in: 0, source_out: m.kind === 'image' ? null : m.duration
          })
        at = res.end
      }
      await engine.call('transaction.commit')
    } catch (err) {
      await engine.call('transaction.rollback').catch(() => undefined)
      toast(String(err instanceof Error ? err.message : err))
    }
  }

  // ------------------------------------------------------------ render helpers

  const moveGhost = drag?.kind === 'move' && drag.moved ? drag : null
  const trimGhost = drag?.kind === 'trim' ? drag : null
  const toolDrag = drag?.kind === 'tool' ? drag : null
  const preview = toolDrag ? previewTool(seq, toolDrag) : null
  const cursor =
    tool === 'razor' ? 'crosshair'
      : tool === 'hand' ? 'grab'
        : tool === 'zoom' ? 'zoom-in'
          : tool === 'slip' || tool === 'slide' ? 'ew-resize'
            : hover?.edge ? (tool === 'ripple' || tool === 'rolling' ? 'col-resize' : 'ew-resize') : 'default'

  const renderClip = (clip: Clip, row: Row, rowIdx: number) => {
    let start = clip.start
    let stop = clip.end
    let srcIn = clip.source_in
    let rowOffset = 0
    const ghost = moveGhost && moveGhost.ids.includes(clip.id)
    if (ghost) {
      start += moveGhost.dt
      stop += moveGhost.dt
      const anchor = rows[moveGhost.anchorRow]
      rowOffset = anchor.kind === row.kind ? moveGhost.dRow : -moveGhost.dRow
      // linked partner on the other kind mirrors visually (V up = A down)
      if (anchor.kind !== row.kind) rowOffset = moveGhost.dRow
    }
    if (trimGhost && trimGhost.clipId === clip.id) {
      if (trimGhost.edge === 'start') {
        if (trimGhost.ripple) {
          stop -= trimGhost.dt
          srcIn += trimGhost.dt * clip.speed
        } else {
          start += trimGhost.dt
          srcIn += trimGhost.dt * clip.speed
        }
      } else stop += trimGhost.dt
    }
    const pv = preview?.get(clip.id)
    if (pv) {
      start = pv.start
      stop = pv.end
      srcIn = pv.srcIn
    }
    const x0 = toX(start)
    const x1 = toX(stop)
    if (x1 < -50 || x0 > width + 50) return null
    const target = rows[rowIdx + (row.kind === 'video' ? -rowOffset : rowOffset)] ?? row
    const m = clip.media_id ? media[clip.media_id] : undefined
    const isSel = selected.includes(clip.id)
    return (
      <div
        key={clip.id}
        data-clip={clip.id}
        className={`lm-clip ${row.kind} ${clip.graphic ? 'graphic' : ''} ${clip.caption ? 'graphic caption' : ''} ${clip.freeze != null ? 'held' : ''} ${clip.sequence_id ? 'nested' : ''} ${isSel ? 'selected' : ''} ${clip.enabled ? '' : 'disabled'} ${ghost || pv ? 'ghost' : ''}`}
        style={{ left: x0, width: Math.max(2, x1 - x0), top: target.top + 1, height: target.height - 2, ['--clip-color' as string]: `var(--label-${clip.label})` }}
        onMouseDown={(e) => clipMouseDown(e, clip, rowIdx)}
        onDoubleClick={() => {
          if (clip.sequence_id) {
            // open the nested sequence at the frame under the playhead (Premiere)
            const inner = clip.sequence_id
            const t = clip.source_in + Math.round((Math.max(clip.start, Math.min(clip.end - 1, get().programTime)) - clip.start) * clip.speed)
            void exec('sequence.set_active', { sequence_id: inner }).then(() => goTo('program', t))
          } else if (clip.caption) {
            editCaptionText(clip.id)
          } else if (m) {
            set({ sourceMediaId: m.id, sourceTime: clip.source_in })
          }
        }}
        onContextMenu={(e) => {
          if (!selected.includes(clip.id)) set({ selectedClips: [clip.id] })
          openContextMenu(e, [
            { label: tx('Enable'), action: 'clip.enable' },
            { label: clip.link_id ? 'Unlink' : 'Link', action: 'clip.link' },
            'sep',
            { label: tx('Speed/Duration...'), action: 'clip.speed' },
            ...(m && row.kind === 'video' && m.kind === 'video'
              ? [
                  { label: tx('Reverse Speed'), action: 'clip.reverse' },
                  'sep' as const,
                  { label: tx('Frame Hold Options...'), action: 'clip.frameHoldOptions' },
                  { label: tx('Add Frame Hold'), action: 'clip.addFrameHold' },
                  { label: tx('Insert Frame Hold Segment'), action: 'clip.insertFrameHold' },
                  ...(clip.freeze != null ? [{ label: tx('Remove Frame Hold'), action: 'clip.removeFrameHold' }] : []),
                  { label: tx('Scene Edit Detection'), action: 'clip.sceneEditDetection' }
                ]
              : m && m.kind !== 'image'
                ? [{ label: tx('Reverse Speed'), action: 'clip.reverse' }]
                : []),
            ...(clip.caption
              ? ['sep' as const, { label: tx('Edit Caption'), action: 'caption.edit' }, { label: tx('Caption Track Style...'), action: 'caption.style' }, { label: tx('Upgrade Caption to Graphic'), action: 'caption.upgrade' }]
              : []),
            'sep',
            { label: tx('Nest...'), action: 'clip.nest' },
            ...(clip.sequence_id ? [{ label: tx('Un-nest'), action: 'clip.unnest' }] : []),
            'sep',
            { label: tx('Clear'), action: 'edit.clear' },
            { label: tx('Ripple Delete'), action: 'edit.rippleDelete' },
            'sep',
            { label: tx('Rename...'), action: 'clip.rename' },
            { label: tx('Reveal in Project'), run: () => m && set({ selectedItems: [m.id], currentBin: m.bin_id }) }
          ])
        }}
      >
        {row.kind === 'video' && m?.filmstrip_path && (
          <ClipFilmstrip path={m.filmstrip_path} srcIn={srcIn} speed={pv?.speed ?? clip.speed} duration={stop - start} x0={x0} width={x1 - x0} lane={width} height={target.height - 2} />
        )}
        {row.kind === 'audio' && m?.waveform_path && <ClipWaveform path={m.waveform_path} srcIn={srcIn} speed={clip.speed} duration={stop - start} />}
        {showKfs && !pv && !ghost && x1 - x0 > 10 && (
          <span className="lm-clip-kfs">
            {clipKeyframeTimes(clip, row.kind).map((k) => (
              <span
                key={k.t}
                className="k"
                data-clip-kf={k.t}
                style={{ left: ((k.t - clip.start) / Math.max(1, clip.end - clip.start)) * (x1 - x0) }}
                title={`${timecode(k.t, st)} · ${k.props.map((p) => tx(p.split('.').pop()!.replace(/_/g, ' ').replace(/^./, (ch) => ch.toUpperCase()))).join(', ')}`}
                onMouseDown={(e) => {
                  // jump to the keyframe and show its clip in Effect Controls
                  e.stopPropagation()
                  set({ selectedClips: [clip.id] })
                  goTo('program', k.t)
                }}
              />
            ))}
          </span>
        )}
        {x1 - x0 > 6 && (tool === 'select' || tool === 'pen') && !pv && (
          <VolumeBand clip={clip} seq={seq} width={Math.max(2, x1 - x0)} height={target.height - 2} prop={row.kind === 'audio' ? 'volume_db' : 'opacity'} />
        )}
        <span className="name">
          {clip.sequence_id && <b className="nest-badge">⧉ </b>}
          {(pv?.speed ?? clip.speed) !== 1 && <b>{Math.round((pv?.speed ?? clip.speed) * 100)}% </b>}
          {clip.freeze != null && <b title={tx('Frame Hold')}>❚❚ </b>}
          {clip.caption ? clip.caption.text.split('\n')[0] : clip.name}
        </span>
        {pv && toolDrag?.tool === 'slip' && toolDrag.clipId === clip.id && (
          <span className="lm-tooltip-tc" data-slip-in="">
            {timecode(Math.round(pv.srcIn), st)} – {timecode(Math.round(pv.srcIn + (stop - start) * clip.speed), st)}
          </span>
        )}
        {(() => {
          const off = ghost || pv ? 0 : syncOffset(seq, clip)
          if (!off) return null
          return (
            <span
              className="lm-sync"
              data-sync={off}
              title={tx('Out of sync with its linked clip (frames). Right-click to fix.')}
              onMouseDown={(e) => e.stopPropagation()}
              onContextMenu={(e) => {
                e.stopPropagation()
                openContextMenu(e, [
                  { label: tx('Move into Sync'), run: () => void exec('clip.resync', { sequence_id: seq.id, clip_id: clip.id, mode: 'move' }, tx('Move into Sync')) },
                  { label: tx('Slip into Sync'), run: () => void exec('clip.resync', { sequence_id: seq.id, clip_id: clip.id, mode: 'slip' }, tx('Slip into Sync')) }
                ])
              }}
            >
              {off > 0 ? '+' : ''}
              {off}
            </span>
          )
        })()}
        {row.kind === 'video' && (clip.effects?.length ?? 0) > 0 ? (
          <span className="fx on" title={(clip.effects ?? []).map((f) => tx(EFFECTS[f.type].label)).join(', ')}>fx</span>
        ) : (
          row.kind === 'video' && (clip.opacity !== 100 || Object.keys(clip.keyframes).length > 0 || clip.motion.scale !== 100 || clip.motion.rotation !== 0) && <span className="fx">fx</span>
        )}
      </div>
    )
  }

  const renderTransition = (w: TransitionWindow, row: (typeof rows)[number]) => {
    const x0 = toX(w.start)
    const x1 = toX(w.end)
    if (x1 < -50 || x0 > width + 50) return null
    const isSel = selectedTransition === w.tr.id && !selected.length
    const label = tx(TRANSITIONS[w.tr.type].label)
    return (
      <div
        key={w.tr.id}
        data-transition={w.tr.id}
        className={`lm-transition ${isSel ? 'selected' : ''} ${w.a && w.b ? '' : 'single'}`}
        style={{ left: x0, width: Math.max(4, x1 - x0), top: row.top + 1, height: Math.max(10, (row.height - 2) / 2) }}
        title={`${label} · ${timecode(w.end - w.start, st)}`}
        onMouseDown={(e) => {
          e.stopPropagation()
          if (e.button === 0) set({ selectedTransition: w.tr.id, selectedClips: [] })
        }}
        onDoubleClick={() => transitionDurationDialog(seq.id, w.tr.id, w.end - w.start, st)}
        onContextMenu={(e) => {
          set({ selectedTransition: w.tr.id, selectedClips: [] })
          openContextMenu(e, [
            { label: tx('Set Transition Duration...'), run: () => transitionDurationDialog(seq.id, w.tr.id, w.end - w.start, st) },
            'sep',
            { label: tx('Clear'), run: () => void exec('transition.remove', { sequence_id: seq.id, transition_id: w.tr.id }, tx('Clear')) }
          ])
        }}
      >
        <span>{label}</span>
      </div>
    )
  }

  const inOut = seq.in_point != null || seq.out_point != null
  const vClip = `inset(0 0 ${Math.max(0, bodyH - layout.videoH)}px 0)`
  const aClip = `inset(${layout.audioTop}px 0 0 0)`

  return (
    <div className="lm-panel lm-timeline" style={{ cursor }}>
      <div className="lm-seqtabs">
        {openIds.map((id) => sequences?.[id]).filter((s): s is Sequence => !!s).map((s) => (
          <span
            key={s.id}
            className={`tab ${s.id === activeId ? 'active' : ''}`}
            onClick={() => openSequence(s.id)}
            onAuxClick={(e) => e.button === 1 && closeSequence(s.id)}
          >
            {s.name}
            <button className="close" title={tx('Close')} onClick={(e) => (e.stopPropagation(), closeSequence(s.id))}>
              <X size={11} />
            </button>
          </span>
        ))}
      </div>
      <div className="lm-tl-top">
        <div className="lm-tl-corner" style={{ width: HEADER_W }}>
          <PlayheadTimecode value={time} st={st} />
          <div className="lm-tl-tools">
            <button className={`lm-iconbtn ${snapOn ? 'on' : ''}`} title="Snap in Timeline (S)" onClick={() => runAction('sequence.toggleSnap')}>
              <Magnet size={14} />
            </button>
            <button className="lm-iconbtn on" title={tx('Linked Selection')}>
              <Link2 size={14} />
            </button>
            <button className="lm-iconbtn" title="Add Marker (M)" onClick={() => runAction('markers.add')}>
              <Bookmark size={14} />
            </button>
            <button className="lm-iconbtn" title={tx('Timeline Display Settings')} onClick={(e) => openContextMenu(e, [{ label: tx('Add Tracks...'), action: 'sequence.addTracks' }, { label: tx('Zoom to Sequence'), action: 'view.zoomFit' }, 'sep', { label: `${get().showClipKeyframes ? '✓ ' : ''}${tx('Show Keyframes')}`, run: () => set({ showClipKeyframes: !get().showClipKeyframes }) }])}>
              <Wrench size={14} />
            </button>
          </div>
        </div>
        <div className="lm-ruler" style={{ height: RULER_H }} onMouseDown={scrubRuler} onWheel={onWheel}>
          {ticks.map((s) => (
            <div key={s} className="tick" style={{ left: s * pps - scrollX }}>
              <span>{timecode(Math.round(s * TICKS_PER_SECOND), st)}</span>
            </div>
          ))}
          {inOut && (
            <div className="inout" style={{ left: toX(seq.in_point ?? 0), width: toX(seq.out_point ?? end) - toX(seq.in_point ?? 0) }} />
          )}
          {Object.values(seq.markers).map((mk) => (
            <div key={mk.id} className={`marker ${mk.color}`} style={{ left: toX(mk.start) }} title={mk.name || 'Marker'} />
          ))}
          <div className="head" style={{ left: toX(time) }} />
        </div>
      </div>
      <div className="lm-tl-body" ref={bodyRef} onWheel={onWheel}>
        <div className="lm-tl-headers" style={{ width: HEADER_W }}>
          {(['video', 'audio'] as const).map((k) => (
            <div key={k} className="lm-tl-pane" style={{ clipPath: k === 'video' ? vClip : aClip }}>
              {rows.filter((r) => r.kind === k).map((r) => (
                <TrackHeader key={r.track.id} row={r} seq={seq} onResize={(h, commit) => resizeTrack(r.track.id, r.kind, h, commit)} />
              ))}
            </div>
          ))}
        </div>
        <div
          className="lm-tl-lanes"
          ref={lanesRef}
          onMouseDown={laneMouseDown}
          onMouseMove={laneMouseMove}
          onMouseLeave={() => setHover(null)}
          onDragOver={(e) => {
            e.preventDefault()
            const { x, y } = laneXY(e)
            setDropTarget({ row: rowAt(y), t: snapToFrame(toT(x), st) })
          }}
          onDragLeave={() => setDropTarget(null)}
          onDrop={dropOnLanes}
        >
          {(['video', 'audio'] as const).map((k) => (
            <div key={k} className="lm-tl-pane" data-pane={k} style={{ clipPath: k === 'video' ? vClip : aClip }}>
              {rows.map((r, i) => r.kind === k && (
                <div key={r.track.id} className={`lm-lane ${r.kind} ${r.track.locked ? 'locked' : ''} ${dropTarget?.row === i ? 'drop' : ''} ${flash.includes(r.track.id) ? 'flash' : ''}`} style={{ top: r.top, height: r.height }} />
              ))}
              {rows.map((r, i) => r.kind === k && Object.values(r.track.clips).map((c) => renderClip(c, r, i)))}
              {rows.map((r) => r.kind === k && trackTransitions(r.track).map((w) => renderTransition(w, r)))}
            </div>
          ))}
          {inOut && <div className="lm-lane-inout" style={{ left: toX(seq.in_point ?? 0), width: toX(seq.out_point ?? end) - toX(seq.in_point ?? 0), height: bodyH }} />}
          <div className="lm-playhead" style={{ left: toX(time), height: bodyH }} />
          {drag?.kind === 'marquee' && (
            <div className="lm-tl-marquee" style={{ left: Math.min(drag.x0, drag.x1), top: Math.min(drag.y0, drag.y1), width: Math.abs(drag.x1 - drag.x0), height: Math.abs(drag.y1 - drag.y0) }} />
          )}
          {dropTarget && <div className="lm-dropline" style={{ left: toX(dropTarget.t), height: bodyH }} />}
        </div>
        <div className="lm-tl-vbars" style={{ width: VBAR_W }}>
          <VZoomBar kind="video" top={0} pane={layout.videoH} max={layout.vMax} content={layout.vContent} pos={layout.vMax - layout.vScroll} onPos={(p) => setVScroll(layout.vMax - p)} zoom={zoom.video} onZoom={(z, c) => zoomArea('video', z, c)} />
          <VZoomBar kind="audio" top={layout.audioTop} pane={layout.audioH} max={layout.aMax} content={layout.aContent} pos={layout.aScroll} onPos={setAScroll} zoom={zoom.audio} onZoom={(z, c) => zoomArea('audio', z, c)} />
        </div>
        <div className="lm-tl-divider" data-divider="" style={{ top: layout.videoH, height: SECTION_GAP }} onMouseDown={dragDivider} title={tx('Drag to resize the video and audio track areas')} />
      </div>
      <ZoomBar width={width} contentW={contentW} pps={pps} scrollX={scrollX} end={end} />
    </div>
  )
}

/** A track area's vertical zoom scroll bar (Premiere): drag the thumb to scroll, its round ends to make
 * every track of the area taller or shorter. pos is the scroll offset from the top of the bar. */
function VZoomBar(p: { kind: Kind; top: number; pane: number; max: number; content: number; pos: number; onPos: (v: number) => void; zoom: number; onZoom: (z: number, commit: boolean) => void }) {
  const total = Math.max(1, p.pane + p.max)
  const H = Math.max(1, p.pane)
  const len = Math.max(24, Math.min(H, (p.pane / total) * H))
  const top = Math.min(H - len, (p.pos / total) * H)
  const grab = (e: React.MouseEvent, part: 'thumb' | 'start' | 'end') => {
    e.preventDefault()
    e.stopPropagation()
    const [y0, pos0, z0, l0, c0] = [e.clientY, p.pos, p.zoom, len, Math.max(1, p.content, p.pane)]
    const at = (ev: MouseEvent) => {
      const dy = ev.clientY - y0
      if (part === 'thumb') return p.onPos(Math.max(0, Math.min(p.max, pos0 + (dy * total) / H)))
      // the thumb follows the knob: its length is the share of the tracks on screen, so a thumb pulled
      // to the full bar fits every track exactly (nothing left to scroll)
      const l = Math.max(8, Math.min(H, part === 'start' ? l0 - dy : l0 + dy))
      // (tracks that already fit just scale with the thumb)
      return p.content < p.pane ? z0 * (l0 / l) : (z0 * (p.pane * H) / l) / c0
    }
    const move = (ev: MouseEvent) => {
      const z = at(ev)
      if (typeof z === 'number') p.onZoom(z, false)
    }
    const up = (ev: MouseEvent) => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      const z = at(ev)
      if (typeof z === 'number') p.onZoom(z, true)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }
  return (
    <div className="lm-tl-vbar" data-vbar={p.kind} style={{ top: p.top, height: p.pane }}>
      <div className="thumb" style={{ top, height: len }} onMouseDown={(e) => grab(e, 'thumb')} onDoubleClick={() => p.onZoom(1, true)}>
        <span className="knob t" data-vknob={`${p.kind}-start`} onMouseDown={(e) => grab(e, 'start')} />
        <span className="knob b" data-vknob={`${p.kind}-end`} onMouseDown={(e) => grab(e, 'end')} />
      </div>
    </div>
  )
}

/** Premiere's zoom scroll bar: drag the thumb to scroll, its round ends to zoom. */
function ZoomBar({ width, contentW, pps, scrollX, end }: { width: number; contentW: number; pps: number; scrollX: number; end: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const total = contentW / pps
  const viewStart = scrollX / pps
  const viewLen = width / pps
  const barW = Math.max(1, ref.current?.clientWidth ?? width)
  const left = (viewStart / total) * barW
  const w = Math.max(24, (viewLen / total) * barW)
  const grab = (e: React.MouseEvent, part: 'thumb' | 'start' | 'end') => {
    e.preventDefault()
    e.stopPropagation()
    const x0 = e.clientX
    const [s0, l0, tot] = [viewStart, viewLen, Math.max(total, end / TICKS_PER_SECOND + 30)]
    const secPerPx = tot / barW
    const move = (ev: MouseEvent) => {
      const d = (ev.clientX - x0) * secPerPx
      if (part === 'thumb') {
        set({ scrollX: Math.max(0, Math.min(contentW - width, (s0 + d) * pps)) })
        return
      }
      const [a, b] = part === 'start' ? [Math.max(0, Math.min(s0 + d, s0 + l0 - 0.05)), s0 + l0] : [s0, Math.max(s0 + 0.05, s0 + l0 + d)]
      const next = Math.max(0.5, Math.min(4000, width / (b - a)))
      set({ pxPerSecond: next, scrollX: Math.max(0, (part === 'start' ? b - width / next : a) * next) })
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }
  return (
    <div className="lm-tl-zoombar" ref={ref} style={{ marginLeft: HEADER_W, marginRight: VBAR_W }} data-zoombar="">
      <div className="thumb" style={{ left, width: w }} onMouseDown={(e) => grab(e, 'thumb')}>
        <span className="knob l" data-knob="start" onMouseDown={(e) => grab(e, 'start')} />
        <span className="knob r" data-knob="end" onMouseDown={(e) => grab(e, 'end')} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- pieces

function PlayheadTimecode({ value, st }: { value: number; st: Sequence['settings'] }) {
  const [edit, setEdit] = useState<string | null>(null)
  if (edit !== null) {
    return (
      <input
        className="lm-tc big"
        autoFocus
        value={edit}
        onChange={(e) => setEdit(e.target.value)}
        onBlur={() => setEdit(null)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            const t = parseTimecode(edit, st)
            if (t != null) goTo('program', t)
            setEdit(null)
          }
          if (e.key === 'Escape') setEdit(null)
        }}
      />
    )
  }
  return (
    <button className="lm-tc big" onClick={() => setEdit(timecode(value, st))}>
      {timecode(value, st)}
    </button>
  )
}

/** The track source video / audio is patched to (Insert/Overwrite), null = unpatched. */
function patchedTrack(seq: Sequence, kind: 'video' | 'audio'): string | null {
  const set_ = kind === 'video' ? seq.source_video_track : seq.source_audio_track
  if (set_ === '') return null
  if (set_ && seq.tracks[set_]) return set_
  const order = kind === 'video' ? seq.video_tracks : seq.audio_tracks
  const usable = order.filter((id) => !seq.tracks[id].locked && !seq.tracks[id].caption_style)
  return usable.find((id) => seq.tracks[id].targeted) ?? usable[0] ?? null
}

const VIDEO_FX = ['transform', 'gaussian_blur', 'lumetri'] as const
const AUDIO_FX = ['parametric_eq', 'compressor', 'hard_limiter'] as const

function TrackHeader({ row, seq, onResize }: { row: Row; seq: Sequence; onResize: (h: number, commit: boolean) => void }) {
  const t = row.track
  const caption = isCaptionTrack(t)
  const patched = !caption && patchedTrack(seq, row.kind) === t.id
  const fxCount = (t.effects ?? []).length
  const hasGraphics = Object.values(t.clips).some((c) => c.graphic)
  // the edge away from the divider: a video track grows upward, an audio track downward
  const resize = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const [y0, h0] = [e.clientY, row.height]
    const at = (ev: MouseEvent) => h0 + (row.kind === 'video' ? y0 - ev.clientY : ev.clientY - y0)
    const move = (ev: MouseEvent) => onResize(at(ev), false)
    const up = (ev: MouseEvent) => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      onResize(at(ev), true)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }
  const toggle = (key: 'locked' | 'sync_lock' | 'hidden' | 'muted' | 'solo' | 'targeted') =>
    void exec('track.set', { sequence_id: seq.id, track_id: t.id, [key]: !t[key] }, `Track ${key}`)
  return (
    <div
      className={`lm-th ${row.kind} ${caption ? 'caption' : ''}`}
      style={{ top: row.top, height: row.height }}
      onDragOver={(e) => e.dataTransfer.types.includes(EFFECT_MIME) && e.preventDefault()}
      onDrop={(e) => {
        // an effect or preset dropped on a track header becomes a track effect (every clip, as is)
        const raw = e.dataTransfer.getData(EFFECT_MIME)
        if (!raw) return
        e.preventDefault()
        const item = JSON.parse(raw) as EffectDrag
        if (item.kind === 'effect' && effectMedia(item.type as never) === row.kind) void addTrackEffect(t.id, item.type)
        else if (item.kind === 'preset' && row.kind === 'video') void applyPresetToTrack(t.id, item.type)
        else toast(tx('That effect does not go on this track'), 'info')
      }}
      onContextMenu={(e) =>
        openContextMenu(e, [
          { label: tx('Rename'), run: () => renameTrack(seq, t) },
          'sep',
          ...(row.kind === 'video' ? VIDEO_FX : AUDIO_FX).map((type) => ({
            label: `${tx('Add Track Effect')}: ${tx(EFFECTS[type].label)}`, run: () => void addTrackEffect(t.id, type)
          })),
          ...(fxCount
            ? [{ label: tx('Remove All Track Effects'), run: () => void removeTrackEffects(seq, t) }]
            : []),
          'sep',
          ...(row.kind === 'video'
            ? caption
              ? [
                  { label: tx('Caption Track Style...'), run: () => captionStyleDialog(t.id) },
                  { label: tx('Import Captions...'), run: () => void importCaptions(undefined, t.id) },
                  { label: tx('Export Captions (SRT)...'), run: () => void exportCaptions(t.id) },
                  { label: tx('Upgrade Captions to Graphics'), run: () => void upgradeCaptions(t.id) },
                  'sep' as const
                ]
              : [
                  ...(hasGraphics ? [{ label: tx('Convert Graphics to Captions'), run: () => void graphicsToCaptions(t.id) }] : []),
                  { label: tx('Add Caption Track'), run: () => void addCaptionTrack() },
                  { label: tx('Import Captions...'), run: () => void importCaptions() },
                  'sep' as const
                ]
            : []),
          ...(!caption
            ? [
                patched
                  ? { label: tx('Unpatch Source'), run: () => void exec('sequence.set_patch', { sequence_id: seq.id, [row.kind === 'video' ? 'clear_video' : 'clear_audio']: true }, tx('Source Patching')) }
                  : { label: tx('Patch Source Here'), run: () => void exec('sequence.set_patch', { sequence_id: seq.id, [row.kind === 'video' ? 'video_track_id' : 'audio_track_id']: t.id }, tx('Source Patching')) },
                'sep' as const
              ]
            : []),
          { label: tx('Add Tracks...'), action: 'sequence.addTracks' },
          { label: tx('Delete Track'), run: () => void exec('track.remove', { sequence_id: seq.id, track_id: t.id }) }
        ])
      }
    >
      {caption ? (
        <span className="patch cc" title={tx('Caption track: cues are text in the track style')}>CC</span>
      ) : (
        <button
          className={`patch ${patched ? 'on' : ''}`}
          data-patch={row.label}
          title={tx('Source patching: Insert/Overwrite put source here (click to patch, click again to unpatch)')}
          onClick={() =>
            void exec('sequence.set_patch', patched
              ? { sequence_id: seq.id, [row.kind === 'video' ? 'clear_video' : 'clear_audio']: true }
              : { sequence_id: seq.id, [row.kind === 'video' ? 'video_track_id' : 'audio_track_id']: t.id }, tx('Source Patching'))
          }
        >
          {patched ? (row.kind === 'video' ? 'V1' : 'A1') : ''}
        </button>
      )}
      <button className={`target ${t.targeted ? 'on' : ''}`} onClick={() => toggle('targeted')} title={tx('Toggle Track Targeting: paste, Add Edit and keyboard edits go to the lowest targeted track')}>
        {row.label}
      </button>
      <button className={`lm-iconbtn ${t.locked ? 'on' : ''}`} onClick={() => toggle('locked')} title={tx('Toggle Track Lock')}>
        {t.locked ? <Lock size={13} /> : <LockOpen size={13} />}
      </button>
      <button className={`lm-iconbtn ${t.sync_lock ? 'on' : ''}`} onClick={() => toggle('sync_lock')} title={tx('Toggle Sync Lock')}>
        <RefreshCcw size={12} />
      </button>
      {row.kind === 'video' ? (
        <button className="lm-iconbtn" onClick={() => toggle('hidden')} title={tx('Toggle Track Output')}>
          {t.hidden ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
      ) : (
        <>
          <button className={`lm-ms m ${t.muted ? 'on' : ''}`} onClick={() => toggle('muted')} title={tx('Mute Track')}>
            M
          </button>
          <button className={`lm-ms s ${t.solo ? 'on' : ''}`} onClick={() => toggle('solo')} title={tx('Solo Track')}>
            S
          </button>
        </>
      )}
      <span className="tname">{t.name.replace(/^(Video|Audio) (\d+)$/, (_, k: string, n: string) => `${tx(k)} ${n}`)}</span>
      {fxCount > 0 && (
        <span className="tfx" data-track-fx={fxCount} title={`${tx('Track effects')}: ${(t.effects ?? []).map((fx) => tx(EFFECTS[fx.type].label)).join(', ')}`}>
          fx{fxCount > 1 ? fxCount : ''}
        </span>
      )}
      <div className={`lm-th-resize ${row.kind}`} data-track-resize={row.label} onMouseDown={resize} />
    </div>
  )
}

async function removeTrackEffects(seq: Sequence, t: Track) {
  await engine.call('transaction.begin', { label: tx('Remove All Track Effects') })
  try {
    for (const fx of t.effects ?? []) await engine.exec('effect.remove', { sequence_id: seq.id, track_id: t.id, effect_id: fx.id })
    await engine.call('transaction.commit', {})
  } catch (err) {
    await engine.call('transaction.rollback', {})
    toast(String(err))
  }
}

function renameTrack(seq: Sequence, t: Track) {
  openDialog({
    title: tx('Rename Track'),
    fields: [{ key: 'name', label: tx('Name'), type: 'text', value: t.name }],
    onSubmit: async (v) => void (await exec('track.set', { sequence_id: seq.id, track_id: t.id, name: String(v.name) }))
  })
}

function ClipWaveform({ path, srcIn, speed, duration }: { path: string; srcIn: number; speed: number; duration: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [, force] = useState(0)
  useEffect(() => {
    const w = loadWaveform(path, () => force((n) => n + 1))
    if (!w || !ref.current) return
    const s = srcIn / TICKS_PER_SECOND
    drawWaveform(ref.current, w, s, s + (duration * speed) / TICKS_PER_SECOND, 'rgba(255,255,255,0.55)')
  })
  return <canvas ref={ref} className="wave" />
}

/** Continuous video thumbnails (Premiere's "Continuous Video Thumbnails"): frames from the media's
 * filmstrip sprite, one per tile across the clip, only for the part of the clip on screen. */
function ClipFilmstrip(p: { path: string; srcIn: number; speed: number; duration: number; x0: number; width: number; lane: number; height: number }) {
  const [, force] = useState(0)
  const f = loadFilmstrip(p.path, () => force((n) => n + 1))
  if (!f || p.width < 8) return null
  const h = Math.max(8, p.height - 14)
  const tw = (f.width * h) / f.height
  const tiles: { left: number; frame: number }[] = []
  const from = Math.max(0, Math.floor(-p.x0 / tw))
  const to = Math.min(Math.ceil(p.width / tw), Math.ceil((p.lane - p.x0) / tw) + 1)
  const srcSec = (x: number) => (p.srcIn + (x / p.width) * p.duration * p.speed) / TICKS_PER_SECOND
  for (let i = from; i < to && tiles.length < 200; i++) {
    const frame = Math.max(0, Math.min(f.count - 1, Math.floor(srcSec(i * tw) / f.interval)))
    tiles.push({ left: i * tw, frame })
  }
  const url = `url("${mediaUrl(f.image)}")`
  return (
    <div className="lm-filmstrip" style={{ height: h }} data-filmstrip="">
      {tiles.map((t) => (
        <div key={t.left} style={{ left: t.left, width: tw, backgroundImage: url, backgroundSize: `${tw * f.count}px ${h}px`, backgroundPosition: `${-t.frame * tw}px 0` }} />
      ))}
    </div>
  )
}
