// Clip > Speed/Duration (Ctrl+R) that says what it will do before it does it: Speed and Duration are
// linked (edit either), and the dialog spells out the ripple - which clips move and by how much,
// the gap left behind, or the collision that would stop it - and applies to every selected clip.
import { useEffect, useState } from 'react'
import { Link2, Link2Off } from 'lucide-react'
import { create } from 'zustand'
import { t } from '@shared/i18n'
import type { Clip, Sequence } from '../engine/document'
import { engine } from '../engine/client'
import { activeSequence, findClip, get, toast } from '../state/store'
import { parseTimecode, ticksPerFrame, timecode, TICKS_PER_SECOND } from '../util/time'

const useSpeed = create<{ ids: string[] | null }>(() => ({ ids: null }))

export function openSpeedDialog(ids: string[]): void {
  useSpeed.setState({ ids })
}

type Plan = { clip: Clip; track: string; newLen: number; delta: number; blocked: Clip | null; followers: number }

/** What clip.set_speed will do to each clip (its engine logic, for the preview). */
function plan(seq: Sequence, clips: { clip: Clip; track: string }[], speed: number, ripple: boolean): Plan[] {
  const tpf = ticksPerFrame(seq.settings)
  return clips.map(({ clip, track }) => {
    const src = (clip.end - clip.start) * clip.speed
    const newLen = Math.max(tpf, Math.round(src / speed / tpf) * tpf)
    const delta = newLen - (clip.end - clip.start)
    const others = Object.values(seq.tracks[track].clips).filter((c) => c.id !== clip.id && c.start >= clip.end)
    const blocked = !ripple && delta > 0 ? others.find((c) => c.start < clip.start + newLen) ?? null : null
    // with ripple, everything after the clip on its track and on sync-locked tracks follows
    const followers = ripple && delta !== 0
      ? Object.values(seq.tracks).filter((tr) => tr.id === track || (tr.sync_lock && !tr.locked)).reduce((n, tr) => n + Object.values(tr.clips).filter((c) => c.start >= clip.end && c.id !== clip.id && !clips.some((x) => x.clip.id === c.id)).length, 0)
      : 0
    return { clip, track, newLen, delta, blocked, followers }
  })
}

export function SpeedDialog() {
  const ids = useSpeed((s) => s.ids)
  const seq = activeSequence()
  const [speed, setSpeed] = useState(100)
  const [dur, setDur] = useState('')
  const [linked, setLinked] = useState(true)
  const [ripple, setRipple] = useState(true)
  const clips = (seq && ids ? ids.map((id) => findClip(seq, id)).filter(Boolean).map((f) => ({ clip: f!.clip, track: f!.track.id })) : [])
    // linked partners follow their clip in the engine: one entry per link group
    .filter((x, i, all) => !x.clip.link_id || all.findIndex((y) => y.clip.link_id === x.clip.link_id) === i)
  const first = clips[0]?.clip
  useEffect(() => {
    if (!first || !seq) return
    setSpeed(Math.round(first.speed * 10000) / 100)
    setDur(timecode(first.end - first.start, seq.settings))
    setRipple(true)
    setLinked(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids])
  if (!ids || !seq || !first) return null
  const close = () => useSpeed.setState({ ids: null })
  const st = seq.settings
  const srcLen = (first.end - first.start) * first.speed
  const onSpeed = (v: number) => {
    setSpeed(v)
    if (linked && v > 0) setDur(timecode(Math.round(srcLen / (v / 100) / ticksPerFrame(st)) * ticksPerFrame(st), st))
  }
  const onDur = (text: string) => {
    setDur(text)
    const d = parseTimecode(text, st)
    if (linked && d && d > 0) setSpeed(Math.round((srcLen / d) * 10000) / 100)
  }
  const s = speed / 100
  const p = s > 0 ? plan(seq, clips, s, ripple) : []
  const blocked = p.find((x) => x.blocked)
  const secs = (ticks: number) => `${(ticks / TICKS_PER_SECOND).toFixed(2)}${t('s')}`
  const moved = p.reduce((n, x) => n + x.followers, 0)
  const longest = p.reduce((m, x) => (Math.abs(x.delta) > Math.abs(m) ? x.delta : m), 0)
  let note: string
  if (s <= 0 || s > 100) note = t('Speed must be between 1% and 10000%')
  else if (blocked) note = `${t('Not possible without ripple')}: ${blocked.clip.name} ${t('would run into')} ${blocked.blocked!.name}. ${t('Turn on Ripple Edit or choose a faster speed.')}`
  else if (!longest) note = t('The duration does not change.')
  else if (ripple) note = `${t('Duration')} ${longest > 0 ? '+' : ''}${secs(longest)}; ${moved ? t('{n} later clip(s) move by the same amount (sync-locked tracks too)').replace('{n}', String(moved)) : t('nothing after it moves')}.`
  else note = longest < 0 ? `${t('A gap of')} ${secs(-longest)} ${t('is left after the clip; nothing else moves.')}` : `${t('The clip grows into empty space; nothing else moves.')}`
  const ok = s > 0 && s <= 100 && !blocked
  const apply = async () => {
    close()
    await engine.call('transaction.begin', { label: t('Speed/Duration') })
    try {
      for (const { clip } of clips) await engine.exec('clip.set_speed', { sequence_id: seq.id, clip_id: clip.id, speed: s, ripple })
      await engine.call('transaction.commit')
    } catch (err) {
      await engine.call('transaction.rollback').catch(() => undefined)
      toast(String(err instanceof Error ? err.message : err))
    }
  }
  return (
    <div className="lm-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <form
        className="lm-dialog lm-speed"
        data-speed-dialog=""
        onSubmit={(e) => {
          e.preventDefault()
          if (ok) void apply()
        }}
        onKeyDown={(e) => e.key === 'Escape' && close()}
      >
        <h3>
          {t('Clip Speed / Duration')}
          {clips.length > 1 && <span className="dim"> · {t('{n} clips').replace('{n}', String(clips.length))}</span>}
        </h3>
        <div className="body">
          <label>{t('Speed')}</label>
          <span className="pair">
            <input type="number" min={1} max={10000} step={1} value={speed} onChange={(e) => onSpeed(Number(e.target.value))} data-field="speed" autoFocus />
            %
            <button type="button" className={`lm-iconbtn ${linked ? 'on' : ''}`} title={t('Link speed and duration')} onClick={() => setLinked(!linked)}>
              {linked ? <Link2 size={13} /> : <Link2Off size={13} />}
            </button>
          </span>
          <label>{t('Duration')}</label>
          <input className="lm-tc" value={dur} onChange={(e) => onDur(e.target.value)} data-field="duration" />
          <label>{t('Ripple Edit, Shifting Trailing Clips')}</label>
          <input type="checkbox" checked={ripple} onChange={(e) => setRipple(e.target.checked)} data-field="ripple" />
          <div className={`note ${blocked ? 'bad' : ''}`} data-speed-note="">{note}</div>
        </div>
        <div className="actions">
          <button type="button" className="lm-btn" onClick={close}>
            {t('Cancel')}
          </button>
          <button type="submit" className="lm-btn primary" disabled={!ok}>
            {t('OK')}
          </button>
        </div>
      </form>
    </div>
  )
}

/** Clip > Speed/Duration on the current selection. */
export function speedDialogForSelection(): void {
  const seq = activeSequence()
  if (!seq) return
  const ids = get().selectedClips.filter((id) => findClip(seq, id))
  if (!ids.length) return toast(t('Select a clip first'), 'info')
  openSpeedDialog(ids)
}
