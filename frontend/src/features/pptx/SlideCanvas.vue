<script setup>
import {computed} from 'vue'
const props=defineProps({slide:Object,revision:Number,baseUrl:String,thumbnail:Boolean,profile:Object,highlights:{type:Array,default:()=>[]}})
const width=960,height=540
const FALLBACK={background:'FFFFFF',card:'EDF4FC',border:'D3DFEC',text:'17324D',muted:'536B82',accent:'246BCE',surface:'FBFCFE',chart_series:['246BCE','60A5B8','92B14B','C0715A']}
const design=computed(()=>({colors:{...FALLBACK,...(props.profile?.colors||{})},tables:props.profile?.tables||{header_fill_token:'card'},fonts:props.profile?.fonts||{body:'Noto Sans CJK SC'}}))
const formal=computed(()=>props.slide?.formal&&(props.slide.complex||props.slide.review?.automatic_passed))
const token=name=>'#'+(design.value.colors[name]||FALLBACK[name]||'17324D')
const marker=computed(()=>'pptx-arrow-'+(props.slide?.slide_id||'slide')+(props.thumbnail?'-t':''))
function box(e){const g=e.geometry;return {x:g.x*width,y:g.y*height,w:g.w*width,h:g.h*height}}
function style(e){const s=e.style||{};return {fontFamily:`'${s.font_family||'Noto Sans CJK SC'}',sans-serif`,fontSize:(s.font_size||20)+'px',fontWeight:s.bold?700:400,color:'#'+(s.color||FALLBACK.text),lineHeight:s.line_height||1.22,padding:'3px',whiteSpace:'pre-wrap',overflowWrap:'break-word',height:'100%',width:'100%',boxSizing:'border-box',textAlign:e.align||'left'}}
function nodeStyle(e){return {...style(e),display:'flex',flexDirection:'column',justifyContent:'center',textAlign:'center',padding:'4px 8px'}}
function chartMax(e){return Math.max(1,...e.chart.series.flatMap(s=>s.values))}
function points(e,series){const b=box(e);return series.values.map((v,i)=>`${b.x+35+(i+.5)*(b.w-50)/e.chart.categories.length},${b.y+b.h-45-v/chartMax(e)*(b.h-75)}`).join(' ')}
function seriesColor(index){const values=design.value.colors.chart_series||FALLBACK.chart_series;return '#'+values[index%values.length]}
function componentLabel(e){return [e.component?.component_id,e.component?.variant&&e.component.variant!=='default'?e.component.variant:''].filter(Boolean).join(' · ')}
function route(e){const path=e.route||(e.connector?[e.connector.begin,e.connector.end]:null);return path?path.map(([x,y])=>`${x*width},${y*height}`).join(' '):''}
const dashed=e=>['returns','depends_on'].includes(e.relation)?'6 4':e.relation==='association'?'2 4':null
const boxes=computed(()=>{
 const result=[];const elements=props.slide?.elements||[]
 for(const item of props.highlights){
  if(item.region)result.push({key:item.id+'-r',x:item.region[0],y:item.region[1],w:item.region[2],h:item.region[3],severity:item.severity,active:item.active,kind:'region'})
  for(const id of item.element_ids||[]){const e=elements.find(value=>value.id===id);if(e)result.push({key:item.id+'-'+id,...e.geometry,severity:item.severity,active:item.active,kind:'element'})}
 }
 return result
})
</script>
<template>
 <div v-if="slide" class="pptx-canvas" :class="{thumbnail}">
  <img v-if="formal" :src="`${baseUrl}/renders/${slide.formal.revision}/${slide.formal.image}`" :alt="slide.slide_id" draggable="false">
  <div v-else-if="slide.complex" class="pptx-complex">模板包含复杂对象，等待正式渲染</div>
  <svg v-else viewBox="0 0 960 540" xmlns="http://www.w3.org/2000/svg" role="img" :aria-label="slide.slide_id">
   <defs><marker :id="marker" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" :fill="token('muted')"/></marker></defs>
   <rect width="960" height="540" :fill="'#'+slide.background"/>
   <template v-for="e in slide.elements" :key="e.id">
    <foreignObject v-if="e.type==='text'" :x="box(e).x" :y="box(e).y" :width="box(e).w" :height="box(e).h"><div xmlns="http://www.w3.org/1999/xhtml" :style="style(e)">{{ e.text }}</div></foreignObject>
    <polyline v-else-if="e.type==='connector'" :points="route(e)" fill="none" :stroke="'#'+(e.fill||design.colors.muted)" stroke-width="2" :stroke-dasharray="dashed(e)" :marker-end="e.arrow&&e.arrow!=='none'?`url(#${marker})`:null" :marker-start="e.arrow==='both'?`url(#${marker})`:null"/>
    <line v-else-if="e.type==='shape'&&e.shape==='line'" :x1="box(e).x" :y1="box(e).y+box(e).h/2" :x2="box(e).x+box(e).w" :y2="box(e).y+box(e).h/2" :stroke="token('accent')" stroke-width="2"/>
    <ellipse v-else-if="e.type==='shape'&&e.shape==='ellipse'" :cx="box(e).x+box(e).w/2" :cy="box(e).y+box(e).h/2" :rx="box(e).w/2" :ry="box(e).h/2" :fill="'#'+(e.fill||design.colors.card)"/>
    <polygon v-else-if="e.type==='shape'&&e.shape==='arrow'" :points="`${box(e).x},${box(e).y+box(e).h*.3} ${box(e).x+box(e).w*.65},${box(e).y+box(e).h*.3} ${box(e).x+box(e).w*.65},${box(e).y} ${box(e).x+box(e).w},${box(e).y+box(e).h/2} ${box(e).x+box(e).w*.65},${box(e).y+box(e).h} ${box(e).x+box(e).w*.65},${box(e).y+box(e).h*.7} ${box(e).x},${box(e).y+box(e).h*.7}`" :fill="'#'+(e.fill||design.colors.accent)"/>
    <g v-else-if="e.type==='shape'&&e.text">
     <rect :x="box(e).x" :y="box(e).y" :width="box(e).w" :height="box(e).h" :rx="e.shape==='rounded_rectangle'?10:0" :fill="e.fill===null?'none':'#'+(e.fill||design.colors.card)"/>
     <foreignObject :x="box(e).x" :y="box(e).y" :width="box(e).w" :height="box(e).h"><div xmlns="http://www.w3.org/1999/xhtml" :style="nodeStyle(e)"><span v-for="(block,index) in e.text_blocks||[{text:e.text,font_size:e.style?.font_size}]" :key="index" :style="{fontSize:block.font_size+'px',fontWeight:index?400:700}">{{ block.text }}</span></div></foreignObject>
    </g>
    <rect v-else-if="e.type==='shape'" :x="box(e).x" :y="box(e).y" :width="box(e).w" :height="box(e).h" :rx="e.shape==='rounded_rectangle'?12:0" :fill="e.fill===null?'none':'#'+(e.fill||design.colors.card)"/>
    <image v-else-if="e.type==='image'" :x="box(e).x" :y="box(e).y" :width="box(e).w" :height="box(e).h" :href="`${baseUrl}/assets/${revision}/${slide.slide_id}/${e.id}`" preserveAspectRatio="xMidYMid meet"/>
    <g v-else-if="e.type==='component'" :data-component="e.component?.component_id" :data-component-id="e.id">
     <image :x="box(e).x" :y="box(e).y" :width="box(e).w" :height="box(e).h" :href="`${baseUrl}/assets/${revision}/${slide.slide_id}/${e.id}`" preserveAspectRatio="xMidYMid meet"/>
     <foreignObject v-if="!thumbnail" :x="box(e).x" :y="box(e).y" :width="Math.min(box(e).w,340)" :height="18"><div xmlns="http://www.w3.org/1999/xhtml" class="pptx-component-tag">{{ componentLabel(e) }}</div></foreignObject>
    </g>
    <g v-else-if="e.type==='table'">
     <g v-for="(row,r) in e.rows" :key="r"><g v-for="(cell,c) in row" :key="c"><rect :x="box(e).x+c*box(e).w/row.length" :y="box(e).y+r*box(e).h/e.rows.length" :width="box(e).w/row.length" :height="box(e).h/e.rows.length" :fill="r===0?token(design.tables.header_fill_token||'card'):token('background')" :stroke="token('border')"/><foreignObject :x="box(e).x+c*box(e).w/row.length+4" :y="box(e).y+r*box(e).h/e.rows.length+5" :width="box(e).w/row.length-8" :height="box(e).h/e.rows.length-10"><div xmlns="http://www.w3.org/1999/xhtml" :style="`font:14px '${design.fonts.body}',sans-serif;color:${token('text')};white-space:pre-wrap`">{{ cell }}</div></foreignObject></g></g>
    </g>
    <g v-else-if="e.type==='chart'">
     <line :x1="box(e).x+30" :y1="box(e).y+box(e).h-40" :x2="box(e).x+box(e).w" :y2="box(e).y+box(e).h-40" :stroke="token('border')"/>
     <template v-for="(series,s) in e.chart.series" :key="s"><polyline v-if="e.chart.type==='line'" :points="points(e,series)" fill="none" :stroke="seriesColor(s)" stroke-width="3"/><rect v-else v-for="(value,i) in series.values" :key="i" :x="box(e).x+35+(i+(s/e.chart.series.length))*(box(e).w-50)/e.chart.categories.length" :y="box(e).y+box(e).h-45-value/chartMax(e)*(box(e).h-75)" :width="(box(e).w-65)/e.chart.categories.length/e.chart.series.length*.7" :height="value/chartMax(e)*(box(e).h-75)" :fill="seriesColor(s)"/></template>
     <text v-for="(label,i) in e.chart.categories" :key="i" :x="box(e).x+35+(i+.5)*(box(e).w-50)/e.chart.categories.length" :y="box(e).y+box(e).h-15" text-anchor="middle" font-size="12" :fill="token('muted')">{{ label }}</text>
    </g>
   </template>
  </svg>
  <div v-for="item in boxes" :key="item.key" class="pptx-highlight" :class="['pptx-highlight-'+(item.severity||'minor'),'pptx-highlight-'+item.kind,{active:item.active}]" :style="{left:item.x*100+'%',top:item.y*100+'%',width:item.w*100+'%',height:item.h*100+'%'}"></div>
 </div>
