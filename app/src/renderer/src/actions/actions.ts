// Every user-facing operation is an action with an id. Menus, the keymap,
// context menus and toolbar buttons all invoke actions by id, and actions
// only talk to the engine through commands - never by editing state locally.

import type { Clip, Sequence } from '../engine/document'
import { copySelectedKeyframes, deleteSelectedKeyframes, nudgeSelectedKeyframes, pasteKeyframes } from '../panels/KeyframeSelect'
import { t } from '@shared/i18n'
import { engine } from '../engine/client'
import { factoryLayout, focusPanel, isLayoutLocked, resetToSaved, saveLayout, setLayoutLocked, toggleMaximize } from '../layout/dock'
import { openDialog } from '../state/dialog'
import { exportFrame, openExportDialog } from '../layout/ExportDialog'
import { activeSequence, call, exec, findClip, get, sequenceEnd, set, sourceMedia, toast, type PanelId, type Tool } from '../state/store'
import { goTo, shuttle, slowShuttle, step, stop, togglePlay, type Monitor } from '../state/transport'
import { secondsToTicks, ticksPerFrame, ticksToSeconds } from '../util/time'

export type Action = { id: string; label: string; run: () => void | Promise<void> }

const actions = new Map<string, Action>()

export function def(id: string, label: string, run: () => void | Promise<void>): void {
  actions.set(id, { id, label, run })
}

export function runAction(id: string): void {
  const a = actions.get(id)
  if (!a) {
    toast(`Not implemented yet: ${id}`, 'info')
    return
  }
  void Promise.resolve(a.run()).catch((err) => toast(String(err)))
}

export function allActions(): Action[] {
  return [...actions.values()]
}

// ---------------------------------------------------------------- helpers

/** Which monitor do transport keys drive? Premiere: the focused one; panels other than Source drive Program. */
function monitor(): Monitor {
  return get().focusedPanel === 'source' ? 'source' : 'program'
}

function seqOrToast(): Sequence | null {
  const seq = activeSequence()
  if (!seq) toast(t('No sequence is open'), 'info')
  return seq
}

function targetedTracks(seq: Sequence, kind?: 'video' | 'audio'): string[] {
  const ids = [...(kind !== 'audio' ? seq.video_tracks : []), ...(kind !== 'video' ? seq.audio_tracks : [])]
  return ids.filter((id) => seq.tracks[id].targeted && !seq.tracks[id].locked)
}

function editPoints(seq: Sequence, trackIds: string[]): number[] {
  const pts = new Set<number>([0])
  for (const id of trackIds) for (const c of Object.values(seq.tracks[id].clips)) pts.add(c.start).add(c.end)
  return [...pts].sort((a, b) => a - b)
}

function selectedClipObjects(seq: Sequence): Clip[] {
  return get().selectedClips.map((id) => findClip(seq, id)?.clip).filter((c): c is Clip => !!c)
}

/** Save As for the active project, or the open project ``key``; false when cancelled or failed. */
export async function saveAs(key?: string): Promise<boolean> {
  const name = key ? get().projects.find((p) => p.key === key)?.name : get().doc?.name
  const path = await window.lumina.saveDialog({
    title: 'Save Project',
    defaultPath: `${name ?? 'Untitled'}.lumina`,
    filters: [{ name: 'Lumina Project', extensions: ['lumina'] }]
  }, 'project')
  if (!path || (await call('project.save', { path, ...(key ? { key } : {}) })) === null) return false
  void window.lumina.addRecent(path)
  return true
}

const isPremiere = (p: string) => /\.prproj$/i.test(p)

type PremiereImport = { media: number; sequences: string[]; bins: number; warnings: string[] }

/** Import a Premiere project (read-only) into the open project, like Premiere's Import of a .prproj. */
async function importPremiere(path: string, intoRoot = false): Promise<boolean> {
  toast(`Importing ${path.split(/[\\/]/).pop()}…`, 'info')
  const res = await exec<PremiereImport>('project.import_premiere', { path, into_root: intoRoot, bin_id: intoRoot ? null : get().currentBin }, 'Import Premiere Project')
  if (!res) return false
  toast(`Imported ${res.sequences.length} sequence(s), ${res.media} media, ${res.bins} bin(s)`, 'info')
  if (res.warnings.length) {
    openDialog({
      title: t('Premiere Import Report'),
      okLabel: t('OK'),
      fields: res.warnings.map((w, i) => ({ key: `w${i}`, label: '', type: 'note' as const, value: w })),
      onSubmit: () => undefined
    })
  }
  return true
}

