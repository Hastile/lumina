// Edit > Keyboard Shortcuts (Ctrl+Alt+K): every action with its keys, searchable; click + and press a
// key to bind it (a key belongs to one action, so it moves), x to unbind, reset to Premiere defaults.
import { useEffect, useMemo, useState } from 'react'
import { Plus, RotateCcw, Search, X } from 'lucide-react'
import { create } from 'zustand'
import { t } from '@shared/i18n'
import { allActions } from '../actions/actions'
import { currentKeymap, DEFAULT_KEYMAP, keyString, keysOf, loadOverrides, saveOverrides } from '../actions/keymap'

const useShortcuts = create<{ open: boolean }>(() => ({ open: false }))
export const openShortcuts = () => useShortcuts.setState({ open: true })

const GROUPS: [string, string][] = [
  ['file', 'File'], ['edit', 'Edit'], ['clip', 'Clip'], ['sequence', 'Sequence'], ['markers', 'Markers'], ['graphics', 'Graphics and Titles'],
  ['transport', 'Playback'], ['tool', 'Tools'], ['view', 'View'], ['window', 'Window']
]

const MOD_ONLY = new Set(['Control', 'Shift', 'Alt', 'Meta'])

function setBinding(key: string, action: string | null): void {
  const o = loadOverrides()
  const def = DEFAULT_KEYMAP[key] ?? null
  if (action === def) delete o[key]
  else o[key] = action
  saveOverrides(o)
}

export function ShortcutsDialog() {
  const open = useShortcuts((s) => s.open)
  const [q, setQ] = useState('')
  const [capture, setCapture] = useState<string | null>(null)
  const [, bump] = useState(0)
  const [note, setNote] = useState<string | null>(null)
  const actions = useMemo(() => allActions(), [open])
  useEffect(() => {
    if (!capture) return
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape') return setCapture(null)
      if (MOD_ONLY.has(e.key)) return
      const key = keyString(e)
      const prev = currentKeymap()[key]
      setBinding(key, capture)
      const label = prev && prev !== capture ? actions.find((a) => a.id === prev)?.label ?? prev : null
      setNote(label ? `${key}: ${t('removed from')} ${label}` : null)
      setCapture(null)
      bump((n) => n + 1)
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [capture, actions])
  if (!open) return null
  const map = currentKeymap()
  const close = () => {
    setCapture(null)
    setNote(null)
    useShortcuts.setState({ open: false })
  }
  const ql = q.trim().toLowerCase()
  const shown = actions.filter((a) => !ql || a.label.toLowerCase().includes(ql) || a.id.includes(ql) || keysOf(a.id, map).some((k) => k.toLowerCase().includes(ql)))
  const group = (id: string) => GROUPS.find(([p]) => id.startsWith(p + '.'))?.[1] ?? 'Other'
  const groups = [...GROUPS.map(([, g]) => g), 'Other'].map((g) => [g, shown.filter((a) => group(a.id) === g)] as const).filter(([, list]) => list.length)
  return (
    <div className="lm-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="lm-dialog lm-shortcuts" data-shortcuts="">
        <h3>{t('Keyboard Shortcuts')}</h3>
        <div className="bar">
          <Search size={13} />
          <input autoFocus placeholder={t('Search')} value={q} onChange={(e) => setQ(e.target.value)} />
          <button
            className="lm-btn"
            data-action="reset-keys"
            onClick={() => {
              saveOverrides({})
              setNote(null)
              bump((n) => n + 1)
            }}
          >
            <RotateCcw size={12} /> {t('Premiere Pro Default')}
          </button>
        </div>
        {note && <div className="note">{note}</div>}
        <div className="list">
          {groups.map(([g, list]) => (
            <div key={g}>
              <div className="group">{t(g)}</div>
              {list.map((a) => (
                <div key={a.id} className="row" data-action-id={a.id}>
                  <span className="label">{a.label}</span>
                  <span className="keys">
                    {keysOf(a.id, map).map((k) => (
                      <span key={k} className={`chip ${DEFAULT_KEYMAP[k] === a.id ? '' : 'custom'}`} data-key={k}>
                        {k}
                        <X
                          size={10}
                          onClick={() => {
                            setBinding(k, null)
                            bump((n) => n + 1)
                          }}
                        />
                      </span>
                    ))}
                    <button className={`add ${capture === a.id ? 'on' : ''}`} title={t('Add Shortcut')} onClick={() => setCapture(capture === a.id ? null : a.id)}>
                      {capture === a.id ? t('Press a key...') : <Plus size={11} />}
                    </button>
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="actions">
          <button className="lm-btn primary" onClick={close}>
            {t('OK')}
          </button>
        </div>
      </div>
    </div>
  )
}
