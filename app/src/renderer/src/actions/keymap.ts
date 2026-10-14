// Premiere Pro default keyboard shortcuts (Windows), mapped to action ids.
// docs/premiere-parity.md tracks coverage.

import { runAction } from './actions'

export const DEFAULT_KEYMAP: Record<string, string> = {
  // File
  'Ctrl+Alt+N': 'file.newProject',
  'Ctrl+N': 'file.newSequence',
  'Ctrl+/': 'file.newBin',
  'Ctrl+O': 'file.open',
  'Ctrl+S': 'file.save',
  'Ctrl+Shift+S': 'file.saveAs',
  'Ctrl+I': 'file.import',
  'Ctrl+T': 'graphics.newText',
  'Ctrl+Alt+R': 'graphics.newRectangle',
  'Ctrl+Alt+E': 'graphics.newEllipse',
  'Ctrl+M': 'file.export',
  'Ctrl+Shift+E': 'file.exportFrame',
  // Edit
  'Ctrl+Z': 'edit.undo',
  'Ctrl+Shift+Z': 'edit.redo',
  Delete: 'edit.clear',
  Backspace: 'edit.clear',
  'Shift+Delete': 'edit.rippleDelete',
  'Shift+Backspace': 'edit.rippleDelete',
  'Ctrl+A': 'edit.selectAll',
  'Ctrl+Shift+A': 'edit.deselectAll',
  // Clip
  'Ctrl+R': 'clip.speed',
  ',': 'clip.insert',
  '.': 'clip.overwrite',
  'Shift+E': 'clip.enable',
  'Ctrl+L': 'clip.link',
  // Sequence
  'Ctrl+K': 'sequence.addEdit',
  'Ctrl+D': 'sequence.applyVideoTransition',
  'Ctrl+Shift+D': 'sequence.applyAudioTransition',
  'Shift+D': 'sequence.applyDefaultTransitions',
  'Ctrl+Shift+K': 'sequence.addEditAll',
  ';': 'sequence.lift',
  "'": 'sequence.extract',
  Q: 'sequence.rippleTrimPrev',
  W: 'sequence.rippleTrimNext',
  S: 'sequence.toggleSnap',
  // Markers
  I: 'markers.markIn',
  O: 'markers.markOut',
  X: 'markers.markClip',
  'Shift+I': 'markers.goIn',
  'Shift+O': 'markers.goOut',
  'Ctrl+Shift+X': 'markers.clearInOut',
  M: 'markers.add',
  // Transport
  Space: 'transport.playStop',
  J: 'transport.shuttleLeft',
  K: 'transport.shuttleStop',
  L: 'transport.shuttleRight',
  ArrowLeft: 'transport.stepBack',
  ArrowRight: 'transport.stepForward',
  'Shift+ArrowLeft': 'transport.stepBack5',
  'Shift+ArrowRight': 'transport.stepForward5',
  Home: 'transport.goStart',
  End: 'transport.goEnd',
  ArrowUp: 'transport.prevEdit',
  ArrowDown: 'transport.nextEdit',
  // Tools
  V: 'tool.select',
  A: 'tool.trackSelect',
  B: 'tool.ripple',
  N: 'tool.rolling',
  R: 'tool.rate',
  C: 'tool.razor',
  Y: 'tool.slip',
  U: 'tool.slide',
  P: 'tool.pen',
  H: 'tool.hand',
  Z: 'tool.zoom',
  T: 'tool.type',
  // View
  '=': 'view.zoomIn',
  '-': 'view.zoomOut',
  '\\': 'view.zoomFit',
  // Window
  'Shift+1': 'window.project',
  'Shift+2': 'window.source',
  'Shift+3': 'window.timeline',
  'Shift+4': 'window.program',
  'Shift+5': 'window.effectControls',
  'Shift+6': 'window.audioMixer',
  'Shift+7': 'window.effects',
  'Shift+8': 'window.mediaBrowser',
  '`': 'window.maximize',
  'Alt+Shift+0': 'window.resetLayout'
}

/** Normalize a KeyboardEvent to "Ctrl+Shift+K" form (layout-independent for letters/digits). */
export function keyString(e: KeyboardEvent): string {
  let key: string
  if (e.code.startsWith('Key')) key = e.code.slice(3)
  else if (e.code.startsWith('Digit')) key = e.code.slice(5)
  else if (e.code === 'Space') key = 'Space'
  else {
    const map: Record<string, string> = {
      Comma: ',', Period: '.', Semicolon: ';', Quote: "'", Equal: '=', Minus: '-', Backslash: '\\', Backquote: '`', Slash: '/',
      NumpadAdd: '=', NumpadSubtract: '-'
    }
    key = map[e.code] ?? e.key
  }
  const mods = [e.ctrlKey || e.metaKey ? 'Ctrl' : '', e.altKey ? 'Alt' : '', e.shiftKey ? 'Shift' : ''].filter(Boolean)
  return [...mods, key].join('+')
}

export function installKeymap(keymap: Record<string, string> = DEFAULT_KEYMAP): () => void {
  const onKey = (e: KeyboardEvent) => {
    const target = e.target as HTMLElement | null
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable)) return
    if (document.querySelector('.lm-dialog-backdrop')) return
    const action = keymap[keyString(e)]
    if (!action) return
    e.preventDefault()
    e.stopPropagation()
    runAction(action)
  }
  window.addEventListener('keydown', onKey, true)
  const off = window.lumina.onMenuAction((id) => runAction(id))
  return () => {
    window.removeEventListener('keydown', onKey, true)
    off()
  }
}
