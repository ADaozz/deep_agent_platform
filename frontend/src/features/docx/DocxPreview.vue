<script setup>
import { ref, watch, computed, onBeforeUnmount, onMounted, nextTick } from 'vue'
import { t } from '../../preferences'
const props = defineProps({ revision: Number, renderRevision: Number, renderPages: Number, pageHashes: Array, renderBaseUrl: String,
  imageBaseUrl: String, pages: { type: Array, default: () => [] }, storageKey: String })
const emit = defineEmits(['rendered'])
const stage = ref(null), thumbs = ref(null), loading = ref(false), error = ref(''), follow = ref(true), images = ref([]), shownRevision = ref(-1)
const selected = ref(Number(props.storageKey && sessionStorage.getItem(props.storageKey + ':page')) || 1)
const changed = ref(new Set()), zoom = ref('page'), paperWidth = ref(500)
const pending = computed(() => shownRevision.value < props.revision)
const current = computed(() => images.value[Math.min(selected.value, images.value.length) - 1])
const states = computed(() => Object.fromEntries(props.pages.map(page => [page.page, page.status])))
const changedLabel = computed(() => [...changed.value].sort((a, b) => a - b).join(', '))
let resizeObserver, generation = 0, disposed = false

function resizePage() {
  const el = stage.value
  if (!el) return
  paperWidth.value = zoom.value === 'page' ? Math.max(180, Math.min(el.clientWidth - 36, (el.clientHeight - 56) * 210 / 297))
    : zoom.value === 'width' ? Math.max(180, el.clientWidth - 36) : 794 * Number(zoom.value) / 100
}
watch(zoom, resizePage)
onMounted(() => { resizeObserver = new ResizeObserver(resizePage); resizeObserver.observe(stage.value); resizePage() })
onBeforeUnmount(() => { disposed = true; generation++; resizeObserver?.disconnect() })

function pageList(revision) {
  const hashes = props.pageHashes?.length === props.renderPages && props.imageBaseUrl ? props.pageHashes : null
  return Array.from({ length: props.renderPages }, (_, i) => hashes
    ? { page: i + 1, key: hashes[i], url: `${props.imageBaseUrl}/${hashes[i]}.png` }
    : { page: i + 1, key: `${revision}:${i}`, url: `${props.renderBaseUrl}/${revision}/page-${String(i + 1).padStart(3, '0')}.png` })
}
function decode(url) {
  return new Promise((resolve, reject) => {
    const image = new Image(); image.onload = resolve; image.onerror = () => reject(new Error(t('docx.loadError'))); image.src = url
  })
}
async function update() {
  const revision = props.renderRevision
  if (!Number.isInteger(revision) || revision < 0 || revision < shownRevision.value || !props.renderPages || !props.renderBaseUrl) return
  const ticket = ++generation
  loading.value = true; error.value = ''
  const started = performance.now()
  try {
    const next = pageList(revision)
    const known = new Set(images.value.map(item => item.key))
    // Unchanged pages keep their URL and decoded image; only new page images are fetched
    // before the swap, so a late older render cannot replace newer pages.
    await Promise.all(next.filter(item => !known.has(item.key)).map(item => decode(item.url)))
    if (ticket !== generation || disposed || revision < shownRevision.value) return
    const firstLoad = !images.value.length
    const updated = next.filter((item, i) => images.value[i]?.key !== item.key).map(item => item.page)
    images.value = next; shownRevision.value = revision
    changed.value = new Set(firstLoad ? [] : updated)
    if (follow.value && !firstLoad && updated.length) show(updated[0])
    else if (selected.value > next.length) show(next.length)
    emit('rendered', revision)
    performance.measure('docx-preview-update', { start: started, end: performance.now(), detail: { revision, changed: updated.length } })
  } catch (e) { if (ticket === generation) error.value = e.message }
  finally { if (ticket === generation) loading.value = false }
}
watch(() => [props.renderBaseUrl, props.renderRevision, props.renderPages, props.pageHashes], update, { immediate: true })

