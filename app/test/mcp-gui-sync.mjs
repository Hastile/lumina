// Real MCP stdio -> discovery -> GUI lifecycle test. Uses an isolated home/profile.
import { _electron as electron } from 'playwright'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import assert from 'node:assert/strict'

const work = mkdtempSync(join(tmpdir(), 'lumina-mcp-gui-'))
const env = { ...process.env, LUMINA_HOME: work, LUMINA_RECENT: join(work, 'recent.json') }
const executablePath = process.env.LUMINA_TEST_EXE || resolve('node_modules/electron/dist/electron.exe')
const args = process.env.LUMINA_TEST_EXE ? [] : ['.']
const bridge = spawn('uv', ['run', '--project', '../engine', 'python', 'test/mcp-sync-bridge.py'], { env, windowsHide: true, stdio: ['pipe', 'pipe', 'inherit'] })
let id = 0
const pending = new Map()
let readyResolve
const ready = new Promise(resolve => { readyResolve = resolve })
createInterface({ input: bridge.stdout }).on('line', line => {
  const message = JSON.parse(line)
  if (message.ready) return readyResolve()
  const request = pending.get(message.id)
  if (!request) return
  pending.delete(message.id)
  message.error ? request.reject(new Error(message.error)) : request.resolve(message.result)
})
const call = (tool, arguments_ = {}) => new Promise((resolve, reject) => {
  const requestId = ++id
  const timer = setTimeout(() => { pending.delete(requestId); reject(new Error(`MCP timeout: ${tool}`)) }, 30000)
  pending.set(requestId, { resolve: value => { clearTimeout(timer); resolve(value) }, reject: error => { clearTimeout(timer); reject(error) } })
  bridge.stdin.write(JSON.stringify({ id: requestId, tool, arguments: arguments_ }) + '\n')
})
let app
const launch = async () => {
  app = await electron.launch({ executablePath, args, env })
  const page = await app.firstWindow()
  await page.waitForFunction(() => window.__lumina?.get().connected, null, { timeout: 30000 })
  return page
}
const visible = (page, name) => page.waitForFunction(name => window.__lumina.get().doc?.name === name, name)
try {
  await Promise.race([ready, new Promise((_, reject) => setTimeout(() => reject(new Error('MCP startup timeout')), 30000))])
  assert.deepEqual(await call('get_editor_status'), { mode: 'gui', connected: false, headless: false, projects: [] })
  console.log('MCP ready while GUI is off; no headless engine')
  let page = await launch()
  console.log('GUI connected')
  assert.equal((await call('ensure_editor')).connected, true)
  await call('project_rename', { name: 'MCP live update' })
  await visible(page, 'MCP live update')
  await call('sequence_create', { name: 'MCP visible sequence' })
  await page.waitForFunction(() => document.body.textContent.includes('MCP visible sequence'))
  await page.evaluate(() => window.__lumina.engine.exec('project.rename', { name: 'GUI live update' }))
  assert.equal((await call('get_project')).name, 'GUI live update')
  console.log('bidirectional edits visible')
  await page.evaluate(() => { window.__savedListeners = [...window.__lumina.engine.listeners]; window.__lumina.engine.listeners.clear() })
  await call('project_rename', { name: 'event loss recovered' })
  await visible(page, 'event loss recovered')
  console.log('lost event recovered')
  await page.evaluate(() => { for (const fn of window.__savedListeners) window.__lumina.engine.listeners.add(fn) })

  const endpoint = readFileSync(join(work, 'engine.json'), 'utf8')
  const secondExe = process.env.LUMINA_TEST_SECOND_EXE || executablePath
  const secondArgs = /electron\.exe$/i.test(secondExe) ? ['.'] : []
  const second = spawn(secondExe, secondArgs, { env, windowsHide: true, stdio: 'ignore' })
  await new Promise((resolve, reject) => { second.on('error', reject); second.on('exit', resolve) })
  console.log('second launch exited')
  assert.equal(readFileSync(join(work, 'engine.json'), 'utf8'), endpoint, 'second launch must not replace GUI engine')
  await call('project_rename', { name: 'single GUI still connected' })
  await visible(page, 'single GUI still connected')

  const project = join(work, 'saved.lumina')
  await call('save_project', { path: project })
  await page.evaluate(() => window.__lumina.engine.exec('project.rename', { name: 'unsaved GUI target' }))
  assert.equal((await call('ensure_editor', { project_path: project })).case, 'project_already_open')
  await visible(page, 'unsaved GUI target')
  await call('project_rename', { name: 'single GUI still connected' })
  await call('save_project')
  await call('close_project')
  await app.close()
  app = undefined
  page = await launch()
  await page.evaluate(() => window.__lumina.engine.exec('project.rename', { name: 'manual first after restart' }))
  assert.ok((await call('get_editor_status')).projects.some(p => p.name === 'manual first after restart'))
  assert.equal((await call('ensure_editor', { project_path: project })).case, 'project_opened')
  await visible(page, 'single GUI still connected')
  await call('project_rename', { name: 'MCP follows restarted GUI' })
  await visible(page, 'MCP follows restarted GUI')
  for (const p of await call('list_projects')) await call('close_project', { key: p.key, discard: true })
  console.log('PASS: idle MCP before GUI without headless, bidirectional live edits, lost-event recovery, second launch, manual-first GUI restart, same target preserves unsaved edits, other project preserved while opening target.')
} catch (error) {
  console.error(error)
  throw error
} finally {
  bridge.stdin.end()
  if (app) {
    // This is an isolated disposable test profile, never the user's editor.
    await app.firstWindow().then(page => page.evaluate(async () => {
      for (const p of await window.__lumina.engine.call('project.list'))
        await window.__lumina.engine.call('project.close', { key: p.key, discard: true })
    })).catch(() => {})
    await app.close()
  }
}
