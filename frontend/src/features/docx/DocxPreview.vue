<script setup>
import { ref, watch, computed, onBeforeUnmount, onMounted, nextTick } from 'vue'
import Icon from '../../Icon.vue'
import { t } from '../../preferences'
const props = defineProps({ url: String, revision: Number, renderRevision: Number, renderPages: Number, renderBaseUrl: String,
  pages: { type: Array, default: () => [] }, storageKey: String })
const emit = defineEmits(['rendered'])
const scroller = ref(null), loading = ref(false), error = ref(''), follow = ref(true), images = ref([]), shownRevision = ref(-1)
const zoom = ref('page'), paperWidth = ref(500)
let resizeObserver
function resizePages() {
  const el = scroller.value
  if (!el) return
  paperWidth.value = zoom.value === 'page' ? Math.max(180, Math.min(el.clientWidth - 36, (el.clientHeight - 65) * 210 / 297)) : zoom.value === 'width' ? Math.max(180, el.clientWidth - 36) : 794 * Number(zoom.value) / 100
}
watch(zoom, resizePages)
onMounted(() => { resizeObserver = new ResizeObserver(resizePages); resizeObserver.observe(scroller.value); resizePages() })
const pending = computed(() => shownRevision.value < props.revision)
let generation = 0, disposed = false, restoring = false
function rememberScroll() {
  if (restoring || !scroller.value) return
  const el = scroller.value
  follow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 100
  if (props.storageKey) sessionStorage.setItem(props.storageKey + ':scroll', String(el.scrollTop))
}
async function update() {
  const revision = props.renderRevision
  if (!Number.isInteger(revision) || revision < 0 || revision < shownRevision.value || !props.renderPages || !props.renderBaseUrl) return
  const ticket = ++generation
  loading.value = true; error.value = ''
  const started = performance.now()
  try {
    const next = Array.from({ length: props.renderPages }, (_, i) => ({ page: i + 1,
      url: `${props.renderBaseUrl}/${revision}/page-${String(i+1).padStart(3,'0')}.png` }))
    // Decode immutable pages before swapping. A late older render cannot replace
    // newer pages, and the existing paper never flashes blank during loading.
    await Promise.all(next.map(item => new Promise((resolve, reject) => {
      const image = new Image(); image.onload = resolve; image.onerror = () => reject(new Error(t('docx.loadError'))); image.src = item.url
    })))
    if (ticket !== generation || disposed || revision < shownRevision.value) return
    const el = scroller.value
    const firstLoad = images.value.length === 0
    const saved = props.storageKey ? sessionStorage.getItem(props.storageKey + ':scroll') : null
    const top = firstLoad ? Number(saved || 0) : el.scrollTop
    restoring = true
    images.value = next; shownRevision.value = revision
    await nextTick()
    if (firstLoad && saved !== null) { el.scrollTop = top; follow.value = false }
    else el.scrollTop = follow.value ? el.scrollHeight : top
    requestAnimationFrame(() => { restoring = false })
    emit('rendered', revision)
    performance.measure('docx-preview-update', { start: started, end: performance.now(), detail: { revision } })
  } catch (e) { if (ticket === generation) error.value = e.message }
  finally { if (ticket === generation) loading.value = false }
}
watch(() => [props.renderBaseUrl, props.renderRevision, props.renderPages], update, { immediate: true })
onBeforeUnmount(() => { disposed = true; generation++; resizeObserver?.disconnect() })
</script>
<template>
  <div class="docx-preview-status" role="status"><span>{{ t('docx.revision', { revision: shownRevision < 0 ? 0 : shownRevision }) }}</span><span v-if="loading || pending">{{ t('docx.paginating') }}</span><select v-model="zoom" :aria-label="t('docx.zoom')"><option value="page">{{ t('docx.fitPage') }}</option><option value="width">{{ t('docx.fitWidth') }}</option><option value="75">75%</option><option value="100">100%</option><option value="125">125%</option></select><button v-if="!follow" type="button" class="text-button" @click="follow = true; scroller.scrollTop = scroller.scrollHeight">{{ t('docx.follow') }}</button></div>
  <p v-if="error" class="docx-preview-error" role="alert">{{ error }} <button type="button" class="text-button" @click="update">{{ t('docx.retry') }}</button></p>
  <div ref="scroller" class="docx-preview-scroll" @scroll.passive="rememberScroll">
    <div class="docx-preview-pages" :style="{ '--paper-width': paperWidth + 'px' }">
      <figure v-for="image in images" :key="image.page" class="docx-paper"><img :src="image.url" :alt="t('docx.page', { page: image.page })" draggable="false"><figcaption>{{ t('docx.page', { page: image.page }) }} / {{ images.length }}</figcaption></figure>
      <figure v-if="!images.length" class="docx-paper blank"><div class="docx-blank-sheet"></div><figcaption>{{ t('docx.page', { page: 1 }) }}</figcaption></figure>
    </div>
  </div>
  <div v-if="pages.length" class="docx-page-states"><span v-for="page in pages" :key="page.page" :class="page.status"><Icon :name="page.status === 'passed' ? 'Check' : page.status === 'failed' ? 'TriangleAlert' : 'Clock'" :size="12" />{{ t('docx.page', { page: page.page }) }} · {{ t('docx.' + page.status) }}</span></div>
</template>
<style>
.docx-preview-scroll{flex:1;min-height:0;overflow:auto;background:#dfe2e7;overscroll-behavior:contain;color-scheme:light}.docx-preview-pages{min-height:100%;min-width:100%;width:max-content;padding:20px 18px 28px;display:flex;flex-direction:column;align-items:center;gap:24px}.docx-paper{margin:0;width:var(--paper-width);flex-shrink:0;position:relative}.docx-paper>img{display:block;width:100%;height:auto;background:white;box-shadow:0 2px 10px #0002;user-select:none}.docx-paper>figcaption{text-align:center;font-size:10px;color:#626a74;margin-top:8px;font-variant-numeric:tabular-nums}.docx-blank-sheet{width:100%;aspect-ratio:210/297;background:white;box-shadow:0 2px 10px #0002}.docx-preview-status{display:flex;align-items:center;gap:15px;padding:9px 16px;border-bottom:1px solid var(--border);font-size:11px;color:var(--text-muted)}.docx-preview-status>select{margin-left:auto;font-size:10px;max-width:115px;padding:3px 6px}.docx-preview-status>button{margin-left:auto}.docx-preview-error{padding:10px;font-size:12px;color:var(--error-text)}.docx-page-states{display:flex;flex-wrap:wrap;gap:8px;padding:10px 14px;border-top:1px solid var(--border);max-height:110px;overflow:auto}.docx-page-states>span{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--text-muted)}.docx-page-states .passed{color:var(--success-text)}.docx-page-states .failed{color:var(--error-text)}
</style>
