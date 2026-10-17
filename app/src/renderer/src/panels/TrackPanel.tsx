// A track's own controls, shown in Effect Controls and Properties when its header is clicked (or a
// caption cue is selected). Caption tracks work like Premiere's: one Track Style for the text of every
// cue, one Align and Transform for all of them (cues have no transform of their own), and track
// effects that animate every cue without upgrading the captions to graphics.
import { AlignCenter, AlignLeft, AlignRight, Bold, Italic } from 'lucide-react'
import { t } from '@shared/i18n'
import type { CaptionStyle, Clip, Effect, Sequence, Track } from '../engine/document'
import { exec, useStore } from '../state/store'
import { EFFECTS, effectMedia } from '../util/effects'
import { timingClip } from '../util/tracks'
import { EffectGroup, Group, Plain } from './EffectControls'
import { EFFECT_MIME, type EffectDrag } from './EffectsPanel'
import { NumberScrub, type ClipCtx } from './PropertyControls'

const FONTS = ['Pretendard', 'Pretendard-Bold', 'Pretendard-Black', 'Malgun Gothic', 'S-CoreDream-6Bold', 'S-CoreDream-9Black', 'Noto Sans KR', 'Arial', 'Impact']
const VIDEO_FX = ['transform', 'gaussian_blur', 'lumetri'] as const
const AUDIO_FX = ['parametric_eq', 'compressor', 'hard_limiter'] as const

const setStyle = (seq: Sequence, track: Track, style: Partial<CaptionStyle>, label: string) =>
  void exec('caption.set_style', { sequence_id: seq.id, track_id: track.id, style }, label)

