// GUI end-to-end test: drives the real Electron app with Premiere shortcuts
// and mouse input, then checks the engine document.
//   npm run build && node test/e2e.mjs [screenshot.png]
import { _electron as electron } from 'playwright'
import { join, resolve } from 'node:path'
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import assert from 'node:assert/strict'

const SAMPLE = resolve('../samples/test30.mp4')
const shot = process.argv[2]
const TPS = 254016000000

// an isolated preset library holding one Transform preset (scale 70 -> 100 over 0.2 s, anchored to the in point)
const PRESETS = join(mkdtempSync(join(tmpdir(), 'lumina-presets-')), 'presets.json')
writeFileSync(PRESETS, JSON.stringify({ version: 1, presets: [{
  id: 'e2e-pop', name: '팝인', folder: ['쇼츠'], type: 'anchor_in', anchor_in: 0, anchor_out: TPS, speed: 1,
  effects: [{ type: 'transform', enabled: true, params: { position: [0.5, 0.5], anchor: [0.5, 0.5], scale: 70 },
    keyframes: { scale: [{ t: 0, v: 70, interp: 'bezier', ease_out: [0, 1 / 3] }, { t: TPS / 5, v: 100, interp: 'bezier', ease_in: [0, 1 / 3] }] } }]
}] }))
const WORK = mkdtempSync(join(tmpdir(), 'lumina-recent-'))
const app = await electron.launch({ args: ['.'], env: { ...process.env, LUMINA_PRESETS: PRESETS, LUMINA_RECENT: join(WORK, 'recent.json') } })
const page = await app.firstWindow()
const log = (...a) => console.log('·', ...a)
page.on('pageerror', (e) => console.log('[pageerror]', e.message, (e.stack ?? '').split('\n').slice(0, 6).join(' | ')))
page.on('console', (m) => { if (m.type() === 'error' || /\[lumina\]/.test(m.text())) console.log('[console]', m.text().slice(0, 300)) })
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
  // the document patch and the history label arrive as separate events
  s = await until((s) => s.video.at(-1)[1] < before && s.undo === 'Extract', 'extract')
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

  // after stopping, the Program monitor shows exactly the source frame active at the playhead
  // (WebCodecs path: the canvas records the decoded frame's timestamp)
  const check = await page.waitForFunction(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const t = s.programTime
    const clip = seq.video_tracks.flatMap((id) => Object.values(seq.tracks[id].clips)).find((c) => c.start <= t && t < c.end)
    const want = (clip.source_in + (t - clip.start)) / 254016000000
    const c = document.querySelector('.lm-stage canvas[data-gl]')
    if (!c || c.dataset.exact !== '1' || !c.dataset.frames) return null
    const frame = Number(c.dataset.frames.split(',')[0])
    return frame <= want + 1e-4 && want - frame < 1 / 30 ? { want, frame } : null // frames are written with 4 decimals
  }, null, { timeout: 10000, polling: 'raf' }).then((h) => h.jsonValue()).catch(async (e) => {
    const c = await page.evaluate(() => { const c = document.querySelector('.lm-stage canvas[data-gl]'); const s = window.__lumina.get(); return { t: s.programTime / 254016000000, playing: s.playing, exact: c?.dataset.exact, frames: c?.dataset.frames } })
    throw new Error(`${e.message} ${JSON.stringify(c)}`)
  })
  log('program monitor on the exact frame:', check.frame.toFixed(3), 's for source time', check.want.toFixed(3))

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

  // ---- Effect Controls twirl-down: drag a velocity handle -> the keyframe becomes Bezier with that ease
  await page.evaluate((id) => window.__lumina.engine.exec('clip.set_keyframes', {
    clip_id: id, prop: 'opacity', keyframes: [{ t: 0, v: 0 }, { t: '1s', v: 100 }]
  }), v1clip)
  await page.locator('.lm-ec [data-twirl="불투명도"]').first().click()
  const grip = page.locator('.lm-ec [data-expanded="불투명도"] svg[data-graph="velocity"] [data-handle="0-out"]')
  await grip.waitFor()
  const gb = await grip.boundingBox()
  await page.mouse.move(gb.x + gb.width / 2, gb.y + gb.height / 2)
  await page.mouse.down()
  await page.mouse.move(gb.x + gb.width / 2 + 10, gb.y + gb.height / 2 + 12, { steps: 4 })
  await page.mouse.up()
  const eased = await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const k = Object.values(seq.tracks).map((t) => t.clips[id]).find(Boolean).keyframes.opacity[0]
    return k.interp === 'bezier' ? k : null
  }, v1clip, { timeout: 10000 }).then((h) => h.jsonValue())
  assert.ok(eased.ease_out[0] < 100 && eased.ease_out[1] > 1 / 3, `ease_out ${eased.ease_out}`)
  // Ctrl+wheel zooms the keyframe lane, double-click resets it
  const lane = page.locator('.lm-ec .lm-ec-prop .lane').first()
  await lane.hover()
  await page.keyboard.down('Control')
  await page.mouse.wheel(0, -100)
  await page.keyboard.up('Control')
  const zoomed = await page.evaluate(() => window.__lumina.get().ecView)
  assert.ok(zoomed && zoomed.clipId === v1clip, 'Ctrl+wheel zooms the Effect Controls lane')
  await lane.dblclick()
  assert.equal(await page.evaluate(() => window.__lumina.get().ecView), null)
  // keyframe icons, right-click Temporal Interpolation, marquee select, drag, Delete
  const opKfs = () => page.evaluate((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return Object.values(seq.tracks).map((t) => t.clips[id]).find(Boolean).keyframes.opacity ?? null
  }, v1clip)
  const kfIcon = (i) => page.locator('.lm-ec [data-kf^="p:opacity@"]').nth(i)
  await kfIcon(0).click({ button: 'right' })
  await page.locator('.lm-ctx .item', { hasText: '자동 베지어' }).click()
  await page.waitForFunction(() => document.querySelector('.lm-ec [data-kf^="p:opacity@"] svg')?.dataset.shape === 'auto-auto', null, { timeout: 10000 })
  assert.equal((await opKfs())[0].interp, 'auto_bezier')
  await kfIcon(1).click({ button: 'right' })
  await page.locator('.lm-ctx .item', { hasText: '정지' }).click()
  await page.waitForFunction(() => document.querySelectorAll('.lm-ec [data-kf^="p:opacity@"] svg')[1]?.dataset.shape === 'linear-hold', null, { timeout: 10000 })
  const laneBox = await kfIcon(0).locator('xpath=..').boundingBox()
  const k0 = await kfIcon(0).boundingBox()
  await page.mouse.move(laneBox.x + 1, k0.y - 3)
  await page.mouse.down()
  await page.mouse.move(laneBox.x + laneBox.width + 10, k0.y + k0.height + 3, { steps: 5 })
  assert.ok(await page.locator('.lm-kmarquee').isVisible(), 'the marquee rectangle is drawn')
  await page.mouse.up()
  const sel = await page.evaluate(() => window.__lumina.get().kfSel?.keys ?? [])
  assert.equal(sel.filter((k) => k.startsWith('p:opacity@')).length, 2, `marquee selected ${sel}`)
  const kfBefore = await opKfs()
  const k1 = await kfIcon(1).boundingBox()
  await page.mouse.move(k1.x + k1.width / 2, k1.y + k1.height / 2)
  await page.mouse.down()
  await page.mouse.move(k1.x + k1.width / 2 + 25, k1.y + k1.height / 2, { steps: 5 })
  await page.mouse.up()
  const movedKfs = await page.waitForFunction((args) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const k = Object.values(seq.tracks).map((t) => t.clips[args.id]).find(Boolean).keyframes.opacity
    return k && k[0].t > args.t0 ? k : null
  }, { id: v1clip, t0: kfBefore[0].t }, { timeout: 10000 }).then((h) => h.jsonValue())
  assert.equal(movedKfs[1].t - movedKfs[0].t, kfBefore[1].t - kfBefore[0].t, 'both selected keyframes moved together')
  await page.locator('.lm-ec .lm-ec-title').click()
  await page.keyboard.press('Delete')
  await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return !Object.values(seq.tracks).map((t) => t.clips[id]).find(Boolean).keyframes.opacity
  }, v1clip, { timeout: 10000 })
  log('keyframes: right-click Auto Bezier / Hold (icons auto-auto, linear-hold), marquee selected 2, dragged', ((movedKfs[0].t - kfBefore[0].t) / TPS).toFixed(3), 's, Delete removed them')
  for (let i = 0; i < 6; i++) await page.evaluate(() => window.__lumina.engine.call('undo'))
  assert.equal((await opKfs())?.length ?? 0, 0, 'six undo steps restore the opacity property')
  // Shift+drag on the ruler snaps the playhead to an edit point
  const edge = await page.evaluate(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return Object.values(seq.tracks[seq.video_tracks[0]].clips).map((c) => c.end).sort((a, b) => a - b)[0]
  })
  const rulerBox = await page.locator('.lm-ruler').boundingBox()
  const { pxPerSecond: rulerPps, scrollX } = await page.evaluate(() => window.__lumina.get())
  const ex = rulerBox.x + (edge / TPS) * rulerPps - scrollX + 4
  await page.keyboard.down('Shift')
  await page.mouse.click(ex, rulerBox.y + rulerBox.height / 2)
  await page.keyboard.up('Shift')
  assert.equal(await page.evaluate(() => window.__lumina.get().programTime), edge, 'Shift snaps the playhead to the edit')
  await page.mouse.click(ex, rulerBox.y + rulerBox.height / 2)
  assert.notEqual(await page.evaluate(() => window.__lumina.get().programTime), edge, 'without Shift it does not snap')
  log('Shift+click on the ruler snapped the playhead to the edit at', (edge / TPS).toFixed(3), 's')
  log('Effect Controls: twirl-down velocity graph drag set ease_out', eased.ease_out.map((x) => x.toFixed(2)).join(', '), '; lane zoom + reset')

  // ---- WYSIWYG: the GPU-composited Program monitor matches the export renderer pixel for pixel
  await page.keyboard.press('Shift+4')
  await page.locator('.lm-stage canvas[data-gl]').waitFor({ state: 'attached' })
  const wysiwyg = await page.evaluate(async () => {
    const { engine, get } = window.__lumina
    const s = get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const t = s.programTime
    const clip = seq.video_tracks.flatMap((id) => Object.values(seq.tracks[id].clips)).find((c) => c.start <= t && t < c.end && c.media_id)
    const { effect_ids: [tf] } = await engine.exec('effect.add', { sequence_id: seq.id, clip_ids: [clip.id], type: 'transform' })
    await engine.exec('effect.set_param', { sequence_id: seq.id, clip_id: clip.id, effect_id: tf, param: 'rotation', value: 10 })
    const { effect_ids: [bl] } = await engine.exec('effect.add', { sequence_id: seq.id, clip_ids: [clip.id], type: 'gaussian_blur' })
    await engine.exec('effect.set_param', { sequence_id: seq.id, clip_id: clip.id, effect_id: bl, param: 'blurriness', value: 25 })
    window.__luminaGLScale = 1
    const W = seq.settings.width
    const H = seq.settings.height
    const c = document.querySelector('.lm-stage canvas[data-gl]')
    for (let i = 0; i < 100 && !(c.width === W && c.dataset.exact === '1'); i++) await new Promise((r) => setTimeout(r, 50))
    window.__lumina.set({ programTime: t + 1 })
    window.__lumina.set({ programTime: t })
    await new Promise((r) => setTimeout(r, 400))
    const png = c.toDataURL('image/png')
    const out = await engine.call('export.frame', { sequence_id: seq.id, time: t })
    delete window.__luminaGLScale
    await engine.call('undo')
    await engine.call('undo')
    await engine.call('undo')
    await engine.call('undo')
    return { png, path: out.path, W, H }
  })
  {
    const { PNG } = await import('pngjs')
    {
      const { readFileSync } = await import('node:fs')
      const a = PNG.sync.read(Buffer.from(wysiwyg.png.split(',')[1], 'base64'))
      const b = PNG.sync.read(readFileSync(wysiwyg.path))
      let sum = 0
      for (let i = 0; i < a.data.length; i += 4) for (let k = 0; k < 3; k++) sum += Math.abs(a.data[i + k] - b.data[i + k])
      const mean = sum / (a.width * a.height * 3)
      assert.ok(mean < 2, `GPU monitor vs export mean pixel difference ${mean.toFixed(2)}`)
      log('Program monitor (GPU) vs export with Transform + Gaussian Blur: mean pixel difference', mean.toFixed(2))
    }
  }

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
  // panel switches above relayout the monitor: wait until the stage stops moving
  let sbox = await page.locator('.lm-stage').boundingBox()
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(100)
    const nb = await page.locator('.lm-stage').boundingBox()
    if (JSON.stringify(nb) === JSON.stringify(sbox)) break
    sbox = nb
  }
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
    // count monitor refreshes where a visible clip did not show its exact frame
    const run = async (rate, ms) => {
      const out = { frames: 0, inexact: 0, canvases: 0 }
      play('program', rate)
      const t0 = performance.now()
      await new Promise((resolve) => {
        const step = () => {
          out.frames++
          const lv = window.__lumina.audio.levels()
          out.peak = Math.max(out.peak ?? 0, lv.master[0], lv.master[1])
          const c = document.querySelector('.lm-stage canvas[data-gl]')
          if (c) out.canvases = 1
          if (c && c.dataset.exact !== '1') {
            out.inexact++
            if (performance.now() - t0 > 800) out.late = (out.late ?? 0) + 1 // past the decoder warm-up
            ;(out.at ??= []).push(`${(get().programTime / 254016000000).toFixed(3)} frames ${c.dataset.frames}`)
          }
          if (performance.now() - t0 < ms) requestAnimationFrame(step)
          else resolve()
        }
        requestAnimationFrame(step)
      })
      stop()
      out.time = get().programTime / 254016000000
      return out
    }
    const a0 = { ...window.__lumina.audio.stats }
    let elements = 0
    const watch = setInterval(() => (elements = Math.max(elements, document.querySelectorAll('.lm-stage audio').length)), 20)
    const fwd = await run(1, 3000)
    clearInterval(watch)
    fwd.audio = { nodes: window.__lumina.audio.stats.nodes - a0.nodes, late: window.__lumina.audio.stats.late - a0.late, elements }
    await new Promise((r) => setTimeout(r, 500))
    const rev = await run(-1, 1500) // J: reverse, decoded in chunks from keyframes
    return { fwd, rev }
  })
  assert.ok(cutProbe.fwd.canvases > 0, 'the Program monitor composites on the GPU')
  assert.ok(cutProbe.fwd.time > 2.5, `playback crossed the cuts (${cutProbe.fwd.time})`)
  assert.equal(cutProbe.fwd.inexact, 0, `forward play across cuts showed a stale frame: ${JSON.stringify(cutProbe)}`)
  assert.ok(cutProbe.rev.time < cutProbe.fwd.time - 1, `J plays backwards (${cutProbe.rev.time})`)
  assert.ok(!cutProbe.rev.late, `reverse play only waits while it starts: ${JSON.stringify(cutProbe.rev)}`)
  assert.ok(cutProbe.fwd.audio.nodes > 0 && cutProbe.fwd.audio.elements === 0, `audio plays through Web Audio: ${JSON.stringify(cutProbe.fwd.audio)}`)
  log('audio: Web Audio buffers scheduled', cutProbe.fwd.audio.nodes, 'late joins', cutProbe.fwd.audio.late)
  assert.ok(cutProbe.fwd.peak > 0.001, `the Mix is metered while playing (peak ${cutProbe.fwd.peak})`)

  // ---- audio: track mixer fader, volume rubber band, Audio Gain (G), scrubbing
  const audioState = () => page.evaluate(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const a1 = seq.tracks[seq.audio_tracks[0]]
    return { a1: a1.volume_db, pan: a1.pan, clips: Object.values(a1.clips).sort((x, y) => x.start - y.start).map((c) => ({ id: c.id, vol: c.volume_db, gain: c.gain_db ?? 0 })) }
  })
  await page.locator('.dv-tab', { hasText: '오디오 트랙 믹서' }).click()
  const knob = page.locator('[data-mixer="track"] [data-fader="A1"] .knob')
  await knob.waitFor()
  const kb = await knob.boundingBox()
  await page.mouse.move(kb.x + kb.width / 2, kb.y + kb.height / 2)
  await page.mouse.down()
  await page.mouse.move(kb.x + kb.width / 2, kb.y + kb.height / 2 + 20, { steps: 4 })
  await page.mouse.up()
  await page.waitForFunction(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return seq.tracks[seq.audio_tracks[0]].volume_db < 0
  }, null, { timeout: 10000 })
  const afterFader = await audioState()
  const band = page.locator(`[data-vband="${afterFader.clips[0].id}"] .hit`)
  const bb = await page.locator(`[data-vband="${afterFader.clips[0].id}"]`).boundingBox()
  const by = bb.y + 4 + (1 - Math.pow(10, -6 / 40)) * (bb.height - 8)
  await page.mouse.move(bb.x + Math.min(30, bb.width / 2), by)
  await page.mouse.down()
  await page.mouse.move(bb.x + Math.min(30, bb.width / 2), by + 8, { steps: 4 })
  await page.mouse.up()
  await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return seq.tracks[seq.audio_tracks[0]].clips[id].volume_db < 0
  }, afterFader.clips[0].id, { timeout: 10000 })
  assert.ok(await band.count(), 'audio clips show the volume rubber band')
  await page.evaluate((id) => window.__lumina.set({ selectedClips: [id], focusedPanel: 'timeline' }), afterFader.clips[1].id)
  await page.keyboard.press('g')
  await page.locator('.lm-dialog select').selectOption('adjust')
  await page.locator('.lm-dialog input[type="number"]').fill('3')
  await page.locator('.lm-dialog button[type="submit"]').click()
  await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return seq.tracks[seq.audio_tracks[0]].clips[id].gain_db === 3
  }, afterFader.clips[1].id, { timeout: 10000 })
  const scrubs0 = await page.evaluate(() => window.__lumina.audio.stats.scrubs)
  await page.locator('.lm-tl-lanes').click({ position: { x: 5, y: 2 } })
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.waitForFunction((n) => window.__lumina.audio.stats.scrubs >= n + 2, scrubs0, { timeout: 5000 })
  // audio effects: the preview's Web Audio EQ is the export's biquad (+6 dB at the centre frequency),
  // and a Hard Limiter on the selected audio clip shows its settings in Effect Controls
  const eqDb = await page.evaluate(async () => {
    const run = async (fx) => {
      const ctx = new OfflineAudioContext(2, 48000, 48000)
      const o = ctx.createOscillator()
      o.frequency.value = 1000
      window.__lumina.buildFx(ctx, fx, o, ctx.destination)
      o.start()
      const d = (await ctx.startRendering()).getChannelData(0).slice(4800, 43200)
      return 10 * Math.log10(d.reduce((a, x) => a + x * x, 0) / d.length)
    }
    return (await run([{ type: 'parametric_eq', params: { low_freq: 100, low_gain: 0, mid_freq: 1000, mid_gain: 6, mid_q: 1, high_freq: 8000, high_gain: 0 } }])) - (await run([]))
  })
  assert.ok(Math.abs(eqDb - 6) < 0.05, `EQ +6 dB at 1 kHz (${eqDb})`)
  await page.evaluate((id) => window.__lumina.engine.exec('effect.add', { clip_ids: [id], type: 'hard_limiter' }), afterFader.clips[1].id)
  await page.evaluate((id) => window.__lumina.set({ selectedClips: [id] }), afterFader.clips[1].id)
  await page.locator('.dv-tab', { hasText: '효과 컨트롤' }).click()
  await page.locator('.lm-panel.lm-ec .lm-ec-group', { hasText: '하드 리미터' }).locator('.pname', { hasText: '최대 진폭' }).waitFor()
  // Essential Sound: tag the selected audio clip, then Loudness Auto-Match sets its gain from LUFS
  await page.locator('[data-essential-sound] [data-tag="sfx"]').click()
  await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return seq.tracks[seq.audio_tracks[0]].clips[id].audio_tag === 'sfx'
  }, afterFader.clips[1].id, { timeout: 10000 })
  await page.locator('[data-essential-sound] [data-action="auto-match"]').click()
  await page.locator('[data-essential-sound] .results .res').first().waitFor({ timeout: 30000 })
  const matched = await page.locator('[data-essential-sound] .results .res').first().innerText()
  assert.match(matched, /LUFS/, matched)
  const fin = await audioState()
  log('audio: EQ preview', eqDb.toFixed(2), 'dB; Mix peak', cutProbe.fwd.peak.toFixed(3), '; A1 fader', fin.a1, 'dB; rubber band', fin.clips[0].vol, 'dB; G adjust gain', fin.clips[1].gain, 'dB; arrow keys scrub; Essential Sound', matched.replace(/\s+/g, ' '))
  log('forward across 3 cuts: every frame exact', cutProbe.fwd.frames, 'refreshes; reverse:', cutProbe.rev.inexact, 'warm-up refreshes')

  // ---- presets: Effects panel > Presets > 쇼츠 > 팝인, double-click applies to the selected clip
  const pclip = await page.evaluate(() => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    return Object.values(seq.tracks[seq.video_tracks[0]].clips).sort((a, b) => a.start - b.start)[0]
  })
  await page.evaluate((id) => window.__lumina.set({ selectedClips: [id] }), pclip.id)
  await page.keyboard.press('Shift+7')
  await page.locator('.lm-panel .lm-search input').last().fill('팝인')
  await page.locator('[data-effect-item="preset:e2e-pop"]').dblclick()
  const pfx = await page.waitForFunction((id) => {
    const s = window.__lumina.get()
    const seq = s.doc.sequences[s.doc.active_sequence_id]
    const c = Object.values(seq.tracks).map((t) => t.clips[id]).find(Boolean)
    return c.effects.find((f) => f.keyframes?.scale) ?? null
  }, pclip.id, { timeout: 10000 }).then((h) => h.jsonValue())
  assert.equal(pfx.keyframes.scale[0].t, pclip.source_in, 'anchored to the clip in point')
  assert.equal(pfx.keyframes.scale[0].interp, 'bezier')
  log('preset from the Presets folder applied with Bezier keyframes at the clip start')

  // ---- File > Save As remembers the project; File > Open starts in that folder; Open Recent reopens it
  const saved = join(WORK, 'e2e project.lumina')
  await app.evaluate(({ dialog }, target) => {
    globalThis.__dialogs = []
    dialog.showSaveDialog = async (_w, o) => { globalThis.__dialogs.push(o); return { canceled: false, filePath: target } }
    dialog.showOpenDialog = async (_w, o) => { globalThis.__dialogs.push(o); return { canceled: true, filePaths: [] } }
  }, saved)
  const menuAction = (id) => app.evaluate(({ BrowserWindow }, a) => BrowserWindow.getAllWindows()[0].webContents.send('menu:action', a), id)
  await menuAction('file.saveAs')
  await page.waitForFunction((p) => window.__lumina.get().path === p, saved, { timeout: 10000 })
  const recentLabels = async () => app.evaluate(({ Menu }) => {
    const file = Menu.getApplicationMenu().items[0].submenu
    const recent = file.items.find((i) => i.submenu && i.submenu.items.some((x) => x.label.includes('.lumina') || x.label.startsWith('(')))
    return recent ? recent.submenu.items.map((x) => x.label) : null
  })
  await page.waitForFunction(() => true)
  let labels = await recentLabels()
  for (let i = 0; i < 20 && !(labels ?? []).includes(saved); i++) { await page.waitForTimeout(100); labels = await recentLabels() }
  assert.ok(labels?.includes(saved), `Open Recent lists the saved project: ${JSON.stringify(labels)}`)
  await menuAction('file.open')
  await page.waitForTimeout(300)
  const opened = await app.evaluate(() => globalThis.__dialogs.at(-1))
  assert.equal(opened.defaultPath, WORK, 'File > Open starts in the last project folder')
  await page.evaluate(() => window.__lumina.engine.call('project.new', { name: 'Other' }))
  await page.waitForFunction(() => window.__lumina.get().path == null, null, { timeout: 10000 })
  await app.evaluate(({ Menu }, p) => {
    const file = Menu.getApplicationMenu().items[0].submenu
    const item = file.items.flatMap((i) => i.submenu ? i.submenu.items : []).find((x) => x.label === p)
    item.click()
  }, saved)
  await page.waitForFunction((p) => window.__lumina.get().path === p, saved, { timeout: 10000 })
  log('Save As -> Open Recent lists it, File > Open starts in its folder, Open Recent reopens it')

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
