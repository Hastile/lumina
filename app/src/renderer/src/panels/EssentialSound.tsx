// Essential Sound, laid out like Premiere's (Browse / Edit, the clip's type, Preset, one section per
// feature with its switch, Clip Volume and Mute at the bottom). Loudness Auto-Match and Clip Volume work;
// the sections Lumina has no processing for yet are shown, greyed, so the panel reads like Premiere's.
// Below Clip Volume, the level path answers "why is it this loud": every gain the clip goes through.
import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Download, Leaf, Menu, MessageSquare, Music, Sparkles, Trash2, AudioLines, Tags } from 'lucide-react'
import { t } from '@shared/i18n'
import type { Clip, Sequence } from '../engine/document'
import { openContextMenu } from '../layout/Chrome'
import { openDialog } from '../state/dialog'
import { activeSequence, exec, findClip, get, useStore } from '../state/store'
import { valueAt } from '../util/motion'
import { fmtDb } from './Audio'

type AudioTag = 'dialogue' | 'music' | 'sfx' | 'ambience'

const TAGS: [AudioTag, string, React.FC<{ size?: number }>][] = [
  ['dialogue', 'Dialogue', MessageSquare],
  ['music', 'Music', Music],
  ['sfx', 'SFX', Sparkles],
  ['ambience', 'Ambience', Leaf]
]

// Premiere's sections per type; only Loudness has processing in Lumina
const SECTIONS: Record<AudioTag, string[]> = {
  dialogue: ['Enhance Speech', 'Loudness', 'Repair', 'Clarity', 'Creative'],
  music: ['Loudness', 'Duration', 'Ducking'],
  sfx: ['Loudness', 'Creative', 'Pan'],
  ambience: ['Loudness', 'Creative', 'Stereo Width', 'Ducking']
}

// Auto-Match settings (engine audio_analysis.DEFAULT_TARGETS / MATCH_DEFAULTS), kept per user
type MatchSettings = { targets: Record<AudioTag, number>; music_bed_db: number | null; peak_ceiling: number }
const DEFAULTS: MatchSettings = { targets: { dialogue: -23, music: -25, sfx: -21, ambience: -30 }, music_bed_db: 12, peak_ceiling: -1 }
function loadSettings(): MatchSettings {
  try {
    return { ...DEFAULTS, ...(JSON.parse(localStorage.getItem('lumina.autoMatch') || '{}') as Partial<MatchSettings>) }
  } catch {
    return DEFAULTS
  }
}
function saveSettings(s: MatchSettings): void {
  try {
    localStorage.setItem('lumina.autoMatch', JSON.stringify(s))
  } catch {
    /* per-user convenience */
  }
}

// why Auto-Match chose a gain (engine audio_analysis.plan_match)
const NOTES: Record<string, string> = {
  bed: 'music bed under dialogue',
  short: 'too short to gate: loudest moment',
  outlier: 'far from the rest of its source: pulled halfway',
  peak_limited: 'held under the true-peak ceiling',
  boost_capped: 'boost limit reached',
  noisy: 'noisy: the boost raises the noise too',
  kept: 'already matched: unchanged',
  compressed: 're-measured through compressor / limiter',
  silent: 'silent'
}
type MatchResult = Record<string, { lufs: number | null; target?: number; result?: number; gain_db: number; group?: number; notes: string[] }>

/** The selected audio clips, plus the audio linked to selected video (Premiere). */
function selectedAudio(seq: Sequence, selected: string[]): { clip: Clip; track: string }[] {
  const sel = selected.map((id) => findClip(seq, id)).filter(Boolean).map((f) => f!.clip)
  const links = new Set(sel.map((c) => c.link_id).filter(Boolean))
  return seq.audio_tracks.flatMap((tid) => Object.values(seq.tracks[tid].clips).filter((c) => sel.includes(c) || (c.link_id && links.has(c.link_id))).map((clip) => ({ clip, track: tid })))
}

function Switch({ on, onChange, disabled, label }: { on: boolean; onChange?: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button type="button" className={`lm-switch ${on ? 'on' : ''}`} disabled={disabled} aria-pressed={on} data-switch={label} onClick={() => onChange?.(!on)}>
      <span />
    </button>
  )
}

