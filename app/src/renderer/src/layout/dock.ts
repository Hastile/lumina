import type { DockviewApi } from 'dockview-react'
import type { PanelId } from '../state/store'
import { set } from '../state/store'

export const PANEL_TITLES: Record<PanelId, string> = {
  project: 'Project',
  source: 'Source',
  program: 'Program',
  timeline: 'Timeline',
  effectControls: 'Effect Controls',
  audioMixer: 'Audio Clip Mixer',
  effects: 'Effects',
  mediaBrowser: 'Media Browser',
  tools: 'Tools',
  audioMeters: 'Audio Meters'
}

let api: DockviewApi | null = null

export function setDockApi(a: DockviewApi): void {
  api = a
}

export function dockApi(): DockviewApi | null {
  return api
}

/** Premiere's default "Editing" workspace. */
export function buildEditingLayout(a: DockviewApi): void {
  a.clear()
  const add = (id: PanelId, position?: Parameters<DockviewApi['addPanel']>[0]['position']) =>
    a.addPanel({ id, component: id, title: PANEL_TITLES[id], position, minimumWidth: id === 'tools' || id === 'audioMeters' ? 36 : 120 })

  // bottom row first, then the monitor row spans the full width above it
  add('project')
  add('mediaBrowser', { referencePanel: 'project', direction: 'within' })
  add('effects', { referencePanel: 'project', direction: 'within' })
  add('tools', { referencePanel: 'project', direction: 'right' })
  add('timeline', { referencePanel: 'tools', direction: 'right' })
  add('audioMeters', { referencePanel: 'timeline', direction: 'right' })
  add('source', { direction: 'above' })
  add('effectControls', { referencePanel: 'source', direction: 'within' })
  add('audioMixer', { referencePanel: 'source', direction: 'within' })
  add('program', { referencePanel: 'source', direction: 'right' })

  a.getPanel('source')?.api.setActive()
  a.getPanel('project')?.api.setActive()

  // proportions close to Premiere's defaults (after the grid has laid out)
  requestAnimationFrame(() => sizeEditingLayout(a))
}

function sizeEditingLayout(a: DockviewApi): void {
  const total = a.width || window.innerWidth
  const height = a.height || window.innerHeight
  a.getPanel('source')?.group.api.setSize({ height: Math.round(height * 0.52) })
  a.getPanel('source')?.group.api.setSize({ width: Math.round(total * 0.5) })
  a.getPanel('project')?.group.api.setSize({ width: Math.round(total * 0.27) })
  a.getPanel('tools')?.group.api.setSize({ width: 52 })
  a.getPanel('audioMeters')?.group.api.setSize({ width: 60 })
}

export function focusPanel(id: PanelId): void {
  const panel = api?.getPanel(id)
  if (!panel) {
    api?.addPanel({ id, component: id, title: PANEL_TITLES[id], floating: { width: 480, height: 360 } })
  } else {
    panel.api.setActive()
  }
  set({ focusedPanel: id })
}

export function toggleMaximize(id: PanelId): void {
  const panel = api?.getPanel(id)
  if (!panel) return
  if (panel.api.isMaximized()) panel.api.exitMaximized()
  else panel.api.maximize()
}
