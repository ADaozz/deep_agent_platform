<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { t } from './preferences'
import 'superdoc/style.css'

const props = defineProps({ url: { type: String, required: true } })
const emit = defineEmits(['ready', 'change', 'error', 'save'])
const host = ref(null), toolbar = ref(null), loading = ref(true), failed = ref(false)
let editor, disposed = false, ready = false
function fail() {
  if (disposed) return
  failed.value = true; loading.value = false; ready = false
  emit('ready', false); emit('error', t('file.docxError'))
}
onMounted(async () => {
  try {
    const { SuperDoc } = await import('superdoc')
    if (disposed) return
    editor = new SuperDoc({
      selector: host.value,
      document: props.url,
      documentMode: 'editing',
      telemetry: { enabled: false },
      ui: { toolbar: { container: toolbar.value }, comments: false },
      onReady: () => {
        if (disposed || failed.value) return
        ready = true; loading.value = false; emit('ready', true)
      },
      onEditorUpdate: () => { if (ready && !disposed) emit('change') },
      onContentError: fail,
      onException: fail,
    })
  } catch { fail() }
})
onBeforeUnmount(() => { disposed = true; ready = false; editor?.destroy() })
async function exportDocument() {
  if (!ready || !editor) throw new Error(t('file.docxError'))
  const blob = await editor.export({ triggerDownload: false })
  if (!(blob instanceof Blob) || !blob.size) throw new Error(t('file.docxError'))
  return blob
}
defineExpose({ exportDocument })
</script>

<template>
  <div class="docx-editor" @keydown.ctrl.s.prevent.stop="emit('save')" @keydown.meta.s.prevent.stop="emit('save')">
    <p v-if="loading" class="docx-status" role="status">{{ t('file.docxLoading') }}</p>
    <p v-if="failed" class="docx-status" role="alert">{{ t('file.docxError') }}</p>
    <div ref="toolbar" class="docx-toolbar"></div>
    <div class="docx-scroll"><div ref="host" class="docx-host"></div></div>
  </div>
</template>

<style scoped>
.docx-editor{display:flex;flex-direction:column;flex:1;min-height:0;min-width:0;color:#202124;background:#f1f3f5;color-scheme:light}
.docx-toolbar{flex-shrink:0;max-height:150px;overflow:auto;background:white;border-bottom:1px solid #ddd}
.docx-scroll{flex:1;min-height:0;overflow:auto;padding:16px 8px}
.docx-host{min-height:100%;min-width:0}
.docx-status{padding:14px;background:white;font-size:12px}
</style>