async function importFiles(paths?: string[]): Promise<void> {
  const files =
    paths ??
    (await window.lumina.openDialog({
      title: 'Import',
      properties: ['openFile', 'multiSelections'],
      filters: [
        { name: 'All Supported Media', extensions: ['mp4', 'mov', 'm4v', 'mkv', 'webm', 'avi', 'mxf', 'mts', 'wav', 'mp3', 'm4a', 'aac', 'flac', 'png', 'jpg', 'jpeg', 'psd', 'tif', 'tiff', 'webp', 'gif', 'prproj', 'srt', 'mogrt'] },
        { name: 'Adobe Premiere Pro Projects', extensions: ['prproj'] },
        { name: 'Captions (.srt)', extensions: ['srt'] },
        { name: 'Motion Graphics Templates (.mogrt)', extensions: ['mogrt'] },
        { name: 'All Files', extensions: ['*'] }
      ]
    }, 'import'))
  if (!files.length) return
  for (const p of files.filter(isPremiere)) await importPremiere(p)
  // captions files and templates become project items (place them from the Project / Graphic Templates panels)
  for (const p of files.filter((x) => /\.srt$/i.test(x))) await exec('asset.add', { kind: 'captions', path: p, bin_id: get().currentBin }, t('Import'))
  for (const p of files.filter((x) => /\.(mogrt|aegraphic)$/i.test(x))) await exec('asset.add', { kind: 'template', path: p, bin_id: get().currentBin }, t('Import'))
  const media = files.filter((p) => !isPremiere(p) && !/\.(srt|mogrt|aegraphic)$/i.test(p))
  if (!media.length) return
  const res = await exec<{ media_ids: string[]; errors: { path: string; error: string }[] }>('media.import', { paths: media, bin_id: get().currentBin })
  for (const e of res?.errors ?? []) toast(`${e.path}: ${e.error}`)
}
export { importFiles }

function newSequenceDialog(): void {
  openDialog({
    title: t('New Sequence'),
    okLabel: t('OK'),
    fields: [
      { key: 'name', label: t('Sequence Name'), type: 'text', value: `Sequence ${String(Object.keys(get().doc?.sequences ?? {}).length + 1).padStart(2, '0')}` },
      {
        key: 'preset', label: t('Preset'), type: 'select', value: '1080p30',
        options: [
          { value: '1080p30', label: 'HD 1080p 30 fps' },
          { value: '1080p2997', label: 'HD 1080p 29.97 fps' },
          { value: '1080p24', label: 'HD 1080p 24 fps' },
          { value: '1080p2398', label: 'HD 1080p 23.976 fps' },
          { value: '1080p60', label: 'HD 1080p 60 fps' },
          { value: '2160p30', label: 'UHD 2160p 30 fps' },
          { value: 'v1080p30', label: 'Vertical 1080x1920 30 fps' }
        ]
      },
      { key: 'video', label: t('Video Tracks'), type: 'number', value: 3, min: 1, max: 99 },
      { key: 'audio', label: t('Audio Tracks'), type: 'number', value: 3, min: 1, max: 99 }
    ],
    onSubmit: async (v) => {
      const presets: Record<string, [number, number, number, number]> = {
        '1080p30': [1920, 1080, 30, 1], '1080p2997': [1920, 1080, 30000, 1001], '1080p24': [1920, 1080, 24, 1],
        '1080p2398': [1920, 1080, 24000, 1001], '1080p60': [1920, 1080, 60, 1], '2160p30': [3840, 2160, 30, 1], 'v1080p30': [1080, 1920, 30, 1]
      }
      const [width, height, fps_num, fps_den] = presets[String(v.preset)]
      await exec('sequence.create', {
        name: String(v.name), bin_id: get().currentBin, video_tracks: Number(v.video), audio_tracks: Number(v.audio),
        settings: { width, height, fps_num, fps_den, sample_rate: 48000 }
      })
    }
  })
}

function rename(): void {
  const s = get()
  const seq = activeSequence()
  if (s.focusedPanel === 'project' && s.selectedItems.length === 1) {
    const id = s.selectedItems[0]
    const doc = s.doc!
    const current = doc.media[id]?.name ?? doc.sequences[id]?.name ?? doc.assets?.[id]?.name ?? doc.bins[id]?.name
    if (current === undefined) return
    openDialog({
      title: t('Rename'), fields: [{ key: 'name', label: t('Name'), type: 'text', value: current }],
      onSubmit: async (v) => {
        const name = String(v.name)
        if (doc.media[id]) await exec('media.rename', { media_id: id, name })
        else if (doc.sequences[id]) await exec('sequence.rename', { sequence_id: id, name })
        else if (doc.assets?.[id]) await exec('asset.rename', { asset_id: id, name })
        else await exec('bin.rename', { bin_id: id, name })
      }
    })
    return
  }
  if (seq) {
    const clip = selectedClipObjects(seq)[0]
    if (!clip) return
    openDialog({
      title: t('Rename Clip'), fields: [{ key: 'name', label: t('Clip Name'), type: 'text', value: clip.name }],
      onSubmit: async (v) => void (await exec('clip.set', { sequence_id: seq.id, clip_ids: [clip.id], name: String(v.name) }))
    })
  }
}

async function placeFromSource(mode: 'insert' | 'overwrite'): Promise<void> {
  const seq = seqOrToast()
  const media = sourceMedia()
  if (!seq) return
  if (!media) return toast(t('Open a clip in the Source Monitor first (double-click it in the Project panel)'), 'info')
  const v = targetedTracks(seq, 'video')[0]
  const a = targetedTracks(seq, 'audio')[0]
  const res = await exec<{ end: number }>('clip.place', {
    sequence_id: seq.id, media_id: media.id, at: get().programTime, mode,
    video_track_id: v ?? null, audio_track_id: a ?? null, take_video: !!v, take_audio: !!a
  }, mode === 'insert' ? 'Insert' : 'Overwrite')
  if (res) {
    set({ programTime: res.end })
    flashTracks([v, a].filter((x): x is string => !!x))
  }
}

