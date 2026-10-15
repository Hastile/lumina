// Keyframe selection in Effect Controls, as in Premiere: click / Shift+click,
// marquee-drag over the lanes, drag selected keyframes in time (Shift snaps to
// the playhead), Delete, and the right-click Temporal Interpolation menu. The
// icons show each side's interpolation like Premiere's (diamond = linear,
// hourglass = Bezier, rounded hourglass = Continuous Bezier, circle = Auto
// Bezier, square = Hold).

import { t } from '@shared/i18n'
import { exec, get, set } from '../state/store'
import { openContextMenu } from '../layout/Chrome'
import type { KeyframeProp, Kf } from '../util/motion'
import { seqTimeOf } from '../util/motion'
import { ticksPerFrame } from '../util/time'
import { effectiveHandle } from './KeyframeGraph'
import type { ClipCtx } from './PropertyControls'

/** A keyframed property: a Motion/Opacity/Volume prop or an effect parameter. */
export type KfTarget = { prop: KeyframeProp } | { effect_id: string; param: string }

export const targetKey = (tg: KfTarget): string => ('prop' in tg ? `p:${tg.prop}` : `e:${tg.effect_id}:${tg.param}`)
export const kfKey = (tg: KfTarget, time: number): string => `${targetKey(tg)}@${time}`

function parseTarget(key: string): KfTarget {
  const [kind, a, b] = key.split(':')
  return kind === 'p' ? { prop: a as KeyframeProp } : { effect_id: a, param: b }
}

/** The selection as the engine's keyframes.* targets. */
export function selectionTargets(keys: string[]): (KfTarget & { times: number[] })[] {
  const by = new Map<string, number[]>()
  for (const k of keys) {
    const at = k.lastIndexOf('@')
    const tk = k.slice(0, at)
    by.set(tk, [...(by.get(tk) ?? []), Number(k.slice(at + 1))])
  }
  return [...by].map(([tk, times]) => ({ ...parseTarget(tk), times }))
}

export function selectedKeys(clipId: string): string[] {
  const s = get().kfSel
  return s && s.clipId === clipId ? s.keys : []
}

// ---------------------------------------------------------------- commands

export function setSelectedInterp(c: ClipCtx, interp: string): void {
  const keys = selectedKeys(c.clip.id)
  if (!keys.length) return
  void exec('keyframes.set_interp', { sequence_id: c.seq.id, clip_id: c.clip.id, targets: selectionTargets(keys), interp }, t('Temporal Interpolation'))
}

/** Delete key in Effect Controls. Returns false when there was nothing to delete. */
export function deleteSelectedKeyframes(): boolean {
  const s = get()
  const sel = s.kfSel
  if (!sel?.keys.length || s.focusedPanel !== 'effectControls') return false
  const seq = s.doc?.active_sequence_id
  void exec('keyframes.delete', { sequence_id: seq, clip_id: sel.clipId, targets: selectionTargets(sel.keys) }, t('Clear'))
  set({ kfSel: null })
  return true
}

const MENU: [string, string][] = [
  ['linear', 'Linear'],
  ['bezier', 'Bezier'],
  ['auto_bezier', 'Auto Bezier'],
  ['continuous_bezier', 'Continuous Bezier'],
  ['hold', 'Hold'],
  ['ease_in', 'Ease In'],
  ['ease_out', 'Ease Out']
]

/** Right-click on a keyframe: Premiere's Temporal Interpolation list, then Clear. */
export function keyframeMenu(e: React.MouseEvent, c: ClipCtx, key: string): void {
  if (!selectedKeys(c.clip.id).includes(key)) set({ kfSel: { clipId: c.clip.id, keys: [key] } })
  openContextMenu(e, [
    ...MENU.map(([interp, label]) => ({ label: t(label), run: () => setSelectedInterp(c, interp) })),
    'sep' as const,
    { label: t('Clear'), run: () => {
      set({ focusedPanel: 'effectControls' })
      deleteSelectedKeyframes()
    } }
  ])
}

// ---------------------------------------------------------------- dragging

const SNAP_PX = 6

