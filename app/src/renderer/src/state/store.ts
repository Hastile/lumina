import { create } from 'zustand'
import { applyPatch, engine, EngineError, type EngineEvent, type HistoryState, type Job } from '../engine/client'
import type { Clip, MediaItem, Project, Sequence, Track } from '../engine/document'

export type Tool = 'select' | 'trackSelect' | 'ripple' | 'rolling' | 'rate' | 'razor' | 'slip' | 'slide' | 'pen' | 'hand' | 'zoom' | 'type' | 'rectangle' | 'ellipse'

export type PanelId =
  | 'history' | 'project' | 'source' | 'program' | 'timeline' | 'effectControls' | 'audioMixer' | 'effects' | 'mediaBrowser' | 'tools' | 'audioMeters'
  | 'essentialSound' | 'lumetri' | 'properties' | 'metadata' | 'text' | 'audioTrackMixer' | 'graphicTemplates'

export type Toast = { id: number; kind: 'error' | 'info'; text: string }

type State = {
  connected: boolean
  fatal: string | null
  doc: Project | null
  version: number
  path: string | null
  history: HistoryState
  jobs: Record<string, Job>
  toasts: Toast[]

  focusedPanel: PanelId
  tool: Tool
  snap: boolean
  selectedClips: string[]
  selectedTransition: string | null
  selectedItems: string[]
  currentBin: string | null

  sourceMediaId: string | null
  sourceTime: number
  programTime: number
  playing: 'source' | 'program' | null
  shuttle: number // J/K/L rate: negative = reverse
  playbackResolution: 1 | 0.5 | 0.25 // Program/Source monitor resolution while playing (paused = full), like Premiere
  ecView: { clipId: string; start: number; end: number } | null // Effect Controls timeline zoom (sequence ticks)
  audioScrub: boolean // Premiere's "Play audio while scrubbing"
  flashTracks: string[] // tracks an edit just landed on (highlighted briefly)
  showClipKeyframes: boolean // Timeline Display Settings > Show Keyframes on clips
  kfSel: { clipId: string; keys: string[] } | null // selected keyframes in Effect Controls (`target@sourceTicks`)

  pxPerSecond: number
  scrollX: number
  openSequences: string[] // the Timeline panel's tabs (Premiere opens sequences one by one)
}

const emptyHistory: HistoryState = { can_undo: false, can_redo: false, undo_label: null, redo_label: null, dirty: false, in_transaction: false }

export const useStore = create<State>(() => ({
  connected: false,
  fatal: null,
  doc: null,
  version: 0,
  path: null,
  history: emptyHistory,
  jobs: {},
  toasts: [],
  focusedPanel: 'timeline',
  tool: 'select',
  snap: true,
  selectedClips: [],
  selectedTransition: null,
  selectedItems: [],
  currentBin: null,
  sourceMediaId: null,
  sourceTime: 0,
  programTime: 0,
  playing: null,
  shuttle: 0,
  playbackResolution: (() => {
    try {
      const v = Number(localStorage.getItem('lumina.playbackResolution'))
      return v === 1 || v === 0.25 ? v : 0.5
    } catch {
      return 0.5
    }
  })() as 1 | 0.5 | 0.25,
  ecView: null,
  kfSel: null,
  audioScrub: true,
  flashTracks: [],
  showClipKeyframes: true,
  pxPerSecond: 40,
  scrollX: 0,
  openSequences: []
}))

export const set = useStore.setState
export const get = useStore.getState

// ---------------------------------------------------------------- selectors

/** The sequence in the Timeline and Program monitor: the active one, while its tab is open. */
export function activeSequence(s: State = get()): Sequence | null {
  const id = s.doc?.active_sequence_id
  return (id && s.openSequences.includes(id) && s.doc?.sequences[id]) || null
}

export function sourceMedia(s: State = get()): MediaItem | null {
  return (s.sourceMediaId && s.doc?.media[s.sourceMediaId]) || null
}

export function findClip(seq: Sequence, clipId: string): { track: Track; clip: Clip } | null {
  for (const track of Object.values(seq.tracks)) {
    const clip = track.clips[clipId]
    if (clip) return { track, clip }
  }
  return null
}

export function sequenceEnd(seq: Sequence): number {
  let end = 0
  for (const t of Object.values(seq.tracks)) for (const c of Object.values(t.clips)) end = Math.max(end, c.end)
  return end
}

// ---------------------------------------------------------------- open sequences
// Like Premiere, a project opens only the sequences that were open last time (or its active
// one); others open from the Project panel and close from their Timeline tab. The list is a
// view setting, kept per project in localStorage, not part of the document.

const openKey = (doc: Project) => `lumina.openSequences.${doc.id}`

function restoreOpen(doc: Project): string[] {
  let ids: string[] = []
  try {
    ids = JSON.parse(localStorage.getItem(openKey(doc)) ?? '[]')
  } catch {
    /* none saved */
  }
  ids = Array.isArray(ids) ? ids.filter((id) => doc.sequences[id]) : []
  const active = doc.active_sequence_id
  return ids.length || !active || !doc.sequences[active] ? ids : [active]
}

export function openSequence(id: string): void {
  if (!get().openSequences.includes(id)) set((s) => ({ openSequences: [...s.openSequences, id] }))
  if (get().doc?.active_sequence_id !== id) void exec('sequence.set_active', { sequence_id: id })
}

