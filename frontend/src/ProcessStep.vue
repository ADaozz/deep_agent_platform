<script setup>
import {computed,ref,watch,nextTick,onMounted} from 'vue'
import Icon from './Icon.vue'
import {t} from './preferences'
import {terminalStatuses} from './conversation'
import {stepBody,elapsed,formatDuration} from './processView'
const props=defineProps({step:Object,actionLabel:Function,now:Number,collapseEpoch:Number})
const complete=computed(()=>terminalStatuses.has(props.step.status))
const expanded=ref(!complete.value),viewport=ref(null),follow=ref(true)
let programmatic=false
const content=computed(()=>stepBody(props.step,t))
const label=computed(()=>props.step.kind==='reasoning'?t('process.thinking'):props.step.kind==='summary'?t('process.summary'):props.step.kind==='progress'?t('process.progress'):props.actionLabel(props.step.name))
const icon=computed(()=>props.step.status==='failed'?'CircleAlert':props.step.status==='cancelled'||props.step.status==='interrupted'?'X':props.step.kind==='reasoning'?'Brain':props.step.kind==='tool'?(props.step.name==='execute'?'Terminal':'Wrench'):'FileText')
const duration=computed(()=>formatDuration(elapsed(props.step.startedAt,props.step.endedAt,props.step.durationMs,props.now,!complete.value),t))
function onScroll(){if(!programmatic&&viewport.value){const el=viewport.value;follow.value=el.scrollHeight-el.scrollTop-el.clientHeight<=4}}
function tail(){if(!viewport.value||!expanded.value||!follow.value)return;programmatic=true;viewport.value.scrollTop=viewport.value.scrollHeight;requestAnimationFrame(()=>programmatic=false)}
watch(()=>props.step.status,(status,previous)=>{if(status===previous)return;expanded.value=!terminalStatuses.has(status)})
watch(()=>props.collapseEpoch,()=>{if(complete.value)expanded.value=false})
watch(content,async()=>{await nextTick();tail()})
watch(expanded,async value=>{if(value){await nextTick();tail()}})
onMounted(tail)
</script>
<template>
 <li class="process-node" :class="{'process-node-error':step.status==='failed'}" :data-step-id="step.key" :data-step-status="step.status">
  <span class="process-node-icon"><Icon :name="icon" :size="15" :class="{spin:step.status==='running'&&step.kind==='reasoning'}"/></span>
  <div class="process-node-body">
   <button class="process-step-heading" type="button" :aria-expanded="expanded" @click="expanded=!expanded">
    <span class="process-step-name" :title="label">{{ label }}</span><span class="process-step-status" :class="step.status">{{ t('process.status.'+step.status) }}</span><time class="process-step-duration">{{ duration }}</time><span class="process-step-result" :title="step.resultSummary">{{ step.resultSummary }}</span><Icon name="ChevronDown" :size="12" class="process-chevron" :class="{expanded}"/>
   </button>
   <div class="process-step-fold" :class="{expanded}" :inert="!expanded" :aria-hidden="!expanded"><div class="process-step-fold-inner"><pre ref="viewport" class="process-step-content" :class="{'is-live':!complete}" :data-following="follow" tabindex="0" role="region" :aria-label="t('process.details',{name:label})" @scroll.passive="onScroll">{{ content }}</pre></div></div>
  </div>
 </li>
</template>
