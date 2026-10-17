import { contextBridge, ipcRenderer, webUtils } from 'electron'

export type EngineInfo = { host: string; port: number; token: string; pid: number }

const api = {
  engineInfo: (): Promise<{ ok: true; info: EngineInfo } | { ok: false; error: string }> => ipcRenderer.invoke('engine:info'),
  // purpose: 'project' | 'import' | 'export' - the dialog starts in that purpose's last folder
  openDialog: (opts: Electron.OpenDialogOptions, purpose = 'any'): Promise<string[]> => ipcRenderer.invoke('dialog:open', opts, purpose),
  saveDialog: (opts: Electron.SaveDialogOptions, purpose = 'any'): Promise<string | null> => ipcRenderer.invoke('dialog:save', opts, purpose),
  addRecent: (path: string): Promise<void> => ipcRenderer.invoke('recent:add', path),
  removeRecent: (path: string): Promise<void> => ipcRenderer.invoke('recent:remove', path),
  recentProjects: (): Promise<string[]> => ipcRenderer.invoke('recent:list'),
  lastDir: (purpose: string): Promise<string | null> => ipcRenderer.invoke('recent:lastDir', purpose),
  setLastDir: (purpose: string, dir: string): Promise<void> => ipcRenderer.invoke('recent:setDir', purpose, dir),
  setTitle: (title: string): Promise<void> => ipcRenderer.invoke('window:setTitle', title),
  setLocale: (locale: 'ko' | 'en'): Promise<void> => ipcRenderer.invoke('app:setLocale', locale),
  onMenuAction: (fn: (action: string) => void): (() => void) => {
    const listener = (_: unknown, action: string) => fn(action)
    ipcRenderer.on('menu:action', listener)
    return () => ipcRenderer.removeListener('menu:action', listener)
  },
  /** The window is about to close: settle unsaved projects, then confirmClose() lets it close. */
  onCloseRequest: (fn: () => void): (() => void) => {
    const listener = () => fn()
    ipcRenderer.on('app:closeRequest', listener)
    return () => ipcRenderer.removeListener('app:closeRequest', listener)
  },
  confirmClose: (): Promise<void> => ipcRenderer.invoke('app:confirmClose'),
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  showItemInFolder: (path: string): Promise<void> => ipcRenderer.invoke('shell:showItem', path),
  videosDir: (): Promise<string> => ipcRenderer.invoke('app:videosDir')
}

contextBridge.exposeInMainWorld('lumina', api)

export type LuminaApi = typeof api
