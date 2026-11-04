import { _electron as electron } from 'playwright'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import assert from 'node:assert/strict'

const home = mkdtempSync(join(tmpdir(), 'lumina-multi-properties-'))
const app = await electron.launch({ args: ['.'], env: { ...process.env, LUMINA_HOME: home, LUMINA_RECENT: join(home, 'recent.json') } })
try {
  const page = await app.firstWindow()
  await page.waitForFunction(() => window.__lumina?.get().connected)
  const ids = await page.evaluate(async (path) => {
    const { engine } = window.__lumina
    const imported = await engine.exec('media.import', { paths: [path] })
    const media = imported.media_ids[0]
    const { sequence_id } = await engine.exec('sequence.create_from_media', { media_id: media })
    await engine.exec('clip.place', { sequence_id, media_id: media, at: '12s', take_audio: false })
    const seq = window.__lumina.get().doc.sequences[sequence_id]
    const clips = Object.values(seq.tracks[seq.video_tracks[0]].clips).sort((a, b) => a.start - b.start)
    for (let i = 0; i < clips.length; i++) {
      await engine.exec('clip.set_property', { clip_id: clips[i].id, prop: 'position', value: [100 + 100 * i, 300 + 100 * i] })
      await engine.exec('effect.add', { clip_ids: [clips[i].id], type: 'lumetri' })
    }
    window.__lumina.set({ selectedClips: clips.map(c => c.id), selectedTrackId: null })
    return { sequence_id, clips: clips.map(c => c.id) }
  }, resolve('../samples/test30.mp4'))
  await page.locator('.dv-tab').filter({ hasText: /Effect Controls|효과 컨트롤/ }).first().click()
  const panel = page.locator('[data-multi-properties]').filter({ visible: true }).first()
  const row = (name) => panel.locator('.lm-ec-prop').filter({ has: page.locator('.pname', { hasText: name }) }).first()
  const pos = row(/^(Position|위치)$/)
  assert.deepEqual(await pos.locator('.lm-value').allTextContents(), ['-', '-'])
  const common = panel.locator('.lm-value').filter({ hasText: /^100\.0$/ }).first()
  assert.equal(await common.textContent(), '100.0')
  await pos.locator('.lm-value').first().click()
  const input = pos.locator('input')
  assert.equal(await input.evaluate(el => el.selectionStart === 0 && el.selectionEnd === el.value.length), true)
  await input.pressSequentially('250')
  await input.press('Tab')
  await page.waitForFunction(({ sequence_id, clips }) => {
    const s = window.__lumina.get(), seq = s.doc.sequences[sequence_id]
    return !s.history.in_transaction && clips.every(id => Object.values(seq.tracks).some(t => t.clips[id]?.motion.position?.[0] === 250))
  }, ids)
  const positions = () => page.evaluate(({ sequence_id, clips }) => {
    const seq = window.__lumina.get().doc.sequences[sequence_id]
    return clips.map(id => Object.values(seq.tracks).find(t => t.clips[id]).clips[id].motion.position)
  }, ids)
  assert.deepEqual(await positions(), [[250, 300], [250, 400]])
  assert.deepEqual(await pos.locator('.lm-value').allTextContents(), ['250.0 X', '-'])
  await page.evaluate(() => window.__lumina.engine.call('undo'))
  assert.deepEqual(await positions(), [[100, 300], [200, 400]])
  const exposure = row(/^(Exposure|노출)$/)
  await exposure.locator('.lm-value').click()
  await exposure.locator('input').fill('1.5')
  await exposure.locator('input').press('Tab')
  await page.waitForFunction(({ sequence_id, clips }) => {
    const s = window.__lumina.get(), seq = s.doc.sequences[sequence_id]
    return !s.history.in_transaction && clips.every(id => Object.values(seq.tracks).some(t => t.clips[id]?.effects.some(f => f.type === 'lumetri' && f.params.exposure === 1.5)))
  }, ids)
  // Blur caused by switching selection must commit to the original selection.
  await pos.locator('.lm-value').first().click()
  await pos.locator('input').fill('350')
  await page.evaluate(({ clips }) => window.__lumina.set({ selectedClips: [clips[0]] }), ids)
  await page.waitForFunction(({ sequence_id, clips }) => {
    const s = window.__lumina.get(), seq = s.doc.sequences[sequence_id]
    return !s.history.in_transaction && clips.every(id => Object.values(seq.tracks).some(t => t.clips[id]?.motion.position?.[0] === 350))
  }, ids)
  console.log('PASS: common/mixed values, bulk edit, preserve other axis, one-step undo, selection-exit commit')
} finally {
  await app.evaluate(({ BrowserWindow }) => { for (const w of BrowserWindow.getAllWindows()) w.destroy() })
  await app.close()
}