</template>
<style>
.pptx-canvas{position:relative;width:100%;aspect-ratio:16/9;background:white;box-shadow:0 3px 20px #0002;overflow:hidden;user-select:none}.pptx-canvas>svg,.pptx-canvas>img{display:block;width:100%;height:100%;object-fit:contain}.pptx-complex{display:flex;align-items:center;justify-content:center;height:100%;font-size:12px;color:#6c7b8a;background:#f6f8fb}.pptx-canvas.thumbnail{box-shadow:0 1px 5px #0001;pointer-events:none}.pptx-component-tag{display:inline-block;padding:1px 5px;border-radius:0 0 4px 0;background:#000000aa;color:#fff;font:9px/1.5 ui-monospace,monospace;letter-spacing:.2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.pptx-highlight{position:absolute;pointer-events:none;box-sizing:border-box;border:2px dashed #b26b00;border-radius:3px;opacity:.55;transition:opacity .15s}.pptx-highlight-element{border-style:solid;border-width:1.5px}.pptx-highlight.active{opacity:1;background:#b26b0014}.pptx-highlight-critical,.pptx-highlight-major{border-color:#b3372f}.pptx-highlight-critical.active,.pptx-highlight-major.active{background:#b3372f14}.pptx-highlight-minor{border-color:#5b7391}.pptx-canvas.thumbnail .pptx-highlight{display:none}
</style>
