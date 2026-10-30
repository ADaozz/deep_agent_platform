import test from 'node:test'
import assert from 'node:assert/strict'
import {sessionOutputFiles} from '../frontend/src/outputFiles.js'

test('repeated edits of one path produce one current file block', () => {
  const events = [
    {type:'output_files',files:[{path:'report.docx',size:10}]},
    {type:'output_files',files:[{path:'report.docx',size:20},{path:'deck.pptx',size:30}]},
  ]
  const files = sessionOutputFiles({id:'session',artifacts:[],output_files:events.flatMap(event=>event.files)})
  assert.equal(files.length,2)
  assert.equal(files.find(file=>file.path==='report.docx').size,20)
  assert.deepEqual(sessionOutputFiles({id:'session',output_files:[],artifacts:[]}),[])
})

test('current snapshot replaces history and encodes download paths', () => {
  const files=sessionOutputFiles({id:'session',output_files:[{path:'reports/报告 #1.docx',size:40}],artifacts:[]})
  assert.equal(files[0].url,'/api/agent/sessions/session/files/reports/%E6%8A%A5%E5%91%8A%20%231.docx')
})
