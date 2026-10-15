// Panels that exist in Premiere's Editing workspace but whose features land in
// later milestones (see plan: M3 effects/audio). They keep the layout faithful.

export function MediaBrowserPanel() {
  return (
    <div className="lm-panel">
      <div className="lm-empty">Media Browser: use File &gt; Import (Ctrl+I) or drag files into the Project panel.</div>
    </div>
  )
}
