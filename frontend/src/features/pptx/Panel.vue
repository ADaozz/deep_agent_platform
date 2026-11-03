<script setup>
import {ref,computed,watch} from 'vue'
import Icon from '../../Icon.vue'
import PreviewFileSelect from '../../PreviewFileSelect.vue'
import {t} from '../../preferences'
import {api} from '../../featureApi'
import SlideCanvas from './SlideCanvas.vue'
import ReviewDisclosure from './ReviewDisclosure.vue'
const props=defineProps({session:Object,events:{type:Array,default:()=>[]},active:Boolean,files:{type:Array,default:()=>[]},openRequest:Object})
const emit=defineEmits(['open','context','error','catalog','select-file'])
const decks=ref({}),identity=ref(''),opened=ref(false),selectedSlide=ref(''),follow=ref(true),focused=ref(''),busy=ref(false),delegated=ref({})
const deck=computed(()=>decks.value[identity.value]),slides=computed(()=>deck.value?.manifest?.slides||[]),slide=computed(()=>slides.value.find(s=>s.slide_id===selectedSlide.value)||slides.value[0])
const profile=computed(()=>deck.value?.manifest?.profile||null)
const base=computed(()=>props.session&&identity.value?`/api/agent/sessions/${props.session.id}/presentations/${encodeURIComponent(identity.value)}`:'')
const route=computed(()=>base.value.replace(/^\/api/,''))
let cursor=0
function key(id){return `pptx-preview:${props.session?.id}:${id}:closed`}
function receive(updates){for(const update of updates){if(!update.presentation_id||!Number.isInteger(update.revision))continue;const old=decks.value[update.presentation_id];if(old&&old.revision>update.revision)continue;const next={...old,...update};decks.value={...decks.value,[update.presentation_id]:next};if(!old){identity.value=update.presentation_id;opened.value=sessionStorage.getItem(key(identity.value))!=='true';emit('open',opened.value)}if(follow.value&&update.slide_id)selectedSlide.value=update.slide_id;if(!selectedSlide.value&&next.manifest?.slides.length)selectedSlide.value=next.manifest.slides[0].slide_id}}
watch(decks,values=>emit('catalog',Object.values(values).filter(file=>file.path)),{deep:true})
watch(()=>props.openRequest,async request=>{
 if(!request||!props.session)return
 const id=props.session.id
 try{
  const values=await api(`/agent/sessions/${id}/presentations`)
  if(props.session?.id!==id||props.openRequest?.token!==request.token)return
  receive(values)
  const item=Object.values(decks.value).find(file=>file.path===request.path)
  if(!item)throw new Error(t('artifacts.previewUnavailable'))
  identity.value=item.presentation_id;selectedSlide.value=item.manifest?.slides[0]?.slide_id||'';show()
  if(item.manifest?.slides.some(slide=>slide.formal?.revision!==item.revision)){
   const preview=await api(`/agent/sessions/${id}/presentations/${encodeURIComponent(item.presentation_id)}/preview`,{method:'POST'})
   if(props.session?.id===id&&props.openRequest?.token===request.token)receive([preview])
  }
 }catch(e){emit('error',e.message)}
},{immediate:true})
function close(){opened.value=false;sessionStorage.setItem(key(identity.value),'true');emit('open',false)}
function show(){opened.value=true;sessionStorage.removeItem(key(identity.value));emit('open',true)}
function select(sid){selectedSlide.value=sid;follow.value=false;focused.value=''}
async function post(path,body){busy.value=true;try{const result=await api(route.value+path,{method:'POST',body});receive([result]);return result}catch(e){emit('error',e.message)}finally{busy.value=false}}
const dismissIssue=id=>post('/aesthetic-decision',{revision:deck.value.revision,decision:'dismiss_issues',issue_ids:[id],slide_id:slide.value.slide_id})
const requestDeck=()=>post('/aesthetic-review',{revision:deck.value.revision})
watch(()=>props.session?.id,async id=>{decks.value={};identity.value='';opened.value=false;selectedSlide.value='';follow.value=true;cursor=0;emit('open',false);if(!id)return;receive(props.events.filter(e=>e.type.startsWith('presentation_')));cursor=props.events.at(-1)?.seq||0;try{const values=await api(`/agent/sessions/${id}/presentations`);if(props.session?.id===id)receive(values)}catch(e){emit('error',e.message)}},{immediate:true})
watch(()=>props.events.at(-1)?.seq,()=>{const batch=props.events.filter(e=>e.seq>cursor);cursor=props.events.at(-1)?.seq||cursor;receive(batch.filter(e=>e.type.startsWith('presentation_')))})
watch(()=>[deck.value?.revision,slide.value?.slide_id,props.active],()=>{emit('context',deck.value&&slide.value?{feature:'pptx',presentation_id:identity.value,slide_id:slide.value.slide_id,revision:deck.value.revision}:null)})
const styleReference=computed(()=>deck.value?.manifest?.style_reference||null)
const profileName=computed(()=>styleReference.value?.folder_name||t('pptx.styleUnspecified'))
const review=computed(()=>slide.value?.review||{})
const issues=computed(()=>review.value.layout_issues||[])
const aesthetic=computed(()=>review.value.aesthetic_review||null)
const effective=computed(()=>aesthetic.value?.status||'pending')
const findings=computed(()=>aesthetic.value?.issues||[])
const errorLabel=code=>{const key='pptx.error.'+code,value=t(key);return value===key?code:value}
const isCritical=item=>item.severity==='critical'&&['high','medium'].includes(item.confidence)&&!item.needs_verification
const severityOrder={critical:0,major:1,minor:2}
const visibleFindings=items=>(items||[]).filter(item=>!item.dismissed).sort((a,b)=>(severityOrder[a.severity]??3)-(severityOrder[b.severity]??3))
const currentFindings=computed(()=>visibleFindings(findings.value))
const deckFindings=computed(()=>visibleFindings(deckReview.value?.issues))
const reviewScope=computed(()=>`${props.session?.id}:${identity.value}:${slide.value?.slide_id}:${deck.value?.revision}`)
const automaticStatus=computed(()=>{
 if(review.value.render_error||review.value.visual_integrity?.status==='unavailable'||review.value.technical_review?.status==='unavailable')return 'unavailable'
 if(review.value.automatic_passed===true)return 'passed'
 if(review.value.automatic_passed===false||issues.value.some(issue=>(issue.severity||'error')==='error'))return 'failed'
 return 'not_requested'
})
const visualStatus=computed(()=>!aesthetic.value?'not_requested':aesthetic.value.error?'unavailable':['blocked_by_automatic_checks','deferred_until_deck_checked'].includes(aesthetic.value.reason)?'waiting':effective.value)
const deckStatus=computed(()=>!deckReview.value?'not_requested':deckReview.value.error?'unavailable':deckReview.value.status)
const reviewLabel=status=>t('pptx.reviewState.'+status)
const slideNumber=computed(()=>slides.value.findIndex(item=>item.slide_id===slide.value?.slide_id)+1)
async function delegate(item,pages){
 const text=t('pptx.fixRequest',{pages:pages||slideNumber.value,id:item.id,observation:item.observation,suggestion:item.suggestion})
 const id=props.session.id,running=['running','queued'].includes(props.session.status)
 busy.value=true
 try{
  if(running)await api(`/agent/sessions/${id}/actions/steer`,{method:'POST',body:{text,artifact:{feature:'pptx',presentation_id:identity.value,slide_id:slide.value.slide_id,revision:deck.value.revision}}})
  else await api(`/agent/sessions/${id}/messages`,{method:'POST',body:{text}})
  delegated.value={...delegated.value,[item.id+':'+(pages||slideNumber.value)]:true}
 }catch(e){emit('error',e.message)}finally{busy.value=false}
}
const deckReview=computed(()=>deck.value?.deck_review||null)
const highlights=computed(()=>findings.value.filter(item=>!item.dismissed&&(isCritical(item)||item.id===focused.value)).map(item=>({id:item.id,region:item.region,element_ids:item.element_ids,severity:item.severity,active:item.id===focused.value})))
function pageList(item){return item.slides?.join(', ')}
function goToPage(item){const target=slides.value[(item.slides?.[0]||1)-1];if(target)select(target.slide_id)}
</script>
<template>
 <button v-if="deck&&(!opened||!active)" class="secondary pptx-reopen" type="button" @click="show"><Icon name="Presentation" :size="15" />{{ deck.path }}</button>
 <aside v-if="deck&&opened&&active" class="docx-panel pptx-panel" :aria-label="t('pptx.preview')">
  <header class="docx-panel-header"><Icon name="Presentation" :size="18"/><PreviewFileSelect :model-value="deck.path" :files="files" @update:model-value="emit('select-file',$event)" /><a :href="`${base}/revisions/${deck.revision}`" download :aria-label="t('Download')"><Icon name="Download" :size="17"/></a><button class="icon-button" type="button" :aria-label="t('pptx.close')" @click="close"><Icon name="X" :size="17"/></button></header>
  <div class="pptx-status">
   <span>{{ t('docx.revision',{revision:deck.revision}) }}</span>
   <span>{{ deck.finalized?t('pptx.finalized'):t('pptx.draft') }}</span>
   <span v-if="deck.finalized&&deck.finalize_verified!==undefined" :class="deck.finalize_verified?'pptx-ok':'pptx-bad'">{{ deck.finalize_verified?t('pptx.verified'):t('pptx.unverified') }}</span>
   <button class="text-button" type="button" @click="follow=true;selectedSlide=deck.slide_id||deck.active_slide||slides.at(-1)?.slide_id">{{ t('pptx.follow') }}</button>
  </div>
  <p v-if="deck.preview_error" class="pptx-preview-error" role="alert">{{ t('pptx.previewError') }} {{ deck.preview_error }}</p>
  <div class="pptx-workbench">
   <nav class="pptx-thumbnails" :aria-label="t('pptx.slides')"><button v-for="(item,index) in slides" :key="item.slide_id" :class="{selected:item.slide_id===slide?.slide_id}" @click="select(item.slide_id)"><SlideCanvas :slide="item" :profile="profile" :revision="deck.revision" :base-url="base" thumbnail/><span>{{ String(index+1).padStart(2,'0') }} · {{ t('pptx.'+(item.review?.status||'writing')) }}</span></button></nav>
   <div class="pptx-stage">
    <div v-if="slide" class="pptx-slide-heading"><span>{{ t('pptx.profile') }}: <strong :title="styleReference?.style_id">{{ profileName }}</strong></span><span>{{ review.rendered?t('pptx.formal'):t('pptx.instant') }}</span></div>
    <SlideCanvas v-if="slide" :slide="slide" :profile="profile" :revision="deck.revision" :base-url="base" :highlights="highlights"/>
    <div v-else class="pptx-empty">{{ t('pptx.creating') }}</div>
    <section v-if="slide" class="pptx-slide-reviews">
     <ReviewDisclosure :key="reviewScope+':automatic'" :title="t('pptx.automatic')" :status="automaticStatus" :status-label="reviewLabel(automaticStatus)" :issues="issues" kind="automatic">
     <div class="pptx-checks">
      <span v-if="review.technical_review">{{ t('pptx.technical') }}: <b :class="'pptx-check-'+review.technical_review.status">{{ t('pptx.check.'+review.technical_review.status) }}</b></span>
      <span v-if="review.visual_integrity">{{ t('pptx.integrity') }}: <b :class="'pptx-check-'+review.visual_integrity.status">{{ t('pptx.check.'+review.visual_integrity.status) }}</b></span>
     </div>
     <p v-for="(issue,index) in issues" :key="index" :class="'pptx-issue-'+(issue.severity||'error')"><span>{{ issue.severity||'error' }}</span> {{ issue.type }} · {{ issue.element_id||issue.elements?.join(', ') }}<template v-if="issue.message"> — {{ issue.message }}</template></p>
     <p v-if="review.agent_inspection" class="pptx-meta">{{ t('pptx.agentNote',{notes:review.agent_inspection.notes}) }}</p>

     </ReviewDisclosure>
     <ReviewDisclosure :key="reviewScope+':visual'" :title="t('pptx.aesthetic')" :status="visualStatus" :status-label="reviewLabel(visualStatus)" :issues="findings">
     <p v-if="review.revision!==undefined" class="pptx-meta">{{ t('pptx.reviewVersions',{reviewed:aesthetic?.revision??review.revision,current:deck.revision}) }}</p>
     <div v-if="aesthetic" class="pptx-aesthetic">
      <p v-if="aesthetic.model_id" class="pptx-meta">{{ t('pptx.model',{model:aesthetic.model_id,prompt:aesthetic.prompt_version}) }}</p>
      <p v-if="aesthetic.summary">{{ aesthetic.summary }}</p>
      <p v-if="aesthetic.error" class="pptx-meta">{{ t('pptx.reviewError',{code:errorLabel(aesthetic.error.code)}) }}<template v-if="aesthetic.error.message"> — {{ aesthetic.error.message }}</template></p>
      <p v-if="review.constraints?.length" class="pptx-meta">{{ t('pptx.constraints',{items:review.constraints.join('；')}) }}</p>
      <p v-if="aesthetic.auto_repair===false&&!aesthetic.advisory_only" class="pptx-warning">{{ t('pptx.autoRepairStopped') }}</p>
      <p v-if="!currentFindings.length&&['passed','failed'].includes(aesthetic.status)" class="pptx-meta">{{ t('pptx.noIssues') }}</p>
      <ul v-if="currentFindings.length" class="pptx-findings">
       <li v-for="item in currentFindings" :key="item.id" :class="['pptx-finding-'+item.severity,{focused:item.id===focused,dismissed:item.dismissed}]" @mouseenter="focused=item.id" @mouseleave="focused=''" @click="focused=focused===item.id?'':item.id">
        <div class="pptx-finding-head"><span class="pptx-badge">{{ t('pptx.severity.'+item.severity) }}</span><code>{{ item.category }}</code><span v-if="item.needs_verification" class="pptx-badge pptx-badge-muted">{{ t('pptx.needsVerification') }}</span><span v-if="item.dismissed" class="pptx-badge pptx-badge-muted">{{ t('pptx.dismissed') }}</span><span class="pptx-meta">{{ t('pptx.confidence',{value:item.confidence}) }}</span></div>
        <p>{{ item.observation }}</p>
        <p class="pptx-meta">{{ t('pptx.reason') }}：{{ item.reason }}</p>
        <p class="pptx-meta">{{ t('pptx.suggestion') }}：{{ item.suggestion }}</p>
        <p v-if="item.element_ids?.length" class="pptx-meta">{{ t('pptx.elementsRef',{ids:item.element_ids.join(', ')}) }}</p>
        <p v-if="item.unmapped_element_ids?.length" class="pptx-warning">{{ t('pptx.unmapped',{ids:item.unmapped_element_ids.join(', ')}) }}</p>
        <div v-if="!item.dismissed" class="pptx-finding-actions"><button type="button" class="text-button" :disabled="busy||delegated[item.id+':'+slideNumber]" @click.stop="delegate(item)">{{ delegated[item.id+':'+slideNumber]?t('pptx.delegated'):t('pptx.delegate') }}</button><button type="button" class="text-button" :disabled="busy" @click.stop="dismissIssue(item.id)">{{ t('pptx.dismiss') }}</button></div>
       </li>
      </ul>

     </div>
     </ReviewDisclosure>
    </section>
    <ReviewDisclosure v-if="slides.length>1" :key="reviewScope+':deck'" class="pptx-deck-review" :title="t('pptx.deckReview')" :status="deckStatus" :status-label="reviewLabel(deckStatus)" :issues="deckReview?.issues||[]">
     <span v-if="deckReview" class="pptx-meta">{{ t('pptx.reviewVersions',{reviewed:deckReview.revision,current:deck.revision}) }}</span>
     <span v-else class="pptx-meta">{{ t('pptx.deckNotRequested') }}</span>
     <p v-if="deckReview?.status==='stale'" class="pptx-meta">{{ t('pptx.reviewExpired') }}</p>
     <p v-if="deckReview?.summary">{{ deckReview.summary }}</p>
     <p v-if="deckReview?.error" class="pptx-meta">{{ t('pptx.reviewError',{code:errorLabel(deckReview.error.code)}) }}</p>
     <ul v-if="deckFindings.length" class="pptx-findings">
      <li v-for="item in deckFindings" :key="item.id" :class="['pptx-finding-'+item.severity,{dismissed:item.dismissed}]" @click="goToPage(item)">
       <div class="pptx-finding-head"><span class="pptx-badge">{{ t('pptx.severity.'+item.severity) }}</span><code>{{ item.category }}</code><span class="pptx-meta">{{ t('pptx.pages',{pages:pageList(item)}) }}</span></div>
       <p>{{ item.observation }}</p>
       <p class="pptx-meta">{{ t('pptx.suggestion') }}：{{ item.suggestion }}</p>
       <div class="pptx-finding-actions"><button type="button" class="text-button" :disabled="busy||delegated[item.id+':'+pageList(item)]" @click.stop="delegate(item,pageList(item))">{{ delegated[item.id+':'+pageList(item)]?t('pptx.delegated'):t('pptx.delegate') }}</button></div>
      </li>
     </ul>

     <p v-if="deckReview?.rejected_issues?.length" class="pptx-meta">{{ t('pptx.rejectedIssues',{count:deckReview.rejected_issues.length}) }}</p>
     <div class="pptx-actions">
      <button type="button" class="text-button" :disabled="busy||deckReview?.status==='pending'" @click="requestDeck">{{ t('pptx.requestDeck') }}</button>
     </div>
    </ReviewDisclosure>
   </div>
  </div>
 </aside>
