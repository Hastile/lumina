import { t } from '@shared/i18n'
import { engine } from '../engine/client'
import { toast } from '../state/store'
import { type KeyframeProp } from '../util/motion'
import { EFFECTS, effectParam } from '../util/effects'
import { clipTime, current, LABELS, NumberScrub, type ClipCtx } from './PropertyControls'

export function commonValue<T>(values: T[]): T | null {
  return values.length && values.every((v) => Object.is(v, values[0])) ? values[0] : null
}

// Serialize live edits so separate mouse moves cannot interleave transactions.
let pending = Promise.resolve()
function batch(clips: ClipCtx[], label: string, command: (c: ClipCtx) => { name: string; params: Record<string, unknown> }) {
  pending = pending.then(async () => {
    let started = false
    try {
      await engine.call('transaction.begin', { label }); started = true
      for (const c of clips) {
        const { name, params } = command(c)
        await engine.exec(name, { sequence_id: c.seq.id, clip_id: c.clip.id, ...params })
      }
      await engine.call('transaction.commit')
    } catch (err) {
      if (started) await engine.call('transaction.rollback').catch(() => undefined)
      toast(err instanceof Error ? err.message : String(err))
    }
  })
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="lm-ec-prop"><span /><span className="pname">{label}</span><span className="pval">{children}</span><span /><span /></div>
}

function Toggle({ value, onChange, label }: { value: boolean | null; onChange: (value: boolean) => void; label: string }) {
  return <label className="check"><input type="checkbox" checked={value === true} aria-checked={value === null ? 'mixed' : value}
    ref={(el) => { if (el) el.indeterminate = value === null }} onChange={(e) => onChange(e.target.checked)} />{value === null ? '- ' : ''}{label}</label>
}

export function MultiClipProperties({ clips }: { clips: ClipCtx[] }) {
  const videos = clips.filter((c) => c.kind === 'video' && !c.clip.caption)
  const audios = clips.filter((c) => c.kind === 'audio')
  const numeric = (targets: ClipCtx[], prop: KeyframeProp, min?: number, max?: number, axis?: number) => {
    const values = targets.map((c) => { const v = current(c, prop); return axis == null ? v as number : (v as number[])[axis] })
    return <NumberScrub value={commonValue(values)} min={min} max={max} disabled={prop === 'scale_width' && targets.some((c) => c.clip.motion.uniform)} suffix={axis == null ? '' : axis === 0 ? ' X' : ' Y'}
      onChange={(value) => batch(targets, t(LABELS[prop]), (c) => {
        const v = axis == null ? value : (current(c, prop) as number[]).map((x, i) => i === axis ? value : x)
        return { name: 'clip.set_property', params: { prop, value: v, at: clipTime(c) } }
      })} />
  }
  return <div className="lm-panel lm-ec" data-multi-properties="">
    <div className="lm-ec-title">소스 {clips.length}개 선택</div>
    <div className="lm-ec-scroll">
      {!!videos.length && <>
        <div className="lm-ec-section">{t('Video')} · {videos.length}</div>
        {(['position', 'anchor'] as const).map((prop) => <Row key={prop} label={t(LABELS[prop])}>{numeric(videos, prop, undefined, undefined, 0)}{numeric(videos, prop, undefined, undefined, 1)}</Row>)}
        <Row label={t('Scale')}>{numeric(videos, 'scale', 0, 10000)}</Row>
        <Row label={t('Scale Width')}>{numeric(videos, 'scale_width', 0, 10000)}</Row>
        <Row label={t('Uniform Scale')}><Toggle label="" value={commonValue(videos.map((c) => c.clip.motion.uniform))}
          onChange={(uniform) => batch(videos, t('Uniform Scale'), () => ({ name: 'clip.set_uniform_scale', params: { uniform } }))} /></Row>
        <Row label={t('Rotation')}>{numeric(videos, 'rotation')}</Row>
        <Row label={t('Opacity')}>{numeric(videos, 'opacity', 0, 100)}</Row>
        {(['crop_left', 'crop_top', 'crop_right', 'crop_bottom'] as const).map((prop) => <Row key={prop} label={t(LABELS[prop])}>{numeric(videos, prop, 0, 100)}</Row>)}
        <Row label={t('Motion')}><button className="lm-chip" onClick={() => batch(videos, t('Reset'), (c) => ({ name: 'clip.reset_motion', params: { clip_ids: [c.clip.id] } }))}>{t('Reset')}</button></Row>
      </>}
      {!!audios.length && <><div className="lm-ec-section">{t('Audio')} · {audios.length}</div>
        <Row label={t('Level')}>{numeric(audios, 'volume_db', -96, 15)}</Row></>}
      <div className="lm-ec-section">{t('Clip')}</div>
      <Row label={t('Speed')}><NumberScrub value={commonValue(clips.map((c) => Math.abs(c.clip.speed) * 100))} min={1} max={10000} suffix=" %"
        onChange={(speed) => batch(clips, t('Speed'), () => ({ name: 'clip.set_speed', params: { speed: speed / 100, include_linked: false } }))} /></Row>
      <Row label={t('Bypass')}><Toggle label="" value={commonValue(clips.map((c) => !c.clip.enabled))}
        onChange={(bypass) => batch(clips, t('Bypass'), (c) => ({ name: 'clip.set', params: { clip_ids: [c.clip.id], enabled: !bypass } }))} /></Row>
      {[videos, audios].filter((targets) => targets.length > 0).map((targets) => <CommonEffects key={targets[0].kind} clips={targets} />)}
    </div>
  </div>
}

