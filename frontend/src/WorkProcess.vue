<script setup>
import { ref, watch } from 'vue'
import Icon from './Icon.vue'
import { t, thinkingExpanded } from './preferences'

const props = defineProps({ process: Object, actionLabel: Function, statusLabels: Object })
const isComplete = () => props.process.status === 'completed'
const showThoughts = ref(!isComplete() && thinkingExpanded.value)
const showWork = ref(!isComplete())
watch(() => props.process.status, status => {
  if (status !== 'completed') return
  showThoughts.value = false
  showWork.value = false
})
function rememberThoughts(event) {
  const open = event.target.open
  // Ignore the toggle event caused by the automatic collapse above.
  if (open === showThoughts.value) return
  showThoughts.value = open
  thinkingExpanded.value = open
}
function stepIcon(step) {
  if (step.name === 'execute') return 'Terminal'
  return step.status === 'completed' ? 'CircleCheck' : step.status === 'failed' ? 'CircleAlert' : step.status === 'running' ? 'LoaderCircle' : step.status === 'waiting_human' ? 'MessageCircleQuestion' : step.status === 'waiting_confirmation' ? 'ShieldCheck' : 'Circle'
}
</script>

<template>
  <section class="work-process" :aria-label="t('process.title')">
    <div v-if="!process.active && process.status !== 'completed'" class="process-heading">
      <strong>{{ statusLabels[process.status] || t('process.title') }}</strong>
    </div>
    <details v-if="process.thoughts" class="process-thoughts" :open="showThoughts" @toggle="rememberThoughts">
      <summary :title="t('process.rememberThoughts')"><Icon name="Brain" :size="15" /><span>{{ t('process.thinking') }}</span><Icon name="ChevronDown" class="process-chevron" :size="13" /></summary>
      <div class="process-thought-text">{{ process.thoughts }}</div>
    </details>
    <details v-if="process.actionCount" class="process-work" :open="showWork" @toggle="showWork = $event.target.open">
      <summary><Icon name="FileText" :size="15" /><span>{{ t('process.title') }}</span><span class="process-count">{{ t('activity.operations', { count: process.actionCount }) }}</span><Icon name="ChevronDown" class="process-chevron" :size="13" /></summary>

      <ol class="process-timeline">
        <li v-for="step in process.actions" :key="step.key" class="process-node" :class="{ 'process-node-error': step.status === 'failed' }">
          <span class="process-node-icon"><Icon :name="stepIcon(step)" :size="15" :class="{ spin: step.status === 'running' && step.name !== 'execute' }" /></span>
          <div class="process-node-body">
            <div class="process-node-heading"><span>{{ actionLabel(step.name) }}</span><span class="process-node-state"><span v-if="step.successCount" class="process-success">{{ step.successCount }} success</span><span v-if="step.successCount && step.failureCount">, </span><span v-if="step.failureCount" class="process-failure">{{ step.failureCount }} failure</span></span></div>
          </div>
        </li>
      </ol>
    </details>
  </section>
</template>
