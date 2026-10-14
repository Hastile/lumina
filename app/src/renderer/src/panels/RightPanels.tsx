// Panels from Premiere 2025+'s Editing workspace right side and top-left tabs.
// Properties and Metadata are functional; Essential Sound, Lumetri Color, Text
// and Graphic Templates hold their place until their milestones (M3/M4).
import { ChevronDown, Diamond, AudioLines, MessageSquare, Music, Sparkles, Wind, RotateCcw } from 'lucide-react'
import type { MediaItem } from '../engine/document'
import { activeSequence, exec, findClip, get, useStore } from '../state/store'
import { ticksToSeconds, timecode } from '../util/time'
import { ValueScrub } from './EffectControls'

function Section({ title, children, reset }: { title: string; children: React.ReactNode; reset?: () => void }) {
  return (
    <div className="lm-prop-section">
      <div className="head">
        <ChevronDown size={13} />
        <span>{title}</span>
        <span style={{ flex: 1 }} />
        {reset && (
          <button className="lm-iconbtn" title="Reset" onClick={reset}>
            <RotateCcw size={12} />
          </button>
        )}
      </div>
      {children}
    </div>
  )
}

function Row({ name, children, keyframe = true }: { name: string; children: React.ReactNode; keyframe?: boolean }) {
  return (
    <div className="lm-prop-row">
      <span className="name">{name}</span>
      <span className="val">{children}</span>
      <span className="kf">{keyframe && <Diamond size={10} />}</span>
    </div>
  )
}

export function PropertiesPanel() {
  const seq = useStore((s) => activeSequence(s))
  const selected = useStore((s) => s.selectedClips)
  const found = seq && selected.length ? findClip(seq, selected[0]) : null
  if (!seq || !found) return <div className="lm-panel"><div className="lm-empty">Select a clip to see its properties.</div></div>
  const { clip, track } = found
  const set = (params: Record<string, unknown>, label: string) => void exec('clip.set', { sequence_id: seq.id, clip_ids: [clip.id], ...params }, label)
  const st = seq.settings
  return (
    <div className="lm-panel lm-props">
      <div className="lm-props-title">{clip.name}</div>
      <div className="lm-props-scroll">
        {track.kind === 'video' ? (
          <>
            <Section title="Transform" reset={() => set({ opacity: 100 }, 'Reset Transform')}>
              <Row name="Position"><span className="lm-value dim">{st.width / 2} X</span>&nbsp;&nbsp;<span className="lm-value dim">{st.height / 2} Y</span></Row>
              <Row name="Anchor Point"><span className="lm-value dim">{st.width / 2} X</span>&nbsp;&nbsp;<span className="lm-value dim">{st.height / 2} Y</span></Row>
              <Row name="Scale"><span className="lm-value dim">100 %</span></Row>
              <Row name="Rotation"><span className="lm-value dim">0 °</span></Row>
              <Row name="Opacity">
                <ValueScrub value={clip.opacity} min={0} max={100} step={0.5} digits={0} suffix=" %" onChange={(v) => set({ opacity: v }, 'Opacity')} />
              </Row>
            </Section>
            <Section title="Crop">
              {['Left', 'Top', 'Right', 'Bottom'].map((n) => (
                <Row key={n} name={n}><span className="lm-value dim">0.0 %</span></Row>
              ))}
            </Section>
            <Section title="Speed">
              <Row name="Speed" keyframe={false}>
                <ValueScrub value={clip.speed * 100} min={1} max={10000} digits={0} suffix=" %" onChange={(v) => void exec('clip.set_speed', { sequence_id: seq.id, clip_id: clip.id, speed: v / 100 }, 'Speed')} />
              </Row>
            </Section>
          </>
        ) : (
          <Section title="Volume" reset={() => set({ volume_db: 0 }, 'Reset Volume')}>
            <Row name="Level">
              <ValueScrub value={clip.volume_db} min={-96} max={15} step={0.1} suffix=" dB" onChange={(v) => set({ volume_db: v }, 'Volume')} />
            </Row>
          </Section>
        )}
        <Section title="Clip">
          <Row name="Start" keyframe={false}><span className="lm-value dim">{timecode(clip.start, st)}</span></Row>
          <Row name="Duration" keyframe={false}><span className="lm-value dim">{timecode(clip.end - clip.start, st)}</span></Row>
          <Row name="Enabled" keyframe={false}><input type="checkbox" checked={clip.enabled} onChange={(e) => set({ enabled: e.target.checked }, 'Enable')} /></Row>
        </Section>
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
    media = f ? (f.clip.media_id ? doc.media[f.clip.media_id] : null) ?? null : null
  }
  if (!media) return <div className="lm-panel"><div className="lm-empty">Select a clip or project item.</div></div>
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
            <span className="k">{k}</span>
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
        <span>Browse</span>
        <span className="active">Edit</span>
      </div>
      <div className="lm-es-body">
        <div className="dim">No selection</div>
        <AudioLines size={40} color="#888" />
        <div className="title">Select a clip to tag:</div>
        <div className="tags">
          {SOUND_TAGS.map(([t, Icon]) => (
            <button key={t} className="lm-chip" disabled>
              <Icon size={12} /> {t}
            </button>
          ))}
        </div>
        <div className="dim small">Audio tagging and loudness tools arrive with the audio milestone (M3).</div>
      </div>
    </div>
  )
}

export function LumetriPanel() {
  return <div className="lm-panel"><div className="lm-empty">Lumetri Color: color correction arrives with the GPU compositor (M3).</div></div>
}

export function TextPanel() {
  return <div className="lm-panel"><div className="lm-empty">Text / captions arrive with captions and graphics (M4).</div></div>
}

export function GraphicTemplatesPanel() {
  return <div className="lm-panel"><div className="lm-empty">Graphic templates (MOGRT-style) arrive with graphics (M4).</div></div>
}
