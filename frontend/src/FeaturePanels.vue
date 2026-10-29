<script setup>
import {ref,computed,defineAsyncComponent,watch,onMounted} from 'vue'
import {api} from './featureApi'
const props=defineProps({session:Object,events:Array})
const emit=defineEmits(['open','context','error'])
const modules=import.meta.glob('./features/*/Panel.vue')
const components=Object.fromEntries(Object.entries(modules).map(([path,loader])=>[path.split('/')[2],defineAsyncComponent(loader)]))
const features=ref([]),active=ref(''),open=ref({}),contexts=ref({})
async function reload(){try{features.value=(await api('/features')).filter(f=>f.installed&&f.enabled&&components[f.id]);if(!features.value.some(f=>f.id===active.value)){active.value='';emit('open',false);emit('context',null)}}catch(e){emit('error',e.message)}}
function setOpen(feature,value){open.value={...open.value,[feature]:value};if(value)active.value=feature;emit('open',!!open.value[active.value]);emit('context',contexts.value[active.value]||null)}
function setContext(feature,value){contexts.value={...contexts.value,[feature]:value};if(feature===active.value)emit('context',value)}
watch(()=>props.session?.id,()=>{active.value='';open.value={};contexts.value={};emit('open',false);emit('context',null)})
onMounted(reload)
defineExpose({reload})
</script>
<template><component v-for="feature in features" :key="feature.id" :is="components[feature.id]" :session="session" :events="events" :active="active===feature.id" @open="setOpen(feature.id,$event)" @context="setContext(feature.id,$event)" @error="emit('error',$event)" /></template>
