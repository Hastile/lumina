// Shared overlay UI: modal dialogs, toasts, context menus.
import { useEffect, useRef, useState } from 'react'
import { create } from 'zustand'
import { closeDialog, useDialog } from '../state/dialog'
import { useStore } from '../state/store'
import { runAction } from '../actions/actions'
import { DEFAULT_KEYMAP } from '../actions/keymap'

// ---------------------------------------------------------------- dialog

export function DialogHost() {
  const dialog = useDialog((s) => s.dialog)
  const [values, setValues] = useState<Record<string, string | number | boolean>>({})
  const first = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (!dialog) return
    setValues(Object.fromEntries(dialog.fields.map((f) => [f.key, f.value])))
    setTimeout(() => first.current?.select(), 0)
  }, [dialog])

  if (!dialog) return null
  const submit = async () => {
    const d = dialog
    closeDialog()
    await d.onSubmit(values)
  }
  return (
    <div className="lm-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && closeDialog()}>
      <form
        className="lm-dialog"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
        onKeyDown={(e) => e.key === 'Escape' && closeDialog()}
      >
        <h3>{dialog.title}</h3>
        <div className="body">
          {dialog.fields.map((f, i) => f.type === 'note' ? (
            <div key={f.key} className="note">{f.value}</div>
          ) : (
            <FieldRow key={f.key}>
              <label htmlFor={`f-${f.key}`}>{f.label}</label>
              {f.type === 'select' ? (
                <select id={`f-${f.key}`} value={String(values[f.key] ?? '')} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}>
                  {f.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              ) : f.type === 'checkbox' ? (
                <input id={`f-${f.key}`} type="checkbox" checked={Boolean(values[f.key])} onChange={(e) => setValues({ ...values, [f.key]: e.target.checked })} />
              ) : (
                <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <input
                    id={`f-${f.key}`}
                    ref={i === 0 ? first : undefined}
                    autoFocus={i === 0}
                    type={f.type}
                    min={f.type === 'number' ? f.min : undefined}
                    max={f.type === 'number' ? f.max : undefined}
                    step={f.type === 'number' ? f.step : undefined}
                    value={String(values[f.key] ?? '')}
                    onChange={(e) => setValues({ ...values, [f.key]: f.type === 'number' ? Number(e.target.value) : e.target.value })}
                    style={{ width: f.type === 'number' ? 90 : 240 }}
                  />
                  {f.type === 'number' && f.suffix}
                </span>
              )}
            </FieldRow>
          ))}
        </div>
        <div className="actions">
          <button type="button" className="lm-btn" onClick={closeDialog}>
            Cancel
          </button>
          <button type="submit" className="lm-btn primary">
            {dialog.okLabel ?? 'OK'}
          </button>
        </div>
      </form>
    </div>
  )
}

function FieldRow({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}

// ---------------------------------------------------------------- toasts

export function Toasts() {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="lm-toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`lm-toast ${t.kind}`}>
          {t.text}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- context menu

export type MenuItem = { label: string; action?: string; run?: () => void; disabled?: boolean } | 'sep'

const useCtx = create<{ menu: { x: number; y: number; items: MenuItem[] } | null }>(() => ({ menu: null }))

export function openContextMenu(e: React.MouseEvent | MouseEvent, items: MenuItem[]): void {
  e.preventDefault()
  e.stopPropagation()
  useCtx.setState({ menu: { x: e.clientX, y: e.clientY, items } })
}

const keyFor = (action?: string) => (action ? Object.entries(DEFAULT_KEYMAP).find(([, a]) => a === action)?.[0] : undefined)

export function ContextMenuHost() {
  const menu = useCtx((s) => s.menu)
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ x: 0, y: 0 })
  useEffect(() => {
    if (!menu) return
    const close = () => useCtx.setState({ menu: null })
    const r = ref.current?.getBoundingClientRect()
    setPos({
      x: Math.min(menu.x, window.innerWidth - (r?.width ?? 240) - 4),
      y: Math.min(menu.y, window.innerHeight - (r?.height ?? 300) - 4)
    })
    window.addEventListener('mousedown', close)
    window.addEventListener('blur', close)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('blur', close)
    }
  }, [menu])
  if (!menu) return null
  return (
    <div className="lm-ctx" ref={ref} style={{ left: pos.x, top: pos.y }} onMouseDown={(e) => e.stopPropagation()}>
      {menu.items.map((it, i) =>
        it === 'sep' ? (
          <div key={i} className="sep" />
        ) : (
          <div
            key={i}
            className={`item ${it.disabled ? 'disabled' : ''}`}
            onClick={() => {
              if (it.disabled) return
              useCtx.setState({ menu: null })
              if (it.run) it.run()
              else if (it.action) runAction(it.action)
            }}
          >
            <span>{it.label}</span>
            <span className="key">{keyFor(it.action)}</span>
          </div>
        )
      )}
    </div>
  )
}