async function liftExtract(ripple: boolean): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  if (seq.in_point == null || seq.out_point == null) return toast(t('Set sequence In and Out points first (I / O)'), 'info')
  const label = ripple ? 'Extract' : 'Lift'
  const inPoint = seq.in_point
  // one undo step, like Premiere: remove the range and clear the marks
  await engine.call('transaction.begin', { label })
  try {
    await engine.exec('sequence.remove_ranges', { sequence_id: seq.id, ranges: [[inPoint, seq.out_point]], ripple, track_ids: ripple ? undefined : targetedTracks(seq) })
    await engine.exec('sequence.set_in_out', { sequence_id: seq.id, clear_in: true, clear_out: true })
    await engine.call('transaction.commit')
    set({ programTime: inPoint })
  } catch (err) {
    await engine.call('transaction.rollback').catch(() => undefined)
    toast(err instanceof Error ? err.message : String(err))
  }
}

async function rippleTrimToPlayhead(dir: 'prev' | 'next'): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  const t = get().programTime
  const pts = editPoints(seq, targetedTracks(seq))
  if (dir === 'prev') {
    const prev = [...pts].reverse().find((p) => p < t)
    if (prev === undefined) return
    await exec('sequence.remove_ranges', { sequence_id: seq.id, ranges: [[prev, t]], ripple: true }, 'Ripple Trim Previous Edit to Playhead')
    set({ programTime: prev })
  } else {
    const next = pts.find((p) => p > t)
    if (next === undefined) return
    await exec('sequence.remove_ranges', { sequence_id: seq.id, ranges: [[t, next]], ripple: true }, 'Ripple Trim Next Edit to Playhead')
  }
}

async function markInOut(which: 'in' | 'out' | 'clear' | 'clip'): Promise<void> {
  // Send only the mark that changes: the engine merges it (and applies
  // "In after Out clears Out"), so rapid I/O presses never race.
  const s = get()
  const delta = (t: number, frame: number) =>
    which === 'in' ? { in_point: t } : which === 'out' ? { out_point: t + frame } : { clear_in: true, clear_out: true }
  if (monitor() === 'source') {
    const media = sourceMedia()
    if (!media || which === 'clip') return
    const frame = ticksPerFrame(media.video ?? { fps_num: 30, fps_den: 1 })
    await exec('media.set_marks', { media_id: media.id, ...delta(s.sourceTime, frame) }, which === 'clear' ? 'Clear In and Out' : `Mark ${which === 'in' ? 'In' : 'Out'}`)
    return
  }
  const seq = seqOrToast()
  if (!seq) return
  if (which === 'clip') {
    const t = s.programTime
    const under = targetedTracks(seq).flatMap((id) => Object.values(seq.tracks[id].clips)).filter((c) => c.start <= t && t < c.end)
    if (!under.length) return
    await exec('sequence.set_in_out', { sequence_id: seq.id, in_point: Math.min(...under.map((c) => c.start)), out_point: Math.max(...under.map((c) => c.end)) }, 'Mark Clip')
    return
  }
  await exec('sequence.set_in_out', { sequence_id: seq.id, ...delta(s.programTime, ticksPerFrame(seq.settings)) }, which === 'clear' ? 'Clear In and Out' : `Mark ${which === 'in' ? 'In' : 'Out'}`)
}

function goEdit(dir: 1 | -1): void {
  const seq = activeSequence()
  if (!seq) return
  const t = get().programTime
  const pts = editPoints(seq, targetedTracks(seq).length ? targetedTracks(seq) : [...seq.video_tracks, ...seq.audio_tracks])
  const target = dir > 0 ? pts.find((p) => p > t) : [...pts].reverse().find((p) => p < t)
  if (target !== undefined) goTo('program', target)
}

// ---------------------------------------------------------------- definitions

const tool = (t: Tool) => () => set({ tool: t })
const panel = (p: PanelId) => () => focusPanel(p)

// File
def('file.newProject', 'New Project', async () => void (await call('project.new', { name: 'Untitled' })))
def('file.newFromTemplate', 'New Project from Template', async () => {
  const [path] = await window.lumina.openDialog({
    title: 'New Project from Template',
    properties: ['openFile'],
    filters: [{ name: 'Lumina Project', extensions: ['lumina'] }]
  }, 'project')
  // the template is only read: the new project is unsaved and Save asks for its own path
  if (path) await call('project.new', { name: 'Untitled', template: path })
})
def('file.newSequence', t('New Sequence'), newSequenceDialog)
def('file.newBin', t('New Bin'), async () => void (await exec('bin.create', { name: 'New Bin', parent_id: get().currentBin })))
def('file.open', 'Open Project', async () => {
  const [path] = await window.lumina.openDialog({
    title: 'Open Project',
    properties: ['openFile'],
    filters: [
      { name: 'Projects', extensions: ['lumina', 'prproj'] },
      { name: 'Lumina Project', extensions: ['lumina'] },
      { name: 'Adobe Premiere Pro Project (import)', extensions: ['prproj'] }
    ]
  }, 'project')
  if (path) await openProjectPath(path)
})

