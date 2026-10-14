import { useEffect } from 'react'
import { DockviewReact, type DockviewReadyEvent, type IDockviewPanelProps } from 'dockview-react'
import { Home } from 'lucide-react'
import { installKeymap } from './actions/keymap'
import { buildEditingLayout, setDockApi } from './layout/dock'
import { ContextMenuHost, DialogHost, Toasts } from './layout/Chrome'
import { connectEngine, set, useStore, type PanelId } from './state/store'
import { ProjectPanel } from './panels/ProjectPanel'
import { ProgramMonitor, SourceMonitor } from './panels/Monitors'
import { TimelinePanel } from './timeline/TimelinePanel'
import { EffectControlsPanel } from './panels/EffectControls'
import { ToolsPanel } from './panels/ToolsPanel'
import { AudioMeters, AudioMixerPanel, EffectsPanel, MediaBrowserPanel } from './panels/Placeholders'

const panel = (id: PanelId, C: React.FC) => {
  const Wrapped = (_: IDockviewPanelProps) => (
    <div style={{ height: '100%' }} onMouseDownCapture={() => useStore.getState().focusedPanel !== id && set({ focusedPanel: id })}>
      <C />
    </div>
  )
  Wrapped.displayName = `Panel(${id})`
  return Wrapped
}

const components: Record<string, React.FunctionComponent<IDockviewPanelProps>> = {
  project: panel('project', ProjectPanel),
  source: panel('source', SourceMonitor),
  program: panel('program', ProgramMonitor),
  timeline: panel('timeline', TimelinePanel),
  effectControls: panel('effectControls', EffectControlsPanel),
  audioMixer: panel('audioMixer', AudioMixerPanel),
  effects: panel('effects', EffectsPanel),
  mediaBrowser: panel('mediaBrowser', MediaBrowserPanel),
  tools: panel('tools', ToolsPanel),
  audioMeters: panel('audioMeters', AudioMeters)
}

const LUMINA_THEME = { name: 'lumina', className: 'dockview-theme-lumina', colorScheme: 'dark' as const, gap: 4 }

const WORKSPACES = ['Learning', 'Assembly', 'Editing', 'Color', 'Effects', 'Audio', 'Captions and Graphics', 'Libraries']

function WorkspaceBar() {
  const connected = useStore((s) => s.connected)
  const history = useStore((s) => s.history)
  const path = useStore((s) => s.path)
  return (
    <div className="lm-workspaces">
      <span className="home">
        <Home size={15} />
      </span>
      {WORKSPACES.map((w) => (
        <span key={w} className={`ws ${w === 'Editing' ? 'active' : ''}`} title={w === 'Editing' ? '' : 'Coming soon'}>
          {w}
        </span>
      ))}
      <span className="spacer" />
      <span className="status">
        {history.in_transaction && <span>Transaction open</span>}
        <span>{path ? path.split(/[\\/]/).pop() : 'Unsaved project'}{history.dirty ? ' *' : ''}</span>
        <span>
          <span className={`dot ${connected ? '' : 'off'}`} />
          {connected ? 'Engine' : 'Engine offline'}
        </span>
      </span>
    </div>
  )
}

export function App() {
  const fatal = useStore((s) => s.fatal)
  const docName = useStore((s) => s.doc?.name)
  const path = useStore((s) => s.path)
  const dirty = useStore((s) => s.history.dirty)

  useEffect(() => {
    void connectEngine()
    return installKeymap()
  }, [])

  useEffect(() => {
    void window.lumina.setTitle(`Lumina - ${path ?? docName ?? 'Untitled'}${dirty ? ' *' : ''}`)
  }, [docName, path, dirty])

  const onReady = (e: DockviewReadyEvent) => {
    setDockApi(e.api)
    buildEditingLayout(e.api)
  }

  return (
    <div className="lm-app">
      <WorkspaceBar />
      <div className="lm-dock">
        <DockviewReact theme={LUMINA_THEME} components={components} onReady={onReady} />
      </div>
      <DialogHost />
      <ContextMenuHost />
      <Toasts />
      {fatal && (
        <div className="lm-fatal">
          <div>
            <b>The Lumina engine could not start.</b>
            {'\n\n'}
            {fatal}
            {'\n\n'}Check that uv and FFmpeg are installed, or set LUMINA_ENGINE_CMD.
          </div>
        </div>
      )}
    </div>
  )
}