/** Mouse down on a keyframe: select it (Shift/Ctrl toggles) and drag the selection in time. */
export function keyframeMouseDown(e: React.MouseEvent, c: ClipCtx, key: string, span: [number, number]): void {
  if (e.button !== 0) return
  e.stopPropagation()
  e.preventDefault()
  const clipId = c.clip.id
  let keys = selectedKeys(clipId)
  if (e.shiftKey || e.ctrlKey || e.metaKey) {
    keys = keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key]
    set({ kfSel: { clipId, keys } })
    if (!keys.includes(key)) return
  } else if (!keys.includes(key)) {
    keys = [key]
    set({ kfSel: { clipId, keys } })
  }
  const lane = (e.currentTarget as HTMLElement).closest('.lane') as HTMLElement
  const panel = lane.closest('.lm-ec') as HTMLElement
  const width = Math.max(1, lane.getBoundingClientRect().width)
  const perPx = (span[1] - span[0]) / width
  const tpf = ticksPerFrame(c.seq.settings)
  const grabbed = seqTimeOf(c.clip, Number(key.slice(key.lastIndexOf('@') + 1)))
  const els = [...panel.querySelectorAll<HTMLElement>('[data-kf]')].filter((el) => keys.includes(el.dataset.kf!))
  const x0 = e.clientX
  const timeOf = (k: string) => seqTimeOf(c.clip, Number(k.slice(k.lastIndexOf('@') + 1)))
  if (e.altKey && keys.length >= 2) return scaleDrag(c, keys, grabbed, els, panel, perPx, tpf, x0, timeOf)
  let d = 0
  const move = (ev: MouseEvent) => {
    d = Math.round(((ev.clientX - x0) * perPx) / tpf) * tpf
    if (ev.shiftKey) {
      // Shift snaps the grabbed keyframe to the playhead
      const head = get().programTime - grabbed
      if (Math.abs(head - (ev.clientX - x0) * perPx) < SNAP_PX * perPx) d = head
    }
    for (const el of els) el.style.translate = `${d / perPx}px 0`
    panel.dataset.kfdrag = String(Math.round(d / tpf))
  }
  const up = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', up)
    for (const el of els) el.style.translate = ''
    delete panel.dataset.kfdrag
    if (!d) return
    const ds = Math.round(d * c.clip.speed)
    void exec('keyframes.move', { sequence_id: c.seq.id, clip_id: clipId, targets: selectionTargets(keys), delta: d }, t('Move Keyframes')).then((r) => {
      if (r) set({ kfSel: { clipId, keys: keys.map((k) => `${k.slice(0, k.lastIndexOf('@'))}@${Number(k.slice(k.lastIndexOf('@') + 1)) + ds}`) } })
    })
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', up)
}

/** Alt-drag a selected keyframe (After Effects): the selection stretches or squeezes in time around
 * the selected keyframe farthest from the one grabbed. */
function scaleDrag(c: ClipCtx, keys: string[], grabbed: number, els: HTMLElement[], panel: HTMLElement, perPx: number, tpf: number, x0: number, timeOf: (k: string) => number): void {
  const times = keys.map(timeOf)
  const lo = Math.min(...times)
  const hi = Math.max(...times)
  const pivot = Math.abs(grabbed - lo) > Math.abs(grabbed - hi) ? lo : hi
  const span = grabbed - pivot
  if (!span) return
  let factor = 1
  const move = (ev: MouseEvent) => {
    const d = Math.round(((ev.clientX - x0) * perPx) / tpf) * tpf
    factor = Math.max(0.01, (span + d) / span)
    for (const el of els) el.style.translate = `${((timeOf(el.dataset.kf!) - pivot) * (factor - 1)) / perPx}px 0`
    panel.dataset.kfscale = factor.toFixed(2)
  }
  const up = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', up)
    for (const el of els) el.style.translate = ''
    delete panel.dataset.kfscale
    if (Math.abs(factor - 1) < 1e-6) return
    // the selection follows its keyframes (the engine's frame snapping, in source ticks)
    const step = tpf * c.clip.speed
    const pivotSrc = c.clip.source_in + (pivot - c.clip.start) * c.clip.speed
    const moved = keys.map((k) => {
      const t0 = Number(k.slice(k.lastIndexOf('@') + 1))
      const nt = pivotSrc + (t0 - pivotSrc) * factor
      return `${k.slice(0, k.lastIndexOf('@'))}@${Math.round(c.clip.source_in + Math.round((nt - c.clip.source_in) / step) * step)}`
    })
    void exec('keyframes.scale', { sequence_id: c.seq.id, clip_id: c.clip.id, targets: selectionTargets(keys), pivot: Math.round(pivot), factor }, t('Scale Keyframes')).then((r) => {
      if (r) set({ kfSel: { clipId: c.clip.id, keys: moved } })
    })
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', up)
}

