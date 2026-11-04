import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, RotateCcw, Trash2 } from 'lucide-react'
import { t } from '@shared/i18n'
import { exec, get, set } from '../state/store'
import { ticksPerFrame, timecode } from '../util/time'
import { clipTime, current, KeyframeRow, KfRow, laneSpan, NumberScrub, setProp, useSelectedClip, type ClipCtx } from './PropertyControls'
import { EFFECTS, effectMedia, effectParam } from '../util/effects'
import type { Clip, Effect } from '../engine/document'
import { timingClip } from '../util/tracks'
import { findClip, useStore } from '../state/store'
import { CaptionStyleSection, CaptionTransformSection, TrackEffectsSection, TrackPanel, useSelectedTrack } from './TrackPanel'
import type { Kf } from '../util/motion'
import { EFFECT_MIME } from './EffectsPanel'
import { GraphicLayers } from './GraphicLayers'
import { marqueeMouseDown } from './KeyframeSelect'
import { useSelectedClips } from './PropertyControls'
import { MultiClipProperties } from './MultiClipProperties'

export function Group({ title, children, onReset, enabled, onToggleEnabled, onDelete, fxId }: {
  title: string; children: React.ReactNode; onReset?: () => void; enabled?: boolean; onToggleEnabled?: () => void; onDelete?: () => void; fxId?: string
}) {
  const [open, setOpen] = useState(true)
  return (
    <div className={`lm-ec-group ${enabled === false ? 'bypassed' : ''}`} data-effect={fxId}>
      <div className="lm-ec-head">
        <span className="toggle" onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
        <span
          className={`fxbadge ${onToggleEnabled ? 'clickable' : ''} ${enabled === false ? 'off' : ''}`}
          title={onToggleEnabled ? t('Toggle effect on/off') : undefined}
          onClick={onToggleEnabled}
        >
          fx
        </span>
        <span className="toggle" onClick={() => setOpen(!open)}>
          <span>{title}</span>
        </span>
        <span style={{ flex: 1 }} />
        {onDelete && (
          <button className="lm-iconbtn" title={t('Delete Effect')} onClick={onDelete}>
            <Trash2 size={12} />
          </button>
        )}
        {onReset && (
          <button className="lm-iconbtn" title={t('Reset')} onClick={onReset}>
            <RotateCcw size={12} />
          </button>
        )}
      </div>
      {open && children}
    </div>
  )
}

export function Plain({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className="lm-ec-prop">
      <span />
      <span className="pname">{name}</span>
      <span className="pval">{children}</span>
      <span />
      <span />
    </div>
  )
}

export function MotionControls({ c }: { c: ClipCtx }) {
  const pos = current(c, 'position') as number[]
  const anchor = current(c, 'anchor') as number[]
  const uniform = c.clip.motion.uniform
  return (
    <>
      <KeyframeRow c={c} prop="position">
        <NumberScrub value={pos[0]} onChange={(v) => setProp(c, 'position', [v, pos[1]])} />
        <NumberScrub value={pos[1]} onChange={(v) => setProp(c, 'position', [pos[0], v])} />
      </KeyframeRow>
      <KeyframeRow c={c} prop="scale">
        <NumberScrub value={current(c, 'scale') as number} min={0} max={10000} onChange={(v) => setProp(c, 'scale', v)} />
      </KeyframeRow>
      <KeyframeRow c={c} prop="scale_width">
        <NumberScrub value={current(c, 'scale_width') as number} min={0} max={10000} disabled={uniform} onChange={(v) => setProp(c, 'scale_width', v)} />
      </KeyframeRow>
      <Plain name="">
        <label className="check">
          <input
            type="checkbox"
            checked={uniform}
            onChange={(e) => void exec('clip.set_uniform_scale', { sequence_id: c.seq.id, clip_id: c.clip.id, uniform: e.target.checked }, t('Uniform Scale'))}
          />
          {t('Uniform Scale')}
        </label>
      </Plain>
      <KeyframeRow c={c} prop="rotation">
        <NumberScrub value={current(c, 'rotation') as number} suffix="°" onChange={(v) => setProp(c, 'rotation', v)} />
      </KeyframeRow>
      <KeyframeRow c={c} prop="anchor">
        <NumberScrub value={anchor[0]} onChange={(v) => setProp(c, 'anchor', [v, anchor[1]])} />
        <NumberScrub value={anchor[1]} onChange={(v) => setProp(c, 'anchor', [anchor[0], v])} />
      </KeyframeRow>
      {(['crop_left', 'crop_top', 'crop_right', 'crop_bottom'] as const).map((p) => (
        <KeyframeRow key={p} c={c} prop={p}>
          <NumberScrub value={current(c, p) as number} min={0} max={100} suffix=" %" onChange={(v) => setProp(c, p, v)} />
        </KeyframeRow>
      ))}
    </>
  )
}

