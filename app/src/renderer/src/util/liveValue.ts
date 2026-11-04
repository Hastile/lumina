/** Coalesce pointer events to one update per frame, and flush the final value. */
export function liveValue(commit: (value: number) => void) {
  let frame: number | null = null
  let pending: number | null = null
  let sent: number | null = null
  const flush = () => {
    if (frame !== null) cancelAnimationFrame(frame)
    frame = null
    if (pending !== null && pending !== sent) {
      sent = pending
      commit(pending)
    }
    pending = null
  }
  return {
    update(value: number) {
      pending = value
      if (frame === null) frame = requestAnimationFrame(flush)
    },
    finish(value: number) { pending = value; flush() }
  }
}