// ---------------------------------------------------------------- copy / paste / nudge

type CopiedProperty = { prop?: KeyframeProp; effect_type?: string; param?: string; keyframes: Record<string, unknown>[] }
let kfClipboard: CopiedProperty[] = []

/** Ctrl+C in Effect Controls with keyframes selected: copy them (times relative to the earliest). */
export function copySelectedKeyframes(): boolean {
  const s = get()
  const sel = s.kfSel
  if (!sel?.keys.length || s.focusedPanel !== 'effectControls') return false
  const seq = s.doc?.active_sequence_id ? s.doc.sequences[s.doc.active_sequence_id] : null
  const clip = seq && Object.values(seq.tracks).map((tr) => tr.clips[sel.clipId]).find(Boolean)
  if (!clip) return false
  const targets = selectionTargets(sel.keys)
  const all = targets.flatMap((tg) => tg.times.map((x) => seqTimeOf(clip, x)))
  const first = Math.min(...all)
  kfClipboard = targets.map((tg) => {
    const kfs = ('prop' in tg ? clip.keyframes[tg.prop] : clip.effects?.find((e) => e.id === tg.effect_id)?.keyframes?.[tg.param]) as Kf[] | undefined
    const keyframes = (kfs ?? []).filter((k) => tg.times.includes(k.t)).map((k) => ({
      ...k,
      t: Math.round(seqTimeOf(clip, k.t) - first),
      // handle speeds are per source second: carry them per sequence second
      ...(k.ease_in ? { ease_in: [k.ease_in[0] * clip.speed, k.ease_in[1]] } : {}),
      ...(k.ease_out ? { ease_out: [k.ease_out[0] * clip.speed, k.ease_out[1]] } : {})
    }))
    if ('prop' in tg) return { prop: tg.prop, keyframes }
    const fx = clip.effects?.find((e) => e.id === tg.effect_id)
    return { effect_type: fx?.type, param: tg.param, keyframes }
  }).filter((x) => x.keyframes.length)
  return kfClipboard.length > 0
}

/** Ctrl+V in Effect Controls: paste copied keyframes at the playhead into the same properties. */
export function pasteKeyframes(): boolean {
  const s = get()
  if (s.focusedPanel !== 'effectControls' || !kfClipboard.length) return false
  const seq = s.doc?.active_sequence_id ? s.doc.sequences[s.doc.active_sequence_id] : null
  const clipId = s.selectedClips[0]
  if (!seq || !clipId) return false
  void exec('keyframes.paste', { sequence_id: seq.id, clip_id: clipId, at: s.programTime, properties: kfClipboard }, t('Paste Keyframes'))
  return true
}

/** Alt+Left / Alt+Right in Effect Controls: nudge the selected keyframes by frames. */
export function nudgeSelectedKeyframes(frames: number): boolean {
  const s = get()
  const sel = s.kfSel
  if (!sel?.keys.length || s.focusedPanel !== 'effectControls') return false
  const seq = s.doc?.active_sequence_id ? s.doc.sequences[s.doc.active_sequence_id] : null
  const clip = seq && Object.values(seq.tracks).map((tr) => tr.clips[sel.clipId]).find(Boolean)
  if (!seq || !clip) return false
  const d = frames * ticksPerFrame(seq.settings)
  const ds = Math.round(d * clip.speed)
  void exec('keyframes.move', { sequence_id: seq.id, clip_id: clip.id, targets: selectionTargets(sel.keys), delta: d }, t('Move Keyframes')).then((r) => {
    if (r) set({ kfSel: { clipId: clip.id, keys: sel.keys.map((k) => `${k.slice(0, k.lastIndexOf('@'))}@${Number(k.slice(k.lastIndexOf('@') + 1)) + ds}`) } })
  })
  return true
}

