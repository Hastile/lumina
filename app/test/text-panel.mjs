import { _electron as electron } from 'playwright'
import { resolve, join } from 'node:path'
import { mkdirSync } from 'node:fs'
import assert from 'node:assert/strict'
const home = resolve('../.tmp/text-panel-ui-' + Date.now())
mkdirSync(home, { recursive: true })
const app = await electron.launch({ ...(process.env.LUMINA_TEST_EXE ? { executablePath: process.env.LUMINA_TEST_EXE, args: [] } : { args: ['.'] }), env: { ...process.env, LUMINA_HOME: home, LUMINA_RECENT: join(home, 'recent.json') } })
const page = await app.firstWindow()
const errors = []
page.on('pageerror', e => errors.push(e.message))
try {
  await page.waitForFunction(() => window.__lumina?.get().connected, null, { timeout: 45000 })
  assert.equal(await page.evaluate(async () => (await window.__lumina.engine.call('speech.status')).available), true)
  const ids = await page.evaluate(async () => {
    const e = window.__lumina.engine
    const sid = (await e.exec('sequence.create', { name: '텍스트 편집 검수' })).sequence_id
    const tid = (await e.exec('caption.add_track', { name: '기본 자막' })).track_id
    const cid = (await e.exec('caption.add', { track_id: tid, text: '앞 문구 뒤 문구', at: '0s', duration: '4s' })).clip_id
    const tid2 = (await e.exec('caption.add_track', { name: '강조 자막' })).track_id
    await e.exec('caption.add', { track_id: tid2, text: '다른 트랙도 함께 표시', at: '5s', duration: '3s' })
    await e.exec('graphic.create_text', { text: '그래픽 제목', at: '0s', duration: '4s' })
    window.__lumina.set({ programTime: 2 * 254016000000 })
    return { sid, tid, cid }
  })
  const tab = page.locator('.dv-tab').filter({ hasText: /^(텍스트|Text)$/ })
  await tab.first().click()
  const panel = page.locator('[data-text-panel]')
  await panel.waitFor({ state: 'visible' })
  assert.equal(await panel.locator('[data-text-kind="captions"]').count(), 2)
  const field = panel.getByLabel('자막 문구').first()
  await field.focus()
  await field.evaluate(el => el.setSelectionRange(4, 4))
  await page.keyboard.press('Enter')
  await page.waitForFunction(() => {
    const s = window.__lumina.get(); const seq = s.doc.sequences[s.doc.active_sequence_id]
    return Object.values(seq.tracks).flatMap(t => Object.values(t.clips)).filter(c => c.caption).length === 3
  })
  assert.equal(await panel.getByLabel('자막 문구').nth(0).inputValue(), '앞 문구')
  assert.equal(await panel.getByLabel('자막 문구').nth(1).inputValue(), '뒤 문구')
  await page.waitForFunction(() => document.activeElement?.value === '뒤 문구')
  await page.keyboard.press('Backspace')
  await page.waitForFunction(() => document.querySelectorAll('[data-text-kind="captions"]').length === 2)
  assert.equal(await panel.getByLabel('자막 문구').first().inputValue(), '앞 문구 뒤 문구')
  await page.waitForFunction(() => document.activeElement?.value === '앞 문구 뒤 문구' && document.activeElement.selectionStart === 4)
  await page.keyboard.press('Enter')
  await page.waitForFunction(() => document.querySelectorAll('[data-text-kind="captions"]').length === 3)
  await page.evaluate(() => window.__lumina.engine.call('undo'))
  await page.waitForFunction(() => document.querySelectorAll('[data-text-kind="captions"]').length === 2)
  await page.evaluate(() => window.__lumina.engine.call('redo'))
  await page.waitForFunction(() => document.querySelectorAll('[data-text-kind="captions"]').length === 3)
  await panel.getByLabel('텍스트 트랙').selectOption(ids.tid)
  assert.equal(await panel.locator('[data-text-kind="captions"]').count(), 2)
  await panel.getByRole('tab', { name: /그래픽/ }).click()
  assert.equal(await panel.getByLabel('그래픽 문구').inputValue(), '그래픽 제목')
  await panel.getByRole('tab', { name: '대본', exact: true }).click()
  await panel.getByRole('button', { name: '모든 캡션에서 대본 가져오기' }).click()
  assert.match(await panel.getByLabel('대본', { exact: true }).inputValue(), /앞 문구\n뒤 문구\n다른 트랙도 함께 표시/)
  await panel.getByRole('tab', { name: /캡션/ }).click()
  await panel.getByLabel('자막 문구').first().focus()
  await panel.getByLabel('자막 문구').first().evaluate(el => el.setSelectionRange(1, 1))
  await page.keyboard.press('Shift+Enter')
  await page.keyboard.insertText('.')
  await page.waitForFunction(() => {
    const seq = window.__lumina.get().doc.sequences[window.__lumina.get().doc.active_sequence_id]
    return Object.values(seq.tracks).flatMap(t => Object.values(t.clips)).some(c => c.caption?.text === '앞\n. 문구')
  })
  assert.equal(await panel.locator('[data-text-kind="captions"]').count(), 3)
  assert.equal(await panel.getByLabel('자막 문구').first().evaluate(el => el === document.activeElement), true)
  await page.evaluate(async ({ tid }) => {
    for (let i = 0; i < 20; i++) await window.__lumina.engine.exec('caption.add', { track_id: tid, text: `재생 추적 ${i}`, at: `${10+i*2}s`, duration: '2s' })
    window.__lumina.set({ programTime: 44 * 254016000000, playing: 'program' })
  }, ids)
  await page.waitForFunction(() => {
    const list = document.querySelector('[data-playing-caption]')
    const row = list?.querySelector(`[data-text-row="${list.dataset.playingCaption}"]`)
    return row && Math.abs(row.getBoundingClientRect().top - list.getBoundingClientRect().top) < 3
  })
  await page.evaluate(() => window.__lumina.set({ playing: null }))
  await panel.screenshot({ path: join(home, 'text-panel.png') })
  assert.deepEqual(errors, [])
  console.log('PASS: Enter split, Backspace merge, Shift+Enter, autosave without blur, playback follow, undo/redo, track filters, script import')
  console.log('Screenshot:', join(home, 'text-panel.png'))
} finally { await app.evaluate(({ app }) => app.exit(0)).catch(() => {}); await app.close().catch(() => {}) }
