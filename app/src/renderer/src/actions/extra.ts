// Actions for caption tracks, track effects, Frame Hold, media management and
// analysis-driven edits (Remove Silence, Scene Edit Detection). Registered with
// the same action ids the menus, context menus and keymap use.

import { t } from '@shared/i18n'
import type { Clip, Sequence, Track } from '../engine/document'
import { openDialog } from '../state/dialog'
import { activeSequence, call, exec, findClip, get, set, toast } from '../state/store'
import { def } from './actions'
import { isCaptionTrack } from '../util/tracks'
import { secondsToTicks } from '../util/time'

function seqOrToast(): Sequence | null {
  const seq = activeSequence()
  if (!seq) toast(t('No sequence is open'), 'info')
  return seq
}

function selectedClips(seq: Sequence): { clip: Clip; track: Track }[] {
  return get().selectedClips.map((id) => findClip(seq, id)).filter((x): x is { clip: Clip; track: Track } => !!x)
}

/** The caption track an action works on: the given one, the selected cue's, or the topmost. */
function captionTrack(seq: Sequence, trackId?: string): string | null {
  if (trackId) return trackId
  const sel = selectedClips(seq).find((x) => isCaptionTrack(x.track))
  if (sel) return sel.track.id
  return [...seq.video_tracks].reverse().find((id) => isCaptionTrack(seq.tracks[id])) ?? null
}

// ---------------------------------------------------------------- captions

export async function addCaptionTrack(): Promise<string | null> {
  const seq = seqOrToast()
  if (!seq) return null
  const r = await exec<{ track_id: string }>('caption.add_track', { sequence_id: seq.id }, t('Add Caption Track'))
  return r?.track_id ?? null
}

export async function importCaptions(paths?: string[], trackId?: string): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  const files = paths ?? (await window.lumina.openDialog({ title: t('Import Captions'), properties: ['openFile', 'multiSelections'], filters: [{ name: 'SubRip (.srt)', extensions: ['srt'] }] }, 'import'))
  for (const path of files) {
    const r = await exec<{ count: number; skipped: number }>('caption.import_srt', { sequence_id: seq.id, path, track_id: trackId ?? null }, t('Import Captions'))
    if (r) toast(`${t('Imported captions')}: ${r.count}${r.skipped ? ` (${t('skipped')} ${r.skipped})` : ''}`, 'info')
  }
}

export async function exportCaptions(trackId?: string): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  const tid = captionTrack(seq, trackId)
  if (!tid) return toast(t('The sequence has no caption track'), 'info')
  const path = await window.lumina.saveDialog({ title: t('Export Captions'), defaultPath: `${seq.name}.srt`, filters: [{ name: 'SubRip (.srt)', extensions: ['srt'] }] }, 'export')
  if (!path) return
  const r = await call<{ count: number }>('caption.export_srt', { sequence_id: seq.id, track_id: tid, path })
  if (r) toast(`${t('Exported captions')}: ${r.count}`, 'info')
}

export function addCaptionAtPlayhead(): void {
  const seq = seqOrToast()
  if (!seq) return
  openDialog({
    title: t('Add Caption'),
    fields: [
      { key: 'text', label: t('Text'), type: 'text', value: '' },
      { key: 'duration', label: t('Duration'), type: 'number', value: 3, min: 0.1, max: 600, step: 0.1, suffix: 's' }
    ],
    onSubmit: async (v) => {
      if (!String(v.text).trim()) return
      const r = await exec<{ clip_id: string }>('caption.add', { sequence_id: seq.id, track_id: captionTrack(seq), text: String(v.text), at: get().programTime, duration: secondsToTicks(Number(v.duration)) }, t('Add Caption'))
      if (r) set({ selectedClips: [r.clip_id] })
    }
  })
}

export function editCaptionText(clipId?: string): void {
  const seq = seqOrToast()
  if (!seq) return
  const found = clipId ? findClip(seq, clipId) : selectedClips(seq).find((x) => x.clip.caption)
  if (!found?.clip.caption) return
  openDialog({
    title: t('Edit Caption'),
    fields: [{ key: 'text', label: t('Text'), type: 'text', value: found.clip.caption.text }],
    onSubmit: async (v) => void (await exec('caption.set', { sequence_id: seq.id, clip_id: found.clip.id, text: String(v.text) }, t('Edit Caption')))
  })
}