/** Open a .lumina project, or hand off a .prproj (read-only) into a new project; remembered in File > Open Recent. */
export async function openProjectPath(path: string, fromRecent = false): Promise<void> {
  let ok = false
  if (isPremiere(path)) {
    // hand-off: the .prproj is only read; the result is a new, unsaved Lumina project
    const name = path.split(/[\\/]/).pop()!.replace(/\.prproj$/i, '')
    if (await call('project.new', { name })) ok = await importPremiere(path, true)
  } else {
    const r = await call<{ recovered_patches?: number }>('project.open', { path })
    ok = r !== null
    if (r?.recovered_patches) toast(`Recovered ${r.recovered_patches} unsaved change(s) from the op-log`, 'info')
  }
  if (ok) void window.lumina.addRecent(path)
  else if (fromRecent) void window.lumina.removeRecent(path) // moved or deleted: drop it from the list (the error was shown)
}
def('file.save', t('Save'), async () => (get().path ? void (await call('project.save', {})) : void (await saveAs())))
def('file.saveAs', 'Save As', async () => void (await saveAs()))
def('file.import', 'Import', () => importFiles())
def('file.export', t('Export Media...'), () => openExportDialog())
def('file.exportFrame', t('Export Frame...'), () => exportFrame())

// Edit
def('edit.undo', t('Undo'), async () => void (await call('undo')))
def('edit.redo', t('Redo'), async () => void (await call('redo')))
def('edit.clear', t('Clear'), async () => {
  const s = get()
  if (deleteSelectedKeyframes()) return
  if (s.focusedPanel === 'project') {
    const doc = s.doc!
    for (const id of s.selectedItems) {
      if (doc.media[id]) await exec('media.remove', { media_ids: [id], remove_clips: true })
      else if (doc.sequences[id]) await exec('sequence.delete', { sequence_id: id })
      else if (doc.assets?.[id]) await exec('asset.remove', { asset_ids: [id] })
      else if (doc.bins[id]) await exec('bin.delete', { bin_id: id })
    }
    set({ selectedItems: [] })
    return
  }
  const seq = activeSequence()
  if (seq && s.selectedClips.length) await exec('clip.remove', { sequence_id: seq.id, clip_ids: s.selectedClips, include_linked: false }, 'Clear')
  else if (seq && s.selectedTransition) {
    await exec('transition.remove', { sequence_id: seq.id, transition_id: s.selectedTransition }, t('Clear'))
    set({ selectedTransition: null })
  }
})
def('edit.rippleDelete', t('Ripple Delete'), async () => {
  const seq = activeSequence()
  const ids = get().selectedClips
  if (!seq || !ids.length) return
  const r = await exec<{ unsynced_track_ids: string[] }>('clip.remove', { sequence_id: seq.id, clip_ids: ids, ripple: true, include_linked: false }, 'Ripple Delete')
  // a sync-locked track that could not close the gap (something sits in it) stays behind: say so
  const names = (r?.unsynced_track_ids ?? []).map((id) => seq.tracks[id]?.name).filter(Boolean)
  if (names.length) toast(`${t('Ripple Delete left these tracks out of sync')}: ${names.join(', ')}`, 'info')
})
// Clipboard: the copied clips with their tracks, as Premiere's Copy (Ctrl+C) keeps them
// kept by track kind and index, so they can land in another sequence or on other tracks
let clipboard: { kind: 'video' | 'audio'; index: number; clip: Clip }[] = []
const copySelection = (): boolean => {
  const seq = activeSequence()
  if (!seq) return false
  const ids = new Set(get().selectedClips)
  clipboard = Object.values(seq.tracks).flatMap((tr) => {
    const index = (tr.kind === 'video' ? seq.video_tracks : seq.audio_tracks).indexOf(tr.id)
    return Object.values(tr.clips).filter((c) => ids.has(c.id)).map((clip) => ({ kind: tr.kind, index, clip }))
  })
  return clipboard.length > 0
}

/** Where pasted clips go (Premiere): the copied arrangement starts on the lowest targeted track of
 * each kind; with no track of a kind targeted, clips keep their own tracks. */
function pasteTracks(seq: Sequence): { track_id: string; clip: Clip }[] | string {
  const out: { track_id: string; clip: Clip }[] = []
  for (const kind of ['video', 'audio'] as const) {
    const items = clipboard.filter((x) => x.kind === kind)
    if (!items.length) continue
    const order = kind === 'video' ? seq.video_tracks : seq.audio_tracks
    const target = order.findIndex((id) => seq.tracks[id].targeted)
    const low = Math.min(...items.map((x) => x.index))
    const offset = target >= 0 ? target - low : 0
    for (const x of items) {
      const id = order[x.index + offset]
      if (!id) return t('Not enough tracks for the copied clips: add tracks or target a lower track')
      out.push({ track_id: id, clip: x.clip })
    }
  }
  return out
}