export function EffectControlsPanel() {
  const selected = useSelectedClips()
  const c = useSelectedClip()
  const tr = useSelectedTrack()
  // Ctrl/Alt+wheel zooms the keyframe lane around the pointer, Shift+wheel scrolls it. A native,
  // non-passive listener: React's onWheel cannot preventDefault the panel's own scroll.
  const wheel = useRef<(e: WheelEvent) => void>(() => {})
  wheel.current = (e) => {
    const lane = (e.target as HTMLElement).closest('.lane, .lm-kcell') as HTMLElement | null
    if (!c || !lane || !(e.ctrlKey || e.altKey || e.shiftKey)) return
    e.preventDefault()
    const { clip, seq } = c
    const [a, b] = laneSpan(c, get().ecView)
    const r = lane.getBoundingClientRect()
    const span = b - a
    if (e.shiftKey && !e.ctrlKey && !e.altKey) {
      const d = (Math.sign(e.deltaY || e.deltaX) * span) / 8
      set({ ecView: { clipId: clip.id, start: a + d, end: b + d } })
      return
    }
    // between 2 frames and 4x the clip
    const f = (e.clientX - r.left) / Math.max(1, r.width)
    const at = a + f * span
    const next = Math.min(4 * (clip.end - clip.start), Math.max(2 * ticksPerFrame(seq.settings), span * (e.deltaY > 0 ? 1.25 : 0.8)))
    set({ ecView: { clipId: clip.id, start: at - f * next, end: at + (1 - f) * next } })
  }
  const [box, setBox] = useState<[number, number, number, number] | null>(null)
  const panel = useCallback((el: HTMLDivElement | null) => {
    if (!el) return
    el.addEventListener('wheel', (e) => wheel.current(e), { passive: false })
  }, [])
  if (selected.length > 1) return <MultiClipProperties key={selected.map((c) => c.clip.id).join(':')} clips={selected} />
  if (!c && tr) {
    return (
      <div className="lm-panel lm-ec" ref={panel}>
        <div className="lm-ec-title">
          <span>{tr.track.caption_style ? t('Caption Track') : t('Track')} · {tr.track.name}</span>
          <span className="dim">{tr.seq.name}</span>
        </div>
        <div className="lm-ec-scroll">
          <TrackPanel seq={tr.seq} track={tr.track} />
        </div>
      </div>
    )
  }
  if (!c) return <div className="lm-panel"><div className="lm-empty">{t('(no clip selected)')}</div></div>
  const { clip, seq } = c
  const reset = () => void exec('clip.reset_motion', { sequence_id: seq.id, clip_ids: [clip.id] }, t('Reset'))
  return (
    <div
      className="lm-panel lm-ec"
      ref={panel}
      onDoubleClick={(e) => {
        if ((e.target as HTMLElement).closest('.lane, .lm-kcell')) set({ ecView: null })
      }}
      onMouseDown={(e) => {
        // empty keyframe lane: rubber-band selection (keyframes handle their own mouse down)
        if ((e.target as HTMLElement).closest('.lane') && !(e.target as HTMLElement).closest('[data-kf]')) marqueeMouseDown(e, clip.id, setBox)
      }}
      onDragOver={(e) => e.dataTransfer.types.includes(EFFECT_MIME) && e.preventDefault()}
      onDrop={(e) => {
        const raw = e.dataTransfer.getData(EFFECT_MIME)
        if (!raw) return
        const item = JSON.parse(raw) as { kind: string; type: string }
        if (item.kind === 'effect' && effectMedia(item.type as Effect['type']) === c.kind) void exec('effect.add', { sequence_id: seq.id, clip_ids: [clip.id], type: item.type }, t('Apply Effect'))
        if (item.kind === 'preset' && c.kind === 'video') void exec('effect.apply_preset', { sequence_id: seq.id, clip_ids: [clip.id], preset_id: item.type }, t('Apply Preset'))
      }}
    >
      <div className="lm-ec-title">
        <span>{c.media ? `${t('Source')} · ${c.media.name}` : t('Graphic Parameters')}</span>
        <span className="dim">{seq.name} · {clip.name}</span>
      </div>
      <div className="lm-ec-scroll">
        {c.kind === 'video' ? (
          <>
            <div className="lm-ec-section">{t('Video')}</div>
            {clip.graphic && (
              <Group title={t('Graphic Parameters')}>
                <GraphicLayers c={c} compact />
              </Group>
            )}
            {clip.caption && <CaptionCueSections c={c} />}
            {!clip.caption && (<>
            <Group title={t('Motion')} onReset={reset}>
              <MotionControls c={c} />
            </Group>
            <Group title={t('Opacity')}>
              <KeyframeRow c={c} prop="opacity">
                <NumberScrub value={current(c, 'opacity') as number} min={0} max={100} suffix=" %" onChange={(v) => setProp(c, 'opacity', v)} />
              </KeyframeRow>
              <Plain name={t('Blend Mode')}>
                <span className="dim">{t('Normal')}</span>
              </Plain>
            </Group>
            </>)}
            {(clip.effects ?? []).map((fx) => (
              <EffectGroup key={fx.id} c={c} fx={fx} />
            ))}
            <TrackEffects c={c} />
            {!clip.caption && (
            <Group title={t('Time Remapping')}>
              <Plain name={t('Speed')}>
                <NumberScrub value={Math.abs(clip.speed) * 100} min={1} max={10000} digits={2} suffix={clip.speed < 0 ? ' % ◂' : ' %'} onChange={(v) => void exec('clip.set_speed', { sequence_id: seq.id, clip_id: clip.id, speed: v / 100 }, t('Speed'))} />
              </Plain>
              <Plain name="">
                <label className="check">
                  <input type="checkbox" checked={clip.speed < 0} disabled={!clip.media_id} onChange={(e) => void exec('clip.set_speed', { sequence_id: seq.id, clip_id: clip.id, speed: Math.abs(clip.speed), reverse: e.target.checked }, t('Reverse Speed'))} />
                  {t('Reverse Speed')}
                </label>
              </Plain>
            </Group>
            )}
          </>
        ) : (
          <>
            <div className="lm-ec-section">{t('Audio')}</div>
            <Group title={t('Volume')}>
              <Plain name={t('Bypass')}>
                <input type="checkbox" checked={!clip.enabled} onChange={(e) => void exec('clip.set', { sequence_id: seq.id, clip_ids: [clip.id], enabled: !e.target.checked }, t('Bypass'))} />
              </Plain>
              <KeyframeRow c={c} prop="volume_db">
                <NumberScrub value={current(c, 'volume_db') as number} min={-96} max={15} step={0.1} suffix=" dB" onChange={(v) => setProp(c, 'volume_db', v)} />
              </KeyframeRow>
            </Group>
            {(clip.effects ?? []).map((fx) => (
              <EffectGroup key={fx.id} c={c} fx={fx} />
            ))}
            <TrackEffects c={c} />
          </>
        )}
      </div>
      {box && <div className="lm-kmarquee" style={{ left: box[0], top: box[1], width: box[2], height: box[3] }} />}
      <div className="lm-ec-foot">
        <span className="lm-tc dim">{timecode(clipTime(c), seq.settings)}</span>
      </div>
    </div>
  )
}

