// History panel (Premiere's, but dependable): every undoable step, oldest first. Click a step to
// undo or redo back to it; the steps after it stay listed (grey) until a new edit replaces them.
import { useState } from 'react'
import { t } from '@shared/i18n'
import { call, useStore } from '../state/store'

export function HistoryPanel() {
  const h = useStore((s) => s.history)
  const [busy, setBusy] = useState(false)
  const undo = h.undo_labels ?? []
  const redo = h.redo_labels ?? []
  // go to step i: 0 = before the first listed step, undo.length = now
  const goTo = async (i: number) => {
    if (busy || h.in_transaction) return
    setBusy(true)
    try {
      for (let k = undo.length; k > i; k--) if ((await call('undo')) === null) break
      for (let k = undo.length; k < i; k++) if ((await call('redo')) === null) break
    } finally {
      setBusy(false)
    }
  }
  // steps an agent or a script made carry the command name ("clip.set_speed"): show it readably
  const label = (l: string) =>
    /^[a-z_]+\.[a-z_]+$/.test(l) ? `${l.split('.')[1].replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())} · ${l.split('.')[0]}` : t(l)
  return (
    <div className="lm-panel lm-history" data-history="">
      {h.undo_hidden ? <div className="hidden">… {t('{n} earlier steps').replace('{n}', String(h.undo_hidden))}</div> : null}
      <div className={`step ${undo.length === 0 ? 'current' : ''}`} data-step={0} onClick={() => void goTo(0)}>
        <span className="n" />
        {t('Open / New')}
      </div>
      {undo.map((l, i) => (
        <div key={`u${i}`} className={`step ${i === undo.length - 1 ? 'current' : ''}`} data-step={i + 1} title={t('Undo to here')} onClick={() => void goTo(i + 1)}>
          <span className="n">{(h.undo_hidden ?? 0) + i + 1}</span>
          {label(l)}
        </div>
      ))}
      {redo.map((l, i) => (
        <div key={`r${i}`} className="step redo" data-step={undo.length + i + 1} onClick={() => void goTo(undo.length + i + 1)}>
          <span className="n">{(h.undo_hidden ?? 0) + undo.length + i + 1}</span>
          {label(l)}
        </div>
      ))}
    </div>
  )
}
