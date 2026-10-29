<script setup>
import {ref,computed,watch} from 'vue'
import Icon from '../../Icon.vue'
import DocxPreview from './DocxPreview.vue'
import {t} from '../../preferences'
import {mergeDocument,previewKey} from './state'
import {api} from '../../featureApi'
const props=defineProps({session:Object,events:{type:Array,default:()=>[]},active:Boolean})
const emit=defineEmits(['open','context','error'])
const selected=computed(()=>props.session)
async function guard(fn){try{return await fn()}catch(error){emit('error',error.message)}}
const documents = ref({}), activeDocumentId = ref(''), documentOpen = ref(false), reviewPage = ref(null), reviewNotes = ref(''), renderedRevision = ref(null)
const activeDocument = computed(() => documents.value[activeDocumentId.value])
const documentUrl = computed(() => activeDocument.value && selected.value ? `/api/agent/sessions/${selected.value.id}/documents/${encodeURIComponent(activeDocumentId.value)}/revisions/${activeDocument.value.revision}` : '')
const documentStorageKey = computed(() => previewKey(selected.value?.id, activeDocumentId.value))
function receiveDocuments(updates) {
  for (const event of updates) {
    if (!event.document_id || !Number.isInteger(event.revision)) continue
    const previous = documents.value[event.document_id]
    const next = mergeDocument(previous, event)
    if (next === previous) continue
    documents.value = { ...documents.value, [event.document_id]: next }
    if (!previous) {
      activeDocumentId.value = event.document_id
      documentOpen.value = sessionStorage.getItem(previewKey(selected.value?.id, event.document_id) + ':closed') !== 'true'
    }
  }
}
function closeDocument() { documentOpen.value = false; sessionStorage.setItem(documentStorageKey.value + ':closed', 'true') }
function showDocument() { documentOpen.value = true; emit('open',true); sessionStorage.removeItem(documentStorageKey.value + ':closed') }
function feedbackContext() { return activeDocument.value ? { document_id: activeDocumentId.value, revision: renderedRevision.value ?? activeDocument.value.revision } : null }
async function reviewDocumentPage(passed) {
  await guard(async () => {
    const result = await api(`/agent/sessions/${selected.value.id}/documents/${encodeURIComponent(activeDocumentId.value)}/review`, { method: 'POST', body: { revision: activeDocument.value.revision, page: reviewPage.value, passed, notes: reviewNotes.value } })
    receiveDocuments([result]); reviewPage.value = null; reviewNotes.value = ''
  })
}
watch(() => [activeDocumentId.value, activeDocument.value?.revision], () => { reviewPage.value = null; reviewNotes.value = '' })
watch(activeDocumentId, () => { renderedRevision.value = null })
function resetDocuments() { renderedRevision.value = null; documents.value = {}; activeDocumentId.value = ''; documentOpen.value = false; reviewPage.value = null }

let cursor=0
watch(()=>props.session?.id,async id=>{
 resetDocuments();cursor=0
 if(!id)return
 receiveDocuments(props.events.filter(e=>e.type.startsWith('document_')))
 cursor=props.events.at(-1)?.seq||0
 await guard(async()=>{const values=await api(`/agent/sessions/${id}/documents`);if(props.session?.id===id)receiveDocuments(values)})
},{immediate:true})
watch(()=>props.events.at(-1)?.seq,()=>{
 const batch=props.events.filter(e=>e.seq>cursor);cursor=props.events.at(-1)?.seq||cursor
 receiveDocuments(batch.filter(e=>e.type.startsWith('document_')))
})
watch(()=>[documentOpen.value,activeDocumentId.value],()=>emit('open',documentOpen.value&&!!activeDocument.value))
watch(()=>[activeDocument.value?.revision,renderedRevision.value,props.active],()=>emit('context',activeDocument.value?{feature:'docx',...feedbackContext()}:null))
</script>
<template>
    <button v-if="activeDocument && (!documentOpen || !active)" type="button" class="docx-reopen secondary" @click="showDocument"><Icon name="FileText" :size="15" />{{ activeDocument.path }}</button>
    <aside v-if="activeDocument && documentOpen && active" class="docx-panel" :aria-label="t('docx.preview')">
      <header class="docx-panel-header"><Icon name="FileText" :size="17" /><select v-model="activeDocumentId" :aria-label="t('docx.select')"><option v-for="doc in documents" :key="doc.document_id" :value="doc.document_id">{{ doc.path }}</option></select><a :href="documentUrl" download :aria-label="t('Download')"><Icon name="Download" :size="17" /></a><button type="button" class="icon-button" :aria-label="t('docx.close')" @click="closeDocument"><Icon name="X" :size="17" /></button></header>
      <p class="docx-gate-note">{{ activeDocument.finalized ? t('docx.finalized') : t('docx.draft') }}</p>
      <p v-if="activeDocument.external_conflict" class="docx-gate-error">{{ t('docx.externalConflict') }}</p>
      <p v-if="activeDocument.preview_error" class="docx-gate-error">{{ t('docx.previewUnavailable') }}</p>
      <p v-if="activeDocument.render_error" class="docx-gate-error" role="alert">{{ t('docx.checkFailed') }} {{ activeDocument.render_error }}</p>
      <p v-else-if="activeDocument.gate && activeDocument.gate !== 'open'" class="docx-gate-note">{{ t('docx.gate.' + activeDocument.gate) }}</p>
      <DocxPreview :key="selected.id + activeDocumentId" :url="documentUrl" :revision="activeDocument.revision" :render-revision="activeDocument.preview_revision" :render-pages="activeDocument.preview_pages" :render-base-url="`/api/agent/sessions/${selected.id}/documents/${encodeURIComponent(activeDocumentId)}/renders`" :pages="activeDocument.pages || []" :storage-key="documentStorageKey" @rendered="renderedRevision = $event" />
      <div v-if="activeDocument.pages?.length" class="docx-review-controls"><span>{{ t('docx.layoutNote') }}</span><button v-for="page in activeDocument.pages.filter(p => p.checks?.automatic_passed && p.checks?.visual_review !== 'passed')" :key="page.page" type="button" class="text-button" @click="reviewPage = page.page; reviewNotes = ''">{{ t('docx.inspectPage', { page: page.page }) }}</button></div>
      <div v-if="reviewPage" class="docx-human-review"><button class="icon-button" type="button" :aria-label="t('docx.close')" @click="reviewPage = null"><Icon name="X" :size="16" /></button><p>{{ t('docx.actualReview') }}</p><a :href="`/api/agent/sessions/${selected.id}/documents/${encodeURIComponent(activeDocumentId)}/renders/${activeDocument.revision}/page-${String(reviewPage).padStart(3, '0')}.png`" target="_blank" rel="noopener">{{ t('docx.fullPage') }}</a><img :src="`/api/agent/sessions/${selected.id}/documents/${encodeURIComponent(activeDocumentId)}/renders/${activeDocument.revision}/page-${String(reviewPage).padStart(3, '0')}.png`" :alt="t('docx.page', { page: reviewPage })"><textarea v-model="reviewNotes" :placeholder="t('docx.reviewNotes')"></textarea><div><button class="secondary" type="button" :disabled="!reviewNotes.trim()" @click="reviewDocumentPage(false)">{{ t('docx.reject') }}</button><button class="primary" type="button" :disabled="!reviewNotes.trim()" @click="reviewDocumentPage(true)">{{ t('docx.approve') }}</button></div></div>
    </aside>
</template>
