<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import Icon from './Icon.vue'
import FileBlock from './FileBlock.vue'
import FeaturePanels from './FeaturePanels.vue'
import WorkProcess from './WorkProcess.vue'
import { t, locale, theme, toggleLocale, toggleTheme } from './preferences'
import { vPopoverPlacement } from './popoverPlacement'
import { conversationTimeline } from './conversation'

const artifactPanelOpen = ref(false), artifactContext = ref(null), featurePanels = ref(null), featuresList = ref([])
function feedbackContext() { return artifactContext.value }
async function showFeatures() { await guard(async () => { featuresList.value = await api('/features'); modal.value = 'features' }) }
async function setFeature(feature,enabled,uninstall=false) { await guard(async () => { featuresList.value = await api('/features/' + feature.id, {method:uninstall?'DELETE':'PUT', ...(uninstall?{}:{body:{enabled}})}); featurePanels.value?.reload() }) }
const profiles = ref([]), sessions = ref([])
const toolCatalog = ref([])
const modelId = ref(''), effort = ref('')
const selected = ref(null), events = ref([]), draft = ref(''), search = ref('')
const sidebar = ref(localStorage.getItem('sidebar') !== 'false'), popup = ref(''), modal = ref(''), configTab = ref('general')
const loading = ref(true), sending = ref(false), saving = ref(false), error = ref(''), notice = ref('')
const dismissed = ref(sessionStorage.getItem('notice-dismissed') === 'true')
const settingsEdit = ref(null), toolSchemas = ref({}), rulesText = ref(''), fileList = ref([])
const mcpJson = ref('')
const modelConfig = ref(null), modelJson = ref(''), advanced = ref(false), attachments = ref([]), fileInput = ref(null)
const uploading = ref(false)
const fileDragDepth = ref(0)
const uploadDisabled = computed(() => uploading.value || sending.value || busy.value || waiting.value)
const providerForm = ref({ provider: 'deepseek', model: 'deepseek-flash', base_url: 'https://api.deepseek.com/v1', api_key: '', api: 'chat_completions', efforts: 'high, low, max', image: false })
const humanValues = ref({}), decisions = ref({}), mainView = ref('agents')
const draftPermission = ref('ask'), allowConfirmation = ref('')
const reasoningModelId = ref('')
let source, poll, noticeTimer, flushTimer = 0, pendingEvents = [], seenSeqs = new Set()

const profile = computed(() => profiles.value.find(p => p.id === modelId.value))
const visibleSessions = computed(() => sessions.value.filter(s => !search.value || sessionTitle(s).toLowerCase().includes(search.value.toLowerCase())))
const busy = computed(() => ['queued', 'running'].includes(selected.value?.status))
const waiting = computed(() => ['waiting_confirmation', 'waiting_human', 'paused', 'runtime_config_boundary'].includes(selected.value?.status))
const modelLabel = computed(() => profile.value?.model || t('Select model'))
const cacheHitLabel = computed(() => {
  const rate = selected.value?.usage?.cache_hit_rate
  return typeof rate === 'number' && Number.isFinite(rate) && rate >= 0 && rate <= 1
    ? new Intl.NumberFormat(locale.value === 'zh' ? 'zh-CN' : 'en-US', { style: 'percent', maximumFractionDigits: 1 }).format(rate)
    : '—'
})
const cacheHitTitle = computed(() => {
  const usage = selected.value?.usage
  return cacheHitLabel.value === '—' ? t('usage.cacheUnavailable') : t('usage.cacheDetails', { cached: usage.cache_read_input_tokens.toLocaleString(), total: usage.input_tokens.toLocaleString() })
})
const reasoningProfile = computed(() => profiles.value.find(p => p.id === reasoningModelId.value))
const reasoningChoice = computed(() => reasoningModelId.value === modelId.value ? effort.value : reasoningProfile.value?.default_reasoning_effort || '')
const effectivePermission = computed(() => selected.value?.permission_mode || draftPermission.value)
const pendingPermission = computed(() => selected.value?.pending_permission_mode)
const chatHome = computed(() => mainView.value === 'agents' && !selected.value)
const recentSessions = computed(() => {
  const recent = sessions.value.slice(0, 12)
  return selected.value && !recent.some(s => s.id === selected.value.id)
    ? [selected.value, ...recent.slice(0, 11)] : recent
})
const toolLabels = computed(() => ({ ls: t('列出文件'), read_file: t('读取文件'), write_file: t('写入文件'), edit_file: t('编辑文件'), delete: t('删除文件'), glob: t('文件匹配'), grep: t('搜索内容'), execute: t('执行命令'), task: t('子 Agent 委派'), read_subagent_log: t('子任务日志'), request_human_input: t('向用户提问'), web_search: t('联网搜索'), web_fetch: t('抓取网页') }))
const statusLabels = computed(() => ({ idle: t('新会话'), queued: t('排队中'), running: t('执行中'), completed: t('已完成'), failed: t('执行失败'), waiting_confirmation: t('等待审批'), waiting_human: t('等待回复'), paused: t('已暂停'), runtime_config_boundary: t('等待继续'), cancelled: t('已取消'), interrupted: t('已中断') }))

const timeline = computed(() => conversationTimeline(events.value, selected.value?.status))
function actionLabel(name) {
  const labels = { ...toolLabels.value, create_docx: t('activity.document'), create_pptx: t('activity.presentation'), create_xlsx: t('activity.spreadsheet') }
  return labels[name] || t(name?.startsWith('mcp') ? 'activity.external' : 'activity.action')
}
function approvalFields(call) {
  const hidden = key => /^_|^(id|metadata|tool_call_id|session_id|run_id|message_id|trace_id|checkpoint_id|usage)$/i.test(key)
  const labels = { command: 'activity.command', file_path: 'activity.file', path: 'activity.file', url: 'activity.address', query: 'activity.query', title: 'Name', content: 'activity.content', old_string: 'activity.original', new_string: 'activity.replacement', description: 'Description', paragraphs: 'activity.content', slides: 'activity.content', rows: 'activity.content' }
  const text = value => {
    if (Array.isArray(value)) return value.map(text).join('\n')
    if (value && typeof value === 'object') return Object.entries(value).filter(([key]) => !hidden(key)).map(([key, entry]) => `${t(labels[key] || key.replaceAll('_', ' '))}: ${text(entry)}`).join('\n')
    return value == null ? '' : String(value)
  }
  return Object.entries(call.args || {}).filter(([key]) => !hidden(key)).map(([key, value]) => ({ label: t(labels[key] || key.replaceAll('_', ' ')), content: text(value) })).filter(field => field.content)
}

