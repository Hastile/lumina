import { useEffect } from 'react'
import { t } from '@shared/i18n'
import { DockviewReact, type DockviewReadyEvent, type IDockviewPanelProps } from 'dockview-react'
import { Home } from 'lucide-react'
import { installKeymap } from './actions/keymap'
import { buildEditingLayout, setDockApi } from './layout/dock'
import { ContextMenuHost, DialogHost, Toasts } from './layout/Chrome'
import { ExportDialog, RenderQueue } from './layout/ExportDialog'
import { connectEngine, set, useStore, type PanelId } from './state/store'
import { ProjectPanel } from './panels/ProjectPanel'
import { ProgramMonitor, SourceMonitor } from './panels/Monitors'
import { TimelinePanel } from './timeline/TimelinePanel'
import { EffectControlsPanel } from './panels/EffectControls'
import { ToolsPanel } from './panels/ToolsPanel'
import { AudioMeters, AudioMixerPanel, EffectsPanel, MediaBrowserPanel } from './panels/Placeholders'
import { EssentialSoundPanel, GraphicTemplatesPanel, LumetriPanel, MetadataPanel, PropertiesPanel, TextPanel } from './panels/RightPanels'

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
  audioMeters: panel('audioMeters', AudioMeters),
  essentialSound: panel('essentialSound', EssentialSoundPanel),
  lumetri: panel('lumetri', LumetriPanel),
  properties: panel('properties', PropertiesPanel),
  metadata: panel('metadata', MetadataPanel),
  text: panel('text', TextPanel),
  audioTrackMixer: panel('audioTrackMixer', AudioMixerPanel),
  graphicTemplates: panel('graphicTemplates', GraphicTemplatesPanel)
}

const LUMINA_THEME = { name: 'lumina', className: 'dockview-theme-lumina', colorScheme: 'dark' as const, gap: 4 }

// Premiere 2025+ header: no workspace tabs, project name centered.
function TopBar() {
  const connected = useStore((s) => s.connected)
  const history = useStore((s) => s.history)
  const path = useStore((s) => s.path)
  const name = useStore((s) => s.doc?.name)
  const title = path ? path.split(/[\\/]/).pop()!.replace(/\.lumina$/, '') : name ?? 'Untitled'
  return (
    <div className="lm-topbar">
      <span className="home">
        <Home size={15} />
      </span>
      <span className="title">
        {title}
        {history.dirty ? ' *' : ''}
      </span>
      <span className="status">
        {history.in_transaction && <span>{t('Transaction open')}</span>}
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
      <TopBar />
      <div className="lm-dock">
        <DockviewReact theme={LUMINA_THEME} components={components} onReady={onReady} />
      </div>
      <DialogHost />
      <ExportDialog />
      <RenderQueue />
      <ContextMenuHost />
      <Toasts />
      {fatal && (
        <div className="lm-fatal">
          <div>
            <b>{t('The Lumina engine could not start.')}</b>
            {'\n\n'}
            {fatal}
            {'\n\n'}Check that uv and FFmpeg are installed, or set LUMINA_ENGINE_CMD.
          </div>
        </div>
      )}
    </div>
  )
}
