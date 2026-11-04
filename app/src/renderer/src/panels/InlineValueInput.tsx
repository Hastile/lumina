import { useLayoutEffect, useRef, useState } from 'react'

/** One edit session: preserve its target even if selection changes before unmount. */
export function InlineValueInput({ initialValue, className, onCommit, onClose }: {
  initialValue: string; className: string; onCommit: (value: string) => void; onClose: () => void
}) {
  const [value, setValue] = useState(initialValue)
  const session = useRef({ value: initialValue, initialValue, onCommit, finished: false })
  const commit = () => {
    const s = session.current
    if (s.finished || s.value === s.initialValue) return
    s.finished = true
    s.onCommit(s.value)
  }
  useLayoutEffect(() => {
    const blur = () => { commit(); onClose() }
    window.addEventListener('blur', blur)
    return () => { window.removeEventListener('blur', blur); commit() }
  }, [])
  return <input className={className} autoFocus value={value}
    onFocus={(e) => e.currentTarget.select()}
    onChange={(e) => { session.current.value = e.target.value; setValue(e.target.value) }}
    onBlur={() => { commit(); onClose() }}
    onKeyDown={(e) => {
      if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); commit(); onClose() }
      if (e.key === 'Escape') { e.preventDefault(); session.current.finished = true; onClose() }
    }} />
}