/** Close a Timeline tab; closing the active one activates its neighbour (Premiere). */
export function closeSequence(id: string): void {
  const open = get().openSequences
  const i = open.indexOf(id)
  if (i < 0) return
  const rest = open.filter((x) => x !== id)
  set({ openSequences: rest })
  const next = rest[Math.min(i, rest.length - 1)]
  if (get().doc?.active_sequence_id === id && next) void exec('sequence.set_active', { sequence_id: next })
}

useStore.subscribe((s, prev) => {
  const doc = s.doc
  if (!doc || doc === prev.doc) {
    if (doc && s.openSequences !== prev.openSequences) {
      try {
        localStorage.setItem(openKey(doc), JSON.stringify(s.openSequences))
      } catch {
        /* storage unavailable: the tabs just aren't remembered */
      }
    }
    return
  }
  let open = doc.id !== prev.doc?.id ? restoreOpen(doc) : s.openSequences.filter((id) => doc.sequences[id])
  // a sequence made active elsewhere (new sequence, MCP, nested double-click) opens its tab
  const active = doc.active_sequence_id
  if (active && doc.sequences[active] && active !== prev.doc?.active_sequence_id && !open.includes(active)) open = [...open, active]
  if (open.length !== s.openSequences.length || open.some((id, i) => id !== s.openSequences[i])) set({ openSequences: open })
})

// ---------------------------------------------------------------- toasts

let toastId = 1
export function toast(text: string, kind: Toast['kind'] = 'error'): void {
  const id = toastId++
  set((s) => ({ toasts: [...s.toasts, { id, kind, text }] }))
  setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), kind === 'error' ? 6000 : 3000)
}

/** Run a command; surface engine errors as toasts instead of throwing. */
export async function exec<T = Record<string, unknown>>(name: string, params: Record<string, unknown> = {}, label?: string): Promise<T | null> {
  try {
    return await engine.exec<T>(name, params, label)
  } catch (err) {
    toast(err instanceof EngineError ? err.message : String(err))
    return null
  }
}

export async function call<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T | null> {
  try {
    return await engine.call<T>(method, params)
  } catch (err) {
    toast(err instanceof EngineError ? err.message : String(err))
    return null
  }
}

// ---------------------------------------------------------------- engine sync

function onEvent(e: EngineEvent): void {
  switch (e.type) {
    case 'patch': {
      set((s) => {
        let doc = s.doc
        for (const p of e.patches) doc = applyPatch(doc, p)
        const { type: _t, patches: _p, kind: _k, label: _l, version, ...history } = e
        const next: Partial<State> = { doc, version, history }
        // drop selection of clips that no longer exist
        const seq = activeSequence({ ...s, doc })
        if (seq) next.selectedClips = s.selectedClips.filter((id) => findClip(seq, id))
        if (s.sourceMediaId && !doc?.media[s.sourceMediaId]) next.sourceMediaId = null
        // (not the echo of a playhead this window sent: the user may have moved on since)
        if (!s.playing && seq && seq.playhead !== sentPlayhead && e.patches.some((p) => p.path[p.path.length - 1] === 'playhead')) next.programTime = seq.playhead
        return next
      })
      break
    }
    case 'reset': {
      const { type: _t, document, path, version, ...history } = e
      const doc = document as Project
      const seq = doc.active_sequence_id ? doc.sequences[doc.active_sequence_id] : null
      set({ doc, path, version, history, selectedClips: [], selectedItems: [], sourceMediaId: null, currentBin: doc.root_bin_id, programTime: seq?.playhead ?? 0 })
      break
    }
    case 'saved': {
      const { type: _t, path, ...history } = e
      set({ path, history })
      break
    }
    case 'history': {
      const { type: _t, ...history } = e
      set({ history })
      break
    }
    case 'job':
      set((s) => ({ jobs: { ...s.jobs, [e.job.id]: e.job } }))
      if (e.job.status === 'failed' && e.job.kind !== 'export') toast(`${e.job.kind} failed: ${e.job.error}`)
      break
  }
}

export async function connectEngine(): Promise<void> {
  try {
    engine.onEvent(onEvent)
    const hello = (await engine.connect()) as { document: Project; path: string | null; version: number } & HistoryState
    const { document: doc, path, version, ...history } = hello
    const seq = doc.active_sequence_id ? doc.sequences[doc.active_sequence_id] : null
    set({ connected: true, doc, path, version, history: { ...emptyHistory, ...history }, currentBin: doc.root_bin_id, programTime: seq?.playhead ?? 0 })
  } catch (err) {
    set({ fatal: String(err instanceof Error ? err.message : err) })
  }
}

// Keep the engine's playhead in sync for agents (debounced, not every frame).
let playheadTimer: ReturnType<typeof setTimeout> | undefined
let sentPlayhead: number | null = null
useStore.subscribe((s, prev) => {
  if (s.programTime === prev.programTime || s.playing === 'program') return
  clearTimeout(playheadTimer)
  playheadTimer = setTimeout(() => {
    const seq = activeSequence()
    if (seq && seq.playhead !== get().programTime) {
      sentPlayhead = Math.round(get().programTime)
      void engine.exec('sequence.set_playhead', { sequence_id: seq.id, time: sentPlayhead }).catch(() => undefined)
    }
  }, 250)
})