/** Light up the tracks an edit landed on for a moment, so it is clear where it went. */
export function flashTracks(ids: string[]): void {
  set({ flashTracks: [...new Set(ids)] })
  setTimeout(() => set({ flashTracks: [] }), 700)
}
def('edit.cut', t('Cut'), async () => {
  const seq = activeSequence()
  if (seq && copySelection()) await exec('clip.remove', { sequence_id: seq.id, clip_ids: clipboard.map((x) => x.clip.id), include_linked: false }, t('Cut'))
})
def('edit.copy', t('Copy'), () => void (copySelectedKeyframes() || copySelection()))
const paste = async (mode: 'overwrite' | 'insert') => {
  const seq = activeSequence()
  if (!seq || !clipboard.length) return
  const clips = pasteTracks(seq)
  if (typeof clips === 'string') return toast(clips, 'info')
  const r = await exec<{ clip_ids: string[]; end: number }>('clip.paste', { sequence_id: seq.id, clips, at: get().programTime, mode }, mode === 'insert' ? t('Paste Insert') : t('Paste'))
  if (r) {
    set({ selectedClips: r.clip_ids })
    flashTracks(clips.map((x) => x.track_id))
    goTo('program', r.end)
  }
}
def('edit.paste', t('Paste'), () => (pasteKeyframes() ? undefined : paste('overwrite')))
const nudge = (frames: number) => async () => {
  if (nudgeSelectedKeyframes(frames)) return
  // the timeline: Premiere's Nudge Clip Selection Left / Right
  const seq = activeSequence()
  const ids = get().selectedClips
  if (!seq || !ids.length) return
  await exec('clip.move', { sequence_id: seq.id, clip_ids: ids, delta: frames * ticksPerFrame(seq.settings), include_linked: false }, t('Nudge'))
}
def('edit.nudgeLeft', t('Nudge Left'), nudge(-1))
def('edit.nudgeRight', t('Nudge Right'), nudge(1))
def('edit.nudgeLeft5', t('Nudge Left 5 Frames'), nudge(-5))
def('edit.nudgeRight5', t('Nudge Right 5 Frames'), nudge(5))
def('edit.pasteInsert', t('Paste Insert'), () => paste('insert'))
def('edit.pasteAttributes', t('Paste Attributes...'), () => {
  const seq = activeSequence()
  const src = clipboard.length === 1 || new Set(clipboard.map((x) => x.clip.link_id)).size === 1 ? clipboard : []
  const targets = get().selectedClips
  if (!seq || !src.length || !targets.length) return toast(t('Copy one clip, then select the clips to paste its attributes onto'), 'info')
  const video = src.find((x) => x.kind === 'video') ?? src[0]
  const audio = src.find((x) => x.kind === 'audio')
  openDialog({
    title: t('Paste Attributes'),
    okLabel: t('OK'),
    fields: [
      { key: 'note', label: '', type: 'note', value: `${t('Source')}: ${video.clip.name}` },
      { key: 'scale', label: t('Scale Attribute Times'), type: 'checkbox', value: false },
      { key: 'motion', label: t('Motion'), type: 'checkbox', value: true },
      { key: 'opacity', label: t('Opacity'), type: 'checkbox', value: true },
      { key: 'time_remapping', label: t('Time Remapping'), type: 'checkbox', value: false },
      { key: 'volume', label: t('Volume'), type: 'checkbox', value: true },
      { key: 'effects', label: t('Effects'), type: 'checkbox', value: true }
    ],
    onSubmit: async (v) => {
      const attributes = ['motion', 'opacity', 'time_remapping', 'volume', 'effects'].filter((k) => v[k])
      const kinds = (id: string) => Object.values(seq.tracks).find((tr) => tr.clips[id])?.kind
      await engine.call('transaction.begin', { label: t('Paste Attributes') })
      try {
        for (const [kind, s] of [['video', video], ['audio', audio ?? video]] as const) {
          const ids = targets.filter((id) => kinds(id) === kind)
          if (ids.length) await engine.exec('clip.paste_attributes', { sequence_id: seq.id, source: s.clip, clip_ids: ids, attributes, scale_keyframes: !!v.scale })
        }
        await engine.call('transaction.commit')
      } catch (err) {
        await engine.call('transaction.rollback').catch(() => undefined)
        toast(String(err instanceof Error ? err.message : err))
      }
    }
  })
})
def('edit.selectAll', t('Select All'), () => {
  const s = get()
  if (s.focusedPanel === 'project') {
    const doc = s.doc!
    const bin = s.currentBin
    set({ selectedItems: [...Object.values(doc.media), ...Object.values(doc.sequences)].filter((i) => i.bin_id === bin).map((i) => i.id) })
    return
  }
  const seq = activeSequence()
  if (seq) set({ selectedClips: Object.values(seq.tracks).flatMap((t) => (t.locked ? [] : Object.keys(t.clips))) })
})
def('edit.keyboardShortcuts', t('Keyboard Shortcuts...'), () => void import('../layout/ShortcutsDialog').then((m) => m.openShortcuts()))
def('edit.deselectAll', t('Deselect All'), () => set({ selectedClips: [], selectedItems: [] }))

