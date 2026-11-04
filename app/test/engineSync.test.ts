import { beforeEach, afterEach, expect, it, vi } from 'vitest'
const mock = vi.hoisted(() => ({ call: vi.fn(), connect: vi.fn(), listener: undefined as any }))
vi.mock('../src/renderer/src/engine/client', () => ({
  engine: { call: mock.call, connect: mock.connect, exec: vi.fn().mockResolvedValue({}), onEvent: (fn: any) => { mock.listener = fn } },
  EngineError: class extends Error {},
  applyPatch: (doc: any, patch: any) => ({ ...doc, [patch.path[0]]: patch.value })
}))
import { connectEngine, synchronizeEngine, get, set } from '../src/renderer/src/state/store'
const doc = (name: string, id = 'p') => ({ id, name, active_sequence_id: null, root_bin_id: 'root', media: {}, sequences: {} })
const history = { dirty: true, can_undo: true, can_redo: false, undo_label: 'edit', redo_label: null, in_transaction: false }
beforeEach(async () => {
  vi.useFakeTimers()
  vi.stubGlobal('window', { addEventListener: vi.fn() })
  mock.call.mockReset()
  mock.connect.mockResolvedValue({ document: doc('before'), version: 1, path: 'project.lumina', ...history })
  await connectEngine()
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
it('recovers external edits without project.open or project.save', async () => {
  mock.call.mockImplementation(async (method: string) => method === 'snapshot' ? { document: doc('agent edit'), version: 3, path: 'project.lumina', ...history } : [])
  await synchronizeEngine()
  expect(get().doc?.name).toBe('agent edit')
  expect(get().version).toBe(3)
  expect(mock.call.mock.calls.map(c => c[0])).toEqual(['snapshot', 'project.list'])
})
it('does not roll back an event arriving during a snapshot request', async () => {
  let resolve: (value: any) => void = () => {}
  mock.call.mockImplementation(() => new Promise(r => { resolve = r }))
  const pending = synchronizeEngine()
  mock.listener({ type: 'patch', version: 2, kind: 'command', label: 'newer', patches: [{ op: 'set', path: ['name'], value: 'newer' }], ...history })
  resolve({ document: doc('stale'), version: 1, path: 'project.lumina', ...history })
  await pending
  expect(get().doc?.name).toBe('newer')
  expect(get().version).toBe(2)
})
it('recovers a version gap from the server rather than applying a partial patch', async () => {
  mock.call.mockImplementation(async (method: string) => method === 'snapshot' ? { document: doc('complete'), version: 4, path: 'project.lumina', ...history } : [])
  mock.listener({ type: 'patch', version: 4, kind: 'command', label: 'gap', patches: [{ op: 'set', path: ['name'], value: 'partial' }], ...history })
  await vi.waitFor(() => expect(get().doc?.name).toBe('complete'))
  expect(get().version).toBe(4)
})
it('marks disconnection and reconnects without replaying writes', async () => {
  mock.listener({ type: 'disconnected' })
  expect(get().connected).toBe(false)
  mock.connect.mockResolvedValue({ document: doc('reconnected'), version: 8, path: 'project.lumina', ...history })
  await synchronizeEngine()
  expect(get().connected).toBe(true)
  expect(get().doc?.name).toBe('reconnected')
  expect(mock.call).not.toHaveBeenCalled()
})
