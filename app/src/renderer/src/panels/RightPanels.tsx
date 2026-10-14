// Panels from Premiere 2025+'s Editing workspace right side and top-left tabs.
// Properties and Metadata are functional; Essential Sound, Lumetri Color, Text
// and Graphic Templates hold their place until their milestones.
import { ChevronDown, AudioLines, MessageSquare, Music, Sparkles, Wind, RotateCcw, Plus, Maximize, Minimize } from 'lucide-react'
import { t } from '@shared/i18n'
import type { MediaItem } from '../engine/document'
import { activeSequence, exec, findClip, get, useStore } from '../state/store'
import { ticksToSeconds } from '../util/time'
import { current, KeyframeRow, NumberScrub, setProp, useSelectedClip } from './PropertyControls'
import { TextLayerEditor } from './TextLayerEditor'

function Section({ title, children, reset }: { title: string; children: React.ReactNode; reset?: () => void }) {
  return (
    <div className="lm-prop-section">
      <div className="head">
        <ChevronDown size={13} />
        <span>{title}</span>
        <span style={{ flex: 1 }} />
        {reset && (
          <button className="lm-iconbtn" title={t('Reset')} onClick={reset}>
            <RotateCcw size={12} />
          </button>
        )}
      </div>
      {children}
    </div>
  )
}

export function PropertiesPanel() {
  const c = useSelectedClip()
  if (!c) return <div className="lm-panel"><div className="lm-empty">{t('Select a clip to see its properties.')}</div></div>
  const { clip, seq } = c
  const pos = current(c, 'position') as number[]
  const anchor = current(c, 'anchor') as number[]
  const fit = (mode: 'fit' | 'fill') => void exec('clip.fit_to_frame', { sequence_id: seq.id, clip_ids: [clip.id], mode }, t(mode === 'fit' ? 'Fit' : 'Fill'))
  return (
    <div className="lm-panel lm-props">
      <div className="lm-props-title">{clip.name}</div>
      <div className="lm-props-scroll lm-ec">
        {clip.graphic && (
          <Section title={t('Text')}>
            {clip.graphic.layers.map((l) => (
              <TextLayerEditor key={l.id} c={c} layer={l} />
            ))}
            <button className="lm-btn small" onClick={() => void exec('graphic.add_layer', { sequence_id: seq.id, clip_id: clip.id, text: t('New Text Layer') }, t('Add Text Layer'))}>
              <Plus size={12} /> {t('Add Text Layer')}
            </button>
          </Section>
        )}
        {c.kind === 'video' ? (
          <>
            <Section title={t('Transform')} reset={() => void exec('clip.reset_motion', { sequence_id: seq.id, clip_ids: [clip.id] }, t('Reset'))}>
              {c.media?.video && (
                <div className="lm-fitrow">
                  <button className="lm-chip" onClick={() => fit('fill')}>
                    <Maximize size={12} /> {t('Fill')}
                  </button>
                  <button className="lm-chip" onClick={() => fit('fit')}>
                    <Minimize size={12} /> {t('Fit')}
                  </button>
                </div>
              )}
              <KeyframeRow c={c} prop="position">
                <NumberScrub value={pos[0]} digits={0} suffix=" X" onChange={(v) => setProp(c, 'position', [v, pos[1]])} />
                <NumberScrub value={pos[1]} digits={0} suffix=" Y" onChange={(v) => setProp(c, 'position', [pos[0], v])} />
              </KeyframeRow>
              <KeyframeRow c={c} prop="anchor">
                <NumberScrub value={anchor[0]} digits={0} suffix=" X" onChange={(v) => setProp(c, 'anchor', [v, anchor[1]])} />
                <NumberScrub value={anchor[1]} digits={0} suffix=" Y" onChange={(v) => setProp(c, 'anchor', [anchor[0], v])} />
              </KeyframeRow>
              <KeyframeRow c={c} prop="scale">
                <NumberScrub value={current(c, 'scale') as number} min={0} max={10000} digits={0} suffix=" %" onChange={(v) => setProp(c, 'scale', v)} />
              </KeyframeRow>
              <KeyframeRow c={c} prop="rotation">
                <NumberScrub value={current(c, 'rotation') as number} digits={0} suffix=" °" onChange={(v) => setProp(c, 'rotation', v)} />
              </KeyframeRow>
              <KeyframeRow c={c} prop="opacity">
                <NumberScrub value={current(c, 'opacity') as number} min={0} max={100} digits={0} suffix=" %" onChange={(v) => setProp(c, 'opacity', v)} />
              </KeyframeRow>
            </Section>
            <Section title={t('Crop')}>
              {(['crop_left', 'crop_top', 'crop_right', 'crop_bottom'] as const).map((p) => (
                <KeyframeRow key={p} c={c} prop={p} label={{ crop_left: 'Left', crop_top: 'Top', crop_right: 'Right', crop_bottom: 'Bottom' }[p]}>
                  <NumberScrub value={current(c, p) as number} min={0} max={100} suffix=" %" onChange={(v) => setProp(c, p, v)} />
                </KeyframeRow>
              ))}
            </Section>
          </>
        ) : (
          <Section title={t('Volume')} reset={() => setProp(c, 'volume_db', 0)}>
            <KeyframeRow c={c} prop="volume_db">
              <NumberScrub value={current(c, 'volume_db') as number} min={-96} max={15} step={0.1} suffix=" dB" onChange={(v) => setProp(c, 'volume_db', v)} />
            </KeyframeRow>
          </Section>
        )}
      </div>
    </div>
  )
}

