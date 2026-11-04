import { _electron as electron } from 'playwright'
import { resolve, join } from 'node:path'
import { mkdirSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import assert from 'node:assert/strict'
const home=resolve('../.tmp/source-review-live-'+Date.now());mkdirSync(home,{recursive:true})
const source=join(home,'review.mp4'),catalog=join(home,'catalog.db')
execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','color=red:s=320x180:r=30:d=1','-f','lavfi','-i','color=blue:s=320x180:r=30:d=1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264','-pix_fmt','yuv420p',source],{windowsHide:true})
execFileSync(resolve('../engine/.venv/Scripts/python.exe'),['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
c.executescript("""CREATE TABLE ip_entity(ip_id,canonical_name_ko,title_native,title_english,danbooru_tag,needs_label);
CREATE TABLE character_entity(character_id,ip_id,canonical_name_ko,name_native,name_english,danbooru_tag,needs_label,is_active);
CREATE TABLE entity_alias(entity_type,entity_id,alias,is_active,confidence);
INSERT INTO ip_entity VALUES(1,'테스트 작품','Test Work','Test Work','test_work',0);
INSERT INTO character_entity VALUES(1,1,'인물 A','A','A','char_a',0,1);
INSERT INTO character_entity VALUES(2,1,'인물 B','B','B','char_b',0,1);
""")
c.commit()`,catalog],{windowsHide:true})
const app=await electron.launch({...process.env.LUMINA_TEST_EXE?{executablePath:process.env.LUMINA_TEST_EXE,args:[]}:{args:['.']},env:{...process.env,LUMINA_HOME:home,LUMINA_RECENT:join(home,'recent.json'),LUMINA_SOURCE_DB:join(home,'source.db'),LUMINA_ENTITY_CATALOG:catalog}})
const page=await app.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(10000)
try{
  await page.waitForFunction(()=>window.__lumina?.get().connected,null,{timeout:45000})
  const before=await page.evaluate(async path=>{const l=window.__lumina;const ids=(await l.engine.exec('media.import',{paths:[path]})).media_ids;l.set({sourceMediaId:ids[0],sourceTime:0,sourceClip:null});return JSON.stringify(l.get().doc.sequences)},source)
  await page.locator('.dv-tab').filter({hasText:/^(소스 검수|Source Review)$/}).click()
  const panel=page.locator('[data-source-review]')
  await panel.getByRole('button',{name:'컷만 검출',exact:true}).click()
  await page.waitForFunction(()=>document.querySelectorAll('[aria-label="검수할 컷"] option').length===2,null,{timeout:45000})
  const get=()=>page.evaluate(async()=>window.__lumina.engine.call('source_review.get',{media_id:window.__lumina.get().sourceMediaId}))
  const saved=async()=>{await panel.getByRole('status').filter({hasText:'DB 자동 저장 완료'}).waitFor()}
  assert.equal(await panel.getByRole('tab').count(),0)
  assert.equal(await panel.locator('footer').count(),0)
  assert.equal(await panel.getByRole('button',{name:'끝 경계',exact:true}).count(),0)
  await page.waitForFunction(()=>[...document.querySelectorAll('.sr-frame')].every(e=>e.dataset.ready==='true'))
  const pixels=await panel.locator('.sr-frame canvas').evaluateAll(cs=>cs.map(c=>Array.from(c.getContext('2d').getImageData(10,10,1,1).data)))
  assert.ok(pixels.every(c=>c[0]>200 && c[2]<50),'start and final frame of selected red cut match')
  const reference=panel.locator('[data-comparison="reference"] canvas')
  const startFrame=await reference.getAttribute('data-frame')
  const initial=await get()
  await panel.getByRole('button',{name:'다음 프레임',exact:true}).click();await saved()
  let d=await get();assert.ok(d.cuts[0].end>initial.cuts[0].end);assert.equal(d.cuts[0].end,d.cuts[1].start)
  assert.equal(await reference.getAttribute('data-frame'),startFrame)
  await panel.getByRole('button',{name:'다음 컷',exact:true}).click()
  await page.waitForFunction(()=>document.querySelector('[data-comparison="reference"] canvas')?.dataset.ready!==null && document.querySelector('[data-comparison="reference"] .sr-frame')?.dataset.ready==='true')
  assert.ok(Number(await reference.getAttribute('data-frame'))>=1)
  assert.equal(await panel.getByRole('slider').isDisabled(),true)
  assert.equal(await panel.getByRole('button',{name:'이전 프레임',exact:true}).isDisabled(),true)
  await panel.getByRole('button',{name:'이전 컷',exact:true}).click()
  await panel.getByLabel('IP 검색',{exact:true}).fill('테스트')
  await panel.getByLabel('IP / 작품',{exact:true}).locator('option[value="테스트 작품"]').waitFor({state:'attached'})
  await panel.getByLabel('IP / 작품',{exact:true}).selectOption('테스트 작품');await saved()
  await panel.getByLabel('등장인물 추가').locator('option[value="인물 A"]').waitFor({state:'attached'})
  await panel.getByLabel('등장인물 추가').selectOption('인물 A');await saved()
  await panel.getByLabel('소스 종류',{exact:true}).selectOption('episode');await saved()
  await panel.getByLabel('장면 설명').fill('자동 저장한 장면 설명')
  await panel.getByLabel('장면 설명').fill('연속 입력 마지막 값');await saved()
  await panel.getByRole('button',{name:'추가',exact:true}).click()
  assert.equal((await get()).cuts[0].dialogue.length,0,'empty add does not persist placeholder')
  await panel.getByLabel('대사 내용',{exact:true}).fill('대사 자동 저장');await saved()
  await panel.getByLabel('화자',{exact:true}).selectOption('인물 B');await saved()
  await panel.getByLabel('행동',{exact:true}).fill('달리기');await saved()
  d=await get();assert.equal(d.cuts[0].description,'연속 입력 마지막 값');assert.deepEqual(d.cuts[0].characters,['인물 A']);assert.equal(d.cuts[0].dialogue[0].speaker,'인물 B');assert.equal(d.identity.source_type,'episode');assert.equal(d.published_revision,d.revision)
  // Confirm actual search DB facts, not only draft state or a success label.
  const dbFacts=JSON.parse(execFileSync(resolve('../engine/.venv/Scripts/python.exe'),['-c',`import sqlite3,sys,json
c=sqlite3.connect(sys.argv[1]);print(json.dumps(c.execute("SELECT kind,value,status FROM fact WHERE kind IN ('description','dialogue','source_type') AND status!='rejected'").fetchall(),ensure_ascii=False))`,join(home,'source.db')],{encoding:'utf8',windowsHide:true}))
  assert.ok(dbFacts.some(r=>r[0]==='description' && r[1]==='연속 입력 마지막 값' && r[2]==='accepted'))
  assert.ok(dbFacts.some(r=>r[0]==='dialogue' && r[1].includes('인물 B')))
  // Failed autosave retains the edit and retries, and in-flight typing is not lost.
  await page.evaluate(()=>{const e=window.__lumina.engine;window.__reviewOriginal=e.call.bind(e);let once=true;e.call=async(method,args)=>{if(method==='source_review.save' && once){once=false;throw Error('test write failure')}return window.__reviewOriginal(method,args)}})
  await panel.getByLabel('장면 설명').fill('실패 후 보존 입력')
  await panel.getByRole('alert').filter({hasText:'자동 저장 실패'}).waitFor()
  assert.equal(await panel.getByLabel('장면 설명').inputValue(),'실패 후 보존 입력')
  await panel.getByRole('button',{name:'저장 재시도'}).click();await saved()
  assert.equal((await get()).cuts[0].description,'실패 후 보존 입력')
  await page.evaluate(()=>{window.__lumina.engine.call=async(method,args)=>{if(method==='source_review.save'){window.__reviewWriting=true;await new Promise(r=>setTimeout(r,400))}const result=await window.__reviewOriginal(method,args);window.__reviewWriting=false;return result}})
  await panel.getByLabel('장면 설명').fill('저장 중 첫 입력')
  await page.waitForFunction(()=>window.__reviewWriting)
  await panel.getByLabel('장면 설명').fill('저장 중 마지막 입력');await saved()
  assert.equal((await get()).cuts[0].description,'저장 중 마지막 입력')
  await page.evaluate(()=>{window.__lumina.engine.call=window.__reviewOriginal})
  // A legacy IP missing from the catalog must not block existing source characters as speakers.
  await page.evaluate(async()=>{const e=window.__lumina.engine,id=window.__lumina.get().sourceMediaId,d=await e.call('source_review.get',{media_id:id});await e.call('source_review.update',{media_id:id,revision:d.revision,cut_id:d.cuts[0].id,action:'update',fields:{work:'카탈로그에 없는 기존 IP',characters:['기존 인물']}})})
  await panel.getByLabel('IP / 작품',{exact:true}).locator('option[value="카탈로그에 없는 기존 IP"]').waitFor({state:'attached'})
  await panel.getByLabel('화자',{exact:true}).locator('option[value="기존 인물"]').waitFor({state:'attached'})
  await panel.getByLabel('화자',{exact:true}).selectOption('기존 인물');await saved()
  assert.equal((await get()).cuts[0].dialogue[0].speaker,'기존 인물')
  // Existing off-screen speakers remain selectable separately from visible characters.
  assert.equal(await panel.getByLabel('화자',{exact:true}).locator('option[value="기존 인물"]').isEnabled(),true)
  // Drag saves shared end boundary, preserving the left start frame.
  const bar=await panel.getByRole('slider').boundingBox()
  await page.mouse.move(bar.x+bar.width*.5,bar.y+bar.height/2);await page.mouse.down();await page.mouse.move(bar.x+bar.width*.4,bar.y+bar.height/2,{steps:6});await page.mouse.up();await saved()
  d=await get();assert.equal(d.cuts[0].end,d.cuts[1].start);assert.equal(await reference.getAttribute('data-frame'),startFrame)
  await panel.getByRole('button',{name:'소스 재생',exact:true}).click()
  await panel.getByRole('button',{name:'일시 정지',exact:true}).waitFor()
  assert.equal(await reference.isVisible(),true)
  await panel.getByRole('button',{name:'소스 재생',exact:true}).waitFor()
  // Flush pending edits when the source changes, without applying them to the next source.
  const sourceId=await page.evaluate(()=>window.__lumina.get().sourceMediaId)
  await panel.getByLabel('장면 설명').fill('소스 전환 직전 입력')
  await page.evaluate(()=>window.__lumina.set({sourceMediaId:null}))
  await page.waitForFunction(async id=>(await window.__lumina.engine.call('source_review.get',{media_id:id})).cuts[0].description==='소스 전환 직전 입력',sourceId)
  await page.evaluate(id=>window.__lumina.set({sourceMediaId:id}),sourceId)
  await panel.getByLabel('장면 설명').waitFor()
  assert.equal(await panel.getByLabel('장면 설명').inputValue(),'소스 전환 직전 입력')
  await panel.getByLabel('소스 종류').locator('option[value=episode]:not([disabled])').waitFor({state:'attached'})
  for(const [width,height] of [[740,740],[380,480],[320,360]]){
    await panel.evaluate((el,{width,height})=>{el.style.width=width+'px';el.style.height=height+'px'},{width,height})
    assert.ok(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth+1 && el.scrollHeight<=el.clientHeight+1))
    await panel.screenshot({path:join(home,`live-${width}x${height}.png`)})
  }
  assert.equal(await page.evaluate(()=>JSON.stringify(window.__lumina.get().doc.sequences)),before)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({passed:true,home}))
}catch(e){console.error(e,errors);await page.screenshot({path:join(home,'failure.png')}).catch(()=>{});throw e}
finally{await app.evaluate(({BrowserWindow})=>{for(const w of BrowserWindow.getAllWindows())w.destroy()});await app.close()}
