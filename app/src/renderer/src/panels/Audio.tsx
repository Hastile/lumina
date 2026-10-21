// Audio Meters, Audio Track Mixer and Audio Clip Mixer (Premiere's layout).
// Levels come from the Web Audio buses the Program monitor plays through
// (playback/AudioEngine.ts), so the meters show exactly what is heard.

import { useEffect, useRef, useState } from 'react'
import { t } from '@shared/i18n'
import type { Clip, Sequence } from '../engine/document'
import { activeSequence, exec, useStore } from '../state/store'
import { audioEngine } from '../playback/AudioEngine'
import { valueAt } from '../util/motion'

export const dbToGain = (db: number): number => Math.pow(10, db / 20)
const toDb = (g: number): number => (g > 0 ? 20 * Math.log10(g) : -Infinity)

/** Fader / rubber band taper shared by the mixers and the timeline: 0..1 for -inf..+6 dB. */
export const dbToPos = (db: number): number => (db <= -96 ? 0 : Math.min(1, Math.pow(10, (db - 6) / 40)))
export const posToDb = (p: number): number => (p <= 0.0016 ? -96 : Math.max(-96, Math.min(6, 6 + 40 * Math.log10(p))))

export const fmtDb = (db: number): string => (db <= -96 ? '-∞' : `${db > 0 ? '+' : ''}${db.toFixed(1)}`)

// ---------------------------------------------------------------- level ballistics

type Level = { now: number; hold: number; holdAt: number; clip: boolean }
const fresh = (): Level => ({ now: -Infinity, hold: -Infinity, holdAt: 0, clip: false })
const FALL_DB_PER_S = 24 // meter release
const HOLD_MS = 1500

function step(l: Level, peak: number, dt: number, now: number): Level {
  const db = toDb(peak)
  const fall = l.now - FALL_DB_PER_S * dt
  const cur = Math.max(db, fall)
  let { hold, holdAt } = l
  if (cur >= hold || now - holdAt > HOLD_MS) {
    hold = cur
    holdAt = now
  }
  return { now: cur, hold, holdAt, clip: l.clip || peak >= 1 }
}

/** Clear the clip LEDs and the held peaks (the peak readouts start over). */
function clearClip(s: { master: [Level, Level]; tracks: Record<string, [Level, Level]> }) {
  const clear = (l: Level): Level => ({ ...l, clip: false, hold: -Infinity, holdAt: 0 })
  s.master = [clear(s.master[0]), clear(s.master[1])]
  for (const k of Object.keys(s.tracks)) s.tracks[k] = [clear(s.tracks[k][0]), clear(s.tracks[k][1])]
}

