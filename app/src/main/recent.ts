// Recently opened projects (File > Open Recent) and the last folder used per
// kind of file dialog, persisted in userData/recent.json ($LUMINA_RECENT for tests).

import { app } from 'electron'
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

export type Purpose = 'project' | 'import' | 'export' | 'any'
type Recent = { path: string; opened: number }
type State = { projects: Recent[]; dirs: Partial<Record<Purpose, string>> }

const MAX = 15
let cache: State | null = null

function file(): string {
  return process.env.LUMINA_RECENT || join(app.getPath('userData'), 'recent.json')
}

function load(): State {
  if (cache) return cache
  try {
    const s = JSON.parse(readFileSync(file(), 'utf8')) as State
    cache = { projects: Array.isArray(s.projects) ? s.projects : [], dirs: s.dirs ?? {} }
  } catch {
    cache = { projects: [], dirs: {} }
  }
  return cache
}

function save(): void {
  const f = file()
  try {
    mkdirSync(dirname(f), { recursive: true })
    writeFileSync(`${f}.tmp`, JSON.stringify(load(), null, 1))
    renameSync(`${f}.tmp`, f)
  } catch {
    // a read-only profile only loses the history
  }
}

const same = (a: string, b: string) => (process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b)

export function recentProjects(): string[] {
  return load().projects.map((p) => p.path)
}

export function addRecent(path: string): void {
  const s = load()
  s.projects = [{ path, opened: Date.now() }, ...s.projects.filter((p) => !same(p.path, path))].slice(0, MAX)
  setDir('project', dirname(path), false)
  save()
}

export function removeRecent(path: string): void {
  const s = load()
  s.projects = s.projects.filter((p) => !same(p.path, path))
  save()
}

export function clearRecent(): void {
  load().projects = []
  save()
}

export function lastDir(purpose: Purpose): string | null {
  const d = load().dirs
  for (const dir of [d[purpose], d.any]) if (dir && existsSync(dir)) return dir
  return null
}

export function setDir(purpose: Purpose, dir: string, persist = true): void {
  const d = load().dirs
  d[purpose] = dir
  d.any = dir
  if (persist) save()
}

/** Start a dialog in the purpose's last folder unless the caller gave a full path. */
export function withDefaultDir<T extends { defaultPath?: string }>(opts: T, purpose: Purpose): T {
  const dir = lastDir(purpose)
  if (!dir) return opts
  if (!opts.defaultPath) return { ...opts, defaultPath: dir }
  if (!/[\\/]/.test(opts.defaultPath)) return { ...opts, defaultPath: join(dir, opts.defaultPath) }
  return opts
}

/** Remember where the user picked something: a file's folder, or a chosen folder itself. */
export function rememberPick(purpose: Purpose, picked: string): void {
  let dir = picked
  try {
    if (!statSync(picked).isDirectory()) dir = dirname(picked)
  } catch {
    dir = dirname(picked) // a save target that does not exist yet
  }
  setDir(purpose, dir)
}
