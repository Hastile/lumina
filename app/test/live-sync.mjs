import { _electron as electron } from 'playwright'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import assert from 'node:assert/strict'
const work = mkdtempSync(join(tmpdir(), 'lumina-live-sync-'))
const app = await electron.launch({ ...(process.env.LUMINA_TEST_EXE ? { executablePath: process.env.LUMINA_TEST_EXE } : {}), args: process.env.LUMINA_TEST_EXE ? ['--user-data-dir=' + join(work, 'profile')] : ['.'], env: { ...process.env, LUMINA_HOME: work, LUMINA_RECENT: join(work, 'recent.json') } })
let socket
try {
 const page = await app.firstWindow()
 await page.waitForFunction(() => window.__lumina?.get().connected, null, { timeout: 30000 })
 const info = await page.evaluate(async () => (await window.lumina.engineInfo()).info)
 socket = new WebSocket(`ws://${info.host}:${info.port}`)
 await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject })
 let id = 0; const pending = new Map()
 socket.onmessage = ev => { const m=JSON.parse(ev.data); if(pending.has(m.id)){ const [ok,no]=pending.get(m.id); pending.delete(m.id); m.error ? no(new Error(m.error.message)) : ok(m.result) } }
 const rpc = (method, params={}) => new Promise((ok,no)=>{ const n=++id; pending.set(n,[ok,no]); socket.send(JSON.stringify({jsonrpc:'2.0',id:n,method,params})) })
 await rpc('hello', {token:info.token})
 await rpc('execute', {name:'project.rename',params:{name:'agent live update'}})
 await page.waitForFunction(()=>document.body.textContent.includes('agent live update'))
 await page.evaluate(()=>{window.__savedListeners=[...window.__lumina.engine.listeners];window.__lumina.engine.listeners.clear()})
 await rpc('execute', {name:'project.rename',params:{name:'missed event recovered'}})
 await page.waitForFunction(()=>document.body.textContent.includes('missed event recovered'),null,{timeout:10000})
 await page.evaluate(()=>{for(const fn of window.__savedListeners)window.__lumina.engine.listeners.add(fn)})
 await page.evaluate(()=>window.__lumina.engine.exec('project.rename',{name:'manual client update'}))
 assert.equal((await rpc('snapshot')).document.name,'manual client update')
 await rpc('project.close', {discard:true})
 console.log('PASS: external edits visible, dropped event recovered without reopen/save, GUI command visible to other client.')
} finally { socket?.close(); await app.close() }