/** Live levels of the Mix and every track (L, R), updated on animation frames. */
function useLevels(): { master: [Level, Level]; tracks: Record<string, [Level, Level]>; reset: () => void } {
  const [, force] = useState(0)
  const state = useRef<{ master: [Level, Level]; tracks: Record<string, [Level, Level]> }>({ master: [fresh(), fresh()], tracks: {} })
  useEffect(() => {
    let raf = 0
    let last = performance.now()
    let wasPlaying = useStore.getState().playing !== null
    const tick = (now: number) => {
      const dt = Math.max(0, (now - last) / 1000)
      last = now
      const lv = audioEngine.levels()
      const s = state.current
      // the clip LEDs stay red after playback stops and clear when it starts again
      const playing = useStore.getState().playing !== null
      if (playing && !wasPlaying) clearClip(s)
      wasPlaying = playing
      s.master = [step(s.master[0], lv.master[0], dt, now), step(s.master[1], lv.master[1], dt, now)]
      for (const [id, [l, r]] of Object.entries(lv.tracks)) {
        const prev = s.tracks[id] ?? [fresh(), fresh()]
        s.tracks[id] = [step(prev[0], l, dt, now), step(prev[1], r, dt, now)]
      }
      force((x) => (x + 1) % 1e6)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])
  return { ...state.current, reset: () => clearClip(state.current) }
}

const FLOOR = -60
const meterPos = (db: number) => Math.max(0, Math.min(1, (db - FLOOR) / -FLOOR))

function Bar({ l, onReset }: { l: Level; onReset?: () => void }) {
  return (
    <div className="lm-mbar" onClick={onReset}>
      <div className={`clipled ${l.clip ? 'on' : ''}`} />
      <div className="well">
        <div className="fill" style={{ clipPath: `inset(${(1 - meterPos(l.now)) * 100}% 0 0 0)` }} />
        {Number.isFinite(l.hold) && l.hold > FLOOR && <div className="hold" style={{ bottom: `${meterPos(l.hold) * 100}%` }} />}
      </div>
    </div>
  )
}

// Premiere's meter scale: linear in dB, a label every 3 dB down to -57
const SCALE = Array.from({ length: 20 }, (_, i) => -3 * i)

function Scale() {
  return (
    <div className="lm-mscale">
      {SCALE.map((d) => (
        <span key={d} style={{ bottom: `${meterPos(d) * 100}%` }}>
          {d}
        </span>
      ))}
    </div>
  )
}

const fmtPeak = (db: number): string => (Number.isFinite(db) && db > FLOOR ? db.toFixed(1) : '-∞')

/** A strip's stereo meter: clip LEDs, the two bars against a 3 dB scale, and the held peak in dB
 * (click to reset). */
function StereoMeter({ levels, onReset }: { levels?: [Level, Level]; onReset?: () => void }) {
  const [l, r] = levels ?? [fresh(), fresh()]
  const hold = Math.max(l.hold, r.hold)
  return (
    <div className="lm-smeter" onClick={onReset} title={t('Peak (click to reset)')}>
      <div className="leds">
        <div className={`clipled ${l.clip ? 'on' : ''}`} />
        <div className={`clipled ${r.clip ? 'on' : ''}`} />
        <span />
      </div>
      <div className="wells">
        {[l, r].map((x, i) => (
          <div key={i} className="well">
            <div className="fill" style={{ clipPath: `inset(${(1 - meterPos(x.now)) * 100}% 0 0 0)` }} />
            {Number.isFinite(x.hold) && x.hold > FLOOR && <div className="hold" style={{ bottom: `${meterPos(x.hold) * 100}%` }} />}
          </div>
        ))}
        <Scale />
      </div>
      <div className={`peak ${l.clip || r.clip ? 'over' : ''}`} data-peak={fmtPeak(hold)}>{fmtPeak(hold)}</div>
    </div>
  )
}

// fader marks on the fader's own taper (dbToPos); -∞ at the bottom
const FADER_MARKS = [6, 3, 0, -3, -6, -9, -12, -15, -18, -24, -30, -40, -96]

export function AudioMeters() {
  const lv = useLevels()
  return (
    <div className="lm-panel lm-meters" data-meters="">
      <Scale />
      <Bar l={lv.master[0]} onReset={lv.reset} />
      <Bar l={lv.master[1]} onReset={lv.reset} />
      <div className="lm-mpeak" data-peak={Number.isFinite(lv.master[0].hold) ? Math.max(lv.master[0].hold, lv.master[1].hold).toFixed(1) : ''}>
        {['L', 'R'].map((c) => <span key={c}>{c}</span>)}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- controls

/** Vertical fader in dB: drag (Ctrl = fine), double-click = 0 dB. Live preview, one undo step on release. */
function Fader({ db, onPreview, onCommit, label }: { db: number; onPreview: (db: number) => void; onCommit: (db: number) => void; label: string }) {
  const rail = useRef<HTMLDivElement>(null)
  const [live, setLive] = useState<number | null>(null)
  const [edit, setEdit] = useState<string | null>(null)
  const shown = live ?? db
  const down = (e: React.MouseEvent) => {
    e.preventDefault()
    const h = rail.current!.getBoundingClientRect().height
    const y0 = e.clientY
    const p0 = dbToPos(db)
    let v = db
    const move = (ev: MouseEvent) => {
      const k = ev.ctrlKey ? 0.1 : 1
      v = Math.round(posToDb(Math.max(0, Math.min(1, p0 - ((ev.clientY - y0) / h) * k))) * 10) / 10
      setLive(v)
      onPreview(v)
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      setLive(null)
      if (v !== db) onCommit(v)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }
  return (
    <div className="lm-fader" data-fader={label}>
      <div className="rail-wrap">
        <div className="fscale">
          {FADER_MARKS.map((d) => (
            <span key={d} className={d === 0 ? 'zero-mark' : ''} style={{ bottom: `${dbToPos(d) * 100}%` }}>{d <= -96 ? '-∞' : d > 0 ? `+${d}` : d}</span>
          ))}
        </div>
        <div className="rail" ref={rail} onDoubleClick={() => onCommit(0)}>
          <div className="zero" style={{ bottom: `${dbToPos(0) * 100}%` }} />
          <div className="knob" style={{ bottom: `calc(${dbToPos(shown) * 100}% - 5px)` }} onMouseDown={down} />
        </div>
      </div>
      {edit === null ? (
        <span className="val" onClick={() => setEdit(fmtDb(db))}>{fmtDb(shown)}</span>
      ) : (
        <input
          className="val"
          autoFocus
          value={edit}
          onChange={(e) => setEdit(e.target.value)}
          onBlur={() => setEdit(null)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const n = Number(edit.replace('∞', 'Infinity'))
              if (!Number.isNaN(n)) onCommit(Math.max(-96, Math.min(6, n)))
              setEdit(null)
            } else if (e.key === 'Escape') setEdit(null)
          }}
        />
      )}
    </div>
  )
}

/** Pan knob -100..100: drag up/right = right, double-click = center. */
function PanKnob({ pan, onPreview, onCommit, disabled }: { pan: number; onPreview?: (p: number) => void; onCommit: (p: number) => void; disabled?: boolean }) {
  const [live, setLive] = useState<number | null>(null)
  const v = live ?? pan
  const down = (e: React.MouseEvent) => {
    if (disabled) return
    e.preventDefault()
    const x0 = e.clientX
    const y0 = e.clientY
    let p = pan
    const move = (ev: MouseEvent) => {
      p = Math.round(Math.max(-100, Math.min(100, pan + (ev.clientX - x0) - (ev.clientY - y0))))
      setLive(p)
      onPreview?.(p)
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      setLive(null)
      if (p !== pan) onCommit(p)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }
  const a = (v / 100) * 135
  return (
    <div className={`lm-pan ${disabled ? 'disabled' : ''}`} onMouseDown={down} onDoubleClick={() => !disabled && onCommit(0)} title={t('Pan')}>
      <svg width={30} height={30} viewBox="-15 -15 30 30">
        <circle r={11} className="ring" />
        <path d={`M0,0 L${11 * Math.sin((a * Math.PI) / 180)},${-11 * Math.cos((a * Math.PI) / 180)}`} className="needle" />
      </svg>
      <span>{v === 0 ? '0' : v < 0 ? `L ${-v}` : `R ${v}`}</span>
    </div>
  )
}

function Strip({ name, children, fader, levels, onReset, className }: { name: string; children: React.ReactNode; fader: React.ReactNode; levels?: [Level, Level]; onReset?: () => void; className?: string }) {
  return (
    <div className={`strip ${className ?? ''}`} data-strip={name}>
      {children}
      <div className="body-row">
        {fader}
        <StereoMeter levels={levels} onReset={onReset} />
      </div>
      <div className="name">{name}</div>
    </div>
  )
}

// ---------------------------------------------------------------- mixers

const previewMix = (seq: Sequence, track?: { id: string; db?: number; pan?: number }, master?: number) =>
  audioEngine.mix({
    tracks: seq.audio_tracks.map((id) => {
      const tr = seq.tracks[id]
      const db = track?.id === id && track.db !== undefined ? track.db : tr.volume_db ?? 0
      const pan = track?.id === id && track.pan !== undefined ? track.pan : tr.pan ?? 0
      return { id, gain: dbToGain(db), pan: pan / 100 }
    }),
    master: dbToGain(master ?? seq.master_volume_db ?? 0)
  })

/** Audio Track Mixer: per track pan, mute/solo, fader and meters, then the Mix. */
export function AudioTrackMixerPanel() {
  const seq = useStore((s) => activeSequence(s))
  const lv = useLevels()
  if (!seq) return <div className="lm-panel"><div className="lm-empty">{t('(no sequences)')}</div></div>
  const setTrack = (id: string, v: Record<string, unknown>, label: string) => void exec('track.set', { sequence_id: seq.id, track_id: id, ...v }, label)
  return (
    <div className="lm-panel lm-mixer" data-mixer="track">
      {seq.audio_tracks.map((id, i) => {
        const tr = seq.tracks[id]
        return (
          <Strip key={id} name={`A${i + 1}`} levels={lv.tracks[id]} onReset={lv.reset} fader={<Fader label={`A${i + 1}`} db={tr.volume_db ?? 0} onPreview={(db) => previewMix(seq, { id, db })} onCommit={(db) => setTrack(id, { volume_db: db }, t('Track Volume'))} />}>
            <PanKnob pan={tr.pan ?? 0} onPreview={(p) => previewMix(seq, { id, pan: p })} onCommit={(p) => setTrack(id, { pan: p }, t('Pan'))} />
            <div className="ms">
              <button className={`lm-ms m ${tr.muted ? 'on' : ''}`} onClick={() => setTrack(id, { muted: !tr.muted }, t('Mute Track'))}>M</button>
              <button className={`lm-ms s ${tr.solo ? 'on' : ''}`} onClick={() => setTrack(id, { solo: !tr.solo }, t('Solo Track'))}>S</button>
            </div>
          </Strip>
        )
      })}
      <Strip name={t('Mix')} levels={lv.master} onReset={lv.reset} className="mix" fader={<Fader
          label="Mix"
          db={seq.master_volume_db ?? 0}
          onPreview={(db) => previewMix(seq, undefined, db)}
          onCommit={(db) => void exec('sequence.set_master', { sequence_id: seq.id, volume_db: db }, t('Mix Volume'))}
        />}>
        <div className="lm-pan disabled" />
        <div className="ms" />
      </Strip>
    </div>
  )
}

/** Audio Clip Mixer: one strip per audio track for the clip under the playhead; the fader is that
 * clip's Volume (a keyframe at the playhead when Volume is animated, like Premiere). */
export function AudioClipMixerPanel() {
  const seq = useStore((s) => activeSequence(s))
  const time = useStore((s) => s.programTime)
  const lv = useLevels()
  if (!seq) return <div className="lm-panel"><div className="lm-empty">{t('(no sequences)')}</div></div>
  return (
    <div className="lm-panel lm-mixer" data-mixer="clip">
      {seq.audio_tracks.map((id, i) => {
        const tr = seq.tracks[id]
        const clip: Clip | undefined = Object.values(tr.clips).find((c) => c.start <= time && time < c.end)
        const db = clip ? (valueAt(clip, 'volume_db', time, seq) as number) : 0
        return (
          <Strip key={id} name={`A${i + 1}`} levels={lv.tracks[id]} onReset={lv.reset} fader={clip ? (
              <Fader
                label={`A${i + 1}`}
                db={db}
                onPreview={() => undefined}
                onCommit={(v) => void exec('clip.set_property', { sequence_id: seq.id, clip_id: clip.id, prop: 'volume_db', value: Math.min(15, v), at: time }, t('Volume'))}
              />
            ) : (
              <div className="lm-fader empty" />
            )}>
            <div className="clipname" title={clip?.name}>{clip?.name ?? '—'}</div>
            <div className="ms">
              <button className={`lm-ms m ${tr.muted ? 'on' : ''}`} onClick={() => void exec('track.set', { sequence_id: seq.id, track_id: id, muted: !tr.muted }, t('Mute Track'))}>M</button>
              <button className={`lm-ms s ${tr.solo ? 'on' : ''}`} onClick={() => void exec('track.set', { sequence_id: seq.id, track_id: id, solo: !tr.solo }, t('Solo Track'))}>S</button>
            </div>
          </Strip>
        )
      })}
    </div>
  )
}
