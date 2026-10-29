import { test } from 'node:test'
import assert from 'node:assert/strict'
import { conversationTimeline } from '../frontend/src/conversation.js'
import { stepBody } from '../frontend/src/processView.js'
const processOf = (events, status='running') => conversationTimeline(events,status).find(block=>block.kind==='process')
const at=(second)=>`2026-10-29T14:00:${String(second).padStart(2,'0')}.000Z`

test('reasoning, interim summary and distinct calls stay in their occurrence order',()=>{
 const events=[
  {seq:1,type:'operation',operation:'message',text:'Inspect files',occurred_at:at(0)},
  {seq:2,type:'assistant_started',occurred_at:at(1)},
  {seq:3,type:'thinking_delta',content:'Inspect',occurred_at:at(2)},
  {seq:4,type:'thinking_delta',content:'Inspect the files.',occurred_at:at(3)},
  {seq:5,type:'assistant_completed',content:'Reading now.',result:{thinking:'Inspect the files.'},occurred_at:at(4)},
  {seq:6,type:'tool_started',name:'read_file',tool_call_id:'a',arguments:{file_path:'a.txt'},occurred_at:at(5)},
  {seq:7,type:'tool_started',name:'read_file',tool_call_id:'b',occurred_at:at(6)},
  {seq:8,type:'tool_completed',name:'tool',tool_call_id:'b',content:'B finished',occurred_at:at(7)},
  {seq:9,type:'tool_completed',name:'tool',tool_call_id:'a',content:'A finished',occurred_at:at(9)},
  {seq:10,type:'assistant_started',occurred_at:at(10)},
  {seq:11,type:'thinking_delta',content:'Checking results.',occurred_at:at(11)},
 ]
 const original=JSON.stringify(events),process=processOf(events)
 assert.deepEqual(process.steps.map(step=>step.kind),['reasoning','summary','tool','tool','reasoning'])
 assert.equal(process.steps[0].content,'Inspect the files.')
 assert.deepEqual(process.steps.filter(s=>s.kind==='tool').map(s=>[s.name,s.finalOutput,s.durationMs]),[['read_file','A finished',4000],['read_file','B finished',1000]])
 assert.equal(process.steps.at(-1).status,'running')
 assert.equal(process.finished,false)
 assert.equal(JSON.stringify(events),original)
})

test('stream chunks append without losing equal chunks, duplicate seqs are ignored',()=>{
 const events=[
  {seq:1,type:'tool_started',name:'execute',tool_call_id:'a',arguments:{command:'run'}},
  {seq:2,type:'tool_output_delta',tool_call_id:'a',content:'same\n',stream:'stdout'},
  {seq:3,type:'tool_output_delta',tool_call_id:'a',content:'same\n',stream:'stdout'},
  {seq:3,type:'tool_output_delta',tool_call_id:'a',content:'same\n',stream:'stdout'},
  {seq:4,type:'tool_output_delta',tool_call_id:'a',content:'warning\n',stream:'stderr'},
  {seq:5,type:'tool_completed',tool_call_id:'a',name:'tool',content:'Finished successfully'},
 ]
 const process=processOf(events),step=process.steps[0]
 assert.equal(process.steps.length,1)
 assert.equal(step.streamedOutput,'same\nsame\nwarning\n')
 assert.equal(step.finalOutput,'Finished successfully')
 const body=stepBody(step,key=>key)
 assert(body.includes('[stdout]\nsame\nsame\n'))
 assert(body.includes('[stderr]\nwarning\n'))
 assert(body.includes('"command": "run"'))
 assert(body.includes('Finished successfully'))
})

test('all tool rows completing never finishes a task while the Agent is still running',()=>{
 const events=[{seq:1,type:'tool_started',name:'execute',tool_call_id:'a'}, {seq:2,type:'tool_completed',tool_call_id:'a',content:'Done'}]
 let process=processOf(events)
 assert.equal(process.steps[0].status,'completed')
 assert.equal(process.active,true);assert.equal(process.finished,false)
 events.push({seq:3,type:'assistant_started'},{seq:4,type:'thinking_delta',content:'Preparing the final response.'})
 process=processOf(events)
 assert.equal(process.steps.at(-1).status,'running');assert.equal(process.finished,false)
 events.push({seq:5,type:'assistant_completed',content:'All done'},{seq:6,type:'result',status:'completed'})
 process=processOf(events,'completed');assert.equal(process.finished,true)
 assert(process.steps.every(step=>step.status==='completed'))
})

test('parallel calls retain independent status, outputs and stable keys',()=>{
 const events=[{seq:1,type:'tool_started',tool_call_id:'a',name:'execute'},{seq:2,type:'tool_started',tool_call_id:'b',name:'execute'}]
 const initial=processOf(events)
 events.push({seq:3,type:'tool_output_delta',tool_call_id:'a',content:'A'},{seq:4,type:'tool_completed',tool_call_id:'b',is_error:true,content:'B failed'})
 const next=processOf(events)
 assert.deepEqual(next.steps.map(s=>s.key),initial.steps.map(s=>s.key))
 assert.deepEqual(next.steps.map(s=>s.status),['running','failed'])
 assert.equal(next.steps[0].streamedOutput,'A');assert.equal(next.steps[1].streamedOutput,'')
 assert.equal(next.steps.length,2)
})