/** Mouse down on an empty lane: rubber-band selection over every visible keyframe. */
export function marqueeMouseDown(e: React.MouseEvent, clipId: string, draw: (box: [number, number, number, number] | null) => void): void {
  if (e.button !== 0 || e.altKey || e.ctrlKey) return
  const panel = (e.currentTarget as HTMLElement).closest('.lm-ec') as HTMLElement
  const add = e.shiftKey
  const base = add ? selectedKeys(clipId) : []
  const x0 = e.clientX
  const y0 = e.clientY
  const pr = panel.getBoundingClientRect()
  const update = (x1: number, y1: number) => {
    const [l, r] = [Math.min(x0, x1), Math.max(x0, x1)]
    const [tp, b] = [Math.min(y0, y1), Math.max(y0, y1)]
    draw([l - pr.left, tp - pr.top, r - l, b - tp])
    const hit: string[] = []
    for (const el of panel.querySelectorAll<HTMLElement>('[data-kf]')) {
      const q = el.getBoundingClientRect()
      const cx = q.left + q.width / 2
      const cy = q.top + q.height / 2
      if (cx >= l && cx <= r && cy >= tp && cy <= b) hit.push(el.dataset.kf!)
    }
    set({ kfSel: { clipId, keys: [...new Set([...base, ...hit])] } })
  }
  update(x0, y0)
  const move = (ev: MouseEvent) => update(ev.clientX, ev.clientY)
  const up = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', up)
    draw(null)
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', up)
}

// ---------------------------------------------------------------- icons

type Half = 'linear' | 'hold' | 'bezier' | 'continuous' | 'auto'

const near = (a: number[], b: number[]) => Math.abs(a[0] - b[0]) <= 1e-6 * Math.max(1, Math.abs(b[0])) && Math.abs(a[1] - b[1]) < 1e-3

/** Each side's interpolation, the way Premiere draws a keyframe: left half = incoming, right = outgoing.
 * A Bezier side whose handle draws the same straight line as linear shows as linear (Ease In / Out). */
export function halves(kfs: Kf[], i: number): [Half, Half] {
  const k = kfs[i]
  const side = (s: 'in' | 'out'): Half => {
    switch (k.interp) {
      case 'hold':
        return s === 'out' ? 'hold' : 'linear'
      case 'auto_bezier':
        return 'auto'
      case 'continuous_bezier':
        return 'continuous'
      case 'bezier': {
        const asLinear = kfs.map((x, j) => (j === i ? { ...x, interp: 'linear' as const } : x))
        return near(effectiveHandle(kfs, i, s), effectiveHandle(asLinear, i, s)) ? 'linear' : 'bezier'
      }
      case 'ease':
        return 'bezier'
      default:
        return 'linear'
    }
  }
  return [side('in'), side('out')]
}

// left halves in a 12x12 box centred on 0; the right half is the mirror image
const HALF_PATH: Record<Half, string> = {
  linear: 'M0,-5 L-5,0 L0,5 Z',
  hold: 'M0,-4.5 L-4.5,-4.5 L-4.5,4.5 L0,4.5 Z',
  bezier: 'M0,-5 L-4.5,-5 L-0.6,0 L-4.5,5 L0,5 Z',
  continuous: 'M0,-5 L-4.2,-5 Q-4.2,-1.2 -1,0 Q-4.2,1.2 -4.2,5 L0,5 Z',
  auto: 'M0,-4.5 A4.5,4.5 0 0 0 0,4.5 Z'
}

export function KfIcon({ kfs, i }: { kfs: Kf[]; i: number }) {
  const [l, r] = halves(kfs, i)
  return (
    <svg width={11} height={11} viewBox="-6 -6 12 12" data-shape={`${l}-${r}`}>
      <path d={HALF_PATH[l]} />
      <path d={HALF_PATH[r]} transform="scale(-1,1)" />
    </svg>
  )
}
