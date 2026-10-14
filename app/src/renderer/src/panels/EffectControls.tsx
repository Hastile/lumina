import { useState } from 'react'
import { ChevronDown, ChevronRight, Timer } from 'lucide-react'
import { activeSequence, exec, findClip, useStore } from '../state/store'
import { timecode } from '../util/time'

// Premiere-style property tree. Motion / keyframes arrive with the M3
// compositor; Opacity, Speed and Volume are live engine properties today.

function ValueScrub({ value, min, max, step = 1, digits = 1, suffix = '', onChange }: {
  value: number; min: number; max: number; step?: number; digits?: number; suffix?: string; onChange: (v: number) => void
}) {
  const [edit, setEdit] = useState<string | null>(null)
  if (edit !== null) {
    return (
      <input
        className="lm-value-input"
        autoFocus
        value={edit}
        onChange={(e) => setEdit(e.target.value)}
        onBlur={() => setEdit(null)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            const v = Number(edit)
            if (!Number.isNaN(v)) onChange(Math.max(min, Math.min(max, v)))
            setEdit(null)
          }
          if (e.key === 'Escape') setEdit(null)
        }}
      />
    )
  }
  return (
    <span
      className="lm-value"
      title="Drag to scrub, click to type"
      onMouseDown={(e) => {
        const startX = e.clientX
        let moved = false
        let current = value
        const move = (ev: MouseEvent) => {
          const dx = ev.clientX - startX
          if (Math.abs(dx) > 2) moved = true
          current = Math.max(min, Math.min(max, value + Math.round(dx) * step * (ev.shiftKey ? 10 : 1)))
          ;(e.target as HTMLElement).textContent = `${current.toFixed(digits)}${suffix}`
        }
        const up = () => {
          window.removeEventListener('mousemove', move)
          window.removeEventListener('mouseup', up)
          if (moved && current !== value) onChange(current)
          else if (!moved) setEdit(value.toFixed(digits))
        }
        window.addEventListener('mousemove', move)
        window.addEventListener('mouseup', up)
      }}
    >
      {value.toFixed(digits)}
      {suffix}
    </span>
  )
}

function Group({ title, children, fx = true }: { title: string; children: React.ReactNode; fx?: boolean }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="lm-ec-group">
      <div className="lm-ec-head" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        {fx && <span className="fxbadge">fx</span>}
        <span>{title}</span>
      </div>
      {open && children}
    </div>
  )
}

function Prop({ name, children, keyframable = true }: { name: string; children: React.ReactNode; keyframable?: boolean }) {
  return (
    <div className="lm-ec-prop">
      <span className="stopwatch" title={keyframable ? 'Toggle animation (coming with keyframes)' : ''}>{keyframable && <Timer size={12} />}</span>
      <span className="pname">{name}</span>
      <span className="pval">{children}</span>
    </div>
  )
}

export function EffectControlsPanel() {
  const seq = useStore((s) => activeSequence(s))
  const selected = useStore((s) => s.selectedClips)
  const media = useStore((s) => s.doc?.media)
  const found = seq && selected.length ? findClip(seq, selected[0]) : null
  if (!seq || !found) return <div className="lm-panel"><div className="lm-empty">(no clip selected)</div></div>
  const { clip, track } = found
  const m = media?.[clip.media_id]
  const set = (params: Record<string, unknown>, label: string) => void exec('clip.set', { sequence_id: seq.id, clip_ids: [clip.id], ...params }, label)

  return (
    <div className="lm-panel lm-ec">
      <div className="lm-ec-title">
        <span>Source · {m?.name ?? '?'}</span>
        <span className="dim">{seq.name} · {clip.name}</span>
      </div>
      <div className="lm-ec-scroll">
        {track.kind === 'video' ? (
          <>
            <div className="lm-ec-section">Video</div>
            <Group title="Motion">
              <Prop name="Position"><span className="lm-value dim">{seq.settings.width / 2}  {seq.settings.height / 2}</span></Prop>
              <Prop name="Scale"><span className="lm-value dim">100.0</span></Prop>
              <Prop name="Rotation"><span className="lm-value dim">0.0°</span></Prop>
              <Prop name="Anchor Point"><span className="lm-value dim">{(m?.video?.width ?? 0) / 2}  {(m?.video?.height ?? 0) / 2}</span></Prop>
            </Group>
            <Group title="Opacity">
              <Prop name="Opacity">
                <ValueScrub value={clip.opacity} min={0} max={100} step={0.5} suffix=" %" onChange={(v) => set({ opacity: v }, 'Opacity')} />
              </Prop>
              <Prop name="Blend Mode" keyframable={false}><span className="lm-value dim">Normal</span></Prop>
            </Group>
            <Group title="Time Remapping">
              <Prop name="Speed" keyframable={false}>
                <ValueScrub value={clip.speed * 100} min={1} max={10000} step={1} suffix=" %" onChange={(v) => void exec('clip.set_speed', { sequence_id: seq.id, clip_id: clip.id, speed: v / 100 }, 'Speed')} />
              </Prop>
            </Group>
          </>
        ) : (
          <>
            <div className="lm-ec-section">Audio</div>
            <Group title="Volume">
              <Prop name="Bypass" keyframable={false}><input type="checkbox" checked={!clip.enabled} onChange={(e) => set({ enabled: !e.target.checked }, 'Bypass')} /></Prop>
              <Prop name="Level">
                <ValueScrub value={clip.volume_db} min={-96} max={15} step={0.1} suffix=" dB" onChange={(v) => set({ volume_db: v }, 'Volume')} />
              </Prop>
            </Group>
            <Group title="Channel Volume"><Prop name="Left"><span className="lm-value dim">0.0 dB</span></Prop><Prop name="Right"><span className="lm-value dim">0.0 dB</span></Prop></Group>
            <Group title="Panner"><Prop name="Balance"><span className="lm-value dim">0.0</span></Prop></Group>
          </>
        )}
      </div>
      <div className="lm-ec-foot">
        <span className="lm-tc dim">{timecode(clip.start, seq.settings)}</span>
      </div>
    </div>
  )
}