// Clip
def('clip.rename', t('Rename'), rename)
def('clip.speed', 'Speed/Duration', () => void import('../layout/SpeedDialog').then((m) => m.speedDialogForSelection()))
def('clip.audioGain', t('Audio Gain'), () => {
  const seq = activeSequence()
  if (!seq) return
  // the selected audio clips, and the audio linked to selected video (Premiere)
  const sel = selectedClipObjects(seq)
  const links = new Set(sel.map((c) => c.link_id).filter(Boolean))
  const audio = seq.audio_tracks.flatMap((id) => Object.values(seq.tracks[id].clips)).filter((c) => sel.includes(c) || (c.link_id && links.has(c.link_id)))
  if (!audio.length) {
    toast(t('Select an audio clip'), 'info')
    return
  }
  const gain = audio[0].gain_db ?? 0
  openDialog({
    title: t('Audio Gain'),
    okLabel: t('OK'),
    fields: [
      { key: 'mode', label: '', type: 'select', value: 'set', options: [
        { value: 'set', label: t('Set Gain to') },
        { value: 'adjust', label: t('Adjust Gain by') },
        { value: 'normalize_max', label: t('Normalize Max Peak to') },
        { value: 'normalize_all', label: t('Normalize All Peaks to') }
      ] },
      { key: 'db', label: 'dB', type: 'number', value: gain, min: -96, max: 96, step: 0.1, suffix: 'dB' }
    ],
    onSubmit: async (v) => {
      const res = await exec<{ peaks_db: Record<string, number | null> }>('clip.audio_gain', { sequence_id: seq.id, clip_ids: audio.map((c) => c.id), mode: v.mode, db: Number(v.db) }, t('Audio Gain'))
      const peaks = Object.values(res?.peaks_db ?? {}).filter((x): x is number => x !== null)
      if (peaks.length) toast(`${t('Peak Amplitude')}: ${Math.max(...peaks).toFixed(1)} dB`, 'info')
    }
  })
})
def('clip.nest', t('Nest...'), () => {
  const seq = activeSequence()
  const ids = get().selectedClips
  if (!seq || !ids.length) return toast(t('Select the clips to nest'), 'info')
  const n = 1 + Object.values(get().doc!.sequences).filter((q) => q.name.startsWith('Nested Sequence')).length
  openDialog({
    title: t('Nested Sequence Name'),
    okLabel: t('OK'),
    fields: [{ key: 'name', label: t('Name'), type: 'text', value: `Nested Sequence ${String(n).padStart(2, '0')}` }],
    onSubmit: async (v) => {
      const r = await exec<{ video_clip_id?: string; audio_clip_id?: string }>('sequence.nest', { sequence_id: seq.id, clip_ids: ids, name: String(v.name) }, t('Nest'))
      if (r) set({ selectedClips: [r.video_clip_id, r.audio_clip_id].filter((x): x is string => !!x) })
    }
  })
})
def('clip.unnest', t('Un-nest'), async () => {
  const seq = activeSequence()
  const c = seq && selectedClipObjects(seq).find((x) => x.sequence_id)
  if (!seq || !c) return toast(t('Select a nested sequence clip'), 'info')
  const r = await exec<{ clip_ids: string[]; warnings: string[] }>('sequence.unnest', { sequence_id: seq.id, clip_id: c.id }, t('Un-nest'))
  if (r) {
    set({ selectedClips: r.clip_ids })
    if (r.warnings.length) toast(r.warnings.join(' · '), 'info')
  }
})
def('clip.insert', t('Insert'), () => placeFromSource('insert'))
def('clip.overwrite', t('Overwrite'), () => placeFromSource('overwrite'))
def('clip.enable', t('Enable'), async () => {
  const seq = activeSequence()
  if (!seq) return
  const clips = selectedClipObjects(seq)
  if (clips.length) await exec('clip.set', { sequence_id: seq.id, clip_ids: clips.map((c) => c.id), enabled: !clips[0].enabled }, 'Enable')
})
def('clip.link', t('Link'), async () => {
  const seq = activeSequence()
  if (!seq) return
  const clips = selectedClipObjects(seq)
  if (!clips.length) return
  if (clips.some((c) => c.link_id)) await exec('clip.unlink', { sequence_id: seq.id, clip_ids: clips.map((c) => c.id) }, 'Unlink')
  else if (clips.length > 1) await exec('clip.link', { sequence_id: seq.id, clip_ids: clips.map((c) => c.id) }, 'Link')
})

// Sequence
def('sequence.addEdit', t('Add Edit'), async () => {
  const seq = seqOrToast()
  if (seq) await exec('clip.razor', { sequence_id: seq.id, at: get().programTime, track_ids: targetedTracks(seq) }, 'Add Edit')
})
def('sequence.addEditAll', t('Add Edit to All Tracks'), async () => {
  const seq = seqOrToast()
  if (seq) await exec('clip.razor', { sequence_id: seq.id, at: get().programTime }, 'Add Edit to All Tracks')
})
def('sequence.lift', t('Lift'), () => liftExtract(false))
def('sequence.extract', t('Extract'), () => liftExtract(true))

