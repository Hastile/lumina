// GUI end-to-end test: drives the real Electron app with Premiere shortcuts
// and mouse input, then checks the engine document.
//   npm run build && node test/e2e.mjs [screenshot.png]
import { _electron as electron } from 'playwright'
import { join, resolve } from 'node:path'
import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
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

  // ---- graphics: Type tool (T) + click in the Program monitor creates a text layer
  await page.keyboard.press('Home')
  await page.keyboard.press('t')
  const stage = page.locator('.lm-stage')
  const sb = await stage.boundingBox()
  await page.mouse.click(sb.x + sb.width * 0.5, sb.y + sb.height * 0.25)
  const g = await page.waitForFunction(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    for (const tid of seq.video_tracks) for (const c of Object.values(seq.tracks[tid].clips)) if (c.graphic) return { id: c.id, layer: c.graphic.layers[0] }
    return null
  }, null, { timeout: 10000 }).then((h) => h.jsonValue())
  assert.ok(Math.abs(g.layer.position[1] - 720 * 0.25) < 4, `text placed where clicked (${g.layer.position})`)
  log('Type tool created a text layer at', g.layer.position.map(Math.round))

  // edit the text in the Properties panel
  const ta = page.locator('.lm-props textarea').first()
  await ta.fill('자막 테스트')
  await ta.press('Control+Enter')
  await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return Object.values(seq.tracks).some((tr) => tr.clips[id]?.graphic.layers[0].text === '자막 테스트')
  }, g.id, { timeout: 10000 })
  await page.locator('.lm-stage [data-layer]').filter({ hasText: '자막 테스트' }).waitFor()
  log('text edited in Properties and rendered in the Program monitor')

  // drag the selected graphic in the Program monitor -> Motion position changes
  const lb = await page.locator('.lm-stage [data-layer]').first().boundingBox()
  await page.mouse.move(lb.x + lb.width / 2, lb.y + lb.height / 2)
  await page.mouse.down()
  await page.mouse.move(lb.x + lb.width / 2 + 40, lb.y + lb.height / 2 + 20, { steps: 5 })
  await page.mouse.up()
  const moved = await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const c = Object.values(seq.tracks).map((tr) => tr.clips[id]).find(Boolean)
    return c.motion.position && c.motion.position
  }, g.id, { timeout: 10000 }).then((h) => h.jsonValue())
  assert.ok(moved[0] > 640 + 10 && moved[1] > 360 + 5, `dragged position ${moved}`)
  log('dragging in the Program monitor moved the clip to', moved.map(Math.round))

  // keyframes: opacity 100 -> 0 over the first second, rendered by the stage
  const vclipId = await page.evaluate(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return Object.values(seq.tracks[seq.video_tracks[0]].clips).sort((a, b) => a.start - b.start)[0].id
  })
  await page.evaluate((id) => window.__lumina.engine.exec('clip.set_keyframes', { clip_id: id, prop: 'opacity', keyframes: [{ t: 0, v: 100 }, { t: '1s', v: 0 }] }), vclipId)
  await page.keyboard.press('Home')
  for (let i = 0; i < 15; i++) await page.keyboard.press('ArrowRight')
  await page.waitForTimeout(300)
  const op = await page.evaluate((id) => getComputedStyle(document.querySelector(`.lm-stage [data-clip="${id}"]`)).opacity, vclipId)
  assert.ok(Math.abs(Number(op) - 0.5) < 0.02, `opacity at 0.5s should be ~0.5, got ${op}`)
  log('opacity keyframes render: 0.5s ->', op)
  await page.keyboard.press('Control+z')

  // ---- export: Ctrl+M -> Export dialog -> render queue -> verified file
  const outDir = mkdtempSync(join(tmpdir(), 'lumina-e2e-'))
  await page.locator('.lm-tl-lanes').click({ position: { x: 5, y: 2 } })
  await page.keyboard.press('Control+m')
  await page.locator('.lm-export').waitFor()
  await page.locator('.lm-export .preview img').waitFor({ timeout: 15000 })
  await page.locator('#ex-name').fill('e2e export')
  await page.locator('#ex-dir').fill(outDir)
  await page.locator('#ex-range').selectOption('entire')
  await page.locator('.lm-export button[type=submit]').click()
  await page.locator('.lm-renderq .card.done').waitFor({ timeout: 120000 })
  const job = await page.evaluate(() => Object.values(window.__lumina.get().jobs).find((j) => j.kind === 'export'))
  assert.equal(job.status, 'done', job.error)
  assert.ok(existsSync(job.result.path) && job.result.path.startsWith(outDir), job.result.path)
  assert.ok(Math.abs(job.result.verification.durationSeconds - job.result.range.seconds) < 0.05)
  log('Ctrl+M export:', job.result.frames, 'frames,', job.result.encoder, `${job.result.elapsed_seconds}s, verified with ffprobe`)

  // ---- effects: Effects panel (Shift+7) > search > double-click Transform on the selected clip
  const v1clip = await page.evaluate(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return Object.values(seq.tracks[seq.video_tracks[0]].clips).sort((a, b) => a.start - b.start)[0].id
  })
  await page.evaluate((id) => window.__lumina.set({ selectedClips: [id] }), v1clip)
  await page.keyboard.press('Shift+7')
  await page.locator('.lm-fxtree').waitFor()
  await page.locator('.lm-panel .lm-search input').last().fill('변형')
  await page.locator('[data-effect-item="effect:transform"]').dblclick()
  const fx = await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const c = Object.values(seq.tracks).map((t) => t.clips[id]).find(Boolean)
    return c.effects.length ? c.effects[0] : null
  }, v1clip, { timeout: 10000 }).then((h) => h.jsonValue())
  assert.equal(fx.type, 'transform')
  await page.locator(`.lm-ec [data-effect="${fx.id}"]`).waitFor()
  await page.evaluate(([cid, fid]) => window.__lumina.engine.exec('effect.set_param', { clip_id: cid, effect_id: fid, param: 'scale', value: 50 }), [v1clip, fx.id])
  await page.keyboard.press('Home')
  await page.waitForFunction((fid) => (document.querySelector(`.lm-stage [data-fx="${fid}"]`)?.style.transform ?? '').startsWith('matrix(0.5'), fx.id, { timeout: 10000 })
  log('Effects panel: Transform applied by double-click, shown in Effect Controls, rendered in the Program monitor')

  // ---- transitions: Ctrl+D at the edit point nearest the playhead, then select + Delete
  await page.evaluate(() => window.__lumina.engine.exec('clip.razor', { at: '1s' }))
  await page.evaluate(() => window.__lumina.engine.exec('sequence.set_playhead', { time: '1s' }))
  await page.locator('.lm-tl-lanes').click({ position: { x: 5, y: 2 } })
  await page.evaluate(() => window.__lumina.set({ selectedClips: [] }))
  await page.keyboard.press('Control+d')
  const trEl = page.locator('.lm-transition').first()
  await trEl.waitFor({ timeout: 10000 })
  const tr = await page.evaluate(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return Object.values(seq.tracks[seq.video_tracks[0]].transitions)[0]
  })
  assert.ok(tr && tr.clip_a && tr.clip_b && tr.type === 'cross_dissolve', JSON.stringify(tr))
  await page.waitForFunction(() => document.querySelectorAll('.lm-stage .lm-tgroup .lm-tpart').length === 2, null, { timeout: 10000 })
  log('Ctrl+D: Cross Dissolve at the edit point; Program monitor blends both clips')
  await trEl.click()
  await page.keyboard.press('Delete')
  await page.waitForFunction(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return Object.keys(seq.tracks[seq.video_tracks[0]].transitions).length === 0
  }, null, { timeout: 10000 })
  log('transition selected in the timeline and deleted')

  // ---- shapes: Rectangle tool drag in the Program monitor, Ctrl+Alt+R adds to the selected graphic, eye toggle
  await page.keyboard.press('Home')
  await page.evaluate(() => window.__lumina.set({ selectedClips: [], tool: 'rectangle' }))
  const sbox = await page.locator('.lm-stage').boundingBox()
  await page.mouse.move(sbox.x + sbox.width * 0.1, sbox.y + sbox.height * 0.6)
  await page.mouse.down()
  await page.mouse.move(sbox.x + sbox.width * 0.3, sbox.y + sbox.height * 0.9, { steps: 6 })
  await page.mouse.up()
  const shp = await page.waitForFunction(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    for (const tid of seq.video_tracks) for (const c of Object.values(seq.tracks[tid].clips)) {
      const l = c.graphic?.layers.find((x) => x.type === 'shape')
      if (l) return { id: c.id, layer: l }
    }
    return null
  }, null, { timeout: 10000 }).then((h) => h.jsonValue())
  const xs = shp.layer.path.map((v) => v[0])
  assert.ok(Math.abs(Math.max(...xs) - Math.min(...xs) - 1280 * 0.2) < 6, `drawn width ${Math.max(...xs) - Math.min(...xs)}`)
  assert.ok(Math.abs(shp.layer.position[0] - 1280 * 0.2) < 6 && Math.abs(shp.layer.position[1] - 720 * 0.75) < 6, `${shp.layer.position}`)
  await page.locator(`.lm-stage svg[data-layer="${shp.layer.id}"] path`).waitFor()
  log('Rectangle tool: drawn by dragging, rendered as SVG in the Program monitor')
  await page.evaluate((id) => window.__lumina.set({ selectedClips: [id], tool: 'select' }), shp.id)
  await page.keyboard.press('Control+Alt+e')
  await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const c = Object.values(seq.tracks).map((t) => t.clips[id]).find(Boolean)
    return c.graphic.layers.length === 2 && c.graphic.layers[1].shape === 'ellipse'
  }, shp.id, { timeout: 10000 })
  await page.waitForFunction(() => document.querySelectorAll('.lm-stage svg[data-layer]').length === 2, null, { timeout: 10000 })
  await page.locator(`.lm-props [data-glayer="${shp.layer.id}"] .head button`).first().click()
  await page.waitForFunction(() => document.querySelectorAll('.lm-stage svg[data-layer]').length === 1, null, { timeout: 10000 })
  log('Ctrl+Alt+E added an ellipse to the selected graphic; eye toggle hides the rectangle')

  // ---- playback across cuts: same-media razor cuts and a different source; the next clip is
  // prerolled so no visible frame waits on a load or a seek
  const cutProbe = await page.evaluate(async () => {
    const { engine, get, set, play, stop } = window.__lumina
    const media = Object.values(get().doc.media).find((m) => m.video)
    const { sequence_id: sid } = await engine.exec('sequence.create_from_media', { media_id: media.id })
    for (const at of ['1s', '2s', '3s']) await engine.exec('clip.razor', { sequence_id: sid, at })
    // shift the second piece's content so the cut jumps in source time (a same-element seek before the fix)
    const seq = get().doc.sequences[sid]
    const v = Object.values(seq.tracks[seq.video_tracks[0]].clips).sort((a, b) => a.start - b.start)
    await engine.exec('clip.slip', { sequence_id: sid, clip_ids: [v[1].id], delta: '2s' }).catch(() => undefined)
    await engine.exec('sequence.set_playhead', { sequence_id: sid, time: '0.5s' })
    await new Promise((r) => setTimeout(r, 1500))
    const out = { notReady: 0, seeks: 0, frames: 0 }
    const hooked = new WeakSet()
    const hook = (el) => {
      if (hooked.has(el)) return
      hooked.add(el)
      el.addEventListener('seeking', () => { if (!el.paused) out.seeks++ })
    }
    play('program', 1)
    const t0 = performance.now()
    await new Promise((resolve) => {
      const step = () => {
        document.querySelectorAll('.lm-stage video').forEach(hook)
        out.frames++
        for (const el of document.querySelectorAll('.lm-stage .lm-layer[data-clip] video')) if (el.readyState < 2 || el.seeking) out.notReady++
        if (performance.now() - t0 < 3000) requestAnimationFrame(step)
        else resolve()
      }
      requestAnimationFrame(step)
    })
    stop()
    out.time = get().programTime / 254016000000
    return out
  })
  assert.ok(cutProbe.time > 2.5, `playback crossed the cuts (${cutProbe.time})`)
  assert.equal(cutProbe.notReady, 0, `frames waiting on video at cuts: ${JSON.stringify(cutProbe)}`)
  assert.equal(cutProbe.seeks, 0, `seeks while playing: ${JSON.stringify(cutProbe)}`)
  log('playback across 3 cuts: no frame waited on a load or seek', cutProbe.frames, 'frames')

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