export function MetadataPanel() {
  const doc = useStore((s) => s.doc)
  const items = useStore((s) => s.selectedItems)
  const clips = useStore((s) => s.selectedClips)
  let media: MediaItem | null = null
  if (doc && items.length) media = doc.media[items[0]] ?? null
  if (!media && doc && clips.length) {
    const seq = activeSequence(get())
    const f = seq ? findClip(seq, clips[0]) : null
    media = f?.clip.media_id ? doc.media[f.clip.media_id] ?? null : null
  }
  if (!media) return <div className="lm-panel"><div className="lm-empty">{t('Select a clip or project item.')}</div></div>
  const rows: [string, string][] = [
    ['Name', media.name],
    ['File Path', media.path],
    ['Media Type', media.kind],
    ['Media Duration', media.kind === 'image' ? '-' : `${ticksToSeconds(media.duration).toFixed(3)} s`],
    ['Video', media.video ? `${media.video.width} x ${media.video.height}, ${(media.video.fps_num / media.video.fps_den).toFixed(3)} fps, ${media.video.codec || '?'} ${media.video.pix_fmt}` : '-'],
    ['Audio', media.audio ? `${media.audio.sample_rate} Hz, ${media.audio.channels} ch, ${media.audio.codec || '?'}` : '-'],
    ['Proxy', media.proxy_path ?? '-'],
    ['Label', media.label]
  ]
  return (
    <div className="lm-panel">
      <div className="lm-meta">
        {rows.map(([k, v]) => (
          <div key={k} className="row">
            <span className="k">{t(k)}</span>
            <span className="v" title={v}>{v}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const SOUND_TAGS: [string, React.FC<{ size?: number }>][] = [
  ['Dialogue', MessageSquare],
  ['Music', Music],
  ['SFX', Sparkles],
  ['Ambience', Wind]
]

export function EssentialSoundPanel() {
  return (
    <div className="lm-panel">
      <div className="lm-subtabs">
        <span>{t('Browse')}</span>
        <span className="active">{t('Edit')}</span>
      </div>
      <div className="lm-es-body">
        <div className="dim">{t('No selection')}</div>
        <AudioLines size={40} color="#888" />
        <div className="title">{t('Select a clip to tag:')}</div>
        <div className="tags">
          {SOUND_TAGS.map(([tag, Icon]) => (
            <button key={tag} className="lm-chip" disabled>
              <Icon size={12} /> {t(tag)}
            </button>
          ))}
        </div>
        <div className="dim small">{t('Audio tagging and loudness tools arrive with the audio milestone (M3).')}</div>
      </div>
    </div>
  )
}

export function LumetriPanel() {
  return <div className="lm-panel"><div className="lm-empty">{t('Lumetri Color: color correction arrives with the GPU compositor (M3).')}</div></div>
}

export function TextPanel() {
  return <div className="lm-panel"><div className="lm-empty">{t('Text / captions arrive with captions and graphics (M4).')}</div></div>
}

export function GraphicTemplatesPanel() {
  return <div className="lm-panel"><div className="lm-empty">{t('Graphic templates (MOGRT-style) arrive with graphics (M4).')}</div></div>
}
