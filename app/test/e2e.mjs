// GUI end-to-end test: drives the real Electron app with Premiere shortcuts
// and mouse input, then checks the engine document.
//   npm run build && node test/e2e.mjs [screenshot.png]
import { _electron as electron } from 'playwright'
import { resolve } from 'node:path'
import assert from 'node:assert/strict'

const SAMPLE = resolve('../samples/test30.mp4')
const shot = process.argv[2]
const TPS = 254016000000

const app = await electron.launch({ args: ['.'], env: { ...process.env } })
const page = await app.firstWindow()
const log = (...a) => console.log('·', ...a)
const state = () => page.evaluate(() => {
  const s = window.__lumina.get()
  const seq = s.doc?.active_sequence_id ? s.doc.sequences[s.doc.active_sequence_id] : null
  const clips = (kind) => seq ? seq[kind].flatMap((id) => Object.values(seq.tracks[id].clips)).sort((a, b) => a.start - b.start).map((c) => [c.start, c.end, c.source_in]) : []
  return { connected: s.connected, time: s.programTime, video: clips('video_tracks'), audio: clips('audio_tracks'), inPoint: seq?.in_point, outPoint: seq?.out_point, undo: s.history.undo_label }
})
const until = async (fn, what, ms = 20000) => {
  const t0 = Date.now()
  for (;;) {
    const s = await state()
    if (fn(s)) return s
    if (Date.now() - t0 > ms) throw new Error(`timeout waiting for ${what}: ${JSON.stringify(s)}`)
    await page.waitForTimeout(100)
  }
}
await page.waitForFunction(() => !!window.__lumina, null, { timeout: 30000 })
let failed = false
try {
  await until((s) => s.connected, 'engine')
  log('engine connected')
  await page.evaluate(async (p) => {
    const { engine } = window.__lumina
    const r = await engine.exec('media.import', { paths: [p] })
    await engine.exec('sequence.create_from_media', { media_id: r.media_ids[0] })
  }, SAMPLE)
  await until((s) => s.video.length === 1, 'sequence with clip')
  await page.locator('.lm-clip.video').first().waitFor()
  log('sequence created from media (12s clip)')

  // zoom to sequence so pixel math is simple
  await page.locator('.lm-tl-lanes').click({ position: { x: 5, y: 2 } })
  await page.keyboard.press('Backslash')
  await page.waitForTimeout(200)

  // Razor tool (C): cut at the middle of the video clip -> linked audio is cut too
  await page.keyboard.press('c')
  const vclip = page.locator('.lm-clip.video').first()
  const box = await vclip.boundingBox()
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
  let s = await until((s) => s.video.length === 2 && s.audio.length === 2, 'razor on linked clips')
  log('razor (C + click) split video and audio at', (s.video[0][1] / TPS).toFixed(2), 's')

  // Selection tool (V), select the 1st clip, Shift+Delete = ripple delete
  await page.keyboard.press('v')
  const first = page.locator('.lm-clip.video').first()
  const fb = await first.boundingBox()
  await page.mouse.click(fb.x + 20, fb.y + fb.height / 2)
  await page.keyboard.press('Shift+Delete')
  s = await until((s) => s.video.length === 1, 'ripple delete')
  assert.equal(s.video[0][0], 0, 'remaining clip rippled to 0')
  assert.equal(s.audio.length, 1, 'linked audio removed too')
  log('ripple delete closed the gap; remaining clip starts at 0, source_in', (s.video[0][2] / TPS).toFixed(2), 's')

  // Ctrl+Z restores, Ctrl+Shift+Z redoes
  await page.keyboard.press('Control+z')
  s = await until((s) => s.video.length === 2, 'undo')
  await page.keyboard.press('Control+Shift+z')
  s = await until((s) => s.video.length === 1, 'redo')
  await page.keyboard.press('Control+z')
  s = await until((s) => s.video.length === 2, 'undo again')
  log('undo / redo / undo ok')

  // Home, then frame steps with arrows
  await page.keyboard.press('Home')
  for (let i = 0; i < 5; i++) await page.keyboard.press('ArrowRight')
  s = await until((s) => Math.round(s.time / (TPS / 30)) === 5, 'step 5 frames')
  log('Home + 5x Right = frame', Math.round(s.time / (TPS / 30)))

  // Mark In at 1s, Out at 3s via ruler scrub, then Extract (')
  const ruler = page.locator('.lm-ruler')
  const rb = await ruler.boundingBox()
  const lanes = await page.locator('.lm-tl-lanes').boundingBox()
  const pps = await page.evaluate(() => window.__lumina.get().pxPerSecond)
  const xAt = (sec) => lanes.x + sec * pps
  await page.mouse.click(xAt(1.0) + 1, rb.y + rb.height / 2)
  await page.keyboard.press('i')
  await page.mouse.click(xAt(3.0) + 1, rb.y + rb.height / 2)
  await page.keyboard.press('o')
  s = await until((s) => s.inPoint != null && s.outPoint != null, 'in/out marks')
  log('In/Out set:', (s.inPoint / TPS).toFixed(3), '-', (s.outPoint / TPS).toFixed(3))
  const before = s.video.at(-1)[1]
  await page.keyboard.press("'")
  s = await until((s) => s.video.at(-1)[1] < before, 'extract')
  log('Extract removed', ((before - s.video.at(-1)[1]) / TPS).toFixed(3), 's; undo label:', s.undo)
  assert.equal(s.undo, 'Extract', 'extract + clear marks is one undo step')

  // Space plays, Space stops
  await page.keyboard.press('Home')
  await page.keyboard.press('Space')
  await page.waitForTimeout(1200)
  await page.keyboard.press('Space')
  s = await state()
  assert.ok(s.time > TPS * 0.8, `playback advanced (${s.time / TPS}s)`)
  log('playback advanced to', (s.time / TPS).toFixed(2), 's')

  // after stopping, the Program monitor's video must sit exactly on the playhead frame
  await page.waitForTimeout(600)
  const check = await page.evaluate(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const t = s.programTime
    const clip = seq.video_tracks.flatMap((id) => Object.values(seq.tracks[id].clips)).find((c) => c.start <= t && t < c.end)
    const expected = (clip.source_in + (t - clip.start)) / 254016000000
    const v = [...document.querySelectorAll('.lm-frame video')].find((el) => !el.paused || el.readyState >= 2)
    return { expected, actual: v?.currentTime, seeking: v?.seeking }
  })
  assert.ok(Math.abs(check.actual - check.expected) < 0.005, `monitor frame ${check.actual} vs ${check.expected}`)
  log('program monitor on exact frame:', check.expected.toFixed(3), 's')

  if (shot) await page.screenshot({ path: shot })
  log('PASS')
} catch (err) {
  failed = true
  console.error('FAIL', err.message)
  if (shot) await page.screenshot({ path: shot }).catch(() => {})
} finally {
  const proc = app.process()
  await Promise.race([app.close(), new Promise((r) => setTimeout(r, 5000))])
  try { proc.kill() } catch {}
  process.exit(failed ? 1 : 0)
}
