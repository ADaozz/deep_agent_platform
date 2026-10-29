import {chromium} from 'playwright'
import assert from 'node:assert/strict'
import {mkdir,writeFile} from 'node:fs/promises'
const base=process.env.PPTX_E2E_BASE_URL||'http://127.0.0.1:8084'
const timeoutScale = Number(process.env.OFFICE_E2E_TIMEOUT_SCALE || 1)
const output=new URL('../test-results/',import.meta.url).pathname
await mkdir(output,{recursive:true})
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']})
const page=await browser.newPage({viewport:{width:1680,height:1040}}),errors=[]
page.on('pageerror',e=>errors.push(e.message))
async function poll(fn,timeout=180000){const end=Date.now()+timeout*timeoutScale;while(Date.now()<end){const result=await fn();if(result)return result;await new Promise(resolve=>setTimeout(resolve,180))}throw Error('PPTX acceptance timed out')}
try{
 await page.goto(base);await page.evaluate(()=>localStorage.setItem('locale','zh'))
 const session=await(await page.request.post(base+'/api/agent/sessions',{data:{model_id:'test/model'}})).json();const url=base+'/api/agent/sessions/'+session.id
 await page.goto(base+'/?session='+session.id)
 const input=page.locator('.composer textarea')
 await input.fill('参考企业模板制作12页 AI Agent 平台技术汇报，需要封面、背景、架构图、能力、技术路线、对比图表和总结，保留原生可编辑对象。');await input.press('Enter')
 const waiting=await poll(async()=>{const state=await(await page.request.get(url)).json();return state.status==='waiting_confirmation'?state:null},30000)
 assert.equal(waiting.pending_tool_calls.length,1);assert.equal(waiting.pending_tool_calls[0].name,'pptx_init')
 await page.screenshot({path:output+'/pptx-scoped-approval.png'})
 const approval=await page.request.post(url+'/actions/approve',{data:{decisions:waiting.pending_tool_calls.map(p=>({tool_call_id:p.toolCallId,decision:'approve'}))}});assert.equal(approval.status(),202)
 await page.locator('.pptx-panel').waitFor({timeout:20000})
 async function deck(){const rows=await(await page.request.get(url+'/presentations')).json();return rows[0]}
 await poll(async()=>{const value=await deck();return value?.manifest.slides.length>=2},60000)
 await input.fill('封面太暗了，整体换成简洁的科技蓝白配色。');await input.press('Enter')
 await poll(async()=>{const value=await deck();return value?.manifest.slides.length>=5},90000)
 await input.fill('第5页把文字列表替换为架构图，展示目标输入、受控执行和质量交付。');await input.press('Enter')
 await poll(async()=>{const value=await deck();return value?.manifest.slides.length>=9},90000)
 await page.locator('.pptx-thumbnails>button').nth(2).click()
 await input.fill('返回修改第3页技术描述：主 Agent 统一驱动、安全检查点纠偏、持久化执行记录。');await input.press('Enter')
 await poll(async()=>{const value=await deck();return value?.history.some(h=>h.operation_id==='feedback-history')},30000)
 await page.screenshot({path:output+'/pptx-live-steering.png'})
 const final=await poll(async()=>{const state=await(await page.request.get(url)).json();if(state.status==='waiting_confirmation')throw Error('Unexpected repeated per-element approval');const value=await deck();return state.status==='completed'&&value?.manifest.slides.length===12?value:null},180000)
 assert(final.manifest.slides.every(s=>s.review.automatic_passed),JSON.stringify(final.manifest.slides.filter(s=>!s.review.automatic_passed)))
 assert(final.manifest.slides.every(s=>s.review.visual_review==='pending'))
 const fifth=final.manifest.slides.find(s=>s.slide_id==='slide-05');assert(!fifth.elements.some(e=>e.id==='process-list'));assert(fifth.elements.some(e=>e.id==='flow-bg-2'))
 const third=final.manifest.slides.find(s=>s.slide_id==='slide-03');assert(third.elements.some(e=>e.id==='architecture-body-1'&&e.text.includes('安全检查点纠偏')))
 assert(final.manifest.slides.every(s=>s.background==='FFFFFF'))
 assert.equal(await page.locator('.pptx-panel [contenteditable=true]').count(),0)
 await page.locator('.pptx-thumbnails>button').nth(2).click();await page.waitForTimeout(400)
 await page.screenshot({path:output+'/pptx-complete-architecture.png'})
 await page.locator('.pptx-thumbnails>button').nth(7).click();await page.waitForTimeout(400)
 await page.screenshot({path:output+'/pptx-complete-chart.png'})
 await page.getByRole('button',{name:'关闭演示文稿预览',exact:true}).click();await page.reload();await page.locator('.pptx-reopen').waitFor({timeout:15000});assert.equal(await page.locator('.pptx-panel').count(),0)
 await page.locator('.pptx-reopen').click();await page.locator('.pptx-thumbnails>button').first().waitFor()
 assert.equal(await page.locator('.pptx-thumbnails>button').count(),12)
 const result={session_id:session.id,revision:final.revision,slides:12,errors,tests:['template_preserved','one_deck_approval','element_streaming','theme_steering','layout_steering','historical_slide_steering','native_table_and_charts','formal_checks','reload','read_only']}
 assert.deepEqual(errors,[]);await writeFile(output+'/pptx-result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result))
}finally{await browser.close()}