export function EssentialSoundPanel() {
  const seq = useStore((s) => activeSequence(s))
  const selected = useStore((s) => s.selectedClips)
  const time = useStore((s) => s.programTime)
  const [tab, setTab] = useState<'browse' | 'edit'>('edit')
  const [open, setOpen] = useState<Record<string, boolean>>({ Loudness: true, 'Enhance Speech': true })
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<MatchResult | null>(null)
  const [settings, setSettings] = useState<MatchSettings>(loadSettings)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
    } finally {
      setBusy(false)
    }
  }
  // selecting an untagged audio clip Auto-Tags it once; the type buttons are for clips it could not
  // classify (or whose type was cleared by hand)
  const tried = useRef(new Set<string>())
  const [failed, setFailed] = useState<Set<string>>(new Set())
  const pending = seq
    ? selectedAudio(seq, selected).filter((a) => !a.clip.audio_tag && !seq.tracks[a.track].locked && !tried.current.has(a.clip.id)).map((a) => a.clip.id)
    : []
  const pendingKey = pending.join(',')
  useEffect(() => {
    if (!seq || !pending.length) return
    for (const id of pending) tried.current.add(id)
    void run(async () => {
      const r = await exec<{ clips: Record<string, { tag: string | null }> }>('audio.auto_tag', { sequence_id: seq.id, clip_ids: pending, overwrite: false }, t('Auto-Tag'))
      const bad = pending.filter((id) => !r?.clips[id]?.tag)
      if (bad.length) setFailed((f) => new Set([...f, ...bad]))
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingKey])
  if (!seq) return <div className="lm-panel"><div className="lm-empty">{t('(no sequences)')}</div></div>
  const audio = selectedAudio(seq, selected)
  const ids = audio.map((a) => a.clip.id)
  const tags = new Set(audio.map((a) => a.clip.audio_tag ?? null))
  const tag = tags.size === 1 ? ([...tags][0] as AudioTag | null) : null
  const title = !audio.length ? t('No Selection') : audio.length === 1 ? audio[0].clip.name : t('{n} clips selected').replace('{n}', String(audio.length))
  const setTag = (v: AudioTag | null) => void exec('clip.set_audio_tag', { sequence_id: seq.id, clip_ids: ids, tag: v }, v ? t('Audio Type') : t('Clear Audio Type'))
  const autoTag = () => run(() => exec('audio.auto_tag', { sequence_id: seq.id, clip_ids: ids.length ? ids : null, overwrite: ids.length > 0 }, t('Auto-Tag')))
  const autoMatch = () =>
    run(async () => {
      const r = await exec<{ clips: MatchResult }>('audio.auto_match', { sequence_id: seq.id, clip_ids: ids, targets: settings.targets, music_bed_db: settings.music_bed_db, peak_ceiling: settings.peak_ceiling }, t('Auto-Match'))
      if (r) setResult(r.clips)
    })
  const reset = () => {
    setResult(null)
    void exec('audio.reset_match', { sequence_id: seq.id, clip_ids: ids }, t('Reset'))
  }
  // matched: every selected clip still carries the gain Auto-Match gave it
  const matched = audio.length > 0 && audio.every((a) => a.clip.auto_match && a.clip.auto_match.gain_db === a.clip.gain_db)
  const changed = audio.some((a) => a.clip.auto_match && a.clip.auto_match.gain_db !== a.clip.gain_db)
  const first = audio[0]?.clip
  const status = matched
    ? t('Matched to {v} LUFS').replace('{v}', first!.auto_match!.target.toFixed(2))
    : changed ? t('Gain changed after matching') : t('Not matched')
  const why = matched && audio.length === 1 && result?.[first!.id]
    ? [...((result[first!.id].group ?? 1) > 1 ? [t('same gain as {n} cuts of this source').replace('{n}', String(result[first!.id].group))] : []), ...result[first!.id].notes.map((n) => t(NOTES[n] ?? n))].join(' · ')
    : ''

  const menu = (e: React.MouseEvent) =>
    openContextMenu(e, [
      {
        label: t('Auto-Match Settings...'),
        run: () =>
          openDialog({
            title: t('Auto-Match Settings'),
            okLabel: t('OK'),
            fields: [
              ...TAGS.map(([k, label]) => ({ key: k, label: `${t(label)} (LUFS)`, type: 'number' as const, value: settings.targets[k], min: -40, max: -5, step: 0.5 })),
              { key: 'bed_on', label: t('Music under dialogue'), type: 'checkbox' as const, value: settings.music_bed_db !== null },
              { key: 'bed', label: `${t('Music under dialogue')} (LU ${t('lower')})`, type: 'number' as const, value: settings.music_bed_db ?? 12, min: 0, max: 40, step: 1 },
              { key: 'ceiling', label: `${t('True peak ceiling')} (dBTP)`, type: 'number' as const, value: settings.peak_ceiling, min: -20, max: 0, step: 0.5 }
            ],
            onSubmit: (v) => {
              const next: MatchSettings = {
                targets: Object.fromEntries(TAGS.map(([k]) => [k, Number(v[k])])) as Record<AudioTag, number>,
                music_bed_db: v.bed_on ? Number(v.bed) : null,
                peak_ceiling: Number(v.ceiling)
              }
              setSettings(next)
              saveSettings(next)
            }
          })
      },
      { label: t('Restore Defaults'), run: () => { setSettings(DEFAULTS); saveSettings(DEFAULTS) } }
    ])

  return (
    <div className="lm-panel lm-es2" data-essential-sound="">
      <div className="lm-es2-head">
        <div className="tabs">
          <span className={tab === 'browse' ? 'active' : ''} onClick={() => setTab('browse')}>{t('Browse')}</span>
          <span className={tab === 'edit' ? 'active' : ''} onClick={() => setTab('edit')}>{t('Edit')}</span>
          <button className="lm-iconbtn menu" title={t('Auto-Match Settings...')} onClick={menu}>
            <Menu size={13} />
          </button>
        </div>
        <div className="title" title={title}>{title}</div>
      </div>
      {tab === 'browse' ? (
        <div className="lm-es2-browse">
          {TAGS.map(([k, label, Icon]) => (
            <div key={k} className="preset" onDoubleClick={() => ids.length && setTag(k)} title={t('Double-click to apply to the selection')}>
              <Icon size={13} /> {t(label)} <span className="dim">{t('(Default)')}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="lm-es2-body">
          {tag && (
            <div className="typerow">
              {(() => {
                const [, label, Icon] = TAGS.find((x) => x[0] === tag)!
                return (
                  <span className="type">
                    <Icon size={13} /> {t(label)}
                  </span>
                )
              })()}
              <button className="lm-btn" onClick={() => setTag(null)} data-action="clear-type">{t('Clear Audio Type')}</button>
            </div>
          )}
          <div className="presetlabel">{t('Preset:')}</div>
          <div className="presetrow">
            <select disabled={!tag} value="default" onChange={() => undefined}>
              <option value="default">{tag ? t('(Default)') : ''}</option>
            </select>
            <button className="lm-iconbtn" disabled title={t('Save Preset')}><Download size={13} /></button>
            <button className="lm-iconbtn" disabled title={t('Delete Preset')}><Trash2 size={13} /></button>
          </div>
          <div className="rule" />
          {!tag ? (
            <div className="lm-es2-empty">
              <AudioLines size={40} />
              <div className="big">{t('Tag clips to use the Essential Sound panel')}</div>
              {audio.some((a) => !a.clip.audio_tag && failed.has(a.clip.id)) && (
                <div className="hint failed" data-autotag-failed="">{t('Auto-Tag could not tell the type of this clip: choose it below.')}</div>
              )}
              <button className="lm-btn" disabled={busy} onClick={() => void autoTag()} data-action="auto-tag">
                <Tags size={13} /> {busy ? t('Analyzing...') : t('Auto-Tag')}
              </button>
              <div className="hint">{t('You can also assign one of these audio types to each clip directly.')}</div>
              <div className="chips">
                {TAGS.map(([k, label, Icon]) => (
                  <button key={k} className="lm-chip" data-tag={k} disabled={!audio.length} onClick={() => setTag(k)}>
                    <Icon size={12} /> {t(label)}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="sections">
              {SECTIONS[tag].map((name) => {
                const live = name === 'Loudness'
                const isOpen = !!open[name]
                return (
                  <div key={name} className={`sec ${live ? '' : 'off'}`} data-section={name}>
                    <div className="sechead" onClick={() => setOpen({ ...open, [name]: !isOpen })}>
                      {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      <span className="name">{t(name)}</span>
                      <span onClick={(e) => e.stopPropagation()}>
                        {live ? (
                          <Switch on={matched} disabled={busy} label="loudness" onChange={(v) => (v ? void autoMatch() : reset())} />
                        ) : (
                          <Switch on={false} disabled />
                        )}
                      </span>
                    </div>
                    {isOpen && live && (
                      <div className="secbody loud">
                        <div className="btns">
                          <button className="lm-btn" disabled={busy} onClick={() => void autoMatch()} data-action="auto-match">
                            {busy ? t('Analyzing...') : t('Auto-Match')}
                          </button>
                          <button className="lm-btn" disabled={busy || !audio.some((a) => a.clip.auto_match)} onClick={reset} data-action="reset-match">
                            {t('Reset')}
                          </button>
                        </div>
                        <div className="status" data-match-status={matched ? 'matched' : 'none'}>{status}</div>
                        {why && <div className="why" data-match-why="">{why}</div>}
                      </div>
                    )}
                    {isOpen && !live && name === 'Enhance Speech' && (
                      <div className="secbody">
                        <button className="lm-btn" disabled>{t('Enhance')}</button>
                        <div className="sliderrow"><span>{t('Mix Amount:')}</span><span className="dim">7.0</span></div>
                        <input type="range" disabled min={0} max={10} step={0.1} defaultValue={7} />
                        <div className="ends"><span>{t('Less')}</span><span>{t('More')}</span></div>
                        <div className="soon">{t('Not available in Lumina yet')}</div>
                      </div>
                    )}
                    {isOpen && !live && name !== 'Enhance Speech' && <div className="secbody"><div className="soon">{t('Not available in Lumina yet')}</div></div>}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
      {audio.length > 0 && <ClipVolume seq={seq} audio={audio} time={time} />}
    </div>
  )
}

/** Clip Volume and Mute (the clip's Volume - the same value as Effect Controls' Level, the Audio Clip
 * Mixer and the rubber band) and the level path: every gain between the file and the speakers. */
function ClipVolume({ seq, audio, time }: { seq: Sequence; audio: { clip: Clip; track: string }[]; time: number }) {
  const [live, setLive] = useState<number | null>(null)
  const keyTimer = useRef<number | undefined>(undefined)
  const clips = audio.map((a) => a.clip)
  const first = clips[0]
  const keyed = clips.some((c) => (c.keyframes.volume_db?.length ?? 0) > 0)
  const vol = live ?? (keyed ? (valueAt(first, 'volume_db', Math.max(first.start, Math.min(first.end - 1, time)), seq) as number) : first.volume_db)
  const on = keyed || clips.some((c) => c.volume_db !== 0) || live !== null
  const [enabled, setEnabled] = useState(on)
  const active = enabled || on
  const muted = clips.every((c) => c.muted)
  const commit = (db: number) =>
    exec('clip.set', { sequence_id: seq.id, clip_ids: clips.map((c) => c.id), volume_db: Math.max(-96, Math.min(15, Math.round(db * 10) / 10)) }, t('Clip Volume'))
  // keep showing the dragged value until the engine has it (no jump back in between)
  const release = () => {
    if (live === null) return
    const v = live
    void commit(v).then(() => setLive((cur) => (cur === v ? null : cur)))
  }
  // the level path of the first clip at the playhead
  const track = seq.tracks[audio[0].track]
  const steps: [string, number][] = [
    [t('Audio Gain'), first.gain_db ?? 0],
    [t('Clip Volume'), vol],
    [`${t('Track')} ${track.name.replace(/^Audio /, 'A')}`, track.volume_db ?? 0],
    [t('Mix'), seq.master_volume_db ?? 0]
  ]
  const total = steps.reduce((s, [, v]) => s + v, 0)
  return (
    <div className="lm-es2-foot">
      <div className="row">
        <Switch
          on={active}
          disabled={keyed}
          label="clip-volume"
          onChange={(v) => {
            setEnabled(v)
            if (!v && first.volume_db !== 0) void commit(0)
          }}
        />
        <span className="name">{t('Clip Volume')}</span>
        <span className="val" data-clip-volume={vol.toFixed(1)}>{fmtDb(vol)} dB</span>
      </div>
      <input
        type="range"
        className="vol"
        min={-15}
        max={15}
        step={0.1}
        disabled={!active || keyed}
        value={Math.max(-15, Math.min(15, vol))}
        title={keyed ? t('This clip has volume keyframes: adjust them on the rubber band or in Effect Controls') : undefined}
        onChange={(e) => setLive(Number(e.target.value))}
        onMouseUp={release}
        onKeyUp={() => {
          // a run of arrow keys is one change: commit half a second after the last one
          clearTimeout(keyTimer.current)
          keyTimer.current = window.setTimeout(release, 500)
        }}
        onBlur={release}
        onDoubleClick={() => void commit(0)}
        data-action="clip-volume"
      />
      <div className="row">
        <Switch on={muted} label="mute" onChange={(v) => void exec('clip.set', { sequence_id: seq.id, clip_ids: clips.map((c) => c.id), muted: v }, t('Mute'))} />
        <span className="name">{t('Mute')}</span>
      </div>
      <div className="path" data-level-path="" title={t('Everything that sets the loudness of this clip, in the order it is applied (at the playhead)')}>
        {steps.map(([n, v], i) => (
          <span key={n} className={v !== 0 ? 'nz' : ''}>
            {i > 0 && ' + '}
            {n} {fmtDb(v)}
          </span>
        ))}
        <span className="total"> = {fmtDb(total)} dB</span>
        {muted && <span className="muted"> · {t('Muted')}</span>}
      </div>
    </div>
  )
}

/** Clips the Essential Sound panel acts on (tests). */
export const essentialSoundTargets = () => {
  const seq = activeSequence()
  return seq ? selectedAudio(seq, get().selectedClips).map((a) => a.clip.id) : []
}