</template>
<style>
.pptx-preview-error{margin:0;padding:10px 14px;color:var(--error-text);background:var(--surface);font-size:11px}
.pptx-reopen{position:fixed;bottom:66px;right:20px;z-index:34;font-size:12px}.pptx-status{display:flex;gap:14px;align-items:center;padding:11px 14px;border-bottom:1px solid var(--border);font-size:10px;color:var(--text-muted)}.pptx-status button{margin-left:auto}.pptx-ok{color:var(--success-text)}.pptx-bad{color:var(--error-text)}.pptx-workbench{display:flex;flex:1;min-height:0;background:var(--page)}.pptx-thumbnails{width:132px;flex-shrink:0;overflow:auto;padding:14px 10px;background:var(--surface);border-right:1px solid var(--border)}.pptx-thumbnails>button{display:block;width:100%;padding:5px;border:1px solid transparent;border-radius:6px;margin-bottom:13px}.pptx-thumbnails>button.selected{border-color:var(--blue-text);background:var(--blue-bg)}.pptx-thumbnails>button>span{display:block;font-size:9px;margin-top:8px;color:var(--text-muted)}.pptx-stage{flex:1;min-width:0;overflow:auto;padding:20px 18px;background:var(--page)}.pptx-slide-heading{display:flex;gap:16px;flex-wrap:wrap;color:var(--text-secondary);font-size:10px;margin-bottom:14px}.pptx-slide-heading strong{color:var(--text-primary);font-weight:600}.pptx-checks{display:flex;flex-wrap:wrap;gap:14px;flex-basis:100%;color:var(--text-muted)}.pptx-checks b{font-weight:600}.pptx-issue-error>span{color:var(--error-text)}.pptx-issue-warning>span{color:var(--warning-text)}.pptx-issue-advisory>span{color:var(--text-secondary)}.pptx-empty{aspect-ratio:16/9;display:flex;justify-content:center;align-items:center;background:var(--surface);color:var(--text-secondary);font-size:12px}
.pptx-check-passed,.pptx-aesthetic-passed{color:var(--success-text)}.pptx-check-failed,.pptx-aesthetic-failed{color:var(--error-text)}.pptx-aesthetic-uncertain,.pptx-aesthetic-unavailable,.pptx-check-unavailable{color:var(--warning-text)}.pptx-aesthetic-pending{color:var(--text-secondary)}
.pptx-aesthetic{display:flex;flex-direction:column;gap:6px}.pptx-aesthetic>p{font-size:11px;color:var(--text-primary)}
.pptx-findings{list-style:none;margin:4px 0 0;padding:0;display:flex;flex-direction:column;gap:8px;flex-basis:100%}.pptx-findings li{border:1px solid var(--border);border-left:3px solid var(--text-secondary);border-radius:6px;padding:8px 10px;cursor:pointer;display:flex;flex-direction:column;gap:3px}.pptx-findings li.focused{background:var(--blue-bg)}.pptx-findings li.dismissed{opacity:.55}.pptx-findings li p{font-size:11px;color:var(--text-primary)}.pptx-finding-critical,.pptx-finding-major{border-left-color:var(--error-text)!important}.pptx-finding-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.pptx-finding-head code{font-size:10px;color:var(--text-muted)}.pptx-badge{font-size:9px;padding:1px 6px;border-radius:9px;background:var(--surface-active);color:var(--text-primary)}.pptx-finding-critical .pptx-badge:first-child,.pptx-finding-major .pptx-badge:first-child{background:var(--error-bg);color:var(--error-text)}.pptx-badge-muted{background:var(--surface-active);color:var(--text-muted)}.pptx-findings li .text-button{font-size:10px}
.pptx-actions{display:flex;gap:14px;flex-basis:100%}.pptx-more{flex-basis:100%;font-size:11px;color:var(--text-muted)}.pptx-more>summary{cursor:pointer;padding:4px 0}.pptx-finding-actions{display:flex;gap:12px}.pptx-aesthetic-suggestions{color:var(--text-secondary)}.pptx-actions .text-button{font-size:11px}
@media(max-width:700px){.pptx-thumbnails{width:90px}.pptx-stage{padding:12px 10px}.pptx-status{padding:7px 12px}}
</style>
