import { EFFECT_MIME, type EffectDrag } from '../panels/EffectsPanel'
import { EFFECTS, TRANSITIONS, trackTransitions, type TransitionWindow } from '../util/effects'
import { transitionDurationDialog } from './transitionDialog'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { t as tx } from '@shared/i18n'
import { Lock, LockOpen, Eye, EyeOff, Magnet, Link2, Bookmark, Wrench, RefreshCcw } from 'lucide-react'
import type { Clip, MediaItem, Sequence, Track } from '../engine/document'
import { engine } from '../engine/client'
import { runAction } from '../actions/actions'
import { openContextMenu } from '../layout/Chrome'
import { openDialog } from '../state/dialog'
import { DRAG_MIME } from '../panels/ProjectPanel'
import { activeSequence, exec, findClip, get, sequenceEnd, set, toast, useStore } from '../state/store'
import { goTo } from '../state/transport'
import { floorToFrame, parseTimecode, snapToFrame, ticksPerFrame, timecode, TICKS_PER_SECOND } from '../util/time'
import { drawWaveform, loadWaveform } from './waveform'

const HEADER_W = 190
const RULER_H = 30
const VIDEO_H = 42
const AUDIO_H = 46
const SECTION_GAP = 6
const EDGE_PX = 6
const SNAP_PX = 8

type Row = { track: Track; label: string; kind: 'video' | 'audio'; index: number; top: number; height: number }

type Drag =
  | { kind: 'move'; ids: string[]; anchorRow: number; startX: number; startY: number; dt: number; dRow: number; moved: boolean; base: Clip }
  | { kind: 'trim'; clipId: string; edge: 'start' | 'end'; ripple: boolean; startX: number; dt: number }
  | { kind: 'hand'; startX: number; startScroll: number }

