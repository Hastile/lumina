import { _electron as electron } from 'playwright'
import { mkdtempSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import assert from 'node:assert/strict'

const home = mkdtempSync(join(tmpdir(), 'lumina-source-preview-'))
const wav = join(home, 'audio.wav')
const silent = join(home, 'silent.mp4')
execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', wav])
execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:d=4', '-an', silent])
const app = await electron.launch({ args: ['.'], env: { ...process.env, LUMINA_HOME: home, LUMINA_RECENT: join(home, 'recent.json') } })
try {
  const page = await app.firstWindow()
  await page.waitForFunction(() => window.__lumina?.get().connected)
  const ids = await page.evaluate(async (paths) => (await window.__lumina.engine.exec('media.import', { paths })).media_ids,
    [resolve('../samples/test30.mp4'), wav, silent])
  const open = async (id) => {
    await page.evaluate((id) => window.__lumina.set({ sourceMediaId: id, sourceClip: null, sourceTime: 0, focusedPanel: 'source' }), id)
    await page.locator('.dv-tab').filter({ hasText: /Source|소스/ }).first().click()
  }
  await open(ids[0])
  const video = page.getByRole('button', { name: '비디오 미리보기', exact: true })
  const audio = page.getByRole('button', { name: '오디오 파형', exact: true })
  assert.equal(await video.getAttribute('aria-pressed'), 'true')
  await audio.click()
  await page.locator('.lm-source-waveform').waitFor()
  await page.waitForFunction(() => !document.querySelector('.wave-status'), { timeout: 30000 })
  await video.click()
  assert.equal(await page.locator('.lm-source-waveform').count(), 0)
  await open(ids[1])
  assert.equal(await video.isDisabled(), true)
  assert.equal(await audio.getAttribute('aria-pressed'), 'true')
  await page.waitForFunction(() => !document.querySelector('.wave-status'))
  await page.evaluate(async (id) => {
    await window.__lumina.engine.exec('media.set_marks', { media_id: id, in_point: 254016000000, out_point: 3 * 254016000000 })
  }, ids[1])
  await page.waitForFunction(() => document.querySelector('.lm-source-waveform .range-edge.in')?.style.left === '25%')
  assert.equal(await page.locator('.lm-source-waveform .range-edge.out').evaluate(el => el.style.left), '75%')
  const wave = page.locator('.lm-source-waveform')
  const box = await wave.boundingBox()
  await wave.click({ position: { x: box.width / 2, y: box.height / 2 } })
  assert.ok(Math.abs(await page.evaluate(() => window.__lumina.get().sourceTime / 254016000000) - 2) < .1)
  assert.equal(await wave.locator('canvas').first().evaluate(el => {
    const pixels = el.getContext('2d').getImageData(0, 0, el.width, el.height).data
    return pixels.some((v, i) => i % 4 === 3 && v > 0)
  }), true, 'waveform must contain drawn samples')
  await page.screenshot({ path: join(home, 'waveform.png') })
  await open(ids[2])
  assert.equal(await audio.isDisabled(), true)
  assert.equal(await video.getAttribute('aria-pressed'), 'true')
  console.log(`PASS: preview modes, waveform pixels, In/Out range, seek, disabled controls. Screenshot: ${join(home, 'waveform.png')}`)
} finally {
  await app.evaluate(({ BrowserWindow }) => { for (const w of BrowserWindow.getAllWindows()) w.destroy() })
  await app.close()
}
