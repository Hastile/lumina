// Open projects (Premiere keeps several open): switch, Close Project, Close All Projects, and the
// Save / Don't Save / Cancel question before unsaved changes are closed - also when the window closes.
import { t } from '@shared/i18n'
import type { ProjectInfo } from '../engine/client'
import { askChoice } from '../state/dialog'
import { call, get } from '../state/store'
import { def, saveAs } from './actions'

export async function activateProject(key: string): Promise<void> {
  if (get().projects.find((p) => p.key === key && !p.active)) await call('project.activate', { key })
}

/** Ask about unsaved changes and save them when asked to: true when the project may be closed. */
async function settle(p: ProjectInfo): Promise<'closed-ok' | 'discard' | false> {
  if (!p.dirty) return 'closed-ok'
  const answer = await askChoice({
    title: t('Save Project'),
    message: t('Save changes to "{name}" before closing?').replace('{name}', p.name),
    buttons: [
      { label: t('Save'), value: 'save', primary: true },
      { label: t("Don't Save"), value: 'discard' },
      { label: t('Cancel'), value: 'cancel' }
    ],
    cancel: 'cancel'
  })
  if (answer === 'cancel') return false
  if (answer === 'discard') return 'discard'
  if (p.path) return (await call('project.save', { key: p.key })) !== null ? 'closed-ok' : false
  return (await saveAs(p.key)) ? 'closed-ok' : false
}

/** File > Close Project: the active project, or ``key``. False when the user cancelled. */
export async function closeProject(key?: string): Promise<boolean> {
  const p = get().projects.find((x) => (key ? x.key === key : x.active))
  if (!p) return true
  const ok = await settle(p)
  if (!ok) return false
  return (await call('project.close', { key: p.key, discard: ok === 'discard' })) !== null
}

/** File > Close All Projects (and closing the window): asks about each unsaved project in turn
 * (shown while asked); Cancel stops and leaves every project open. */
export async function closeAllProjects(): Promise<boolean> {
  for (const p of get().projects.filter((x) => x.dirty)) {
    await activateProject(p.key)
    if (!(await settle(get().projects.find((x) => x.key === p.key) ?? p))) return false
  }
  // what is still unsaved now is what the user chose not to save
  return (await call('project.close_all', { discard: true })) !== null
}

def('file.closeProject', t('Close Project'), async () => void (await closeProject()))
def('file.closeAllProjects', t('Close All Projects'), async () => void (await closeAllProjects()))

// The window is closing (X button, Alt+F4, File > Exit): settle unsaved projects first.
window.lumina.onCloseRequest?.(async () => {
  let ok = true
  try {
    ok = !get().connected || (await closeAllProjects())
  } catch {
    ok = true // a broken engine must not keep the window open
  }
  if (ok) void window.lumina.confirmClose()
})
