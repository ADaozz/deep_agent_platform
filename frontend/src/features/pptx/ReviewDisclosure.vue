<script setup>
import {computed} from 'vue'
import {t} from '../../preferences'

const props=defineProps({title:String,status:String,statusLabel:String,issues:{type:Array,default:()=>[]},kind:{type:String,default:'visual'}})
const levels=computed(()=>props.kind==='automatic'
 ?[['error','pptx.errors'],['warning','pptx.warnings'],['advisory','pptx.advisories']]
 :[['critical','pptx.severity.critical'],['major','pptx.severity.major'],['minor','pptx.severity.minor']])
const current=computed(()=>props.issues.filter(issue=>!issue.dismissed))
const counts=computed(()=>{
 const rows=levels.value.map(([level,label])=>({level,label,count:current.value.filter(issue=>issue.severity===level||(props.kind==='automatic'&&level==='error'&&!issue.severity)).length}))
 const unknown=current.value.length-rows.reduce((sum,row)=>sum+row.count,0)
 if(unknown)rows.push({level:'ungraded',label:'pptx.severity.ungraded',count:unknown})
 return rows.filter(row=>row.count)
})
const showCount=computed(()=>current.value.length>0||['passed','failed','blocked','needs_attention'].includes(props.status))
</script>

<template>
 <details class="pptx-review-disclosure" :data-review-kind="kind" :data-status="status">
  <summary>
   <strong>{{ title }}</strong>
   <span class="pptx-review-state" :class="'pptx-review-state-'+status">{{ statusLabel }}</span>
   <span v-if="showCount" class="pptx-review-count">{{ t('pptx.issueCount',{count:current.length}) }}</span>
   <span v-for="row in counts" :key="row.level" class="pptx-review-level" :class="'pptx-review-level-'+row.level">{{ t(row.label) }} {{ row.count }}</span>
  </summary>
  <div class="pptx-review-details"><slot /></div>
 </details>
</template>

<style>
.pptx-review-disclosure{margin-top:12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);min-width:0;font-size:11px}
.pptx-review-disclosure>summary{padding:11px 12px;cursor:pointer;overflow-wrap:anywhere;line-height:1.8;color:var(--text-muted)}
.pptx-review-disclosure>summary>strong{font-weight:600;color:var(--text-primary)}
.pptx-review-disclosure>summary>span{margin-left:9px;display:inline-block}
.pptx-review-disclosure[open]>summary{border-bottom:1px solid var(--border)}
.pptx-review-details{display:flex;flex-direction:column;gap:10px;padding:12px;min-width:0;overflow-wrap:anywhere}
.pptx-review-details .pptx-checks,.pptx-review-details .pptx-actions{flex-basis:auto;flex-wrap:wrap}
.pptx-review-details .pptx-findings,.pptx-review-details .pptx-more{flex-basis:auto}
.pptx-review-details .pptx-meta{font-size:10px;color:var(--text-muted)}
.pptx-review-details .pptx-warning{font-size:10px;color:var(--warning-text)}
.pptx-review-state-passed{color:var(--success-text)}.pptx-review-state-failed,.pptx-review-state-blocked,.pptx-review-state-needs_attention,.pptx-review-level-error,.pptx-review-level-critical{color:var(--error-text)}
.pptx-review-level-warning,.pptx-review-level-major,.pptx-review-state-unavailable{color:var(--warning-text)}
.pptx-review-level{font-size:10px}.pptx-slide-reviews>.pptx-more{display:block;margin-top:12px}
</style>