function buildRows(seq: Sequence): { rows: Row[]; height: number; audioTop: number } {
  const rows: Row[] = []
  let y = 0
  const vids = [...seq.video_tracks].reverse()
  vids.forEach((id) => {
    const index = seq.video_tracks.indexOf(id)
    rows.push({ track: seq.tracks[id], label: `V${index + 1}`, kind: 'video', index, top: y, height: VIDEO_H })
    y += VIDEO_H
  })
  y += SECTION_GAP
  const audioTop = y
  seq.audio_tracks.forEach((id, index) => {
    rows.push({ track: seq.tracks[id], label: `A${index + 1}`, kind: 'audio', index, top: y, height: AUDIO_H })
    y += AUDIO_H
  })
  return { rows, height: y, audioTop }
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
  const pps = useStore((s) => s.pxPerSecond)
  const scrollX = useStore((s) => s.scrollX)
  const time = useStore((s) => s.programTime)
  const tool = useStore((s) => s.tool)
  const snapOn = useStore((s) => s.snap)
  const selected = useStore((s) => s.selectedClips)
  const selectedTransition = useStore((s) => s.selectedTransition)
  const playing = useStore((s) => s.playing === 'program')

  const lanesRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const hbarRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [hover, setHover] = useState<{ clipId: string; edge: 'start' | 'end' | null } | null>(null)
  const [dropTarget, setDropTarget] = useState<{ row: number; t: number } | null>(null)

  useLayoutEffect(() => {
    const el = lanesRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [seq.id])

  const layout = useMemo(() => buildRows(seq), [seq])
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

  useEffect(() => {
    if (hbarRef.current && Math.abs(hbarRef.current.scrollLeft - scrollX) > 1) hbarRef.current.scrollLeft = scrollX
  }, [scrollX])

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

  const rowAt = (y: number): number => {
    const idx = rows.findIndex((r) => y >= r.top && y < r.top + r.height)
    return idx
  }
  const laneXY = (e: { clientX: number; clientY: number }) => {
    const r = lanesRef.current!.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top + (scrollRef.current?.scrollTop ?? 0) }
  }

  // ------------------------------------------------------------ ruler

  const step = niceStep(pps)
  const firstTick = Math.floor(scrollX / pps / step) * step
  const ticks: number[] = []
  for (let s = firstTick; s * pps - scrollX < width; s += step) ticks.push(s)

  const scrubRuler = (e: React.MouseEvent) => {
    const r = e.currentTarget.getBoundingClientRect()
    const go = (cx: number) => {
      const t = floorToFrame(toT(cx - r.left), st)
      goTo('program', Math.min(t, Math.max(end, t)))
    }
    go(e.clientX)
    const move = (ev: MouseEvent) => go(ev.clientX)
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
    const edge = hover?.clipId === clip.id ? hover.edge : null
    if (edge) {
      setDrag({ kind: 'trim', clipId: clip.id, edge, ripple: tool === 'ripple', startX: e.clientX, dt: 0 })
    } else if (tool === 'select' || tool === 'ripple') {
      setDrag({ kind: 'move', ids: sel.includes(clip.id) ? sel : linked, anchorRow: rowIdx, startX: e.clientX, startY: e.clientY, dt: 0, dRow: 0, moved: false, base: clip })
    }
  }

  const laneMouseMove = (e: React.MouseEvent) => {
    if (drag) return
    const target = (e.target as HTMLElement).closest('[data-clip]') as HTMLElement | null
    if (!target || (tool !== 'select' && tool !== 'ripple')) {
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
      }
    }
    const up = async (e: MouseEvent) => {
      const d = drag
      setDrag(null)
      if (d.kind === 'move' && d.moved && (d.dt !== 0 || d.dRow !== 0)) {
        const row = rows[d.anchorRow]
        const trackDelta = row.kind === 'video' ? -d.dRow : d.dRow
        await exec('clip.move', { sequence_id: seq.id, clip_ids: d.ids, delta: d.dt, track_delta: trackDelta, include_linked: false, mode: e.ctrlKey ? 'insert' : 'overwrite' }, 'Move')
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
    set({ selectedClips: [] })
  }

  const onWheel = (e: React.WheelEvent) => {
    if (e.altKey || e.ctrlKey) {
      const { x } = laneXY(e)
      const tSec = (x + scrollX) / pps
      const next = Math.max(0.5, Math.min(4000, pps * (e.deltaY < 0 ? 1.25 : 0.8)))
      set({ pxPerSecond: next, scrollX: Math.max(0, tSec * next - x) })
    } else if (e.shiftKey) {
      set({ scrollX: Math.max(0, Math.min(contentW - width, scrollX + e.deltaY)) })
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
      if (item.kind === 'effect') {
        if (r.kind !== 'video') return toast(tx('Video effects go on video clips'), 'info')
        await exec('effect.add', { sequence_id: seq.id, clip_ids: [clip.id], type: item.type }, tx('Apply Effect'))
      } else {
        const edge = at - clip.start < clip.end - at ? 'start' : 'end'
        await exec('transition.add', { sequence_id: seq.id, clip_id: clip.id, edge, type: item.type }, tx('Apply Transition'))
      }
      return
    }
    const ids = JSON.parse(e.dataTransfer.getData(DRAG_MIME) || '[]') as string[]
    const doc = get().doc!
    const items = ids.map((id) => doc.media[id]).filter((m): m is MediaItem => !!m)
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
        const res = await engine.exec<{ end: number }>('clip.place', {
          sequence_id: seq.id, media_id: m.id, at, mode, video_track_id: seq.video_tracks[vIdx], audio_track_id: seq.audio_tracks[aIdx],
          source_in: 0, source_out: m.kind === 'image' ? null : m.duration
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
  const cursor = tool === 'razor' ? 'crosshair' : tool === 'hand' ? 'grab' : tool === 'zoom' ? 'zoom-in' : hover?.edge ? (tool === 'ripple' ? 'col-resize' : 'ew-resize') : 'default'

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
        className={`lm-clip ${row.kind} ${clip.graphic ? 'graphic' : ''} ${isSel ? 'selected' : ''} ${clip.enabled ? '' : 'disabled'} ${ghost ? 'ghost' : ''}`}
        style={{ left: x0, width: Math.max(2, x1 - x0), top: target.top + 1, height: target.height - 2, ['--clip-color' as string]: `var(--label-${clip.label})` }}
        onMouseDown={(e) => clipMouseDown(e, clip, rowIdx)}
        onDoubleClick={() => {
          if (m) {
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
            'sep',
            { label: tx('Clear'), action: 'edit.clear' },
            { label: tx('Ripple Delete'), action: 'edit.rippleDelete' },
            'sep',
            { label: tx('Rename...'), action: 'clip.rename' },
            { label: tx('Reveal in Project'), run: () => m && set({ selectedItems: [m.id], currentBin: m.bin_id }) }
          ])
        }}
      >
        {row.kind === 'audio' && m?.waveform_path && <ClipWaveform path={m.waveform_path} srcIn={srcIn} speed={clip.speed} duration={stop - start} />}
        <span className="name">
          {clip.speed !== 1 && <b>{Math.round(clip.speed * 100)}% </b>}
          {clip.name}
        </span>
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

  return (
    <div className="lm-panel lm-timeline" style={{ cursor }}>
      <div className="lm-seqtabs">
        {Object.values(sequences ?? {}).map((s) => (
          <span key={s.id} className={`tab ${s.id === activeId ? 'active' : ''}`} onClick={() => void exec('sequence.set_active', { sequence_id: s.id })}>
            {s.name}
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
            <button className="lm-iconbtn" title={tx('Timeline Display Settings')} onClick={(e) => openContextMenu(e, [{ label: tx('Add Tracks...'), action: 'sequence.addTracks' }, { label: tx('Zoom to Sequence'), action: 'view.zoomFit' }])}>
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
      <div className="lm-tl-body" ref={scrollRef} onWheel={onWheel}>
        <div className="lm-tl-inner" style={{ height: layout.height + 40 }}>
          <div className="lm-tl-headers" style={{ width: HEADER_W }}>
            {rows.map((r) => (
              <TrackHeader key={r.track.id} row={r} seq={seq} />
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
            {rows.map((r, i) => (
              <div key={r.track.id} className={`lm-lane ${r.kind} ${r.track.locked ? 'locked' : ''} ${dropTarget?.row === i ? 'drop' : ''}`} style={{ top: r.top, height: r.height }} />
            ))}
            {rows.map((r, i) => Object.values(r.track.clips).map((c) => renderClip(c, r, i)))}
            {rows.map((r) => trackTransitions(r.track).map((w) => renderTransition(w, r)))}
            {inOut && <div className="lm-lane-inout" style={{ left: toX(seq.in_point ?? 0), width: toX(seq.out_point ?? end) - toX(seq.in_point ?? 0), height: layout.height }} />}
            <div className="lm-playhead" style={{ left: toX(time), height: layout.height + 40 }} />
            {dropTarget && <div className="lm-dropline" style={{ left: toX(dropTarget.t), height: layout.height }} />}
          </div>
        </div>
      </div>
      <div className="lm-tl-hbar" style={{ marginLeft: HEADER_W }} ref={hbarRef} onScroll={(e) => set({ scrollX: (e.target as HTMLDivElement).scrollLeft })}>
        <div style={{ width: contentW, height: 1 }} />
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

function TrackHeader({ row, seq }: { row: Row; seq: Sequence }) {
  const t = row.track
  const toggle = (key: 'locked' | 'sync_lock' | 'hidden' | 'muted' | 'solo' | 'targeted') =>
    void exec('track.set', { sequence_id: seq.id, track_id: t.id, [key]: !t[key] }, `Track ${key}`)
  return (
    <div
      className={`lm-th ${row.kind}`}
      style={{ top: row.top, height: row.height }}
      onContextMenu={(e) =>
        openContextMenu(e, [
          { label: tx('Rename'), run: () => renameTrack(seq, t) },
          'sep',
          { label: tx('Add Tracks...'), action: 'sequence.addTracks' },
          { label: tx('Delete Track'), run: () => void exec('track.remove', { sequence_id: seq.id, track_id: t.id }) }
        ])
      }
    >
      <span className="patch">{row.label}</span>
      <button className={`target ${t.targeted ? 'on' : ''}`} onClick={() => toggle('targeted')} title={tx('Toggle Track Targeting')}>
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
    </div>
  )
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