function html(text) { return DOMPurify.sanitize(marked.parse(text || '')) }
function sessionTitle(s) { return s.messages?.[0]?.content?.split('\n')[0] || t('New chat') }
function dateLabel(s) { const date = new Date(s.updated_at || s.created_at); return date.toLocaleDateString(locale.value === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric' }) }
function fileIcon(name) { return /\.(docx?|pptx?|xlsx?|pdf|md|txt|csv|json|log|py|js|ts|vue|html?|css|zip|png|jpe?g|gif|webp)$/i.test(name) ? 'FileText' : 'FileBox' }
function toast(text) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 4500) }
function resetStream() { clearTimeout(flushTimer); flushTimer = 0; pendingEvents = []; seenSeqs = new Set() }
function queueEvent(data) {
  if (seenSeqs.has(data.seq)) return
  seenSeqs.add(data.seq); pendingEvents.push(data)
  // Coalesce token events into one reactive update per frame; long runs otherwise
  // rebuild the whole timeline for every token.
  if (!flushTimer) flushTimer = setTimeout(flushEvents, 40)
}
function flushEvents() {
  flushTimer = 0
  if (!pendingEvents.length) return
  const batch = pendingEvents; pendingEvents = []
  events.value = events.value.concat(batch)
  if (batch.some(e => ['result', 'error', 'usage', 'compacted', 'status', 'permission_selected', 'runtime_config_applied', 'runtime_config_failed','sandbox_selected'].includes(e.type))) refreshSessions().catch(() => {})
  nextTick(() => { const el = document.querySelector('.transcript'); if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 300) el.scrollTop = el.scrollHeight })
}
async function api(path, options = {}) {
  const res = await fetch('/api' + path, { ...options, headers: { ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...options.headers }, body: options.body === undefined ? undefined : options.body instanceof FormData ? options.body : JSON.stringify(options.body) })
  if (!res.ok) { const data = await res.json().catch(() => ({})); throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入配置') }
  return res.json()
}
async function guard(fn) { error.value = ''; try { return await fn() } catch (e) { error.value = e.message } }
async function refreshModels() {
  const data = await api('/models'); profiles.value = data.profiles; modelConfig.value = data
  if (!profiles.value.some(p => p.id === modelId.value)) {
    modelId.value = data.llm.default || profiles.value[0]?.id || ''
    effort.value = profiles.value.find(p => p.id === modelId.value)?.default_reasoning_effort || ''
  }
}
async function refreshSessions() { sessions.value = await api('/agent/sessions'); if (selected.value) { const s = sessions.value.find(s => s.id === selected.value.id); if (s) selected.value = s } }
async function init() {
  await guard(async () => {
    toolCatalog.value = await api('/tool-catalog')
    await Promise.all([refreshModels(), refreshSessions()])
    const id = new URL(location.href).searchParams.get('session'); if (id) await openSession(id)
  }); loading.value = false
}
function toggle(name) { popup.value = popup.value === name ? '' : name }
function newChat() {
  source?.close(); source = null; resetStream(); selected.value = null; draftPermission.value = 'ask'; events.value = []; error.value = ''; draft.value = ''; clearAttachments(); humanValues.value = {}
  modelId.value = modelConfig.value?.llm.default || profiles.value[0]?.id || ''
  effort.value = profiles.value.find(p => p.id === modelId.value)?.default_reasoning_effort || ''
  history.replaceState(null, '', '/'); mainView.value = 'agents'
}
async function openSession(id, preserveAttachments = false) {
  await guard(async () => {
    source?.close(); resetStream()
    const [next, journal] = await Promise.all([api('/agent/sessions/' + id), api(`/agent/sessions/${id}/journal`).catch(() => ({ events: [] }))])
    if (!preserveAttachments) clearAttachments()
    selected.value = next; events.value = journal.events || []; humanValues.value = {}; decisions.value = {}
    modelId.value = selected.value.model_id; effort.value = selected.value.reasoning_effort || ''; mainView.value = 'agents'; popup.value = ''
    history.replaceState(null, '', `/?session=${id}`)
    const cursor = events.value.at(-1)?.seq || 0
    source = new EventSource(`/api/agent/sessions/${id}/events?after=${cursor}`)
    source.onmessage = event => queueEvent(JSON.parse(event.data))
  })
}
async function ensureSession() {
  if (selected.value) return selected.value
  const session = await api('/agent/sessions', {method:'POST',body:{model_id:modelId.value||null,reasoning_effort:effort.value||null,permission_mode:draftPermission.value,permission_confirmation:draftPermission.value==='allow'?'ALLOW':null}})
  await openSession(session.id, true)
  return selected.value
}
function fileUrl(path) { return `/api/agent/sessions/${selected.value.id}/files/${path.split('/').map(encodeURIComponent).join('/')}` }
const sandboxConfirmation=ref('')
async function selectSandbox(mode, confirmation) {
  if(mode==='full-access' && confirmation!=='FULL_ACCESS') {sandboxConfirmation.value='';modal.value='sandbox';popup.value='';return}
  saving.value=true
  await guard(async()=>{
    const session=await ensureSession()
    selected.value=await api(`/agent/sessions/${session.id}/sandbox`,{method:'PUT',body:{mode,confirmation}})
    modal.value='';popup.value=''
    if(selected.value.pending_sandbox_mode)toast('sandbox.pending')
  })
  saving.value=false
}
async function send() {
  if ((!draft.value.trim() && !attachments.value.length) || sending.value || uploading.value) return
  sending.value = true
  await guard(async () => {
    if (busy.value) { const result = await action('steer', { text: draft.value, artifact: feedbackContext() }); if (result?.accepted) { toast('docx.steerAccepted'); draft.value = '' }; return }
    if (waiting.value) throw new Error('请先处理审批、提问或暂停，再发送下一条消息')
    await ensureSession()
    const s = await api(`/agent/sessions/${selected.value.id}/messages`, { method: 'POST', body: { text: draft.value.trim() || t('upload.defaultPrompt'), upload_ids: attachments.value.map(file => file.id) } })
    selected.value = s; draft.value = ''; clearAttachments(); await refreshSessions()
  }); sending.value = false
}
async function action(name, body = {}) {
  return guard(async () => { const result = await api(`/agent/sessions/${selected.value.id}/actions/${name}`, { method: 'POST', body }); await refreshSessions(); return result })
}
async function queueFollowUp() { const result = await action('follow_up', { text: draft.value, artifact: feedbackContext() }); if (result?.accepted) draft.value = '' }
async function selectModel(id, strength) {
  strength ??= profiles.value.find(p => p.id === id)?.default_reasoning_effort || ''
  await guard(async () => {
    if (selected.value) selected.value = await api(`/agent/sessions/${selected.value.id}/model`, { method: 'PUT', body: { model_id: id, reasoning_effort: strength || null } })
    modelId.value = id; effort.value = strength; popup.value = ''; if (busy.value) toast('模型配置已排队，将在内核安全边界生效')
  })
}
function dismissBanner() { dismissed.value = true; sessionStorage.setItem('notice-dismissed', 'true') }
function keydown(e) {
  if (e.key === 'Escape') { popup.value = ''; closeModal() }
  if (e.ctrlKey && e.altKey && e.code === 'KeyB') { e.preventDefault(); sidebar.value = !sidebar.value }
}
function composerKey(e) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() } }
function removeAttachment(index) { const [file] = attachments.value.splice(index, 1); if (file?.url) URL.revokeObjectURL(file.url) }
function clearAttachments() { while (attachments.value.length) removeAttachment(0) }
async function upload(event) {
  try { await uploadFiles(Array.from(event.target.files || [])) }
  finally { event.target.value = '' }
}
function fileDrag(event, entering = false) {
  if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return
  event.preventDefault()
  event.dataTransfer.dropEffect = uploadDisabled.value ? 'none' : 'copy'
  if (entering) fileDragDepth.value++
}
function fileDragLeave() { fileDragDepth.value = Math.max(0, fileDragDepth.value - 1) }
async function dropFiles(event) {
  fileDragDepth.value = 0
  const files = Array.from(event.dataTransfer?.files || [])
  if (!files.length) return
  event.preventDefault()
  await uploadFiles(files)
}
async function pasteImages(event) {
  const clipboard = event.clipboardData
  let images = Array.from(clipboard?.files || []).filter(file => file.type.startsWith('image/'))
  if (!images.length) {
    images = Array.from(clipboard?.items || [])
      .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
      .map(item => item.getAsFile()).filter(Boolean)
  }
  if (!images.length) return
  event.preventDefault()
  await uploadFiles(images)
}
async function uploadFiles(files) {
  if (uploadDisabled.value || !files.length) return
  uploading.value = true; error.value = ''
  try {
    const session=await ensureSession()
    for (const file of files) {
      if (attachments.value.length >= 10 || file.size > 20 * 1024 * 1024) { toast('upload.limit'); continue }
      const form = new FormData(); form.append('file', file)
      const item = await api(`/agent/sessions/${session.id}/uploads`, { method: 'POST', body: form })
      attachments.value.push({ ...item, url: item.type.startsWith('image/') ? URL.createObjectURL(file) : null })
    }
  } catch (e) { error.value = e.message }
  finally { uploading.value = false }
}
async function openAgentSettings(tab = 'general') {
  await guard(async () => {
    settingsEdit.value = await api('/settings')
    settingsEdit.value.skill_bindings ||= {}; settingsEdit.value.native_skills ||= []
    settingsEdit.value.core_tools ||= {}; rulesText.value = settingsEdit.value.rules.join('\n'); toolSchemas.value = {}
    mcpJson.value = JSON.stringify(settingsEdit.value.mcp || { enabled: false, servers: {} }, null, 2)
    for (const t of settingsEdit.value.tools) toolSchemas.value[t.name] = JSON.stringify(t.schema, null, 2)
    configTab.value = tab; modal.value = 'agent-settings'; popup.value = ''; error.value = ''
  })
}
function addTool(tool) { const item = JSON.parse(JSON.stringify(tool)); settingsEdit.value.tools.push(item); toolSchemas.value[item.name] = JSON.stringify(item.schema, null, 2) }
async function saveSettings() {
  saving.value = true
  await guard(async () => {
    const data = JSON.parse(JSON.stringify(settingsEdit.value)); data.rules = rulesText.value.split('\n').filter(Boolean)
    data.mcp = JSON.parse(mcpJson.value)
    for (const t of data.tools) t.schema = JSON.parse(toolSchemas.value[t.name])
    await api('/settings', { method: 'PUT', body: data })
    modal.value = ''; toast('设置已保存；新会话使用新配置')
  }); saving.value = false
}
async function openModels() {
  await guard(async () => { await refreshModels(); modelJson.value = JSON.stringify(modelConfig.value.llm, null, 2); modal.value = 'models'; popup.value = ''; advanced.value = false })
}
async function saveModels() {
  saving.value = true
  await guard(async () => {
    let llm
    if (advanced.value) llm = JSON.parse(modelJson.value)
    else {
      const f = providerForm.value
      if (!f.provider.trim() || !f.model.trim() || !f.base_url.trim() || !f.api_key.trim()) throw new Error('请填写来源、模型 ID、端点和 API Key')
      if (!/^[a-zA-Z0-9_.-]+$/.test(f.provider)) throw new Error('来源名称请使用字母、数字、下划线或短横线')
      llm = JSON.parse(JSON.stringify(modelConfig.value.llm)); llm.models ||= {}
      const existing = llm.models[f.provider] || { models: {} }
      existing.models ||= {}
      existing.models[f.model] = { base_url: f.base_url, api_key: f.api_key, api: f.api, input: f.image ? ['text', 'image'] : ['text'], reasoning_efforts: f.efforts.split(',').map(s => s.trim()).filter(Boolean) }
      llm.models[f.provider] = existing; llm.default ||= `${f.provider}/${f.model}`
    }
    const data = await api('/models', { method: 'PUT', body: { llm, revision: modelConfig.value.revision } }); modelConfig.value = data; profiles.value = data.profiles; modelJson.value = JSON.stringify(data.llm, null, 2)
    if (!modelId.value) modelId.value = data.llm.default
    providerForm.value.api_key = ''; modal.value = ''; toast('模型配置已保存')
  }); saving.value = false
}
function closeModal() { modal.value = '' }
async function showFiles() { await guard(async () => { fileList.value = selected.value ? await api(`/agent/sessions/${selected.value.id}/files`) : []; modal.value = 'files'; popup.value = '' }) }
async function permission(mode, confirmation = null) {
  error.value = ''
  if (mode === 'allow' && (pendingPermission.value || effectivePermission.value) !== 'allow' && confirmation !== 'ALLOW') {
    allowConfirmation.value = ''; modal.value = 'permission'; popup.value = ''; return
  }
  saving.value = true
  await guard(async () => {
    if (selected.value) selected.value = await api(`/agent/sessions/${selected.value.id}/permission`, { method: 'PUT', body: { mode, confirmation } })
    else draftPermission.value = mode
    modal.value = ''; popup.value = ''
    if (selected.value) toast(pendingPermission.value ? '权限配置已交给内核，将在安全边界生效' : 'permission.saved')
  })
  saving.value = false
}
function approval() { return action('approve', { decisions: selected.value.pending_tool_calls.map(p => ({ tool_call_id: p.toolCallId, decision: decisions.value[p.toolCallId] || 'approve' })) }) }
function answer() { return action('answer', { values: humanValues.value }) }
watch(sidebar, value => localStorage.setItem('sidebar', value))
watch(popup, value => { if (value !== 'model') reasoningModelId.value = '' })
watch(() => selected.value?.status, () => { humanValues.value = {}; decisions.value = {} })
onMounted(() => { init(); document.addEventListener('keydown', keydown); poll = setInterval(() => refreshSessions().catch(() => {}), 3000) })
onUnmounted(() => { source?.close(); clearInterval(poll); clearTimeout(noticeTimer); document.removeEventListener('keydown', keydown); clearAttachments() })
</script>

