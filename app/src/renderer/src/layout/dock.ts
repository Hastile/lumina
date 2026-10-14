import type { DockviewApi } from 'dockview-react'
import type { PanelId } from '../state/store'
import { set } from '../state/store'
import { t } from '@shared/i18n'

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
  audioMeters: 'Audio Meters',
  essentialSound: 'Essential Sound',
  lumetri: 'Lumetri Color',
  properties: 'Properties',
  metadata: 'Metadata',
  text: 'Text',
  audioTrackMixer: 'Audio Track Mixer',
  graphicTemplates: 'Graphic Templates'
}

let api: DockviewApi | null = null

export function setDockApi(a: DockviewApi): void {
  api = a
}

export function dockApi(): DockviewApi | null {
  return api
}

/**
 * Premiere 2025+ "Editing" workspace:
 *   [Effect Controls group | Program | Essential Sound ] [ Lumetri Color /
 *   [Project group | Tools | Timeline | Audio Meters   ] [ Properties     ]
 */
export function buildEditingLayout(a: DockviewApi): void {
  a.clear()
  const narrow = new Set<PanelId>(['tools', 'audioMeters'])
  const add = (id: PanelId, position?: Parameters<DockviewApi['addPanel']>[0]['position']) =>
    a.addPanel({ id, component: id, title: t(PANEL_TITLES[id]), position, minimumWidth: narrow.has(id) ? 36 : 120 })

  // bottom row
  add('project')
  add('effects', { referencePanel: 'project', direction: 'within', index: 0 })
  add('graphicTemplates', { referencePanel: 'project', direction: 'within', index: 1 })
  add('mediaBrowser', { referencePanel: 'project', direction: 'within' })
  add('tools', { referencePanel: 'project', direction: 'right' })
  add('timeline', { referencePanel: 'tools', direction: 'right' })
  add('audioMeters', { referencePanel: 'timeline', direction: 'right' })
  // top row spans the bottom row's width
  add('effectControls', { direction: 'above' })
  add('source', { referencePanel: 'effectControls', direction: 'within' })
  add('audioMixer', { referencePanel: 'effectControls', direction: 'within' })
  add('metadata', { referencePanel: 'effectControls', direction: 'within' })
  add('text', { referencePanel: 'effectControls', direction: 'within' })
  add('audioTrackMixer', { referencePanel: 'effectControls', direction: 'within' })
  add('program', { referencePanel: 'effectControls', direction: 'right' })
  add('essentialSound', { referencePanel: 'program', direction: 'right' })
  // full-height column on the far right
  add('lumetri', { direction: 'right' })
  add('properties', { referencePanel: 'lumetri', direction: 'within' })

  // Tools and Audio Meters have no tab strip in Premiere
  for (const id of ['tools', 'audioMeters'] as const) {
    const g = a.getPanel(id)?.group
    if (g) g.header.hidden = true
  }

  a.getPanel('effectControls')?.api.setActive()
  a.getPanel('project')?.api.setActive()
  a.getPanel('properties')?.api.setActive()

  // proportions close to Premiere's defaults (after the grid has laid out)
  requestAnimationFrame(() => sizeEditingLayout(a))
}

function sizeEditingLayout(a: DockviewApi): void {
  const total = a.width || window.innerWidth
  const height = a.height || window.innerHeight
  a.getPanel('properties')?.group.api.setSize({ width: Math.round(total * 0.18) })
  a.getPanel('effectControls')?.group.api.setSize({ height: Math.round(height * 0.6) })
  a.getPanel('effectControls')?.group.api.setSize({ width: Math.round(total * 0.29) })
  a.getPanel('essentialSound')?.group.api.setSize({ width: Math.round(total * 0.18) })
  a.getPanel('project')?.group.api.setSize({ width: Math.round(total * 0.31) })
  a.getPanel('tools')?.group.api.setSize({ width: 40 })
  a.getPanel('audioMeters')?.group.api.setSize({ width: 60 })
}

export function focusPanel(id: PanelId): void {
  const panel = api?.getPanel(id)
  if (!panel) {
    api?.addPanel({ id, component: id, title: t(PANEL_TITLES[id]), floating: { width: 480, height: 360 } })
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