test('approval, pause and resume keep the same row and do not finish the task',()=>{
 const events=[{seq:1,type:'operation',operation:'message',text:'Write a file'},{seq:2,type:'tool_started',tool_call_id:'a',name:'write_file'},{seq:3,type:'result',status:'waiting_confirmation'}]
 const pending=processOf(events,'waiting_confirmation');assert.equal(pending.finished,false);assert.equal(pending.steps[0].status,'pending')
 events.push({seq:4,type:'operation',operation:'approve'})
 const resumed=processOf(events);assert.equal(resumed.steps[0].key,pending.steps[0].key);assert.equal(resumed.steps[0].status,'running')
 events.push({seq:5,type:'tool_completed',tool_call_id:'a',name:'tool',content:'Saved'},{seq:6,type:'result',status:'completed'})
 assert.equal(processOf(events,'completed').finished,true)
})

test('failure and cancellation close outstanding steps without inventing success',()=>{
 for(const status of ['failed','cancelled','interrupted']){
  const process=processOf([{seq:1,type:'tool_started',tool_call_id:'a',name:'execute'},{seq:2,type:'tool_output_delta',tool_call_id:'a',content:'Partial output'},{seq:3,type:'result',status}],status)
  assert.equal(process.steps[0].status,status);assert.equal(process.steps[0].streamedOutput,'Partial output');assert.equal(process.finished,true)
 }
 const partial=processOf([{seq:1,type:'tool_started',tool_call_id:'a',name:'execute'}],'completed')
 assert.equal(partial.steps[0].status,'interrupted');assert.equal(partial.finished,true)
})

test('late duplicate completions cannot change failure to success or reopen rows',()=>{
 const events=[{seq:1,type:'tool_started',name:'execute',tool_call_id:'a'},{seq:2,type:'tool_completed',tool_call_id:'a',is_error:true,content:'Original failure'},{seq:3,type:'tool_completed',tool_call_id:'a',content:'Duplicate success'},{seq:4,type:'tool_output_delta',tool_call_id:'a',content:'Late log'}]
 const step=processOf(events).steps[0]
 assert.equal(step.status,'failed');assert.equal(step.finalOutput,'Original failure');assert.equal(step.streamedOutput,'Late log')
})

test('full histories remain intact beyond five lines; raw artifact envelopes are not rendered',()=>{
 const output=Array.from({length:1000},(_,i)=>`Line ${i}`).join('\n')
 const process=processOf([{seq:1,type:'tool_started',tool_call_id:'a',name:'execute',arguments:{command:'long job'}},{seq:2,type:'tool_output_delta',tool_call_id:'a',content:output},{seq:3,type:'tool_completed',tool_call_id:'a',content:output,artifact:{internal:'NOT_A_TOOL_LOG'}}])
 assert.equal(process.steps[0].streamedOutput,output)
 const body=stepBody(process.steps[0],key=>key)
 assert(body.includes('Line 0\n'));assert(body.endsWith('Line 999'));assert(!body.includes('NOT_A_TOOL_LOG'))
 assert.equal((body.match(/Line 999/g)||[]).length,1)
})

test('separate turns keep their summaries, final answers, file blocks and lead labels',()=>{
 const blocks=conversationTimeline([
  {seq:1,type:'operation',operation:'message',text:'Build'},
  {seq:2,type:'thinking_delta',content:'Planning'},
  {seq:3,type:'assistant_completed',content:'Created'},
  {seq:4,type:'output_files',files:[{path:'report.docx',size:42}]},
  {seq:5,type:'result',status:'completed',output:'Created'},
  {seq:6,type:'operation',operation:'message',text:'Next'},
  {seq:7,type:'assistant_completed',content:'Ready'},
  {seq:8,type:'result',status:'completed',output:'Ready'},
 ],'completed')
 assert.deepEqual(blocks.map(b=>b.kind),['user','process','assistant','files','user','assistant'])
 assert.equal(blocks[1].finished,true);assert.equal(blocks[1].lead,true);assert.equal(blocks.at(-1).lead,true)
 assert.deepEqual(blocks[3].files,[{path:'report.docx',size:42}])
})

test('task and step duration use persisted event timestamps, no fabricated old timings',()=>{
 const process=processOf([{seq:1,type:'operation',operation:'message',occurred_at:at(0)},{seq:2,type:'tool_started',name:'execute',tool_call_id:'a',occurred_at:at(2)},{seq:3,type:'tool_completed',tool_call_id:'a',occurred_at:at(5)},{seq:4,type:'result',status:'completed',occurred_at:at(8)}],'completed')
 assert.equal(process.durationMs,8000);assert.equal(process.steps[0].durationMs,3000)
 assert.equal(processOf([{seq:1,type:'tool_completed',tool_call_id:'old',name:'read_file'}],'completed').steps[0].durationMs,null)
})