export function captionStyleDialog(trackId?: string): void {
  const seq = seqOrToast()
  if (!seq) return
  const tid = captionTrack(seq, trackId)
  const style = tid ? seq.tracks[tid].caption_style : null
  if (!tid || !style) return toast(t('The sequence has no caption track'), 'info')
  openDialog({
    title: `${t('Caption Track Style')} · ${seq.tracks[tid].name}`,
    fields: [
      { key: 'font_family', label: t('Font'), type: 'text', value: style.font_family },
      { key: 'font_size', label: t('Font Size'), type: 'number', value: style.font_size, min: 1, max: 2000 },
      { key: 'bold', label: t('Bold'), type: 'checkbox', value: style.bold },
      { key: 'color', label: t('Fill'), type: 'text', value: style.color },
      { key: 'stroke_color', label: t('Stroke'), type: 'text', value: style.stroke_color ?? '' },
      { key: 'stroke_width', label: t('Stroke Width'), type: 'number', value: style.stroke_width, min: 0, max: 200, step: 0.5 },
      { key: 'background', label: t('Background'), type: 'text', value: style.background ?? '' },
      {
        key: 'align', label: t('Align'), type: 'select', value: style.align,
        options: [{ value: 'left', label: t('Left') }, { value: 'center', label: t('Center') }, { value: 'right', label: t('Right') }]
      },
      { key: 'x', label: t('Position X'), type: 'number', value: style.position[0] },
      { key: 'y', label: t('Position Y'), type: 'number', value: style.position[1] }
    ],
    onSubmit: async (v) => {
      const s = {
        font_family: String(v.font_family), font_size: Number(v.font_size), bold: !!v.bold, color: String(v.color),
        stroke_color: String(v.stroke_color) || null, stroke_width: Number(v.stroke_width), background: String(v.background) || null,
        align: String(v.align), position: [Number(v.x), Number(v.y)]
      }
      await exec('caption.set_style', { sequence_id: seq.id, track_id: tid, style: s }, t('Caption Track Style'))
    }
  })
}

export async function graphicsToCaptions(trackId: string): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  const r = await exec<{ converted: number }>('caption.from_graphics', { sequence_id: seq.id, track_id: trackId }, t('Convert Graphics to Captions'))
  if (r) toast(`${t('Converted to captions')}: ${r.converted}`, 'info')
}

export async function upgradeCaptions(trackId?: string): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  const cues = selectedClips(seq).filter((x) => x.clip.caption).map((x) => x.clip.id)
  await exec('caption.to_graphics', { sequence_id: seq.id, track_id: cues.length ? null : captionTrack(seq, trackId), clip_ids: cues }, t('Upgrade Caption to Graphic'))
}

// ---------------------------------------------------------------- project items (assets)

export async function newAdjustmentLayer(): Promise<void> {
  const r = await exec<{ asset_id: string }>('asset.add', { kind: 'adjustment', name: t('Adjustment Layer'), bin_id: get().currentBin }, t('New Item > Adjustment Layer'))
  if (r) set({ selectedItems: [r.asset_id] })
}

/** Put a project item on the timeline (double-click / drop): captions -> a caption track at the
 * sequence start, templates and adjustment layers -> the playhead (or ``at`` / ``trackId``). */
export async function placeAsset(assetId: string, at?: number, trackId?: string): Promise<void> {
  const seq = seqOrToast()
  const a = get().doc?.assets?.[assetId]
  if (!seq || !a) return
  const label = a.kind === 'captions' ? t('Place Captions') : a.kind === 'template' ? t('Place Motion Graphics Template') : t('Adjustment Layer')
  const r = await exec<{ clip_id?: string; clip_ids?: string[] }>('asset.place', {
    sequence_id: seq.id, asset_id: assetId, at: at ?? (a.kind === 'captions' ? null : get().programTime), track_id: trackId ?? null
  }, label)
  if (r?.clip_id) set({ selectedClips: [r.clip_id] })
}

// ---------------------------------------------------------------- track effects

export async function addTrackEffect(trackId: string, type: string): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  await exec('effect.add', { sequence_id: seq.id, track_ids: [trackId], type }, t('Apply Track Effect'))
  selectOnTrack(seq, trackId)
}

export async function applyPresetToTrack(trackId: string, presetId: string): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  await exec('effect.apply_preset', { sequence_id: seq.id, track_ids: [trackId], preset_id: presetId }, t('Apply Preset to Track'))
  selectOnTrack(seq, trackId)
}

