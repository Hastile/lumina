import { _electron as electron } from 'playwright'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import assert from 'node:assert/strict'

const home = mkdtempSync(join(tmpdir(), 'lumina-interactions-'))
const app = await electron.launch({ args: ['.'], env: { ...process.env, LUMINA_HOME: home, LUMINA_RECENT: join(home, 'recent.json') } })
try {
  const page = await app.firstWindow()
  await page.waitForFunction(() => window.__lumina?.get().connected)
  const ids = await page.evaluate(async (path) => {
    const { engine } = window.__lumina
    const m = await engine.exec('media.import', { paths: [path] })
    const s = await engine.exec('sequence.create_from_media', { media_id: m.media_ids[0] })
    const cap = await engine.exec('caption.add', { text: 'original', at: '1s', duration: '2s' })
    const seq = window.__lumina.get().doc.sequences[s.sequence_id]
    const video = Object.values(seq.tracks).flatMap(t => Object.values(t.clips)).find(c => c.media_id === m.media_ids[0])
    return { media: m.media_ids[0], sequence: s.sequence_id, caption: cap.clip_id, video: video.id }
  }, resolve('../samples/test30.mp4'))
  // Drag previews use the current marked source range on both destination tracks.
  await page.evaluate(async ({ media }) => {
    await window.__lumina.engine.exec('media.set_marks', { media_id: media, in_point: 2 * 254016000000, out_point: 5 * 254016000000 })
    window.__lumina.set({ pxPerSecond: 30, scrollX: 0, snap: false, selectedItems: [media] })
  }, ids)
  const transfer = await page.evaluateHandle(() => new DataTransfer())
  const sourceRow = page.locator('.lm-project [draggable=true]').filter({ hasText: 'test30.mp4' }).first()
  await sourceRow.dispatchEvent('dragstart', { dataTransfer: transfer })
  const lanes = page.locator('.lm-tl-lanes')
  const laneBox = await lanes.boundingBox()
  await lanes.dispatchEvent('dragover', { dataTransfer: transfer, clientX: laneBox.x + 40, clientY: laneBox.y + 80 })
  await page.waitForFunction(() => document.querySelectorAll('[data-source-ghost]').length === 2)
  assert.equal(await page.locator('.lm-lane.drop').count(), 0, 'source drag must not highlight the entire lane')
  for (const ghost of await page.locator('[data-source-ghost]').all()) {
    assert.equal(Math.round((await ghost.boundingBox()).width), 89)
  }
  await lanes.dispatchEvent('dragleave', { dataTransfer: transfer })
  await sourceRow.dispatchEvent('dragend', { dataTransfer: transfer })
  await page.evaluate(async ({ media }) => {
    await window.__lumina.engine.exec('media.set_marks', { media_id: media, in_point: null, out_point: null })
  }, ids)
  await page.locator(`.lm-clip[data-clip="${ids.video}"]`).dblclick()
  await page.waitForFunction(() => window.__lumina.get().focusedPanel === 'source')
  assert.equal(await page.evaluate(() => !!window.__lumina.get().sourceClip), true)

  // Caption dialog: blur, outside click, Escape, and window blur all retain edits.
  for (const method of ['blur', 'outside', 'escape', 'window']) {
    await page.locator(`.lm-clip[data-clip="${ids.caption}"]`).dblclick()
    const input = page.locator('.lm-dialog input[type=text]')
    await input.fill(`edited-${method}`)
    if (method === 'blur') await input.press('Tab')
    if (method === 'outside') await page.locator('.lm-dialog-backdrop').click({ position: { x: 2, y: 2 } })
    if (method === 'escape') await input.press('Escape')
    if (method === 'window') await page.evaluate(() => window.dispatchEvent(new Event('blur')))
    await page.waitForFunction(({ sequence, caption, text }) => {
      const seq = window.__lumina.get().doc.sequences[sequence]
      return Object.values(seq.tracks).some(t => t.clips[caption]?.caption?.text === text)
    }, { ...ids, text: `edited-${method}` })
  }
  // Effect Controls numeric scrub must change the document before mouse release.
  await page.locator(`.lm-clip[data-clip="${ids.caption}"]`).click()
  await page.locator('.dv-tab').filter({ hasText: /Effect Controls|효과 컨트롤/ }).first().click()
  await page.locator('.lm-caption-text').fill('saved-on-selection-change')
  await page.locator(`.lm-clip[data-clip="${ids.video}"]`).click()
  await page.waitForFunction(({ sequence, caption }) => {
    const seq = window.__lumina.get().doc.sequences[sequence]
    return Object.values(seq.tracks).some(t => t.clips[caption]?.caption?.text === 'saved-on-selection-change')
  }, ids)
  const value = page.locator('.lm-value').filter({ hasText: /^100\.0$/ }).first()
  await value.waitFor()
  // Clicking selects the whole numeric value; leaving it commits without Enter.
  for (const method of ['tab', 'window', 'selection']) {
    await page.locator('.lm-value').filter({ hasText: /^100\.0$/ }).first().click()
    const input = page.locator('.lm-value-input')
    assert.equal(await input.evaluate((el) => el.selectionStart === 0 && el.selectionEnd === el.value.length), true)
    await input.pressSequentially('125')
    if (method === 'tab') await input.press('Tab')
    if (method === 'window') await page.evaluate(() => window.dispatchEvent(new Event('blur')))
    if (method === 'selection') await page.locator(`.lm-clip[data-clip="${ids.caption}"]`).click()
    await page.waitForFunction(({ sequence, video }) => {
      const seq = window.__lumina.get().doc.sequences[sequence]
      return Object.values(seq.tracks).some(t => t.clips[video]?.motion.scale === 125)
    }, ids)
    await page.evaluate(async ({ video }) => {
      await window.__lumina.engine.exec('clip.set_property', { clip_id: video, prop: 'scale', value: 100 })
    }, ids)
    await page.locator(`.lm-clip[data-clip="${ids.video}"]`).click()
  }
  const box = await value.boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 18, box.y + box.height / 2, { steps: 4 })
  await page.waitForFunction(({ sequence }) => {
    const seq = window.__lumina.get().doc.sequences[sequence]
    return Object.values(seq.tracks).some(t => Object.values(t.clips).some(c => c.media_id && c.motion.scale !== 100))
  }, ids)
  await page.mouse.up()
  // Source handles clamp the ghost before release, then commit exactly that boundary.
  await page.evaluate(async ({ video }) => {
    const { engine, set } = window.__lumina
    await engine.exec('clip.trim', { clip_id: video, edge: 'end', delta: '-4s', include_linked: true })
    set({ pxPerSecond: 30, scrollX: 0, snap: false })
  }, ids)
  const video = page.locator(`.lm-clip[data-clip="${ids.video}"]`)
  await page.waitForTimeout(100)
  const vb = await video.boundingBox()
  await page.mouse.move(vb.x + vb.width - 2, vb.y + vb.height / 2)
  await page.mouse.down()
  await page.mouse.move(vb.x + vb.width + 450, vb.y + vb.height / 2, { steps: 4 })
  await page.waitForTimeout(100)
  const preview = await video.boundingBox()
  assert.ok(Math.abs(preview.width - 360) < 3, `source-limited preview width: ${preview.width}`)
  await page.mouse.up()
  await page.waitForFunction(({ sequence, video }) => {
    const seq = window.__lumina.get().doc.sequences[sequence]
    return Object.values(seq.tracks).some(t => t.clips[video]?.end === 12 * 254016000000)
  }, ids)
  assert.equal(await page.locator('.lm-toast').filter({ hasText: /no more source/i }).count(), 0)
  // Ctrl+K follows selection, not the unrelated targeted video/audio tracks.
  await page.locator(`.lm-clip[data-clip="${ids.caption}"]`).click()
  await page.evaluate(({ caption }) => window.__lumina.set({ selectedClips: [caption], focusedPanel: 'timeline', programTime: 2 * 254016000000 }), ids)
  await page.keyboard.press('Control+k')
  await page.waitForFunction(({ sequence, caption }) => Object.values(window.__lumina.get().doc.sequences[sequence].tracks).some(t => t.clips[caption]?.end === 2 * 254016000000), ids)
  assert.equal(await page.evaluate(({ sequence, video }) => Object.values(window.__lumina.get().doc.sequences[sequence].tracks).find(t => t.clips[video]).clips[video].end, ids), 12 * 254016000000)
  // Source panel uses source time, even when the program playhead is elsewhere.
  await page.locator(`.lm-clip[data-clip="${ids.video}"]`).dblclick()
  await page.evaluate(() => window.__lumina.set({ sourceTime: 254016000000, programTime: 7 * 254016000000 }))
  await page.keyboard.press('Control+k')
  await page.waitForFunction(({ sequence, video }) => Object.values(window.__lumina.get().doc.sequences[sequence].tracks).some(t => t.clips[video]?.end === 254016000000), ids)
  console.log('PASS: source focus, caption focus exits, live numeric scrub')
} finally {
  await app.evaluate(({ BrowserWindow }) => { for (const w of BrowserWindow.getAllWindows()) w.destroy() })
  await app.close()
}