<template>
  <div class="shell" :class="{ expanded: sidebar, 'document-open': artifactPanelOpen }">
    <aside class="sidebar">
      <div class="sidebar-top"><button class="brand icon-button" :aria-label='t("展开或收起侧栏")' :title='t("Toggle sidebar · Ctrl+Alt+B")' @click="sidebar = !sidebar"><img src="/favicon.svg" alt="Deep Agent"></button><span v-if="sidebar" class="brand-name">Deep Agent</span><button v-if="sidebar" class="icon-button" :aria-label='t("搜索会话")' @click="modal = 'search'"><Icon name="Search" /></button></div>
      <nav>
        <button :class="{ active: chatHome && modal !== 'agent-settings' }" :title='t("New Chat")' @click="newChat"><Icon name="Send" /><span v-if="sidebar">{{ t("New Chat") }}</span></button>
        <button :class="{ active: mainView === 'history' }" :title='t("会话历史")' @click="mainView = 'history'; popup = ''"><Icon name="History" /><span v-if="sidebar">{{ t("History") }}</span></button>
        <button :title='t("Features")' :aria-label='t("Features")' @click="showFeatures"><Icon name="Blocks" /><span v-if="sidebar">{{ t("Features") }}</span></button>
        <button :title='t("Filebase")' :aria-label='t("Filebase")' @click="showFiles"><Icon name="CodeXml" /><span v-if="sidebar">{{ t("Filebase") }}</span></button>
        <button :title="t('Agent settings')" :aria-label="t('Agent settings')" @click="openAgentSettings()"><Icon name="SlidersHorizontal" /><span v-if="sidebar">{{ t('Agent settings') }}</span></button>
      </nav>
      <div v-if="sidebar" class="sidebar-content"><div class="sidebar-section recent-heading">{{ t("RECENT CHATS") }}</div><button v-for="s in recentSessions" :key="s.id" class="recent-item" :class="{ active: mainView === 'agents' && modal !== 'agent-settings' && selected?.id === s.id }" :aria-current="mainView === 'agents' && modal !== 'agent-settings' && selected?.id === s.id ? 'page' : undefined" @click="openSession(s.id)"><span class="status-dot" :class="s.status"></span><span>{{ sessionTitle(s) }}</span></button><p v-if="!sessions.length" class="sidebar-empty">{{ t("Your ideas start here.") }}</p></div>
      <div class="sidebar-bottom"><button class="icon-button" :title='t("模型配置")' :aria-label='t("模型配置")' @click="openModels"><Icon name="Settings2" /></button><span v-if="sidebar" @click="openModels" class="settings-label">{{ t("Settings") }}</span><div v-if="sidebar" class="local-badge">{{ t("LOCAL") }}</div></div>
    </aside>

    <div class="display-preferences" role="group" :aria-label="t('appearance')">
      <button class="icon-button theme-toggle" :title="t(theme === 'dark' ? 'theme.light' : 'theme.dark')" :aria-label="t(theme === 'dark' ? 'theme.light' : 'theme.dark')" :aria-pressed="theme === 'dark'" @click="toggleTheme"><Icon :name="theme === 'dark' ? 'Sun' : 'Moon'" :size="16" /></button>
      <button class="language-toggle" :title="t('language.switch')" :aria-label="t('language.switch')" @click="toggleLocale"><Icon name="Languages" :size="15" /><span>{{ locale === 'zh' ? '中文' : 'EN' }}</span></button>
    </div>

    <main>
      <header v-if="selected || mainView === 'history'" class="page-header"><button @click="newChat" class="text-button"><Icon name="ArrowLeft" :size="15" /> {{ t("Agents") }}</button><span class="header-title">{{ selected ? sessionTitle(selected) : t('All chats') }}</span></header>

      <div v-if="loading" class="loading"><Icon name="LoaderCircle" class="spin" /> {{ t("Loading…") }}</div>
      <div v-else-if="mainView === 'history'" class="history-view"><div class="section-eyebrow">{{ t("YOUR CHATS") }}</div><h1>{{ t("Chats") }}</h1><input v-model="search" class="search-input" :placeholder='t("Search chats…")'><button v-for="s in visibleSessions" :key="s.id" class="history-row" @click="openSession(s.id)"><Icon name="MessageSquare" /><div><strong>{{ sessionTitle(s) }}</strong><small>{{ s.model_id }} · {{ dateLabel(s) }}</small></div><span class="status-tag" :class="s.status">{{ statusLabels[s.status] }}</span><Icon name="ChevronRight" /></button><div v-if="!visibleSessions.length" class="empty-state"><Icon name="MessagesSquare" :size="28" /><h3>{{ t("No chats yet") }}</h3><p>{{ t("Start a new chat to bring your ideas to life.") }}</p><button class="primary" @click="newChat">{{ t("New chat") }}</button></div></div>


      <div v-else class="chat-page" :class="{ 'has-session': selected }">
        <div v-if="!selected" class="home-content">
          <div v-if="!dismissed && !profiles.some(p => p.configured)" class="setup-banner"><Icon name="TriangleAlert" :size="15" /><div><strong>{{ t("Connect a model to get started") }}</strong><span>{{ t("配置模型，即可使用 Deep Agent 的完整能力") }}</span></div><button @click="openModels">{{ t("Set up model") }}</button><button class="icon-button" :aria-label='t("关闭提示")' @click="dismissBanner"><Icon name="X" :size="13" /></button></div>
          <div v-else-if="!dismissed" class="ready-banner"><span class="status-dot completed"></span><span>{{ t("Ready to start") }}</span><button @click="dismissBanner" class="icon-button" :aria-label='t("关闭提示")'><Icon name="X" :size="13" /></button></div>
        </div>

        <div v-if="selected" class="transcript">
          <div class="session-meta"><span class="status-tag" :class="selected.status">{{ statusLabels[selected.status] }}</span></div>
          <template v-for="b in timeline" :key="b.key">
            <div v-if="b.lead" class="message-label agent-label"><img src="/favicon.svg" alt=""> {{ t("Agent") }}</div>
            <article v-if="b.kind === 'user'" class="user-message"><div class="message-label">{{ t("You") }}</div><div>{{ b.content }}</div><div v-if="b.attachments?.length" class="message-attachments"><a v-for="file in b.attachments" :key="file.id" :href="fileUrl(file.path)" download><Icon name="Paperclip" :size="13" />{{ file.name }}</a></div></article>
            <WorkProcess v-else-if="b.kind === 'process'" :process="b" :action-label="actionLabel" :status-labels="statusLabels" />
            <article v-else-if="b.kind === 'assistant'" class="assistant-message"><div v-html="html(b.content)" class="markdown"></div></article>
            <section v-else-if="b.kind === 'files'" class="output-files">
              <div class="output-files-heading"><Icon name="FileBox" :size="14" />{{ t("artifacts.title") }}</div>
              <FileBlock v-for="file in b.files" :key="file.path" :path="file.path" :size="file.size" :download-url="fileUrl(file.path)" />
            </section>
            <div v-else class="turn-note"><Icon name="CornerDownRight" :size="14" />{{ b.type === 'steer' ? t('追加指令') : t('后续任务') }}：{{ b.content }}</div>
          </template>
          <div v-if="busy" class="working"><Icon name="LoaderCircle" :size="15" class="spin" />{{ selected.status === 'queued' ? t('Queued…') : t('Agent is working…') }}<button @click="action('pause')" :disabled="selected.status !== 'running'">{{ t("Pause") }}</button><button @click="action('cancel')" :disabled="selected.status !== 'running'">{{ t("Stop") }}</button></div>
          <div v-if="selected.error" class="session-error"><Icon name="CircleAlert" />{{ t(selected.error) }}</div>
          <section v-if="selected.artifacts.length" class="output-files">
            <div class="output-files-heading"><Icon name="FileBox" :size="14" />{{ t("artifacts.title") }}</div>
            <FileBlock v-for="file in selected.artifacts" :key="file" :path="file" :download-url="`/api/agent/sessions/${selected.id}/artifacts/${encodeURIComponent(file)}`" />
          </section>
          <section v-if="selected.status === 'waiting_confirmation'" class="approval-panel"><div class="panel-title"><Icon name="ShieldCheck" />{{ t("Review tool calls") }}</div><p>{{ t("Agent 请求执行以下操作，请逐项确认。") }}</p><div v-for="call in selected.pending_tool_calls" :key="call.toolCallId" class="approval-call"><div><strong>{{ actionLabel(call.name) }}</strong><select v-model="decisions[call.toolCallId]" :aria-label="t('reviewTool', { name: actionLabel(call.name) })"><option :value="undefined">{{ t("Approve") }}</option><option value="approve">{{ t("Approve") }}</option><option value="reject">{{ t("Reject") }}</option></select></div><dl class="approval-details"><div v-for="(field, index) in approvalFields(call)" :key="index"><dt>{{ field.label }}</dt><dd><pre>{{ field.content }}</pre></dd></div></dl></div><button class="primary" @click="approval">{{ t("Submit decisions") }} <Icon name="ArrowUpRight" :size="14" /></button></section>
          <section v-if="selected.status === 'waiting_human'" class="approval-panel"><div class="panel-title"><Icon name="MessageCircleQuestion" />{{ selected.human_input.title || t('Agent needs your input') }}</div><p>{{ selected.human_input.reason }}</p><h4>{{ selected.human_input.question }}</h4><div v-for="field in selected.human_input.fields" :key="field.id" class="field"><label :for="'human-' + field.id">{{ field.label }}{{ field.required ? ' *' : '' }}</label><select v-if="['single_select', 'multi_select'].includes(field.type)" :id="'human-' + field.id" v-model="humanValues[field.id]" :multiple="field.type === 'multi_select'"><option v-for="o in field.options" :key="o.value" :value="o.value">{{ o.label }} {{ o.description }}</option></select><input v-else-if="field.type === 'boolean'" :id="'human-' + field.id" type="checkbox" v-model="humanValues[field.id]"><textarea v-else :id="'human-' + field.id" v-model="humanValues[field.id]" :placeholder="field.placeholder"></textarea></div><button class="primary" @click="answer">{{ t("Continue") }} <Icon name="ArrowRight" :size="14" /></button></section>
          <section v-if="['paused', 'runtime_config_boundary'].includes(selected.status)" class="pause-panel"><Icon name="Pause" /><span>{{ t("Agent paused") }}</span><button class="primary" @click="action('resume')">{{ t("Continue") }}</button></section>
        </div>

        <div class="composer-area">
          <div class="runtime-bar"><div class="popover-anchor"><button class="text-button runtime-select" :aria-label='t("运行环境")' @click.stop="toggle('runtime')"><Icon name="Cloud" :size="15" /><Icon name="ChevronDown" :size="11" /></button><div v-if="popup === 'runtime'" v-popover-placement class="popover runtime-popover" @click.stop><div class="popover-heading">{{ t("Runtime") }}</div><div class="runtime-card"><Icon name="Monitor" /><div><strong>{{ t("Local files") }}</strong><small>Deep Agent CLI · {{ selected?.sandbox_mode || 'restricted' }}</small></div><Icon name="Check" /></div><p>{{ t(selected?.sandbox_mode === 'full-access' ? 'sandbox.fullAccess' : 'sandbox.restricted') }}</p><button class="menu-row" @click="selectSandbox('restricted')"><Icon name="ShieldCheck"/>restricted<Icon v-if="!selected || selected.sandbox_mode==='restricted'" name="Check"/></button><button class="menu-row" @click="selectSandbox('full-access')"><Icon name="Monitor"/>full-access<Icon v-if="selected?.sandbox_mode==='full-access'" name="Check"/></button><p v-if="selected?.pending_sandbox_mode">{{ t('sandbox.pending') }} → {{ selected.pending_sandbox_mode }}</p><button class="menu-row" @click="showFiles"><Icon name="Files" />{{ t("Browse files") }}</button></div></div><div v-if="selected" class="composer-spacer"></div><button v-if="selected && !busy && !waiting" class="text-button compact-button" @click="action('compact')"><Icon name="Minimize2" :size="13" />{{ t("Compact context") }}</button></div>
          <form class="composer" :class="{ 'is-dragging-files': fileDragDepth > 0 && !uploadDisabled }" @submit.prevent="send" @dragenter="fileDrag($event, true)" @dragover="fileDrag($event)" @dragleave="fileDragLeave" @drop="dropFiles">
            <div v-if="fileDragDepth > 0 && !uploadDisabled" class="composer-drop-hint" role="status"><Icon name="Upload" :size="22" /><span>{{ t('upload.drop') }}</span></div>
            <textarea v-model="draft" :placeholder="busy ? t('Add instructions while Agent works…') : selected ? t('Ask a follow-up…') : t('Ask Agent to build, fix bugs, explore')" :aria-label='t("任务输入")' @keydown="composerKey" @paste="pasteImages"></textarea>
            <div v-if="attachments.length" class="file-attachments"><div v-for="(file, i) in attachments" :key="file.id" class="attachment-chip"><img v-if="file.url" :src="file.url" :alt="file.name"><Icon v-else name="FileText" :size="18" /><div><strong>{{ file.name }}</strong><small>{{ (file.size / 1024).toFixed(1) }} KB</small></div><button type="button" class="icon-button" :aria-label="t('upload.remove', { name: file.name })" @click="removeAttachment(i)"><Icon name="X" :size="13" /></button></div></div>
            <div class="composer-toolbar"><button type="button" class="icon-button add-context" :aria-label="t('upload.files')" :title="t('upload.files')" :disabled="uploading || sending || busy || waiting" @click="fileInput.click()"><Icon :name="uploading ? 'LoaderCircle' : 'Plus'" :class="{ spin: uploading }" :size="18" /></button>
              <div class="popover-anchor model-anchor">
                <button type="button" class="text-button model-select" :aria-label="t('Select model')" :title="modelLabel" @click.stop="toggle('model')"><Icon name="Brain" :size="14" /><span class="model-name">{{ modelLabel }}</span><span v-if="profile?.reasoning_efforts.length" class="model-current-effort">{{ effort || t('Default') }}</span><Icon name="ChevronDown" :size="12" /></button>
                <div v-if="popup === 'model'" v-popover-placement class="popover model-popover" @click.stop>
                  <template v-if="reasoningProfile">
                    <div class="model-reasoning-heading"><button type="button" class="icon-button" :aria-label="t('model.back')" @click="reasoningModelId = ''"><Icon name="ArrowLeft" :size="14" /></button><div><strong>{{ t('Reasoning effort') }}</strong><small>{{ reasoningProfile.model }}</small></div></div>
                    <button type="button" class="menu-row reasoning-option" @click="selectModel(reasoningProfile.id, '')"><span>{{ t('Default') }}</span><Icon v-if="!reasoningChoice" name="Check" :size="14" /></button>
                    <button v-for="strength in reasoningProfile.reasoning_efforts" :key="strength" type="button" class="menu-row reasoning-option" @click="selectModel(reasoningProfile.id, strength)"><span>{{ strength }}</span><Icon v-if="reasoningChoice === strength" name="Check" :size="14" /></button>
                  </template>
                  <template v-else>
                    <div class="popover-heading">{{ t('Select model') }}</div><div v-if="!profiles.length" class="popover-empty">{{ t('Add a model provider to start.') }}</div>
                    <div v-for="p in profiles" :key="p.id" class="model-option-row"><button type="button" class="menu-row model-row" @click="selectModel(p.id, p.id === modelId ? effort : undefined)"><span>{{ p.model }}<small>{{ p.id }} · {{ p.api }}</small></span><span v-if="!p.configured" class="unconfigured">{{ t('未就绪') }}</span><Icon v-if="modelId === p.id" name="Check" :size="14" /></button><button v-if="p.reasoning_efforts.length" type="button" class="icon-button model-effort-edit" :aria-label="t('model.editEffort', { name: p.model })" :title="t('Reasoning effort')" @click.stop="reasoningModelId = p.id"><Icon name="Pencil" :size="14" /></button></div>
                  </template>
                </div>
              </div>
              <div class="popover-anchor permission-anchor"><button type="button" class="text-button permission-select" :aria-label="t('Permission')" :disabled="waiting || sending" @click.stop="toggle('permission')"><Icon name="ShieldCheck" :size="13" /><span class="permission-title">{{ t('Permission') }}</span><span>{{ effectivePermission }}</span><span v-if="pendingPermission" class="permission-pending">→ {{ pendingPermission }}</span><Icon name="ChevronDown" :size="11" /></button><div v-if="popup === 'permission'" v-popover-placement class="popover permission-popover" @click.stop><div class="popover-heading">{{ t('Permission') }}</div><button class="menu-row" @click="permission('ask')"><Icon name="ShieldCheck" :size="15" /><span>ask<small>{{ t('permission.askDescription') }}</small></span><Icon v-if="(pendingPermission || effectivePermission) === 'ask'" name="Check" :size="14" /></button><button class="menu-row" @click="permission('allow')"><Icon name="Wrench" :size="15" /><span>allow<small>{{ t('permission.allowDescription') }}</small></span><Icon v-if="(pendingPermission || effectivePermission) === 'allow'" name="Check" :size="14" /></button><p class="permission-note">{{ t('sandbox.independentPermission') }}</p><p v-if="pendingPermission" class="permission-note">{{ t('permission.pending') }}</p></div></div>
              <div class="composer-spacer"></div><button v-if="busy && draft.trim()" type="button" class="text-button queue-button" @click="queueFollowUp">{{ t("Queue") }}</button><button v-if="!draft.trim()" type="button" class="icon-button files-shortcut" :title='t("查看文件")' @click="showFiles"><Icon name="Paperclip" :size="15" /></button><button type="submit" class="send-button" :disabled="(!draft.trim() && !attachments.length) || sending || waiting || uploading" :aria-label="busy ? t('追加指令') : t('发送任务')"><Icon :name="sending ? 'LoaderCircle' : (draft.trim() || attachments.length) ? 'ArrowUp' : 'AudioLines'" :size="17" :class="{ spin: sending }" /></button>
            </div>
          </form>
          <input ref="fileInput" type="file" multiple hidden @change="upload">
          <div v-if="selected" class="composer-caption"><span>{{ busy ? t('Enter to steer · Queue for a follow-up') : t('Enter to send · Shift+Enter for a new line') }}</span><span class="composer-usage"><span v-if="selected.usage?.input_tokens != null">{{ selected.usage.input_tokens.toLocaleString() }} {{ t("tokens") }}</span><span v-else>Deep Agent CLI</span><span class="cache-hit-rate" :title="cacheHitTitle">{{ t('usage.cacheHit') }} {{ cacheHitLabel }}</span></span></div>
          <div v-if="error && !modal" role="alert" class="error-inline"><Icon name="CircleAlert" :size="14" />{{ t(error) }}<button @click="error = ''" class="icon-button" :aria-label='t("关闭错误")'><Icon name="X" :size="13" /></button></div>
        </div>

        <section v-if="!selected" class="recent-chats"><template v-if="visibleSessions.length"><button v-for="s in visibleSessions.slice(0, 12)" :key="s.id" class="chat-row" @click="openSession(s.id)"><div class="chat-thumbnail"><Icon :name="s.status === 'completed' ? 'Check' : ['failed', 'interrupted'].includes(s.status) ? 'CircleAlert' : s.status === 'running' ? 'LoaderCircle' : 'GitBranch'" :size="20" :class="{ spin: s.status === 'running' }" /><span class="status-pill" :class="s.status">{{ statusLabels[s.status] }}</span></div><div class="chat-info"><strong>{{ sessionTitle(s) }}</strong><div><span class="status-dot" :class="s.status"></span><span>{{ s.model_id.split('/').at(-1) }}</span><span>{{ dateLabel(s) }}</span></div></div><Icon class="chat-arrow" name="ArrowUpRight" :size="15" /></button></template><div v-else class="home-empty"><div class="empty-mark"><Icon name="MessageSquarePlus" :size="23" /></div><h3>{{ t("A little idea. A new beginning.") }}</h3><p>{{ t("Build something, explore a question, or create your next document.") }}</p><span>{{ t("Your chats will appear here.") }}</span></div></section>
      </div>
      <div v-if="error && mainView === 'history'" class="error-inline">{{ t(error) }}</div>
    </main>

    <div v-if="popup" class="popover-backdrop" @click="popup = ''"></div>
    <Transition name="toast"><div v-if="notice" class="toast" role="status"><Icon name="CircleCheck" :size="16" />{{ t(notice) }}</div></Transition>

    <FeaturePanels ref="featurePanels" :session="selected" :events="events" @open="artifactPanelOpen = $event" @context="artifactContext = $event" @error="error = $event" />
    <div v-if="modal" class="modal-backdrop" @click.self="closeModal">
      <section class="modal" :class="{ 'wide-modal': modal === 'agent-settings' || modal === 'models' }" role="dialog" aria-modal="true" :aria-label="modal === 'sandbox' ? t('sandbox.title') : modal === 'features' ? t('Features') : modal === 'permission' ? t('permission.confirmTitle') : modal === 'models' ? t('模型配置') : t('Agent settings')">
        <header class="modal-header"><div><Icon :name="modal === 'models' ? 'Sparkles' : modal === 'files' ? 'Files' : modal === 'permission' ? 'ShieldCheck' : 'SlidersHorizontal'" :size="18" /><h2>{{ modal === 'sandbox' ? t('sandbox.title') : modal === 'features' ? t('Features') : modal === 'permission' ? t('permission.confirmTitle') : modal === 'models' ? t('Model settings') : modal === 'agent-settings' ? t('Agent settings') : modal === 'search' ? t('Search chats') : t('Files') }}</h2></div><button class="icon-button" :aria-label='t("关闭设置")' @click="closeModal"><Icon name="X" :size="18" /></button></header>
        <template v-if="modal === 'sandbox'"><form @submit.prevent="selectSandbox('full-access',sandboxConfirmation)"><div class="modal-body form-stack"><p>{{ t('sandbox.confirmWarning') }}</p><label>{{ t('sandbox.typeConfirmation') }}<input v-model="sandboxConfirmation" autocomplete="off" placeholder="FULL_ACCESS"></label></div><div v-if="error" class="modal-error">{{ error }}</div><footer class="modal-footer"><button type="button" class="secondary" @click="modal=''">{{ t('Cancel') }}</button><button class="primary" :disabled="saving||sandboxConfirmation!=='FULL_ACCESS'">{{ t('sandbox.enableFull') }}</button></footer></form></template>
        <template v-else-if="modal === 'features'"><div class="modal-body"><p class="muted">{{ t('features.explain') }}</p><div v-for="feature in featuresList" :key="feature.id" class="feature-setting"><strong>{{ feature.name }}</strong><small>{{ feature.installed ? (feature.enabled ? t('Enabled') : t('Disabled')) : t('features.uninstalled') }}</small><button class="secondary" @click="setFeature(feature,!feature.enabled)">{{ !feature.installed ? t('features.install') : feature.enabled ? t('features.disable') : t('features.enable') }}</button><button v-if="feature.installed" class="text-button danger" @click="setFeature(feature,false,true)">{{ t('features.uninstall') }}</button></div></div><div v-if="error" class="modal-error">{{ error }}</div></template>
        <template v-else-if="modal === 'permission'"><form @submit.prevent="permission('allow', allowConfirmation)"><div class="modal-body form-stack"><p class="muted">{{ t('permission.allowWarning') }}</p><div class="field"><label for="allow-confirmation">{{ t('permission.typeAllow') }}</label><input id="allow-confirmation" v-model="allowConfirmation" :aria-label="t('permission.typeAllow')" autocomplete="off" spellcheck="false" placeholder="ALLOW" autofocus></div></div><div v-if="error" class="modal-error" role="alert">{{ t(error) }}</div><footer class="modal-footer"><button type="button" class="secondary" @click="modal = ''">{{ t('Cancel') }}</button><button class="primary" :disabled="saving || allowConfirmation !== 'ALLOW'">{{ t('permission.enableAllow') }}</button></footer></form></template>
        <template v-else-if="modal === 'agent-settings' && settingsEdit"><div class="modal-tabs"><button v-for="(label, key) in { general: t('General'), tools: t('Tools'), skills: t('Skills') }" :key="key" :class="{ active: configTab === key }" @click="configTab = key">{{ label }}</button><span>v{{ settingsEdit.revision }}</span></div><div class="modal-body">
          <div v-if="configTab === 'general'" class="form-stack"><div class="field"><label>{{ t("System prompt") }}</label><textarea v-model="settingsEdit.system_prompt" rows="5"></textarea></div><div class="field"><label>{{ t("Rules") }} <small>{{ t("每行一条规则") }}</small></label><textarea v-model="rulesText" rows="4"></textarea></div><div class="settings-note"><Icon name="Info" :size="14" />{{ t("配置保存后用于新会话；现有会话保留创建时的配置快照。") }}</div></div>
          <div v-if="configTab === 'tools'"><fieldset ><div class="settings-intro"><h3>{{ t("Core tools") }}</h3><p>{{ t("使用 deep_agent_cli 的原生工具，具有副作用的操作交由内核请求审批。") }}</p></div><div class="core-tools"><label v-for="(label, key) in toolLabels" :key="key" class="tool-toggle"><div><Icon :name="key === 'execute' ? 'Terminal' : key === 'task' ? 'Network' : 'Wrench'" :size="15" /><span>{{ label }}<small>{{ key }}</small></span></div><input type="checkbox" :checked="settingsEdit.core_tools[key] !== false" @change="settingsEdit.core_tools[key] = $event.target.checked"></label></div><div class="settings-intro office-intro"><h3>{{ t("Additional tools") }}</h3><p>{{ t("编辑已注册工具的描述与 JSON Schema。参数名称和必填项需保留。") }}</p></div><div class="tool-catalog"><button v-for="tool in toolCatalog.filter(t => !settingsEdit.tools.some(w => w.name === t.name))" :key="tool.name" class="secondary" @click="addTool(tool)"><Icon name="Plus" :size="13" />{{ tool.name }}</button></div><p v-if="!settingsEdit.tools.length" class="muted">{{ t("没有额外的 Office 工具，仍可使用上方内核工具。") }}</p><details v-for="tool in settingsEdit.tools" :key="tool.name" class="tool-config"><summary><Icon name="FileBox" :size="16" />{{ tool.name }}<span class="spacer"></span><span>{{ tool.enabled ? t('Enabled') : t('Disabled') }}</span><Icon name="ChevronDown" :size="14" /></summary><div class="form-stack"><label class="checkbox-label"><input v-model="tool.enabled" type="checkbox">{{ t("启用此工具") }}</label><div class="field"><label>{{ t("Description") }}</label><textarea v-model="tool.description" rows="2"></textarea></div><div class="field"><label>JSON Schema</label><textarea class="code-input" v-model="toolSchemas[tool.name]" rows="12" spellcheck="false"></textarea></div></div></details></fieldset></div>
          <div v-if="configTab === 'tools'" class="mcp-settings"><div class="settings-intro"><h3>{{ t("MCP servers") }}</h3><p>{{ t('sandbox.mcpScope') }}</p></div><textarea class="code-input" v-model="mcpJson"  rows="9" spellcheck="false" :aria-label='t("MCP JSON 配置")'></textarea><p class="muted">{{ t('mcp.example') }}</p></div>
          <div v-if="configTab === 'skills'">
            <div class="settings-intro"><h3>{{ t('Skills') }}</h3><p>{{ t('skills.configureHint') }}</p></div>

            <fieldset  class="installed-skills">
              <div v-if="!settingsEdit.native_skills.length && !settingsEdit.skills.length" class="settings-empty"><Icon name="BookOpen" :size="28" /><p>{{ t('skills.empty') }}</p></div>
              <div v-for="skill in settingsEdit.native_skills" :key="skill.id" class="skill-card installed-skill">
                <div class="skill-heading"><Icon name="BookOpen" :size="16" /><strong>{{ skill.name }}</strong><label class="checkbox-label"><input type="checkbox" :aria-label="t('skills.enable', { name: skill.name })" :checked="settingsEdit.skill_bindings[skill.id] ?? skill.enabled" @change="settingsEdit.skill_bindings[skill.id] = $event.target.checked">{{ t('Enabled') }}</label></div>
                <p class="muted">{{ skill.description }}</p><small class="skill-source">{{ t('skills.installed') }}</small>
              </div>
              <div v-for="(skill, i) in settingsEdit.skills" :key="'configured-' + i" class="skill-card installed-skill">
                <div class="skill-heading"><Icon name="BookOpen" :size="16" /><strong>{{ skill.name }}</strong><label class="checkbox-label"><input type="checkbox" :aria-label="t('skills.enable', { name: skill.name })" :checked="skill.enabled !== false" @change="skill.enabled = $event.target.checked">{{ t('Enabled') }}</label></div>
                <p v-if="skill.description" class="muted">{{ skill.description }}</p><small class="skill-source">{{ t('skills.configured') }}</small>
              </div>
            </fieldset>
          </div>
        </div><div v-if="error" class="modal-error" role="alert">{{ t(error) }}</div><footer class="modal-footer"><span>{{ t("Applies to new chats") }}</span><button class="secondary" @click="modal = ''">{{ t("Cancel") }}</button><button class="primary" :disabled="saving" @click="saveSettings"><Icon v-if="saving" name="LoaderCircle" class="spin" :size="14" />{{ t("Save changes") }}</button></footer></template>

        <template v-else-if="modal === 'models'"><div class="modal-tabs"><button :class="{ active: !advanced }" @click="advanced = false">{{ t("Providers & models") }}</button><button :class="{ active: advanced }" @click="advanced = true">{{ t("Advanced JSON") }}</button><span>v{{ modelConfig?.revision }}</span></div><div class="modal-body"><template v-if="!advanced"><div class="configured-models" v-if="profiles.length"><div v-for="p in profiles" :key="p.id"><Icon name="Sparkles" :size="16" /><div><strong>{{ p.model }}</strong><small>{{ p.id }} · {{ p.api }}</small></div><span class="model-ready" :class="{ ready: p.configured }">{{ p.configured ? t('Ready') : t('Key missing') }}</span></div></div><div class="settings-intro"><h3>{{ t("Add a model") }}</h3><p>{{ t("兼容 deep_agent_cli 配置，支持 Chat Completions 和 Responses。") }}</p></div><div class="form-grid"><div class="field"><label>{{ t("来源名称") }}</label><input v-model="providerForm.provider" :placeholder="t('e.g. openai')"></div><div class="field"><label>{{ t("模型 ID") }}</label><input v-model="providerForm.model" :placeholder='t("Provider model ID")'></div><div class="field full"><label>Base URL</label><input v-model="providerForm.base_url" placeholder="https://your-provider.example/v1"></div><div class="field full"><label>API Key</label><input v-model="providerForm.api_key" type="password" autocomplete="new-password" :placeholder='t("API Key 或 ${ENV_VAR}")'><small>{{ t("密钥在服务端加密保存，读取时脱敏。") }}</small></div><div class="field"><label>{{ t("API 协议") }}</label><select v-model="providerForm.api"><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option></select></div><div class="field"><label>{{ t("思考强度") }} <small>{{ t("逗号分隔") }}</small></label><input v-model="providerForm.efforts" placeholder="low, medium, high"></div><label class="checkbox-label full"><input v-model="providerForm.image" type="checkbox">{{ t("模型支持图片输入") }}</label></div></template><template v-else><p class="muted">{{ t("编辑完整 llm 配置，可删除模型、修改默认模型和模型参数。保留 __UNCHANGED_SECRET__ 可沿用已保存的密钥。") }}</p><textarea class="code-input advanced-json" v-model="modelJson" spellcheck="false" :aria-label='t("模型高级 JSON 配置")'></textarea></template></div><div v-if="error" class="modal-error" role="alert">{{ t(error) }}</div><footer class="modal-footer"><span>{{ profiles.length }} {{ t("models configured") }}</span><button class="secondary" @click="modal = ''">{{ t("Cancel") }}</button><button class="primary" :disabled="saving" @click="saveModels">{{ saving ? t('Saving…') : advanced ? t('Save configuration') : t('Add model') }}</button></footer></template>

        <template v-else-if="modal === 'files'"><div class="modal-body"><p class="muted">{{ t("当前会话的文件库") }}</p><div v-if="!fileList.length" class="settings-empty"><Icon name="FolderOpen" :size="30" /><p>{{ t("No files yet.") }}</p></div><div v-for="file in fileList" :key="file.path" class="file-row"><Icon name="FileText" :size="16" /><span>{{ file.path }}</span><small>{{ (file.size / 1024).toFixed(1) }} KB</small><a :href="fileUrl(file.path)" download :aria-label="t('Download')"><Icon name="Download" :size="15" /></a></div></div></template>
        <template v-else-if="modal === 'search'"><div class="modal-body"><input v-model="search" class="search-input" autofocus :placeholder='t("Search chats…")'><button v-for="s in sessions.filter(s => sessionTitle(s).toLowerCase().includes(search.toLowerCase()))" :key="s.id" class="menu-row" @click="openSession(s.id); modal = ''"><Icon name="MessageSquare" /><span>{{ sessionTitle(s) }}<small>{{ s.model_id }}</small></span></button><p v-if="!sessions.length" class="muted">{{ t("No chats yet.") }}</p></div></template>
      </section>
    </div>
  </div>
</template>