// Ctrl+D / Ctrl+Shift+D / Shift+D: default transitions (selected clips, else the edit point nearest the playhead)
async function applyDefaultTransition(kind: 'video' | 'audio' | 'both'): Promise<void> {
  const seq = activeSequence()
  if (!seq) return
  const sel = get().selectedClips
  await exec('transition.apply_default', { sequence_id: seq.id, kind, clip_ids: sel.length ? sel : null }, t('Apply Transition'))
}
def('sequence.applyVideoTransition', t('Apply Video Transition'), () => applyDefaultTransition('video'))
def('sequence.applyAudioTransition', t('Apply Audio Transition'), () => applyDefaultTransition('audio'))
def('sequence.applyDefaultTransitions', t('Apply Default Transitions to Selection'), () => applyDefaultTransition('both'))
def('sequence.rippleTrimPrev', t('Ripple Trim Previous Edit to Playhead'), () => rippleTrimToPlayhead('prev'))
def('sequence.rippleTrimNext', t('Ripple Trim Next Edit to Playhead'), () => rippleTrimToPlayhead('next'))
def('sequence.toggleSnap', t('Snap in Timeline'), () => set((s) => ({ snap: !s.snap })))
def('sequence.addTracks', 'Add Tracks', () => {
  const seq = seqOrToast()
  if (!seq) return
  openDialog({
    title: 'Add Tracks',
    fields: [
      { key: 'video', label: t('Add Video Track(s)'), type: 'number', value: 1, min: 0, max: 20 },
      { key: 'audio', label: t('Add Audio Track(s)'), type: 'number', value: 0, min: 0, max: 20 }
    ],
    onSubmit: async (v) => {
      await engine.call('transaction.begin', { label: 'Add Tracks' })
      try {
        for (let i = 0; i < Number(v.video); i++) await engine.exec('track.add', { sequence_id: seq.id, kind: 'video' })
        for (let i = 0; i < Number(v.audio); i++) await engine.exec('track.add', { sequence_id: seq.id, kind: 'audio' })
        await engine.call('transaction.commit')
      } catch (err) {
        await engine.call('transaction.rollback').catch(() => undefined)
        toast(String(err))
      }
    }
  })
})

// Markers
def('markers.markIn', t('Mark In'), () => markInOut('in'))
def('markers.markOut', t('Mark Out'), () => markInOut('out'))
def('markers.markClip', t('Mark Clip'), () => markInOut('clip'))
def('markers.clearInOut', t('Clear In and Out'), () => markInOut('clear'))
def('markers.goIn', t('Go to In'), () => {
  const m = monitor()
  const t = m === 'source' ? sourceMedia()?.in_point : activeSequence()?.in_point
  if (t != null) goTo(m, t)
})
def('markers.goOut', t('Go to Out'), () => {
  const m = monitor()
  const t = m === 'source' ? sourceMedia()?.out_point : activeSequence()?.out_point
  if (t != null) goTo(m, t)
})
def('markers.add', t('Add Marker'), async () => {
  const seq = seqOrToast()
  if (seq) await exec('marker.add', { sequence_id: seq.id, at: get().programTime }, 'Add Marker')
})

// Transport
def('transport.playStop', t('Play-Stop Toggle'), () => togglePlay(monitor()))
def('transport.shuttleLeft', 'Shuttle Left', () => shuttle(monitor(), -1))
def('transport.shuttleStop', 'Shuttle Stop', () => stop())
def('transport.shuttleRight', 'Shuttle Right', () => shuttle(monitor(), 1))
def('transport.shuttleSlowLeft', 'Shuttle Slow Left', () => slowShuttle(monitor(), -1))
def('transport.shuttleSlowRight', 'Shuttle Slow Right', () => slowShuttle(monitor(), 1))
def('transport.stepBack', t('Step Back 1 Frame'), () => step(monitor(), -1))
def('transport.stepForward', t('Step Forward 1 Frame'), () => step(monitor(), 1))
def('transport.stepBack5', 'Step Back Five Frames', () => step(monitor(), -5))
def('transport.stepForward5', 'Step Forward Five Frames', () => step(monitor(), 5))
def('transport.goStart', 'Go to Start', () => goTo(monitor(), 0))
def('transport.goEnd', 'Go to End', () => {
  const m = monitor()
  if (m === 'program') {
    const seq = activeSequence()
    if (seq) goTo('program', sequenceEnd(seq))
  } else {
    const media = sourceMedia()
    if (media) goTo('source', media.duration)
  }
})
def('transport.prevEdit', t('Go to Previous Edit Point'), () => goEdit(-1))
def('transport.nextEdit', t('Go to Next Edit Point'), () => goEdit(1))

// Tools
def('tool.select', t('Selection Tool'), tool('select'))
def('tool.trackSelect', t('Track Select Forward Tool'), tool('trackSelect'))
def('tool.ripple', t('Ripple Edit Tool'), tool('ripple'))
def('tool.rolling', t('Rolling Edit Tool'), tool('rolling'))
def('tool.rate', t('Rate Stretch Tool'), tool('rate'))
def('tool.razor', t('Razor Tool'), tool('razor'))
def('tool.slip', t('Slip Tool'), tool('slip'))
def('tool.slide', t('Slide Tool'), tool('slide'))
def('tool.pen', t('Pen Tool'), tool('pen'))
def('tool.hand', t('Hand Tool'), tool('hand'))
def('tool.zoom', t('Zoom Tool'), tool('zoom'))
def('tool.type', t('Type Tool'), tool('type'))
def('tool.rectangle', t('Rectangle Tool'), tool('rectangle'))
def('tool.ellipse', t('Ellipse Tool'), tool('ellipse'))