const DIMENSIONS = ['Horizontal and Vertical', 'Horizontal', 'Vertical']

/** The track effects of the selected clip's track: edited like the clip's own effects, their keyframes
 * shown relative to this clip (they apply to every clip of the track, each from its own In/Out). */
function TrackEffects({ c }: { c: ClipCtx }) {
  const track = useStore((s) => (s.doc ? findClip(c.seq, c.clip.id)?.track ?? null : null))
  const fxs = track?.effects ?? []
  if (track && (fxs.length || track.caption_style)) return <TrackEffectsSection seq={c.seq} track={track} refClip={c.clip} time={c.time} />
  if (!track || !fxs.length) return null
  return (
    <>
      <div className="lm-ec-section" data-track-effects={track.id} title={t('Applies to every clip on {track}; keyframes shown on this clip').replace('{track}', track.name)}>
        {t('Track Effects')} · {track.name}
      </div>
      {fxs.map((fx) => {
        // a clip whose source time is the effect's keyframe time, over this clip's span
        const tm = timingClip(fx, c.clip)
        const clip: Clip = { ...c.clip, source_in: tm.source_in - (tm.start - c.clip.start) * tm.speed, speed: tm.speed, effects: fxs }
        return <EffectGroup key={fx.id} c={{ ...c, clip, media: null }} fx={fx} trackId={track.id} />
      })}
    </>
  )
}

