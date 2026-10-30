<script setup>
import {computed,ref,watch,onUnmounted} from 'vue'
import Icon from './Icon.vue'
import ProcessStep from './ProcessStep.vue'
import {t} from './preferences'
import {elapsed,formatDuration} from './processView'
const props=defineProps({process:Object,actionLabel:Function,statusLabels:Object})
const collapsed=ref(props.process.finished),collapseEpoch=ref(0),now=ref(Date.now())
let timer
watch(()=>props.process.finished,(finished,previous)=>{if(finished&&!previous){collapsed.value=true;collapseEpoch.value++}else if(!finished&&previous)collapsed.value=false})
watch(()=>props.process.active,active=>{clearInterval(timer);now.value=Date.now();if(active)timer=setInterval(()=>now.value=Date.now(),250)},{immediate:true})
onUnmounted(()=>clearInterval(timer))
function toggleTask(){if(collapsed.value&&props.process.finished)collapseEpoch.value++;collapsed.value=!collapsed.value}
const duration=computed(()=>formatDuration(elapsed(props.process.startedAt,props.process.endedAt,props.process.durationMs,now.value,!props.process.finished),t))
const stateLabel=computed(()=>t('process.task.'+props.process.status)!=='process.task.'+props.process.status?t('process.task.'+props.process.status):props.statusLabels[props.process.status]||t('process.title'))
const icon=computed(()=>props.process.active?'LoaderCircle':props.process.status==='completed'?'CircleCheck':props.process.status==='failed'?'CircleAlert':props.process.status==='cancelled'||props.process.status==='interrupted'?'X':'Clock')
</script>
<template>
 <section class="work-process" :class="{'is-finished':process.finished}" :data-task-finished="process.finished" :aria-label="t('process.title')">
  <button type="button" class="process-task-summary" :aria-expanded="!collapsed" @click="toggleTask">
   <Icon :name="icon" :size="15" :class="{spin:process.active}"/><span class="process-task-state">{{ stateLabel }}</span><span class="process-count">{{ t('process.steps',{count:process.stepCount}) }}</span><time>{{ duration }}</time><Icon name="ChevronDown" :size="13" class="process-chevron" :class="{expanded:!collapsed}"/>
  </button>
  <div class="process-task-fold" :class="{expanded:!collapsed}" :inert="collapsed" :aria-hidden="collapsed"><div class="process-task-fold-inner">
   <ol class="process-timeline"><template v-for="step in process.steps" :key="step.key"><li v-if="step.kind==='steer'" class="process-node process-steer" :data-step-id="step.key"><span class="process-node-icon"><Icon name="CornerDownRight" :size="15"/></span><div class="process-node-body"><span class="process-steer-label">{{ step.name==='follow_up'?t('后续任务'):t('追加指令') }}</span><span class="process-steer-text">{{ step.content }}</span></div></li><ProcessStep v-else :step="step" :now="now" :action-label="actionLabel" :collapse-epoch="collapseEpoch"/></template></ol>
   <p v-if="!process.steps.length&&process.active" class="process-empty">{{ t('process.waitingStep') }}</p>
  </div></div>
 </section>
</template>
