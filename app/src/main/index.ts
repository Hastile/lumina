import { app, BrowserWindow, dialog, ipcMain, Menu, protocol, shell, type MenuItemConstructorOptions } from 'electron'
import { spawn, execFile, type ChildProcess } from 'node:child_process'
import { createReadStream, statSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { Readable } from 'node:stream'
import { createInterface } from 'node:readline'
import { setLocale, t, type Locale } from '../shared/i18n'

// ---------------------------------------------------------------- media protocol
// Renderer loads media as lumina-media://file/<encodeURIComponent(path)>, with
// HTTP Range support so <video> can seek inside multi-GB files.

protocol.registerSchemesAsPrivileged([
  { scheme: 'lumina-media', privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true, bypassCSP: true, corsEnabled: true } }
])

const MIME: Record<string, string> = {
  mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm', mkv: 'video/x-matroska',
  m4a: 'audio/mp4', mp3: 'audio/mpeg', wav: 'audio/wav', aac: 'audio/aac', flac: 'audio/flac', ogg: 'audio/ogg',
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif', bmp: 'image/bmp',
  json: 'application/json'
}

function registerMediaProtocol(): void {
  protocol.handle('lumina-media', (request) => {
    const url = new URL(request.url)
    const path = decodeURIComponent(url.pathname.replace(/^\//, ''))
    let size: number
    try {
      size = statSync(path).size
    } catch {
      return new Response('not found', { status: 404 })
    }
    const ext = path.split('.').pop()?.toLowerCase() ?? ''
    const type = MIME[ext] ?? 'application/octet-stream'
    const range = request.headers.get('range')
    const headers: Record<string, string> = { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Access-Control-Allow-Origin': '*' }
    if (range) {
      const m = /bytes=(\d*)-(\d*)/.exec(range)
      let start = m && m[1] ? Number(m[1]) : 0
      let end = m && m[2] ? Number(m[2]) : size - 1
      if (m && !m[1] && m[2]) {
        start = size - Number(m[2])
        end = size - 1
      }
      end = Math.min(end, size - 1)
      if (start > end || start >= size) {
        return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}` } })
      }
      const stream = Readable.toWeb(createReadStream(path, { start, end })) as ReadableStream
      return new Response(stream, {
        status: 206,
        headers: { ...headers, 'Content-Range': `bytes ${start}-${end}/${size}`, 'Content-Length': String(end - start + 1) }
      })
    }
    const stream = Readable.toWeb(createReadStream(path)) as ReadableStream
    return new Response(stream, { status: 200, headers: { ...headers, 'Content-Length': String(size) } })
  })
}

// ---------------------------------------------------------------- engine process

type EngineInfo = { host: string; port: number; token: string; pid: number }
let engineProc: ChildProcess | null = null
let engineInfo: EngineInfo | null = null
let engineError: string | null = null

function engineDir(): string {
  return process.env.LUMINA_ENGINE_DIR ?? resolve(app.getAppPath(), '..', 'engine')
}

function startEngine(): Promise<EngineInfo> {
  return new Promise((resolvePromise, reject) => {
    const custom = process.env.LUMINA_ENGINE_CMD
    const [cmd, args] = custom
      ? [custom.split(' ')[0], [...custom.split(' ').slice(1), 'serve']]
      : ['uv', ['run', '--project', engineDir(), 'lumina', 'serve']]
    args.push('--parent-pid', String(process.pid))
    const proc = spawn(cmd, args, { cwd: engineDir(), windowsHide: true, env: { ...process.env, PYTHONUNBUFFERED: '1' } })
    engineProc = proc
    let stderr = ''
    proc.stderr?.on('data', (d) => {
      stderr += d.toString()
      process.stderr.write(`[engine] ${d}`)
    })
    const rl = createInterface({ input: proc.stdout! })
    rl.on('line', (line) => {
      try {
        const msg = JSON.parse(line)
        if (msg.ready) {
          engineInfo = { host: msg.host, port: msg.port, token: msg.token, pid: msg.pid }
          resolvePromise(engineInfo)
          return
        }
      } catch {
        /* plain log line */
      }
      console.log(`[engine] ${line}`)
    })
    proc.on('error', (err) => reject(new Error(`could not start engine (${cmd}): ${err.message}`)))
    proc.on('exit', (code) => {
      if (!engineInfo) reject(new Error(`engine exited (${code}): ${stderr.slice(-2000)}`))
      engineProc = null
    })
  })
}

function stopEngine(): void {
  const proc = engineProc
  if (!proc?.pid) return
  if (process.platform === 'win32') {
    // uv spawns python as a child; kill the whole tree
    execFile('taskkill', ['/pid', String(proc.pid), '/T', '/F'], () => undefined)
  } else {
    proc.kill('SIGTERM')
  }
  engineProc = null
}

// ---------------------------------------------------------------- menus
// Premiere's menu structure. Accelerators are display-only
// (registerAccelerator: false): the renderer's keymap owns every shortcut so
// panels can scope them, exactly like Premiere's keyboard shortcut contexts.

type Item = { label: string; action?: string; accel?: string; enabled?: boolean; submenu?: { label: string; action: string }[] } | 'sep'

const MENU: Record<string, Item[]> = {
  File: [
    { label: 'New Project...', action: 'file.newProject', accel: 'Ctrl+Alt+N' },
    { label: 'New Sequence...', action: 'file.newSequence', accel: 'Ctrl+N' },
    { label: 'New Bin', action: 'file.newBin', accel: 'Ctrl+/' },
    'sep',
    { label: 'Open Project...', action: 'file.open', accel: 'Ctrl+O' },
    { label: 'Open Premiere Pro Project (.prproj)...', action: 'file.open' },
    'sep',
    { label: 'Save', action: 'file.save', accel: 'Ctrl+S' },
    { label: 'Save As...', action: 'file.saveAs', accel: 'Ctrl+Shift+S' },
    'sep',
    { label: 'Import...', action: 'file.import', accel: 'Ctrl+I' },
    { label: 'Export Media...', action: 'file.export', accel: 'Ctrl+M' },
    { label: 'Export Frame...', action: 'file.exportFrame', accel: 'Ctrl+Shift+E' },
    'sep',
    { label: 'Exit', action: 'app.quit', accel: 'Ctrl+Q' }
  ],
  Edit: [
    { label: 'Undo', action: 'edit.undo', accel: 'Ctrl+Z' },
    { label: 'Redo', action: 'edit.redo', accel: 'Ctrl+Shift+Z' },
    'sep',
    { label: 'Clear', action: 'edit.clear', accel: 'Delete' },
    { label: 'Ripple Delete', action: 'edit.rippleDelete', accel: 'Shift+Delete' },
    'sep',
    { label: 'Select All', action: 'edit.selectAll', accel: 'Ctrl+A' },
    { label: 'Deselect All', action: 'edit.deselectAll', accel: 'Ctrl+Shift+A' },
    'sep',
    { label: 'Keyboard Shortcuts...', action: 'edit.keyboardShortcuts', accel: 'Ctrl+Alt+K' }
  ],
  Clip: [
    { label: 'Rename...', action: 'clip.rename' },
    { label: 'Speed/Duration...', action: 'clip.speed', accel: 'Ctrl+R' },
    'sep',
    { label: 'Insert', action: 'clip.insert', accel: ',' },
    { label: 'Overwrite', action: 'clip.overwrite', accel: '.' },
    'sep',
    { label: 'Enable', action: 'clip.enable', accel: 'Shift+E' },
    { label: 'Link', action: 'clip.link', accel: 'Ctrl+L' }
  ],
  Sequence: [
    { label: 'Add Edit', action: 'sequence.addEdit', accel: 'Ctrl+K' },
    { label: 'Add Edit to All Tracks', action: 'sequence.addEditAll', accel: 'Ctrl+Shift+K' },
    'sep',
    { label: 'Lift', action: 'sequence.lift', accel: ';' },
    { label: 'Extract', action: 'sequence.extract', accel: "'" },
    'sep',
    { label: 'Ripple Trim Previous Edit to Playhead', action: 'sequence.rippleTrimPrev', accel: 'Q' },
    { label: 'Ripple Trim Next Edit to Playhead', action: 'sequence.rippleTrimNext', accel: 'W' },
    'sep',
    { label: 'Snap in Timeline', action: 'sequence.toggleSnap', accel: 'S' },
    'sep',
    { label: 'Add Tracks...', action: 'sequence.addTracks' }
  ],
  Markers: [
    { label: 'Mark In', action: 'markers.markIn', accel: 'I' },
    { label: 'Mark Out', action: 'markers.markOut', accel: 'O' },
    { label: 'Mark Clip', action: 'markers.markClip', accel: 'X' },
    'sep',
    { label: 'Go to In', action: 'markers.goIn', accel: 'Shift+I' },
    { label: 'Go to Out', action: 'markers.goOut', accel: 'Shift+O' },
    'sep',
    { label: 'Clear In and Out', action: 'markers.clearInOut', accel: 'Ctrl+Shift+X' },
    'sep',
    { label: 'Add Marker', action: 'markers.add', accel: 'M' }
  ],
  'Graphics and Titles': [{ label: 'New Layer > Text', action: 'graphics.newText', accel: 'Ctrl+T' }],
  View: [
    { label: 'Zoom In', action: 'view.zoomIn', accel: '=' },
    { label: 'Zoom Out', action: 'view.zoomOut', accel: '-' },
    { label: 'Zoom to Sequence', action: 'view.zoomFit', accel: '\\' },
    'sep',
    'sep',
    { label: 'Language', submenu: [{ label: '한국어', action: 'view.locale.ko' }, { label: 'English', action: 'view.locale.en' }] },
    'sep',
    { label: 'Toggle Developer Tools', action: 'app.devtools', accel: 'Ctrl+Shift+I' }
  ],
  Window: [
    { label: 'Workspaces > Editing', action: 'window.workspace.editing', accel: 'Alt+Shift+2' },
    { label: 'Reset to Saved Layout', action: 'window.resetLayout', accel: 'Alt+Shift+0' },
    'sep',
    { label: 'Project', action: 'window.project', accel: 'Shift+1' },
    { label: 'Source Monitor', action: 'window.source', accel: 'Shift+2' },
    { label: 'Timelines', action: 'window.timeline', accel: 'Shift+3' },
    { label: 'Program Monitor', action: 'window.program', accel: 'Shift+4' },
    { label: 'Effect Controls', action: 'window.effectControls', accel: 'Shift+5' },
    { label: 'Audio Clip Mixer', action: 'window.audioMixer', accel: 'Shift+6' },
    { label: 'Effects', action: 'window.effects', accel: 'Shift+7' },
    { label: 'Media Browser', action: 'window.mediaBrowser', accel: 'Shift+8' },
    { label: 'Tools', action: 'window.tools' },
    { label: 'Audio Meters', action: 'window.audioMeters' },
    'sep',
    { label: 'Essential Sound', action: 'window.essentialSound' },
    { label: 'Lumetri Color', action: 'window.lumetri' },
    { label: 'Properties', action: 'window.properties' },
    { label: 'Metadata', action: 'window.metadata' },
    { label: 'Text', action: 'window.text' },
    { label: 'Audio Track Mixer', action: 'window.audioTrackMixer' },
    { label: 'Graphic Templates', action: 'window.graphicTemplates' }
  ],
  Help: [{ label: 'Lumina Help', action: 'help.docs' }]
}

function buildMenu(win: BrowserWindow): void {
  const send = (action: string) => () => {
    if (action === 'app.quit') return app.quit()
    if (action === 'app.devtools') return win.webContents.toggleDevTools()
    if (action === 'help.docs') return shell.openExternal('https://github.com/')
    win.webContents.send('menu:action', action)
  }
  const template: MenuItemConstructorOptions[] = Object.entries(MENU).map(([label, items]) => ({
    label: t(label, { menu: true }),
    submenu: items.map((it): MenuItemConstructorOptions =>
      it === 'sep'
        ? { type: 'separator' }
        : it.submenu
          ? { label: t(it.label), submenu: it.submenu.map((sub) => ({ label: sub.label, click: send(sub.action) })) }
          : {
            label: t(it.label),
            enabled: it.enabled ?? true,
            accelerator: it.accel && /^(Ctrl|Alt|Shift)/.test(it.accel) && it.accel.includes('+') ? it.accel.replace('Ctrl', 'CmdOrCtrl') : undefined,
            registerAccelerator: false,
            click: it.action ? send(it.action) : undefined
          }
    )
  }))
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

// ---------------------------------------------------------------- window

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1600,
    height: 960,
    minWidth: 1024,
    minHeight: 640,
    backgroundColor: '#1d1d1d',
    title: 'Lumina',
    show: false,
    webPreferences: {
      preload: join(import.meta.dirname, '../preload/index.cjs'),
      contextIsolation: true,
      sandbox: false
    }
  })
  win.once('ready-to-show', () => {
    win.maximize()
    win.show()
  })
  buildMenu(win)
  // Test hook: LUMINA_CAPTURE=out.png [LUMINA_CAPTURE_DELAY=ms] saves a screenshot.
  // Repeats every LUMINA_CAPTURE_EVERY ms when set (out.png is overwritten).
  if (process.env.LUMINA_CAPTURE) {
    const shot = async () => {
      const img = await win.webContents.capturePage()
      writeFileSync(process.env.LUMINA_CAPTURE!, img.toPNG())
    }
    win.webContents.once('did-finish-load', () => {
      setTimeout(() => {
        void shot()
        const every = Number(process.env.LUMINA_CAPTURE_EVERY || 0)
        if (every) setInterval(() => void shot(), every)
      }, Number(process.env.LUMINA_CAPTURE_DELAY || 6000))
    })
  }
  if (process.env.ELECTRON_RENDERER_URL) {
    win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    win.loadFile(join(import.meta.dirname, '../renderer/index.html'))
  }
  return win
}

ipcMain.handle('engine:info', async () => {
  if (engineInfo) return { ok: true, info: engineInfo }
  return { ok: false, error: engineError ?? 'engine starting' }
})

ipcMain.handle('dialog:open', async (e, opts: Electron.OpenDialogOptions) => {
  const win = BrowserWindow.fromWebContents(e.sender)!
  const res = await dialog.showOpenDialog(win, opts)
  return res.canceled ? [] : res.filePaths
})

ipcMain.handle('dialog:save', async (e, opts: Electron.SaveDialogOptions) => {
  const win = BrowserWindow.fromWebContents(e.sender)!
  const res = await dialog.showSaveDialog(win, opts)
  return res.canceled ? null : res.filePath
})

ipcMain.handle('app:setLocale', (e, locale: Locale) => {
  setLocale(locale)
  const win = BrowserWindow.fromWebContents(e.sender)
  if (win) buildMenu(win)
})

ipcMain.handle('shell:showItem', (_e, path: string) => shell.showItemInFolder(path))
ipcMain.handle('app:videosDir', () => app.getPath('videos'))

ipcMain.handle('window:setTitle', (e, title: string) => {
  BrowserWindow.fromWebContents(e.sender)?.setTitle(title)
})

app.whenReady().then(async () => {
  registerMediaProtocol()
  const enginePromise = startEngine().catch((err: Error) => {
    engineError = err.message
    console.error(err.message)
  })
  createWindow()
  await enginePromise
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  stopEngine()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', stopEngine)
