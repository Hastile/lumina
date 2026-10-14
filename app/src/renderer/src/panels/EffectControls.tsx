import { useState } from 'react'
import { ChevronDown, ChevronRight, RotateCcw } from 'lucide-react'
import { t } from '@shared/i18n'
import { exec } from '../state/store'
import { timecode } from '../util/time'
import { clipTime, current, KeyframeRow, NumberScrub, setProp, useSelectedClip, type ClipCtx } from './PropertyControls'
import { TextLayerEditor } from './TextLayerEditor'

function Group({ title, children, onReset }: { title: string; children: React.ReactNode; onReset?: () => void }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="lm-ec-group">
      <div className="lm-ec-head">
        <span className="toggle" onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <span className="fxbadge">fx</span>
          <span>{title}</span>
        </span>
        <span style={{ flex: 1 }} />
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
    <div className="lm-panel lm-ec">
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
                {clip.graphic.layers.map((l) => (
                  <TextLayerEditor key={l.id} c={c} layer={l} compact />
                ))}
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