const ANCHORS: { value: NonNullable<Effect['anchor']>; label: string }[] = [
  { value: 'in', label: "From each clip's In" },
  { value: 'out', label: "From each clip's Out" },
  { value: 'stretch', label: 'Stretched over each clip' }
]

/** A selected caption cue: its text, then its track's style and transform (a cue has none of its own). */
function CaptionCueSections({ c }: { c: ClipCtx }) {
  const track = findClip(c.seq, c.clip.id)?.track
  return (
    <>
      <CaptionGroup key={`${c.seq.id}:${c.clip.id}`} c={c} />
      {track?.caption_style && (
        <>
          <div className="lm-ec-hint">{t('Caption cues share their track\'s style and transform: changes here apply to every caption on {track}.').replace('{track}', track.name)}</div>
          <CaptionStyleSection seq={c.seq} track={track} />
          <CaptionTransformSection seq={c.seq} track={track} />
        </>
      )}
    </>
  )
}

function CaptionGroup({ c }: { c: ClipCtx }) {
  const cap = c.clip.caption!
  const [text, setText] = useState<string | null>(null)
  const pending = useRef<string | null>(null)
  const commit = () => {
    const value = pending.current
    pending.current = null
    if (value !== null) void exec('caption.set', { sequence_id: c.seq.id, clip_id: c.clip.id, text: value }, t('Edit Caption'))
  }
  useEffect(() => {
    window.addEventListener('blur', commit)
    return () => {
      window.removeEventListener('blur', commit)
      commit()
    }
  }, [])
  return (
    <Group title={t('Caption')}>
      <div className="lm-ec-prop">
        <span />
        <span className="pname">{t('Text')}</span>
        <span className="pval">
          <textarea
            className="lm-caption-text"
            rows={2}
            value={text ?? cap.text}
            onChange={(e) => {
              pending.current = e.target.value
              setText(e.target.value)
            }}
            onBlur={() => {
              commit()
              setText(null)
            }}
          />
        </span>
        <span />
        <span />
      </div>
    </Group>
  )
}

