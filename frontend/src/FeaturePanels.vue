<script setup>
import {ref,computed,defineAsyncComponent,watch,onMounted} from 'vue'
import {api} from './featureApi'
const props=defineProps({session:Object,events:Array})
const emit=defineEmits(['open','context','error'])
const modules=import.meta.glob('./features/*/Panel.vue')
const components=Object.fromEntries(Object.entries(modules).map(([path,loader])=>[path.split('/')[2],defineAsyncComponent(loader)]))
const visibleFeatures=computed(()=>features.value.filter(f=>!props.session?.enabled_features || props.session.enabled_features.includes(f.id)))
const features=ref([]),active=ref(''),open=ref({}),contexts=ref({})
const catalogs=ref({}),fileRequest=ref(null),library=ref([])
const previewFiles=computed(()=>{
 const available=new Set(visibleFeatures.value.map(f=>f.id)),files=new Map()
 for(const file of library.value){const feature=file.path.split('.').pop().toLowerCase();if(available.has(feature))files.set(file.path,{...file,feature})}
 for(const [feature,values] of Object.entries(catalogs.value))if(available.has(feature))for(const file of values)files.set(file.path,{...file,feature})
 return [...files.values()].sort((a,b)=>a.path.localeCompare(b.path))
})
async function loadFiles(){const id=props.session?.id;if(!id){library.value=[];return}try{const files=await api(`/agent/sessions/${id}/files`);if(props.session?.id===id)library.value=files}catch(e){emit('error',e.message)}}
function openFile(path){const feature=path.split('.').pop().toLowerCase();active.value=feature;fileRequest.value={path,feature,token:(fileRequest.value?.token||0)+1}}
async function reload(){try{features.value=(await api('/features')).filter(f=>f.installed&&f.enabled&&components[f.id]);if(!features.value.some(f=>f.id===active.value)){active.value='';emit('open',false);emit('context',null)}}catch(e){emit('error',e.message)}}
function setOpen(feature,value){open.value={...open.value,[feature]:value};if(value)active.value=feature;emit('open',!!open.value[active.value]);emit('context',contexts.value[active.value]||null)}
function setContext(feature,value){contexts.value={...contexts.value,[feature]:value};if(feature===active.value)emit('context',value)}
watch(()=>props.session?.id,()=>{active.value='';open.value={};contexts.value={};catalogs.value={};library.value=[];fileRequest.value=null;emit('open',false);emit('context',null);loadFiles()},{immediate:true})
watch(()=>props.events?.filter(e=>e.type==='output_files').at(-1)?.seq,loadFiles)
onMounted(reload)
defineExpose({reload,openFile})
</script>
<template><component v-for="feature in visibleFeatures" :key="feature.id" :is="components[feature.id]" :session="session" :events="events" :active="active===feature.id" :files="previewFiles" :open-request="fileRequest?.feature===feature.id ? fileRequest : null" @catalog="catalogs[feature.id]=$event" @select-file="openFile" @open="setOpen(feature.id,$event)" @context="setContext(feature.id,$event)" @error="emit('error',$event)" /></template>
