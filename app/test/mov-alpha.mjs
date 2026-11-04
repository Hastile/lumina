// Real ProRes 4444 MOV, original decoder, and visible GPU alpha compositing.
import { _electron as electron } from 'playwright'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { PNG } from 'pngjs'
import assert from 'node:assert/strict'

const work = mkdtempSync(join(tmpdir(), 'lumina-mov-alpha-'))
const png = new PNG({ width: 1920, height: 1080 })
for (let i = 0; i < png.data.length; i += 4) png.data.set([10, 70, 170, 255], i)
const background = join(work, 'background.png')
writeFileSync(background, PNG.sync.write(png))
const mov = process.env.LUMINA_TEST_MOV || 'D:\\유튜브 채널\\Brand\\인트로\\Intro Comp.mov'
const app = await electron.launch({
  ...(process.env.LUMINA_TEST_EXE ? { executablePath: process.env.LUMINA_TEST_EXE } : {}),
  args: process.env.LUMINA_TEST_EXE ? [] : ['.'],
  env: { ...process.env, LUMINA_HOME: work, LUMINA_CACHE: join(work, 'cache'), LUMINA_RECENT: join(work, 'recent.json') }
})
const page = await app.firstWindow()
page.on('pageerror', error => console.error('renderer error:', error.message))
page.on('console', message => { if (message.type() === 'error') console.error(message.text().slice(0, 400)) })
try {
  await page.waitForFunction(() => window.__lumina?.get().connected)
  const decoded = await page.evaluate(async mov => {
    window.__luminaGLScale = 1
    return window.__lumina.decodeBench('lumina-media://file/' + encodeURIComponent(mov), 2, 0.2)
  }, mov)
  console.log('original MOV decode:', decoded)
  assert.equal(decoded.codec, 'prores')
  assert.equal(decoded.w, 1920)
  assert.equal(decoded.h, 1080)
  assert.ok(decoded.frames > 0)
  await page.evaluate(async ({ background, mov }) => {
    const e = window.__lumina.engine
    const imported = await e.exec('media.import', { paths: [background, mov] })
    const seq = await e.exec('sequence.create', { name: 'MOV alpha verification', settings: { width: 1920, height: 1080, fps_num: 30, fps_den: 1 } })
    await e.exec('clip.place', { media_id: imported.media_ids[0], at: '0s', source_out: '5s', video_track_id: seq.video_tracks[0], take_audio: false })
    await e.exec('clip.place', { media_id: imported.media_ids[1], at: '0s', video_track_id: seq.video_tracks[1], take_audio: false })
    await e.exec('sequence.set_playhead', { time: '2s' })
  }, { background, mov })
  await page.waitForFunction(() => {
    const c = document.querySelector('canvas[data-gl]')
    if (!c || c.width !== 1920 || c.height !== 1080) return false
    const gl = c.getContext('webgl2')
    const pixels = new Uint8Array(c.width * c.height * 4)
    gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
    const corner = [...pixels.slice(0, 3)]
    let foreground = 0
    for (let i = 0; i < pixels.length; i += 16)
      if (Math.abs(pixels[i] - 10) + Math.abs(pixels[i + 1] - 70) + Math.abs(pixels[i + 2] - 170) > 80) foreground++
    return corner.every((v, i) => Math.abs(v - [10, 70, 170][i]) < 12) && foreground > 2000
  }, null, { timeout: 30000 })
  await page.screenshot({ path: join(work, 'mov-alpha.png') })
  console.log('PASS: original 1920x1080 ProRes MOV decodes and alpha reveals the underlying blue layer. Screenshot:', join(work, 'mov-alpha.png'))
} finally {
  await page.evaluate(() => window.__lumina.engine.call('project.close_all', { discard: true })).catch(() => {})
  await app.close()
}
