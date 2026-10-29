// Start tests/docx_e2e_server.py first (real native runner + LibreOffice, scripted model).
import { chromium } from 'playwright'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
const base = process.env.DOCX_E2E_BASE_URL || 'http://127.0.0.1:8081'
const timeoutScale = Number(process.env.OFFICE_E2E_TIMEOUT_SCALE || 1)
const output = process.env.DOCX_E2E_OUTPUT || new URL('../test-results/', import.meta.url).pathname
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] })
const page = await browser.newPage({ viewport: { width: 1560, height: 1000 } })
const errors = []; page.on('pageerror', e => errors.push(e.message))
const deliveries = new Map(), measures = []
const cdp = await page.context().newCDPSession(page)
await cdp.send('Network.enable')
cdp.on('Network.eventSourceMessageReceived', ({ data }) => {
  const event = JSON.parse(data)
  if (['document_revision', 'document_initialized'].includes(event.type) && event.committed_at && !deliveries.has(event.revision)) deliveries.set(event.revision, { ...event, received_at: Date.now()/1000 })
})
await page.exposeFunction('__recordDocxMeasure', entry => measures.push(entry))
await page.addInitScript(() => new PerformanceObserver(list => {
  for (const entry of list.getEntries()) if (entry.name === 'docx-preview-update') window.__recordDocxMeasure({ duration: entry.duration, detail: entry.detail, finished_at: performance.timeOrigin + entry.startTime + entry.duration })
}).observe({ type: 'measure', buffered: true }))
function percentile(values, p) { const ordered = values.toSorted((a,b)=>a-b); return ordered.length ? Math.round(ordered[Math.ceil(ordered.length*p)-1]*100)/100 : null }
async function poll(fn, timeout = 180000) {
  const end = Date.now() + timeout * timeoutScale
  while (Date.now() < end) { const value = await fn(); if (value) return value; await new Promise(resolve => setTimeout(resolve, 250)) }
  throw new Error('Timed out waiting for formatted DOCX acceptance condition')
}
try {
  await page.goto(base)
  await page.evaluate(() => localStorage.setItem('locale', 'zh'))
  const response = await page.request.post(base + '/api/agent/sessions', { data: { model_id: 'test/model' } })
  assert.equal(response.status(), 201)
  const session = await response.json(); const sessionUrl = base + '/api/agent/sessions/' + session.id
  await page.goto(base + '/?session=' + session.id)
  await page.evaluate(id => {
    window.docxEvents = []
    window.docxSource = new EventSource(`/api/agent/sessions/${id}/events`)
    window.docxSource.onmessage = event => {
      const value = JSON.parse(event.data)
      if (value.type.startsWith('document_')) window.docxEvents.push({ ...value, received_at: Date.now()/1000 })
    }
  }, session.id)
  const input = page.locator('.composer textarea')
  await input.fill('生成一份十页以上的业务项目建设方案，采用A4、分级标题、短正文、带表头表格、流程图和居中图注。')
  await input.press('Enter')
  await page.locator('.docx-panel').waitFor({ timeout: 20000 })
  await page.locator('.docx-paper>img').first().waitFor({ timeout: 30000 })
  // Streaming commits must arrive while the task is still running.
  await poll(async () => (await page.request.get(sessionUrl + '/documents')).json().then(docs => docs[0]?.revision >= 16), 60000)
  assert.equal((await (await page.request.get(sessionUrl)).json()).status, 'running')
  await input.fill('第二节改成表格，用业务语言展示职责分工。')
  await input.focus(); await page.waitForTimeout(300)
  assert(await input.evaluate(el => el === document.activeElement), 'preview stole chat focus')
  await input.press('Enter')
  await poll(async () => (await (await page.request.get(sessionUrl + '/journal')).json()).events.some(event => event.operation_id === 'correct-table'), 30000)
  assert.equal(await page.locator('.docx-panel [contenteditable=true]').count(), 0)
  await page.getByRole('button', { name: '关闭文档预览', exact: true }).click()
  await page.waitForTimeout(1200)
  assert.equal(await page.locator('.docx-panel').count(), 0, 'closed panel reopened on updates')
  await page.reload(); await page.locator('.docx-reopen').waitFor({ timeout: 10000 })
  assert.equal(await page.locator('.docx-panel').count(), 0, 'closed preference lost on reload')
  await page.locator('.docx-reopen').click()
  await page.locator('.docx-paper>img').first().waitFor({ timeout: 30000 })
  // Browsing an earlier page pauses auto-follow across updates.
  const scroll = page.locator('.docx-preview-scroll')
  await scroll.evaluate(el => { el.scrollTop = 10; el.dispatchEvent(new Event('scroll')) })
  const oldRevision = (await (await page.request.get(sessionUrl + '/documents')).json())[0].revision
  await poll(async () => (await (await page.request.get(sessionUrl + '/documents')).json())[0].revision > oldRevision + 2, 30000)
  assert(await scroll.evaluate(el => el.scrollTop < 80), 'scroll jumped while browsing earlier pages')
  await page.screenshot({ path: output + '/formatted-live.png' })
  const document = await poll(async () => {
    const state = await (await page.request.get(sessionUrl)).json()
    const docs = await (await page.request.get(sessionUrl + '/documents')).json()
    if (state.status === 'failed') throw new Error(state.error)
    return state.status === 'completed' && docs[0]?.revision >= 112 && docs[0]?.preview_revision === docs[0]?.revision ? docs[0] : null
  })
  assert(document.pages.length >= 10, 'fixture did not naturally reach ten pages')
  assert(document.pages.every(p => p.checks.automatic_passed && p.checks.visual_review === 'pending'))
  assert.equal(document.finalized, false, 'unseen pages were falsely finalized')
  await poll(async () => await page.locator('.docx-paper>img').count() === document.pages.length)
  const geometry = await page.locator('.docx-paper').evaluateAll(pages => pages.map(p => ({ top: p.getBoundingClientRect().top, bottom: p.getBoundingClientRect().bottom, ratio: p.querySelector('img').naturalWidth / p.querySelector('img').naturalHeight })))
  assert(geometry.every(p => Math.abs(p.ratio - 210/297) < .005), 'paper aspect ratio is not A4')
  assert(geometry.slice(1).every((p, i) => p.top - geometry[i].bottom >= 23), 'no physical gap between pages')
  await page.reload(); await page.locator('.docx-paper>img').first().waitFor({ timeout: 20000 })
  assert(await page.locator('.docx-preview-scroll').evaluate(el => el.scrollTop < 80), 'scroll position lost on reload')
  await page.screenshot({ path: output + '/formatted-complete.png' })
  assert.deepEqual(errors, [])
  const journal = (await (await page.request.get(sessionUrl + '/journal')).json()).events
  assert(journal.some(e => e.type === 'steer' && e.document?.document_id === 'report'))
  const pages = document.pages.map(p => ({ page: p.page, status: p.status, checks: p.checks }))
  const sseLatency = [...deliveries.values()].map(e => (e.received_at-e.committed_at)*1000)
  const visibleLatency = measures.filter(m => deliveries.has(m.detail?.revision)).map(m => m.finished_at - deliveries.get(m.detail.revision).committed_at*1000)
  const metrics = { commit_to_sse_p95_ms: percentile(sseLatency, .95), commit_to_paginated_preview_p95_ms: percentile(visibleLatency, .95), page_decode_and_swap_p95_ms: percentile(measures.map(m=>m.duration), .95), rendered_samples: measures.length }
  const result = { metrics, session_id: session.id, revision: document.revision, pages, errors, tests: ['formatted_generation', 'live_sse', 'steering_to_table', 'read_only', 'focus', 'closed_state', 'scroll', 'natural_A4_pagination', 'reload_restores_review_state'] }
  await writeFile(output + '/result.json', JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ ...result, pages: pages.length }))
} finally { await browser.close() }