// View
// Graphics
def('graphics.newText', t('New Layer > Text'), async () => {
  const seq = seqOrToast()
  if (!seq) return
  const res = await exec<{ clip_id: string; layer_id: string }>('graphic.create_text', { sequence_id: seq.id, at: get().programTime, text: t('New Text Layer') }, t('New Layer > Text'))
  if (res) {
    set({ selectedClips: [res.clip_id], selectedLayer: res.layer_id })
    focusPanel('properties')
  }
})

/** Premiere adds the new layer to the selected graphic clip, else makes a new graphic at the playhead. */
export async function newShapeLayer(shape: 'rectangle' | 'ellipse', rect?: number[]): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  const label = t(shape === 'ellipse' ? 'New Layer > Ellipse' : 'New Layer > Rectangle')
  const sel = get().selectedClips
  const target = sel.length === 1 ? Object.values(seq.tracks).map((tr) => tr.clips[sel[0]]).find(Boolean) : undefined
  const at = get().programTime
  if (target?.graphic && target.start <= at && at < target.end) {
    const added = await exec<{ layer_id: string }>('graphic.add_shape', { sequence_id: seq.id, clip_id: target.id, shape, rect }, label)
    if (added) set({ selectedLayer: added.layer_id })
    focusPanel('properties')
    return
  }
  const res = await exec<{ clip_id: string; layer_id: string }>('graphic.create_shape', { sequence_id: seq.id, at, shape, rect }, label)
  if (res) {
    set({ selectedClips: [res.clip_id], selectedLayer: res.layer_id })
    focusPanel('properties')
  }
}
def('graphics.newRectangle', t('New Layer > Rectangle'), () => newShapeLayer('rectangle'))
def('graphics.newEllipse', t('New Layer > Ellipse'), () => newShapeLayer('ellipse'))

// Language (reload so every module re-reads its strings)
for (const l of ['ko', 'en'] as const) {
  def(`view.locale.${l}`, l, () => {
    try {
      localStorage.setItem('lumina.locale', l)
    } catch {
      /* ignore */
    }
    location.reload()
  })
}

def('view.zoomIn', t('Zoom In'), () => set((s) => ({ pxPerSecond: Math.min(4000, s.pxPerSecond * 1.5) })))
def('view.zoomOut', t('Zoom Out'), () => set((s) => ({ pxPerSecond: Math.max(0.5, s.pxPerSecond / 1.5) })))
def('view.zoomFit', t('Zoom to Sequence'), () => void window.dispatchEvent(new CustomEvent('timeline:zoomFit')))

// Window
def('window.workspace.editing', 'Editing', () => factoryLayout())
def('window.resetLayout', t('Reset to Saved Layout'), () => resetToSaved())
def('window.saveLayout', t('Save Changes to This Workspace'), () => {
  saveLayout()
  toast(t('Workspace saved: Reset to Saved Layout (Alt+Shift+0) returns to it'), 'info')
})
def('window.lockLayout', t('Lock Workspace'), () => {
  const locked = !isLayoutLocked()
  setLayoutLocked(locked)
  toast(locked ? t('Workspace locked: panels cannot be dragged') : t('Workspace unlocked'), 'info')
})
def('window.project', t('Project'), panel('project'))
def('window.source', t('Source Monitor'), panel('source'))
def('window.timeline', t('Timelines'), panel('timeline'))
def('window.program', t('Program Monitor'), panel('program'))
def('window.effectControls', t('Effect Controls'), panel('effectControls'))
def('window.audioMixer', t('Audio Clip Mixer'), panel('audioMixer'))
def('window.effects', t('Effects'), panel('effects'))
def('window.mediaBrowser', t('Media Browser'), panel('mediaBrowser'))
def('window.tools', t('Tools'), panel('tools'))
def('window.audioMeters', t('Audio Meters'), panel('audioMeters'))
def('window.essentialSound', t('Essential Sound'), panel('essentialSound'))
def('window.lumetri', t('Lumetri Color'), panel('lumetri'))
def('window.history', t('History'), panel('history'))
def('window.properties', t('Properties'), panel('properties'))
def('window.metadata', t('Metadata'), panel('metadata'))
def('window.text', t('Text'), panel('text'))
def('window.audioTrackMixer', t('Audio Track Mixer'), panel('audioTrackMixer'))
def('window.graphicTemplates', t('Graphic Templates'), panel('graphicTemplates'))
def('window.maximize', 'Maximize or Restore Frame Under Cursor', () => toggleMaximize(get().focusedPanel))

// small util used by panels
export function secondsLabel(t: number): string {
  return `${ticksToSeconds(t).toFixed(2)}s`
}
export { secondsToTicks }