export function EffectGroup({ c, fx, trackId }: { c: ClipCtx; fx: Effect; trackId?: string }) {
  const def = EFFECTS[fx.type]
  const at = clipTime(c)
  const [w, h] = c.media?.video ? [c.media.video.width, c.media.video.height] : [c.seq.settings.width, c.seq.settings.height]
  const base = { sequence_id: c.seq.id, clip_id: c.clip.id, effect_id: fx.id, ...(trackId ? { track_id: trackId } : {}) }
  const val = (n: string) => effectParam(fx, n, c.clip, at, w, h)
  const set = (param: string, value: number | boolean | number[], label: string) =>
    void exec('effect.set_param', { ...base, param, value, at }, label)
  const uniform = fx.type === 'transform' && !!val('uniform')
  return (
    <Group
      title={t(def.label)}
      fxId={fx.id}
      enabled={fx.enabled}
      onToggleEnabled={() => void exec('effect.set', { ...base, enabled: !fx.enabled }, t('Toggle effect on/off'))}
      onDelete={() => void exec('effect.remove', base, t('Delete Effect'))}
    >
      {trackId && (
        <div className="lm-ec-prop">
          <span />
          <span className="pname">{t('Anchor')}</span>
          <span className="pval">
            <select value={fx.anchor ?? 'in'} onChange={(e) => void exec('effect.set', { ...base, anchor: e.target.value }, t('Anchor'))} data-fx-anchor="">
              {ANCHORS.map((a) => (
                <option key={a.value} value={a.value}>
                  {t(a.label)}
                </option>
              ))}
            </select>
          </span>
          <span />
          <span />
        </div>
      )}
      {def.params.map((p) => {
        const label = t(p.label)
        if (p.kind === 'bool') {
          return (
            <div key={p.name} className="lm-ec-prop">
              <span />
              <span className="pname" />
              <span className="pval">
                <label className="check">
                  <input type="checkbox" checked={!!val(p.name)} onChange={(e) => set(p.name, e.target.checked, label)} />
                  {label}
                </label>
              </span>
              <span />
              <span />
            </div>
          )
        }
        if (p.kind === 'level') {
          return (
            <Plain key={p.name} name={label}>
              <NumberScrub
                value={val(p.name) as number}
                min={p.min}
                max={p.max}
                step={p.max !== undefined && p.max > 1000 ? 10 : 0.1}
                digits={p.unit === 'Hz' ? 0 : 1}
                suffix={p.unit ? ` ${p.unit}` : ''}
                onChange={(x) => set(p.name, x, label)}
              />
            </Plain>
          )
        }
        if (p.kind === 'choice') {
          return (
            <div key={p.name} className="lm-ec-prop">
              <span />
              <span className="pname">{label}</span>
              <span className="pval">
                <select value={String(val(p.name))} onChange={(e) => set(p.name, Number(e.target.value), label)}>
                  {DIMENSIONS.map((d, i) => (
                    <option key={d} value={i}>
                      {t(d)}
                    </option>
                  ))}
                </select>
              </span>
              <span />
              <span />
            </div>
          )
        }
        const kfs = (fx.keyframes as Record<string, Kf[] | undefined>)[p.name]
        const v = val(p.name)
        const dial = p.name === 'rotation' || p.name === 'skew_axis'
        return (
          <KfRow
            key={p.name}
            c={c}
            kfs={kfs}
            label={label}
            expand={{
              control: p.kind === 'point' ? undefined : dial ? 'dial' : 'slider',
              min: p.name === 'skew' ? -70 : p.min ?? 0,
              max: p.name === 'skew' ? 70 : p.name === 'blurriness' ? 100 : Math.min(p.max ?? 100, 100),
              value: typeof v === 'number' ? v : undefined,
              onValue: (x) => set(p.name, x, label),
              onEase: (tt, side, speed, influence) =>
                void exec('effect.set_keyframe_ease', { ...base, param: p.name, t: tt, side, speed, influence }, t('Keyframe Velocity'))
            }}
            onToggle={() => void exec('effect.toggle_animation', { ...base, param: p.name, at }, t('Toggle animation'))}
            onKey={() => void exec('effect.keyframe', { ...base, param: p.name, at }, t('Add/Remove Keyframe'))}
            target={{ effect_id: fx.id, param: p.name }}
          >
            {p.kind === 'point' ? (
              <>
                <NumberScrub value={(v as number[])[0]} onChange={(x) => set(p.name, [x, (v as number[])[1]], label)} />
                <NumberScrub value={(v as number[])[1]} onChange={(y) => set(p.name, [(v as number[])[0], y], label)} />
              </>
            ) : (
              <NumberScrub
                value={v as number}
                min={p.min}
                max={p.max}
                disabled={p.name === 'scale_width' && uniform}
                suffix={p.name === 'rotation' || p.name.startsWith('skew') ? '°' : p.name === 'opacity' ? ' %' : ''}
                onChange={(x) => set(p.name, x, label)}
              />
            )}
          </KfRow>
        )
      })}
    </Group>
  )
}