/** Track effects show in Effect Controls with a clip of their track: select the one under the playhead. */
function selectOnTrack(seq: Sequence, trackId: string): void {
  const s = activeSequence() ?? seq
  const clips = Object.values(s.tracks[trackId]?.clips ?? {}).sort((a, b) => a.start - b.start)
  const at = get().programTime
  const c = clips.find((x) => x.start <= at && at < x.end) ?? clips[0]
  if (c) set({ selectedClips: [c.id], focusedPanel: 'effectControls' })
}

// ---------------------------------------------------------------- Frame Hold

async function frameHold(mode: 'hold' | 'add' | 'insert' | 'clear', extra: Record<string, unknown> = {}): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  const v = selectedClips(seq).find((x) => x.track.kind === 'video' && x.clip.media_id && !x.track.caption_style)
  if (!v) return toast(t('Select a video clip'), 'info')
  const label = { hold: t('Frame Hold Options'), add: t('Add Frame Hold'), insert: t('Insert Frame Hold Segment'), clear: t('Remove Frame Hold') }[mode]
  await exec('clip.frame_hold', { sequence_id: seq.id, clip_id: v.clip.id, mode, at: get().programTime, ...extra }, label)
}

function frameHoldOptions(): void {
  const seq = seqOrToast()
  if (!seq) return
  const v = selectedClips(seq).find((x) => x.track.kind === 'video' && x.clip.media_id)
  if (!v) return toast(t('Select a video clip'), 'info')
  openDialog({
    title: t('Frame Hold Options'),
    fields: [
      { key: 'on', label: t('Hold On'), type: 'checkbox', value: true },
      {
        key: 'hold_on', label: t('Frame'), type: 'select', value: 'playhead',
        options: [{ value: 'playhead', label: t('Playhead') }, { value: 'in', label: t('In Point') }, { value: 'out', label: t('Out Point') }]
      }
    ],
    onSubmit: async (r) => void (await frameHold(r.on ? 'hold' : 'clear', { hold_on: String(r.hold_on) }))
  })
}

// ---------------------------------------------------------------- media

export function linkMedia(mediaId: string): void {
  const item = get().doc?.media[mediaId]
  if (!item) return
  void window.lumina
    .openDialog({ title: `${t('Link Media')} · ${item.name}`, properties: ['openFile'] }, 'import')
    .then(async (files) => {
      if (!files.length) return
      const r = await exec<{ warning: string | null }>('media.relink', { media_id: mediaId, path: files[0] }, t('Link Media'))
      if (r?.warning) toast(r.warning, 'info')
    })
}

export async function locateMedia(mediaIds: string[] = []): Promise<void> {
  const dirs = await window.lumina.openDialog({ title: t('Search for missing media in folder'), properties: ['openDirectory'] }, 'import')
  if (!dirs.length) return
  const r = await exec<{ linked: unknown[]; missing: string[]; rejected: unknown[] }>('media.locate', { folder: dirs[0], media_ids: mediaIds }, t('Link Media'))
  if (r) toast(`${t('Linked')}: ${r.linked.length} · ${t('Not found')}: ${r.missing.length}${r.rejected.length ? ` · ${t('Rejected')}: ${r.rejected.length}` : ''}`, 'info')
}

export async function makeOffline(mediaIds: string[], offline = true): Promise<void> {
  if (mediaIds.length) await exec('media.make_offline', { media_ids: mediaIds, offline }, offline ? t('Make Offline') : t('Make Online'))
}

export async function scanMedia(): Promise<void> {
  const r = await call<{ offline: { media_id: string; name: string; reason: string }[]; duplicates: { media_ids: string[]; names: string[] }[]; unused: string[] }>('media.scan')
  if (!r) return
  const lines = [
    `${t('Offline media')}: ${r.offline.length}`,
    ...r.offline.slice(0, 8).map((o) => `  · ${o.name} (${t(o.reason === 'made offline' ? 'made offline' : 'file missing')})`),
    `${t('Duplicate imports')}: ${r.duplicates.length}`,
    ...r.duplicates.slice(0, 8).map((d) => `  · ${d.names.join(' = ')}`),
    `${t('Unused media')}: ${r.unused.length}`
  ]
  openDialog({
    title: t('Media Scan'),
    okLabel: r.offline.length ? t('Search for missing media...') : t('OK'),
    fields: [{ key: 'report', label: '', type: 'note', value: lines.join('\n') }],
    onSubmit: async () => {
      if (r.offline.length) await locateMedia(r.offline.map((o) => o.media_id))
    }
  })
  set({ selectedItems: r.offline.map((o) => o.media_id) })
}

