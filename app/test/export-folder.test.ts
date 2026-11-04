import { beforeEach, expect, it, vi } from 'vitest'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

vi.mock('electron', () => ({ app: { getPath: () => tmpdir() } }))
let folder: string
beforeEach(() => {
  vi.resetModules()
  folder = mkdtempSync(join(tmpdir(), 'lumina-export-folder-'))
  process.env.LUMINA_RECENT = join(folder, 'recent.json')
})

it('finds the existing Outputs folder above a project and keeps it after imports', async () => {
  const output = join(folder, 'Outputs')
  mkdirSync(output)
  const prefs = await import('../src/main/recent')
  expect(prefs.defaultExportDir(join(folder, 'Project', 'work', 'edit.lumina'))).toBe(output)
  prefs.setDir('import', folder)
  prefs.setDir('export', join(folder, 'one-off'))
  expect(prefs.defaultExportDir()).toBe(output)
})

it('uses output by default and persists an explicit folder across restarts', async () => {
  let prefs = await import('../src/main/recent')
  expect(prefs.defaultExportDir(join(folder, 'edit.lumina'))).toBe(join(folder, 'output'))
  const custom = join(folder, 'final-renders')
  prefs.setDefaultExportDir(custom)
  vi.resetModules()
  prefs = await import('../src/main/recent')
  expect(prefs.defaultExportDir()).toBe(custom)
  expect(() => prefs.setDefaultExportDir('relative')).toThrow()
})
