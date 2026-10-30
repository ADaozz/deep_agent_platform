<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'
import { t } from './preferences'
const props = defineProps({ path: { type: String, required: true }, size: Number, downloadUrl: { type: String, required: true } })
const emit = defineEmits(['preview'])
const canPreview = computed(() => /\.(docx|pptx)$/i.test(props.path))
const name = computed(() => props.path.split('/').pop())
const sizeLabel = computed(() => {
  if (!Number.isFinite(props.size) || props.size < 0) return ''
  if (props.size < 1024) return `${props.size} B`
  if (props.size < 1024 * 1024) return `${(props.size / 1024).toFixed(1)} KB`
  return `${(props.size / 1024 / 1024).toFixed(1)} MB`
})
</script>

<template>
  <div class="file-block">
    <component :is="canPreview ? 'button' : 'a'" class="file-block-open" :type="canPreview ? 'button' : undefined" :href="canPreview ? undefined : downloadUrl" :download="canPreview ? undefined : ''" @click="canPreview && emit('preview', path)">
      <span class="file-block-icon"><Icon name="FileText" :size="17" /></span>
      <span class="file-block-text"><strong>{{ name }}</strong><small>{{ t(canPreview ? 'artifacts.preview' : 'artifacts.download') }}<template v-if="sizeLabel"> · {{ sizeLabel }}</template></small></span>
    </component>
    <a class="file-block-download" :href="downloadUrl" download :aria-label="t('Download')"><Icon name="Download" :size="15" /></a>
  </div>
</template>