async function show(page) {
  selected.value = Math.max(1, Math.min(page, images.value.length || 1))
  if (props.storageKey) sessionStorage.setItem(props.storageKey + ':page', String(selected.value))
  await nextTick()
  if (stage.value) stage.value.scrollTop = 0
  thumbs.value?.querySelector(`[data-page="${selected.value}"]`)?.scrollIntoView({ block: 'nearest' })
}
function select(page) { follow.value = false; show(page) }
function resumeFollow() { follow.value = true; show(changed.value.size ? Math.min(...changed.value) : images.value.length) }
function onKey(event) {
  const step = { ArrowDown: 1, PageDown: 1, ArrowRight: 1, ArrowUp: -1, PageUp: -1, ArrowLeft: -1 }[event.key]
  if (!step || !current.value) return
  event.preventDefault(); select(current.value.page + step)
}
</script>
<template>
  <div class="docx-preview-status" role="status"><span>{{ t('docx.revision', { revision: shownRevision < 0 ? 0 : shownRevision }) }}</span><span v-if="loading || pending">{{ t('docx.paginating') }}</span><span v-else-if="changed.size" class="docx-changed-note">{{ t('docx.changedPages', { pages: changedLabel }) }}</span><select v-model="zoom" :aria-label="t('docx.zoom')"><option value="page">{{ t('docx.fitPage') }}</option><option value="width">{{ t('docx.fitWidth') }}</option><option value="75">75%</option><option value="100">100%</option><option value="125">125%</option></select><button v-if="!follow" type="button" class="text-button" @click="resumeFollow">{{ t('docx.follow') }}</button></div>
  <p v-if="error" class="docx-preview-error" role="alert">{{ error }} <button type="button" class="text-button" @click="update">{{ t('docx.retry') }}</button></p>
  <div class="docx-workbench">
    <nav ref="thumbs" class="docx-thumbnails" :aria-label="t('docx.pages')">
      <button v-for="image in images" :key="image.page" type="button" :data-page="image.page" :class="{ selected: image.page === current?.page }" @click="select(image.page)">
        <span class="docx-thumb"><img :src="image.url" alt="" loading="lazy" draggable="false"><i v-if="changed.has(image.page)" class="docx-changed-dot"></i></span>
        <span class="docx-thumb-label">{{ String(image.page).padStart(2, '0') }}<template v-if="states[image.page]"> · {{ t('docx.' + states[image.page]) }}</template></span>
      </button>
    </nav>
    <div ref="stage" class="docx-stage" tabindex="0" @keydown="onKey">
      <figure v-if="current" class="docx-paper" :style="{ width: paperWidth + 'px' }"><img :src="current.url" :alt="t('docx.page', { page: current.page })" draggable="false"><figcaption>{{ t('docx.page', { page: current.page }) }} / {{ images.length }}</figcaption></figure>
      <figure v-else class="docx-paper" :style="{ width: paperWidth + 'px' }"><div class="docx-blank-sheet"></div><figcaption>{{ t('docx.page', { page: 1 }) }}</figcaption></figure>
    </div>
  </div>
</template>
<style>
.docx-workbench{display:flex;flex:1;min-height:0;background:var(--page)}.docx-thumbnails{width:120px;flex-shrink:0;overflow:auto;padding:14px 10px;border-right:1px solid var(--border)}.docx-thumbnails>button{display:block;width:100%;padding:5px;border:1px solid transparent;border-radius:6px;margin-bottom:12px}.docx-thumbnails>button.selected{border-color:var(--blue-text);background:var(--blue-bg)}.docx-thumb{display:block;position:relative}.docx-thumb>img{display:block;width:100%;height:auto;background:white;box-shadow:0 1px 4px #0002;user-select:none}.docx-changed-dot{position:absolute;top:4px;right:4px;width:7px;height:7px;border-radius:50%;background:var(--blue-text);box-shadow:0 0 0 2px white}.docx-thumb-label{display:block;font-size:9px;margin-top:7px;color:var(--text-muted);font-variant-numeric:tabular-nums}.docx-stage{flex:1;min-width:0;overflow:auto;padding:20px 18px;display:flex;flex-direction:column;align-items:center;outline:none;overscroll-behavior:contain;color-scheme:light}.docx-paper{margin:0;flex-shrink:0}.docx-paper>img{display:block;width:100%;height:auto;background:white;box-shadow:0 2px 10px #0002;user-select:none}.docx-paper>figcaption{text-align:center;font-size:10px;color:var(--text-muted);margin-top:8px;font-variant-numeric:tabular-nums}.docx-blank-sheet{width:100%;aspect-ratio:210/297;background:white;box-shadow:0 2px 10px #0002}.docx-preview-status{display:flex;align-items:center;gap:15px;padding:9px 16px;border-bottom:1px solid var(--border);font-size:11px;color:var(--text-muted)}.docx-changed-note{color:var(--blue-text)}.docx-preview-status>select{margin-left:auto;font-size:10px;max-width:115px;padding:3px 6px}.docx-preview-error{padding:10px;font-size:12px;color:var(--error-text)}
@media(max-width:700px){.docx-thumbnails{width:84px}.docx-stage{padding:12px 10px}}
</style>
