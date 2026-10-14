// WebSocket JSON-RPC client for the Lumina engine. The GUI never mutates the
// document locally: it sends commands and mirrors the patches the engine
// broadcasts, so GUI edits and agent (MCP) edits flow through one path.

import type { LuminaApi } from '../../../preload'

declare global {
  interface Window {
    lumina: LuminaApi
  }
}

export type Patch = { op: 'set'; path: string[]; value: unknown } | { op: 'del'; path: string[] }

export type HistoryState = {
  can_undo: boolean
  can_redo: boolean
  undo_label: string | null
  redo_label: string | null
  dirty: boolean
  in_transaction: boolean
}

export type EngineEvent =
  | ({ type: 'patch'; version: number; kind: string; label: string; patches: Patch[] } & HistoryState)
  | ({ type: 'reset'; version: number; document: unknown; path: string | null } & HistoryState)
  | ({ type: 'saved'; path: string } & HistoryState)
  | ({ type: 'history' } & HistoryState)
  | { type: 'job'; job: Job }

export type Job = { id: string; kind: string; media_id: string; status: 'queued' | 'running' | 'done' | 'failed'; error: string | null }

export type CommandSpec = { name: string; description: string; undoable: boolean; params: Record<string, unknown> }

export class EngineError extends Error {
  constructor(
    public code: string,
    message: string,
    public details?: unknown
  ) {
    super(message)
  }
}

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void }

export class EngineClient {
  private ws: WebSocket | null = null
  private nextId = 1
  private pending = new Map<number, Pending>()
  private listeners = new Set<(e: EngineEvent) => void>()

  async connect(): Promise<Record<string, unknown>> {
    let info: Awaited<ReturnType<LuminaApi['engineInfo']>>
    for (let i = 0; ; i++) {
      info = await window.lumina.engineInfo()
      if (info.ok) break
      if (i > 240 || info.error !== 'engine starting') throw new Error(info.error)
      await new Promise((r) => setTimeout(r, 250))
    }
    const { host, port, token } = info.info
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(`ws://${host}:${port}`)
      ws.onopen = () => resolve()
      ws.onerror = () => reject(new Error('cannot connect to engine'))
      ws.onmessage = (ev) => this.onMessage(ev.data as string)
      ws.onclose = () => {
        for (const p of this.pending.values()) p.reject(new Error('engine connection closed'))
        this.pending.clear()
      }
      this.ws = ws
    })
    return (await this.call('hello', { token })) as Record<string, unknown>
  }

  private onMessage(raw: string): void {
    const msg = JSON.parse(raw)
    if (msg.method === 'event') {
      for (const fn of this.listeners) fn(msg.params as EngineEvent)
      return
    }
    const p = this.pending.get(msg.id)
    if (!p) return
    this.pending.delete(msg.id)
    if (msg.error) {
      const data = msg.error.data ?? {}
      p.reject(new EngineError(data.code ?? 'RPC_ERROR', msg.error.message, data.details))
    } else {
      p.resolve(msg.result)
    }
  }

  call<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const ws = this.ws
    if (!ws || ws.readyState !== WebSocket.OPEN) return Promise.reject(new Error('engine not connected'))
    const id = this.nextId++
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject })
      ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }))
    })
  }

  exec<T = Record<string, unknown>>(name: string, params: Record<string, unknown> = {}, label?: string): Promise<T> {
    return this.call<{ result: T }>('execute', { name, params, label }).then((r) => r.result)
  }

  onEvent(fn: (e: EngineEvent) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }
}

export const engine = new EngineClient()

/** Immutable patch application with structural sharing (React-friendly). */
export function applyPatch<T>(doc: T, patch: Patch): T {
  const rec = (node: unknown, depth: number): unknown => {
    const obj = (node ?? {}) as Record<string, unknown>
    const key = patch.path[depth]
    if (depth === patch.path.length - 1) {
      const copy = { ...obj }
      if (patch.op === 'set') copy[key] = patch.value
      else delete copy[key]
      return copy
    }
    return { ...obj, [key]: rec(obj[key], depth + 1) }
  }
  return rec(doc, 0) as T
}

export function mediaUrl(path: string): string {
  return `lumina-media://file/${encodeURIComponent(path)}`
}
