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
  graphicTemplates: 'Graphic Templates',
  history: 'History'
}

let api: DockviewApi | null = null

export function setDockApi(a: DockviewApi): void {
  api = a
}

// Double-clicking the Program panel's header (its tab row) also toggles Maximize Frame, like
// double-clicking the monitor itself. Only while Program is the group's shown tab.
document.addEventListener('dblclick', (e) => {
  const header = (e.target as HTMLElement | null)?.closest?.('.dv-tabs-and-actions-container')
  const panel = api?.getPanel('program')
  if (!header || !panel || !panel.group.element.contains(header) || panel.group.activePanel?.id !== 'program') return
  toggleMaximize('program')
})

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
  add('history', { referencePanel: 'lumetri', direction: 'within' })

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

// ---------------------------------------------------------------- saved workspace and lock

const SAVED = 'lumina.layout'
const LOCK = 'lumina.layoutLocked'

const store = {
  get: (k: string): string | null => {
    try {
      return localStorage.getItem(k)
    } catch {
      return null
    }
  },
  set: (k: string, v: string | null): void => {
    try {
      if (v === null) localStorage.removeItem(k)
      else localStorage.setItem(k, v)
    } catch {
      /* per-viewer convenience only */
    }
  }
}

/** Startup: the user's saved workspace when there is one, else Premiere's Editing layout; and the lock. */
export function restoreLayout(a: DockviewApi): void {
  const saved = store.get(SAVED)
  let ok = false
  if (saved) {
    try {
      a.fromJSON(JSON.parse(saved))
      ok = true
    } catch (err) {
      console.warn('[lumina] saved workspace could not be restored', err)
    }
  }
  if (!ok) buildEditingLayout(a)
  setLayoutLocked(isLayoutLocked())
}

/** Window > Workspaces > Save Changes to This Workspace. */
export function saveLayout(): void {
  if (api) store.set(SAVED, JSON.stringify(api.toJSON()))
}

/** Window > Workspaces > Reset to Saved Layout: back to what was saved (not to the factory layout). */
export function resetToSaved(): void {
  if (!api) return
  const saved = store.get(SAVED)
  if (saved) {
    try {
      api.fromJSON(JSON.parse(saved))
      return
    } catch {
      /* fall through to the default */
    }
  }
  buildEditingLayout(api)
}

/** Window > Workspaces > Editing (factory): also forgets the saved workspace. */
export function factoryLayout(): void {
  if (!api) return
  store.set(SAVED, null)
  buildEditingLayout(api)
}

export function isLayoutLocked(): boolean {
  return store.get(LOCK) === '1'
}

/** Lock Workspace: panels and tabs can no longer be dragged around by accident (resizing still works). */
export function setLayoutLocked(locked: boolean): void {
  store.set(LOCK, locked ? '1' : null)
  api?.updateOptions({ disableDnd: locked })
  document.body.dataset.layoutLocked = locked ? '1' : ''
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

// dockview maximizes by hiding the other leaves and restores from cached sizes, which shifts the
// columns by a few pixels each time. The split sizes of the whole grid are kept before Maximize
// and put back exactly after it. (Its gridview tree is internal: children / getChildSize / resizeChild.)
type GridNode = { children?: GridNode[]; getChildSize?: (i: number) => number; resizeChild?: (i: number, size: number) => void }
type Sizes = { sizes: number[]; children: (Sizes | null)[] }
let beforeMaximize: Sizes | null = null

const gridRoot = (): GridNode | null => (api as unknown as { component?: { gridview?: { root?: GridNode } } })?.component?.gridview?.root ?? null

function snapshot(node: GridNode): Sizes | null {
  if (!node.children || !node.getChildSize) return null
  return { sizes: node.children.map((_, i) => node.getChildSize!(i)), children: node.children.map(snapshot) }
}

function restore(node: GridNode, s: Sizes): void {
  if (!node.children || !node.resizeChild || node.children.length !== s.sizes.length) return
  // left to right: each resize takes its difference from the next child, so the last one is exact too
  for (let i = 0; i < s.sizes.length - 1; i++) node.resizeChild(i, s.sizes[i])
  node.children.forEach((c, i) => s.children[i] && restore(c, s.children[i]!))
}

export function toggleMaximize(id: PanelId): void {
  const panel = api?.getPanel(id)
  if (!api || !panel) return
  if (panel.api.isMaximized()) {
    panel.api.exitMaximized()
    const root = gridRoot()
    if (root && beforeMaximize) restore(root, beforeMaximize)
    beforeMaximize = null
  } else {
    const root = gridRoot()
    beforeMaximize = root ? snapshot(root) : null
    panel.api.maximize()
  }
}