function CommonEffects({ clips }: { clips: ClipCtx[] }) {
  return <>{clips[0].clip.effects.map((first, index, effects) => {
    const ordinal = effects.slice(0, index).filter((fx) => fx.type === first.type).length
    const match = (c: ClipCtx) => c.clip.effects.filter((fx) => fx.type === first.type)[ordinal]
    if (!clips.every(match)) return null
    const def = EFFECTS[first.type]
    const val = (c: ClipCtx, param: string) => effectParam(match(c), param, c.clip, clipTime(c), c.seq.settings.width, c.seq.settings.height)
    const update = (param: string, value: number | boolean, axis?: number) => batch(clips, t(def.label), (c) => ({
      name: 'effect.set_param', params: { effect_id: match(c).id, param, at: clipTime(c),
        value: axis == null ? value : (val(c, param) as number[]).map((v, i) => i === axis ? value : v) }
    }))
    return <div key={first.id}>
      <div className="lm-ec-section">{t(def.label)}{ordinal ? ` · ${ordinal + 1}` : ''}</div>
      <Row label={t('Bypass')}><Toggle label="" value={commonValue(clips.map((c) => !match(c).enabled))}
        onChange={(bypass) => batch(clips, t('Bypass'), (c) => ({ name: 'effect.set', params: { effect_id: match(c).id, enabled: !bypass } }))} /></Row>
      {def.params.map((p) => {
        const values = clips.map((c) => val(c, p.name))
        if (p.kind === 'bool') return <Row key={p.name} label={t(p.label)}><Toggle label="" value={commonValue(values as boolean[])} onChange={(v) => update(p.name, v)} /></Row>
        if (p.kind === 'choice') return <Row key={p.name} label={t(p.label)}><select value={commonValue(values as number[]) ?? ''} onChange={(e) => update(p.name, Number(e.target.value))}>
          <option value="" disabled>-</option>{['Horizontal and Vertical', 'Horizontal', 'Vertical'].map((label, i) => <option key={i} value={i}>{t(label)}</option>)}
        </select></Row>
        return <Row key={p.name} label={t(p.label)}>{(p.kind === 'point' ? [0, 1] : [undefined]).map((axis) => <NumberScrub key={axis ?? 'value'}
          value={commonValue(values.map((v) => axis == null ? v as number : (v as number[])[axis]))} min={p.min} max={p.max}
          suffix={axis == null ? p.unit ? ` ${p.unit}` : '' : axis === 0 ? ' X' : ' Y'} onChange={(v) => update(p.name, v, axis)} />)}</Row>
      })}
    </div>
  })}</>
}
