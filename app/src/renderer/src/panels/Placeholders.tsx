// Panels that exist in Premiere's Editing workspace but whose features land in
// later milestones (see plan: M3 effects/audio). They keep the layout faithful.
import { activeSequence, exec, useStore } from '../state/store'

export function AudioMixerPanel() {
  const seq = useStore((s) => activeSequence(s))
  if (!seq) return <div className="lm-panel"><div className="lm-empty">(no sequence)</div></div>
  return (
    <div className="lm-panel lm-mixer">
      {seq.audio_tracks.map((id, i) => {
        const t = seq.tracks[id]
        return (
          <div key={id} className="strip">
            <div className="pan">L ◦ R</div>
            <div className="ms">
              <button className={`lm-ms m ${t.muted ? 'on' : ''}`} onClick={() => void exec('track.set', { sequence_id: seq.id, track_id: id, muted: !t.muted })}>M</button>
              <button className={`lm-ms s ${t.solo ? 'on' : ''}`} onClick={() => void exec('track.set', { sequence_id: seq.id, track_id: id, solo: !t.solo })}>S</button>
            </div>
            <div className="fader"><div className="rail" /><div className="knob" /></div>
            <div className="name">A{i + 1}</div>
          </div>
        )
      })}
    </div>
  )
}

export function MediaBrowserPanel() {
  return (
    <div className="lm-panel">
      <div className="lm-empty">Media Browser: use File &gt; Import (Ctrl+I) or drag files into the Project panel.</div>
    </div>
  )
}

export function AudioMeters() {

  return (
    <div className="lm-panel lm-meters">
      <div className="bars">
        <div className="bar" />
        <div className="bar" />
      </div>
      <div className="scale">{[0, -6, -12, -18, -24, -30, -36, -42, -48, -54].map((d) => <span key={d}>{d}</span>)}</div>
    </div>
  )
}