/** Track Style: the text formatting every cue of the caption track uses. */
export function CaptionStyleSection({ seq, track }: { seq: Sequence; track: Track }) {
  const s = track.caption_style!
  const set = (style: Partial<CaptionStyle>, label: string) => setStyle(seq, track, style, label)
  const fonts = FONTS.includes(s.font_family) ? FONTS : [s.font_family, ...FONTS]
  const shadow = s.shadow
  return (
    <Group title={t('Track Style')}>
      <div className="lm-text-layer compact" data-caption-style={track.id}>
        <div className="row">
          <select value={s.font_family} onChange={(e) => set({ font_family: e.target.value }, t('Font'))} title={t('Font')}>
            {fonts.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </div>
        <div className="row">
          <span className="k">{t('Font Size')}</span>
          <NumberScrub value={s.font_size} min={1} max={2000} digits={0} onChange={(v) => set({ font_size: v }, t('Font Size'))} />
          <span style={{ flex: 1 }} />
          <button className={`lm-iconbtn ${s.bold ? 'on' : ''}`} onClick={() => set({ bold: !s.bold }, t('Bold'))}>
            <Bold size={13} />
          </button>
          <button className={`lm-iconbtn ${s.italic ? 'on' : ''}`} onClick={() => set({ italic: !s.italic }, 'Italic')}>
            <Italic size={13} />
          </button>
          {(['left', 'center', 'right'] as const).map((a) => {
            const Icon = a === 'left' ? AlignLeft : a === 'center' ? AlignCenter : AlignRight
            return (
              <button key={a} className={`lm-iconbtn ${s.align === a ? 'on' : ''}`} title={t('Alignment')} onClick={() => set({ align: a }, t('Alignment'))}>
                <Icon size={13} />
              </button>
            )
          })}
        </div>
        <div className="row">
          <span className="k">{t('Tracking')}</span>
          <NumberScrub value={s.tracking} min={-500} max={1000} digits={0} onChange={(v) => set({ tracking: v }, t('Tracking'))} />
        </div>
        <div className="row">
          <label className="swatch">
            <input type="color" value={s.color.slice(0, 7)} onChange={(e) => set({ color: e.target.value }, t('Fill Color'))} />
            {t('Fill Color')}
          </label>
          <label className="swatch">
            <input type="checkbox" checked={!!s.stroke_color && s.stroke_width > 0}
              onChange={(e) => set(e.target.checked ? { stroke_color: s.stroke_color ?? '#000000', stroke_width: s.stroke_width || 4 } : { stroke_width: 0 }, t('Stroke'))} />
            <input type="color" value={(s.stroke_color ?? '#000000').slice(0, 7)} onChange={(e) => set({ stroke_color: e.target.value, stroke_width: s.stroke_width || 4 }, t('Stroke'))} />
            {t('Stroke')}
          </label>
          <NumberScrub value={s.stroke_width} min={0} max={200} digits={1} onChange={(v) => set({ stroke_width: v }, t('Stroke Width'))} />
        </div>
        <div className="row">
          <label className="swatch">
            <input type="checkbox" checked={!!s.background} onChange={(e) => set({ background: e.target.checked ? '#000000' : null }, t('Background'))} />
            <input type="color" value={(s.background ?? '#000000').slice(0, 7)} onChange={(e) => set({ background: e.target.value }, t('Background'))} />
            {t('Background')}
          </label>
          <label className="swatch">
            <input type="checkbox" checked={!!shadow}
              onChange={(e) => set({ shadow: e.target.checked ? { color: '#000000', opacity: 75, angle: 135, distance: 4, blur: 4 } : null }, t('Shadow'))} />
            <input type="color" value={(shadow?.color ?? '#000000').slice(0, 7)} disabled={!shadow} onChange={(e) => shadow && set({ shadow: { ...shadow, color: e.target.value } }, t('Shadow'))} />
            {t('Shadow')}
          </label>
        </div>
        {shadow && (
          <div className="row">
            <span className="k">{t('Opacity')}</span>
            <NumberScrub value={shadow.opacity} min={0} max={100} digits={0} suffix="%" onChange={(v) => set({ shadow: { ...shadow, opacity: v } }, t('Shadow'))} />
            <span className="k">{t('Distance')}</span>
            <NumberScrub value={shadow.distance} min={0} max={200} digits={0} onChange={(v) => set({ shadow: { ...shadow, distance: v } }, t('Shadow'))} />
            <span className="k">{t('Blur')}</span>
            <NumberScrub value={shadow.blur} min={0} max={100} digits={0} onChange={(v) => set({ shadow: { ...shadow, blur: v } }, t('Shadow'))} />
          </div>
        )}
      </div>
    </Group>
  )
}

/** Align and Transform: one transform for every cue (Premiere's caption track). */
export function CaptionTransformSection({ seq, track }: { seq: Sequence; track: Track }) {
  const s = track.caption_style!
  const { width: W, height: H } = seq.settings
  const set = (style: Partial<CaptionStyle>, label: string) => setStyle(seq, track, style, label)
  // the 9-point region: title-safe margins (10 %), the block's vertical centre half a line in from the edge
  const half = (s.font_size * s.scale) / 100 / 2
  const xs = { left: W * 0.1, center: W / 2, right: W * 0.9 } as const
  const ys = [H * 0.1 + half, H / 2, H * 0.9 - half]
  const reset = () => set({ position: [W / 2, H * (H > W ? 0.82 : 0.88)], scale: 100, rotation: 0, opacity: 100, align: 'center' }, t('Reset'))
  return (
    <Group title={t('Align and Transform')} onReset={reset}>
      <div className="lm-ec-prop tall">
        <span />
        <span className="pname">{t('Region')}</span>
        <span className="pval">
          <span className="lm-align-grid" data-caption-align="">
            {ys.map((y, r) =>
              (['left', 'center', 'right'] as const).map((a) => {
                const on = s.align === a && Math.abs(s.position[0] - xs[a]) < 1 && Math.abs(s.position[1] - y) < 1
                return (
                  <button key={`${r}${a}`} className={on ? 'on' : ''} data-align={`${r}-${a}`} title={t('Align')}
                    onClick={() => set({ align: a, position: [xs[a], Math.round(y)] }, t('Align'))} />
                )
              })
            )}
          </span>
        </span>
        <span />
        <span />
      </div>
      <Plain name={t('Position')}>
        <NumberScrub value={s.position[0]} onChange={(v) => set({ position: [v, s.position[1]] }, t('Position'))} />
        <NumberScrub value={s.position[1]} onChange={(v) => set({ position: [s.position[0], v] }, t('Position'))} />
      </Plain>
      <Plain name={t('Scale')}>
        <NumberScrub value={s.scale} min={0} max={10000} suffix=" %" onChange={(v) => set({ scale: v }, t('Scale'))} />
      </Plain>
      <Plain name={t('Rotation')}>
        <NumberScrub value={s.rotation} suffix="°" onChange={(v) => set({ rotation: v }, t('Rotation'))} />
      </Plain>
      <Plain name={t('Opacity')}>
        <NumberScrub value={s.opacity} min={0} max={100} suffix=" %" onChange={(v) => set({ opacity: v }, t('Opacity'))} />
      </Plain>
    </Group>
  )
}

/** The track's effects, edited like a clip's; keyframes shown on ``ref`` (they apply to every clip of
 * the track, each from its own In / Out). */
export function TrackEffectsSection({ seq, track, refClip, time }: { seq: Sequence; track: Track; refClip: Clip | null; time: number }) {
  const fxs = track.effects ?? []
  const kind = track.kind
  const add = (type: string) => void exec('effect.add', { sequence_id: seq.id, track_ids: [track.id], type }, t('Apply Track Effect'))
  const base: Clip = refClip ?? ({
    id: '', media_id: null, name: track.name, start: 0, end: Math.max(1, ...Object.values(track.clips).map((c) => c.end)), source_in: 0, speed: 1
  } as unknown as Clip)
  return (
    <>
      <div className="lm-ec-section" data-track-effects={track.id} title={t('Applies to every clip on {track}; keyframes shown on this clip').replace('{track}', track.name)}>
        {t('Track Effects')} · {track.name}
        <select className="lm-add-fx" value="" onChange={(e) => e.target.value && add(e.target.value)} data-add-track-fx="">
          <option value="">{t('Add Track Effect')}…</option>
          {(kind === 'video' ? VIDEO_FX : AUDIO_FX).map((type) => (
            <option key={type} value={type}>
              {t(EFFECTS[type].label)}
            </option>
          ))}
        </select>
      </div>
      {!fxs.length && <div className="lm-ec-hint">{t('Drop an effect or preset here or on the track header: it applies to every clip of the track.')}</div>}
      {fxs.map((fx: Effect) => {
        const tm = timingClip(fx, base)
        const clip: Clip = { ...base, source_in: tm.source_in - (tm.start - base.start) * tm.speed, speed: tm.speed, effects: fxs }
        const c: ClipCtx = { seq, clip, kind, media: null, time }
        return <EffectGroup key={fx.id} c={c} fx={fx} trackId={track.id} />
      })}
    </>
  )
}

/** The cue of ``track`` under the playhead (else its first): the time reference for track effects. */
export function referenceClip(track: Track, time: number): Clip | null {
  const clips = Object.values(track.clips).sort((a, b) => a.start - b.start)
  return clips.find((c) => c.start <= time && time < c.end) ?? clips[0] ?? null
}

/** Everything a selected track shows in Effect Controls / Properties. */
export function TrackPanel({ seq, track }: { seq: Sequence; track: Track }) {
  const time = useStore((s) => s.programTime)
  const caption = !!track.caption_style
  const ref = referenceClip(track, time)
  return (
    <div
      className="lm-track-panel"
      data-track-panel={track.id}
      onDragOver={(e) => e.dataTransfer.types.includes(EFFECT_MIME) && e.preventDefault()}
      onDrop={(e) => {
        const raw = e.dataTransfer.getData(EFFECT_MIME)
        if (!raw) return
        e.preventDefault()
        e.stopPropagation()
        const item = JSON.parse(raw) as EffectDrag
        if (item.kind === 'effect' && effectMedia(item.type as Effect['type']) === track.kind) void exec('effect.add', { sequence_id: seq.id, track_ids: [track.id], type: item.type }, t('Apply Track Effect'))
        if (item.kind === 'preset' && track.kind === 'video') void exec('effect.apply_preset', { sequence_id: seq.id, track_ids: [track.id], preset_id: item.type }, t('Apply Preset to Track'))
      }}
    >
      {caption && (
        <>
          <div className="lm-ec-section">{t('Caption Track')} · {track.name}</div>
          <CaptionStyleSection seq={seq} track={track} />
          <CaptionTransformSection seq={seq} track={track} />
        </>
      )}
      <TrackEffectsSection seq={seq} track={track} refClip={ref} time={time} />
    </div>
  )
}

/** The selected track of the active sequence (a clicked track header), if no clip is selected. */
export function useSelectedTrack(): { seq: Sequence; track: Track } | null {
  const id = useStore((s) => (s.selectedClips.length ? null : s.selectedTrack))
  const doc = useStore((s) => s.doc)
  const seqId = doc?.active_sequence_id
  const seq = seqId ? doc?.sequences[seqId] : undefined
  const track = id && seq ? seq.tracks[id] : undefined
  return seq && track ? { seq, track } : null
}

