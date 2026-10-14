import { contextBridge, ipcRenderer, webUtils } from 'electron'

export type EngineInfo = { host: string; port: number; token: string; pid: number }

const api = {
  engineInfo: (): Promise<{ ok: true; info: EngineInfo } | { ok: false; error: string }> => ipcRenderer.invoke('engine:info'),
  openDialog: (opts: Electron.OpenDialogOptions): Promise<string[]> => ipcRenderer.invoke('dialog:open', opts),
  saveDialog: (opts: Electron.SaveDialogOptions): Promise<string | null> => ipcRenderer.invoke('dialog:save', opts),
  setTitle: (title: string): Promise<void> => ipcRenderer.invoke('window:setTitle', title),
  onMenuAction: (fn: (action: string) => void): (() => void) => {
    const listener = (_: unknown, action: string) => fn(action)
    ipcRenderer.on('menu:action', listener)
    return () => ipcRenderer.removeListener('menu:action', listener)
  },
  pathForFile: (file: File): string => webUtils.getPathForFile(file)
}

contextBridge.exposeInMainWorld('lumina', api)

export type LuminaApi = typeof api
