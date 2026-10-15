import { useState } from 'react'
import { ChevronDown, ChevronRight, RotateCcw, Trash2 } from 'lucide-react'
import { t } from '@shared/i18n'
import { exec } from '../state/store'
import { timecode } from '../util/time'
import { clipTime, current, KeyframeRow, KfRow, NumberScrub, setProp, useSelectedClip, type ClipCtx } from './PropertyControls'
import { EFFECTS, effectParam } from '../util/effects'
import type { Effect } from '../engine/document'
import type { Kf } from '../util/motion'
import { EFFECT_MIME } from './EffectsPanel'
import { GraphicLayers } from './GraphicLayers'

function Group({ title, children, onReset, enabled, onToggleEnabled, onDelete, fxId }: {
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

function Plain({ name, children }: { name: string; children: React.ReactNode }) {
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
  const c = useSelectedClip()
  if (!c) return <div className="lm-panel"><div className="lm-empty">{t('(no clip selected)')}</div></div>
  const { clip, seq } = c
  const reset = () => void exec('clip.reset_motion', { sequence_id: seq.id, clip_ids: [clip.id] }, t('Reset'))
  return (
    <div
      className="lm-panel lm-ec"
      onDragOver={(e) => e.dataTransfer.types.includes(EFFECT_MIME) && e.preventDefault()}
      onDrop={(e) => {
        const raw = e.dataTransfer.getData(EFFECT_MIME)
        if (!raw) return
        const item = JSON.parse(raw) as { kind: string; type: string }
        if (item.kind === 'effect' && c.kind === 'video') void exec('effect.add', { sequence_id: seq.id, clip_ids: [clip.id], type: item.type }, t('Apply Effect'))
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
            {(clip.effects ?? []).map((fx) => (
              <EffectGroup key={fx.id} c={c} fx={fx} />
            ))}
            <Group title={t('Time Remapping')}>
              <Plain name={t('Speed')}>
                <NumberScrub value={clip.speed * 100} min={1} max={10000} digits={2} suffix=" %" onChange={(v) => void exec('clip.set_speed', { sequence_id: seq.id, clip_id: clip.id, speed: v / 100 }, t('Speed'))} />
              </Plain>
            </Group>
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
          </>
        )}
      </div>
      <div className="lm-ec-foot">
        <span className="lm-tc dim">{timecode(clipTime(c), seq.settings)}</span>
      </div>
    </div>
  )
}

const DIMENSIONS = ['Horizontal and Vertical', 'Horizontal', 'Vertical']

function EffectGroup({ c, fx }: { c: ClipCtx; fx: Effect }) {
  const def = EFFECTS[fx.type]
  const at = clipTime(c)
  const [w, h] = c.media?.video ? [c.media.video.width, c.media.video.height] : [c.seq.settings.width, c.seq.settings.height]
  const base = { sequence_id: c.seq.id, clip_id: c.clip.id, effect_id: fx.id }
  const val = (n: string) => effectParam(fx, n, c.clip, at, w, h)
  const set = (param: string, value: number | boolean | number[], label: string) =>
    void exec('effect.set_param', { ...base, param, value, at }, label)
  const uniform = !!val('uniform')
  return (
    <Group
      title={t(def.label)}
      fxId={fx.id}
      enabled={fx.enabled}
      onToggleEnabled={() => void exec('effect.set', { ...base, enabled: !fx.enabled }, t('Toggle effect on/off'))}
      onDelete={() => void exec('effect.remove', base, t('Delete Effect'))}
    >
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
        return (
          <KfRow
            key={p.name}
            c={c}
            kfs={kfs}
            label={label}
            onToggle={() => void exec('effect.toggle_animation', { ...base, param: p.name, at }, t('Toggle animation'))}
            onKey={() => void exec('effect.keyframe', { ...base, param: p.name, at }, t('Add/Remove Keyframe'))}
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