// ---------------------------------------------------------------- analysis

function removeDialog(kind: 'silence' | 'black'): void {
  const seq = seqOrToast()
  if (!seq) return
  const isSilence = kind === 'silence'
  openDialog({
    title: isSilence ? t('Remove Silence') : t('Remove Black Frames'),
    okLabel: t('Remove'),
    fields: [
      ...(isSilence ? [{ key: 'db', label: t('Silence below'), type: 'number' as const, value: -40, min: -90, max: -10, suffix: 'dB' }] : []),
      { key: 'min', label: t('Minimum length'), type: 'number', value: isSilence ? 0.5 : 0.2, min: 0.05, max: 60, step: 0.05, suffix: 's' },
      { key: 'pad', label: t('Keep before/after'), type: 'number', value: isSilence ? 0.1 : 0, min: 0, max: 5, step: 0.05, suffix: 's' },
      { key: 'targeted', label: t('Targeted tracks only'), type: 'checkbox', value: false },
      { key: 'ripple', label: t('Close gaps (Extract)'), type: 'checkbox', value: true },
      { key: 'markers', label: t('Keep marked moments'), type: 'checkbox', value: true }
    ],
    onSubmit: async (v) => {
      const tracks = (isSilence ? seq.audio_tracks : seq.video_tracks).filter((id) => !v.targeted || seq.tracks[id].targeted)
      toast(t('Analysing...'), 'info')
      const r = await call<{ ranges: number[][]; removed_seconds: number }>('analysis.plan_cuts', {
        sequence_id: seq.id, kind, track_ids: tracks, min_duration: Number(v.min), padding_before: Number(v.pad), padding_after: Number(v.pad),
        protect_markers: !!v.markers, apply: true, ripple: !!v.ripple, ...(isSilence ? { silence_db: Number(v.db) } : {})
      })
      if (r) toast(r.ranges.length ? `${t('Removed')} ${r.ranges.length} · ${r.removed_seconds.toFixed(2)}s` : t('Nothing to remove'), 'info')
    }
  })
}

async function sceneEditDetection(): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  const sel = selectedClips(seq).filter((x) => x.track.kind === 'video' && x.clip.media_id).map((x) => x.clip.id)
  if (!sel.length) return toast(t('Select video clips'), 'info')
  toast(t('Analysing...'), 'info')
  const r = await call<{ count: number }>('analysis.plan_scene_cuts', { sequence_id: seq.id, clip_ids: sel, apply: true })
  if (r) toast(`${t('Scene Edit Detection')}: ${r.count}`, 'info')
}

export async function reverseSelection(): Promise<void> {
  const seq = seqOrToast()
  if (!seq) return
  for (const { clip } of selectedClips(seq)) {
    if (!clip.media_id) continue
    await exec('clip.set_speed', { sequence_id: seq.id, clip_id: clip.id, speed: Math.abs(clip.speed), reverse: clip.speed > 0, ripple: false }, t('Reverse Speed'))
  }
}

def('sequence.addCaptionTrack', t('Add Caption Track'), () => void addCaptionTrack())
def('sequence.addCaption', t('Add Caption'), addCaptionAtPlayhead)
def('file.importCaptions', t('Import Captions...'), () => importCaptions())
def('file.exportCaptions', t('Export Captions (SRT)...'), () => exportCaptions())
def('caption.edit', t('Edit Caption'), () => editCaptionText())
def('caption.style', t('Caption Track Style...'), () => captionStyleDialog())
def('caption.upgrade', t('Upgrade Caption to Graphic'), () => upgradeCaptions())
def('clip.addFrameHold', t('Add Frame Hold'), () => frameHold('add'))
def('clip.insertFrameHold', t('Insert Frame Hold Segment'), () => frameHold('insert'))
def('clip.frameHoldOptions', t('Frame Hold Options...'), frameHoldOptions)
def('clip.removeFrameHold', t('Remove Frame Hold'), () => frameHold('clear'))
def('clip.reverse', t('Reverse Speed'), reverseSelection)
def('clip.sceneEditDetection', t('Scene Edit Detection'), sceneEditDetection)
def('sequence.removeSilence', t('Remove Silence...'), () => removeDialog('silence'))
def('sequence.removeBlack', t('Remove Black Frames...'), () => removeDialog('black'))
def('project.scanMedia', t('Scan Media (Offline / Duplicates)...'), scanMedia)
def('project.locateMedia', t('Search for Missing Media...'), () => locateMedia())


