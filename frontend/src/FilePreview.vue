<script setup>
import {ref,computed,watch,onUnmounted} from 'vue'
import {marked} from 'marked'
import DOMPurify from 'dompurify'
import Icon from './Icon.vue'
import PreviewFileSelect from './PreviewFileSelect.vue'
import {t} from './preferences'
import {previewKind,fileUrl} from './filePreview'

const props=defineProps({session:Object,active:Boolean,request:Object,files:Array,events:Array})
const emit=defineEmits(['open','select-file'])
const path=ref(''),opened=ref(false),loading=ref(false),error=ref(''),text=ref(''),source=ref(false),zoom=ref(1),generation=ref(0)
let controller
const kind=computed(()=>previewKind(path.value))
const download=computed(()=>props.session&&fileUrl(props.session.id,path.value))
const url=computed(()=>props.session&&fileUrl(props.session.id,path.value,true)+`?v=${generation.value}`)
const markup=computed(()=>{
 const fragment=DOMPurify.sanitize(marked.parse(text.value||''),{RETURN_DOM_FRAGMENT:true,FORBID_ATTR:['style']})
 const localPath=value=>{
  const resolved=new URL(value,`https://preview.invalid/${path.value}`)
  return resolved.origin==='https://preview.invalid'?decodeURIComponent(resolved.pathname.slice(1)):null
 }
 for(const image of fragment.querySelectorAll('img')){
  try{
   const target=localPath(image.getAttribute('src')||'')
   if(target&&previewKind(target)==='image')image.setAttribute('src',fileUrl(props.session.id,target,true))
   else image.removeAttribute('src')
  }catch{image.removeAttribute('src')}
 }
 for(const link of fragment.querySelectorAll('a')){
  const href=link.getAttribute('href')||''
  if(href.startsWith('#'))continue
  try{
   const target=localPath(href)
   if(target){link.setAttribute('href',fileUrl(props.session.id,target));if(previewKind(target))link.dataset.previewPath=target}
   else{link.setAttribute('target','_blank');link.setAttribute('rel','noopener noreferrer')}
  }catch{link.removeAttribute('href')}
 }
 const wrapper=document.createElement('div');wrapper.append(fragment);return wrapper.innerHTML
})
function markdownClick(event){const target=event.target.closest('a[data-preview-path]');if(target){event.preventDefault();emit('select-file',target.dataset.previewPath)}}
function close(){opened.value=false;emit('open',false)}
async function load(){
 controller?.abort();controller=new AbortController()
 const signal=controller.signal
 text.value='';error.value='';loading.value=true;generation.value++
 if(!['text','markdown'].includes(kind.value))return
 try{
  const response=await fetch(url.value,{signal})
  const result=await response.json()
  if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:t('filePreview.failed'))
  if(!signal.aborted)text.value=result.content
 }catch(e){if(!signal.aborted)error.value=e.message}
 finally{if(!signal.aborted)loading.value=false}
}
watch(()=>props.session?.id,()=>{controller?.abort();opened.value=false;path.value='';text.value='';error.value='';emit('open',false)})
watch(()=>props.request,request=>{
 if(!request||!props.session)return
 path.value=request.path;opened.value=true;source.value=false;zoom.value=1;emit('open',true);load()
},{immediate:true})
watch(()=>props.events?.filter(event=>event.type==='output_files').at(-1)?.seq,()=>{if(opened.value&&props.active)load()})
onUnmounted(()=>controller?.abort())
function mediaError(){loading.value=false;error.value=t('filePreview.failed')}
</script>

<template>
 <aside v-if="opened&&active" class="docx-panel file-preview-panel" :aria-label="t('filePreview.title')">
  <header class="docx-panel-header">
   <Icon name="FileText" :size="18"/><PreviewFileSelect :model-value="path" :files="files" @update:model-value="emit('select-file',$event)" />
   <a :href="download" download :aria-label="t('Download')"><Icon name="Download" :size="17"/></a>
   <button class="icon-button" type="button" :aria-label="t('filePreview.close')" @click="close"><Icon name="X" :size="17"/></button>
  </header>
  <div class="file-preview-toolbar">
   <span>{{ kind==='markdown'?'Markdown':kind==='image'?t('filePreview.image'):kind==='pdf'?'PDF':t('filePreview.text') }}</span>
   <button v-if="kind==='markdown'" type="button" class="text-button" @click="source=!source">{{ t(source?'filePreview.rendered':'filePreview.source') }}</button>
   <template v-if="kind==='image'">
    <button type="button" class="text-button" :disabled="zoom<=.25" :aria-label="t('filePreview.zoomOut')" @click="zoom=Math.max(.25,zoom-.25)">−</button>
    <button type="button" class="text-button" @click="zoom=1">{{ Math.round(zoom*100) }}%</button>
    <button type="button" class="text-button" :disabled="zoom>=4" :aria-label="t('filePreview.zoomIn')" @click="zoom=Math.min(4,zoom+.25)">+</button>
   </template>
   <button type="button" class="text-button" @click="load">{{ t('filePreview.refresh') }}</button>
  </div>
  <p v-if="error" class="file-preview-error" role="alert">{{ error }} <a :href="download" download>{{ t('artifacts.download') }}</a></p>
  <p v-else-if="loading&&kind!=='pdf'" class="file-preview-loading">{{ t('filePreview.loading') }}</p>
  <div v-if="!error" class="file-preview-body">
   <div v-if="kind==='markdown'&&!source" class="markdown file-preview-markdown" @click="markdownClick" v-html="markup"></div>
   <pre v-else-if="kind==='text'||kind==='markdown'" class="file-preview-text">{{ text }}</pre>
   <iframe v-else-if="kind==='pdf'" :key="url" :src="url" :title="path" class="file-preview-pdf" @load="loading=false" @error="mediaError"></iframe>
   <div v-else-if="kind==='image'" class="file-preview-image"><img :key="url" :src="url" :alt="path" :style="{width:zoom*100+'%'}" @load="loading=false" @error="mediaError"/></div>
  </div>
 </aside>
</template>

<style scoped>
.file-preview-toolbar{display:flex;align-items:center;gap:14px;padding:9px 14px;flex-wrap:wrap;border-bottom:1px solid var(--border);color:var(--text-muted);font-size:11px}.file-preview-toolbar>button:last-child{margin-left:auto}
.file-preview-body{flex:1;min-height:0;overflow:auto;background:var(--page);color:var(--text-primary)}
.file-preview-markdown{padding:24px;overflow-wrap:anywhere}.file-preview-markdown :deep(pre){overflow:auto;max-width:100%}.file-preview-markdown :deep(table){display:block;overflow:auto;max-width:100%}.file-preview-markdown :deep(img){max-width:100%;height:auto}
.file-preview-text{margin:0;padding:20px;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:2;font:12px/1.8 ui-monospace,SFMono-Regular,Consolas,monospace;background:transparent;color:var(--text-primary)}
.file-preview-pdf{display:block;width:100%;height:100%;border:0;min-height:240px;background:var(--page)}
.file-preview-image{min-height:100%;overflow:auto;padding:16px}.file-preview-image img{display:block;max-width:none;height:auto;object-fit:contain;margin:auto}
.file-preview-error,.file-preview-loading{padding:12px 16px;font-size:12px;color:var(--text-muted)}.file-preview-error{color:var(--error-text)}
</style>
