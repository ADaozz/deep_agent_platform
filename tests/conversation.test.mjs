import { test } from 'node:test'
import assert from 'node:assert/strict'
import { conversationTimeline } from '../frontend/src/conversation.js'

test('aggregates cumulative reasoning across model calls and keeps ordered actions separate from final answer', () => {
  const events = [
    { seq: 1, type: 'operation', operation: 'message', text: 'Inspect the document.' },
    { seq: 2, type: 'assistant_started' },
    { seq: 3, type: 'thinking_delta', content: 'I will inspect' },
    { seq: 4, type: 'thinking_delta', content: 'I will inspect the document.' },
    { seq: 5, type: 'assistant_completed', content: 'Reading the document now.' },
    { seq: 6, type: 'tool_started', name: 'read_file', tool_call_id: 'internal-id', arguments: { file_path: '/private/internal/document' } },
    { seq: 7, type: 'tool_output_delta', tool_call_id: 'internal-id', content: 'RAW_PARTIAL_LOG' },
    { seq: 8, type: 'tool_completed', tool_call_id: 'internal-id', name: 'read_file', content: '{"content":"Document text", "metadata":{"secret":"PRIVATE_METADATA"},"tool_call_id":"internal-id"}', artifact: { internal: 'PRIVATE_ARTIFACT' } },
    { seq: 9, type: 'assistant_started' },
    { seq: 10, type: 'thinking_delta', content: 'I have the information needed.' },
    { seq: 11, type: 'assistant_completed', content: 'The document is ready.' },
    { seq: 12, type: 'result', status: 'completed', output: 'The document is ready.' },
  ]
  const original = JSON.stringify(events)
  const blocks = conversationTimeline(events, 'completed')
  assert.deepEqual(blocks.map(b => b.kind), ['user', 'process', 'assistant'])
  const process = blocks[1]
  assert.equal(process.actionCount, 1)
  assert.equal(process.active, false)
  assert.equal(process.thoughts, 'I will inspect the document.\n\nReading the document now.\n\nI have the information needed.')
  assert.equal(process.actions[0].status, 'completed')
  assert.equal(process.actions[0].successCount, 1)
  assert.equal(process.actions[0].failureCount, 0)
  assert.equal('detail' in process.actions[0], false)
  const output = JSON.stringify(blocks)
  for (const hidden of ['internal-id', '/private/internal/document', 'RAW_PARTIAL_LOG', 'PRIVATE_METADATA', 'PRIVATE_ARTIFACT', 'Document text']) assert.equal(output.includes(hidden), false)
  assert.equal(JSON.stringify(events), original, 'the native journal remains untouched')
})

test('approval resumes in the same process and later user turns stay separate', () => {
  const events = [
    { seq: 1, type: 'operation', operation: 'message', text: 'Create a document.' },
    { seq: 2, type: 'assistant_started' },
    { seq: 3, type: 'tool_started', tool_call_id: 'write', name: 'write_file' },
    { seq: 4, type: 'result', status: 'waiting_confirmation' },
  ]
  let blocks = conversationTimeline(events, 'waiting_confirmation')
  assert.equal(blocks[1].actions[0].status, 'waiting_confirmation')
  events.push({ seq: 5, type: 'operation', operation: 'approve' })
  blocks = conversationTimeline(events, 'running')
  assert.equal(blocks[1].active, true)
  assert.equal(blocks[1].actions[0].status, 'running')
  events.push(
    { seq: 6, type: 'tool_completed', tool_call_id: 'write', name: 'tool' },
    { seq: 7, type: 'result', status: 'completed', output: 'Done.' },
    { seq: 8, type: 'operation', operation: 'message', text: 'What next?' },
    { seq: 9, type: 'assistant_completed', content: 'Review the document.' },
  )
  blocks = conversationTimeline(events, 'completed')
  assert.deepEqual(blocks.map(b => b.kind), ['user', 'process', 'assistant', 'user', 'assistant'])
  assert.equal(blocks[1].actions[0].name, 'write_file')
  assert.equal(blocks[2].content, 'Done.')
})

test('parallel actions retain start order, individual errors and completion progress', () => {
  const blocks = conversationTimeline([
    { seq: 1, type: 'status', status: 'running' },
    { seq: 2, type: 'tool_started', name: 'execute', tool_call_id: 'a' },
    { seq: 3, type: 'tool_started', name: 'web_fetch', tool_call_id: 'b' },
    { seq: 4, type: 'tool_completed', name: 'tool', tool_call_id: 'b', is_error: true, content: 'Could not fetch the page.' },
    { seq: 5, type: 'tool_completed', name: 'tool', tool_call_id: 'a', content: 'Command done.' },
    { seq: 6, type: 'tool_started', name: 'read_file', tool_call_id: 'c' },
    { seq: 7, type: 'task_progress', content: 'Checking source material.' },
  ], 'running')
  const process = blocks[0]
  assert.deepEqual(process.actions.map(s => s.name), ['execute', 'web_fetch', 'read_file'])
  assert.deepEqual(process.actions.map(s => s.status), ['completed', 'failed', 'running'])
  assert.equal(process.actions[0].successCount, 1)
  assert.equal(process.actions[1].failureCount, 1)
  assert.equal(process.actionCount, 3)
})

test('interrupted tools stop showing running and never invent a final answer', () => {
  const blocks = conversationTimeline([
    { seq: 1, type: 'status', status: 'running' },
    { seq: 2, type: 'tool_started', name: 'execute', tool_call_id: 'command' },
  ], 'interrupted')
  assert.equal(blocks[0].active, false)
  assert.equal(blocks[0].actions[0].status, 'interrupted')
  assert.equal(blocks.length, 1)
})

test('same tools merge per turn with success and failure counts, without commands or output', () => {
  const events = [
    { seq: 1, type: 'operation', operation: 'message', text: 'Check files' },
    { seq: 2, type: 'tool_started', name: 'execute', tool_call_id: 'a', arguments: { command: 'PRIVATE_COMMAND' } },
    { seq: 3, type: 'tool_started', name: 'read_file', tool_call_id: 'b' },
    { seq: 4, type: 'tool_started', name: 'execute', tool_call_id: 'c' },
    { seq: 5, type: 'tool_completed', name: 'tool', tool_call_id: 'c', is_error: true, content: 'PRIVATE_FAILURE' },
    { seq: 6, type: 'tool_completed', name: 'tool', tool_call_id: 'a', content: 'PRIVATE_OUTPUT' },
    { seq: 7, type: 'tool_completed', name: 'tool', tool_call_id: 'a', content: 'DUPLICATE_COMPLETION' },
    { seq: 8, type: 'tool_completed', name: 'tool', tool_call_id: 'b' },
    { seq: 9, type: 'result', status: 'completed' },
    { seq: 10, type: 'operation', operation: 'message', text: 'Check again' },
    { seq: 11, type: 'tool_started', name: 'execute', tool_call_id: 'd' },
  ]
  const blocks = conversationTimeline(events, 'running')
  const processes = blocks.filter(b => b.kind === 'process')
  assert.equal(processes.length, 2)
  assert.deepEqual(processes[0].actions.map(s => s.name), ['execute', 'read_file'])
  const commands = processes[0].actions[0]
  assert.equal(commands.successCount, 1)
  assert.equal(commands.failureCount, 1)
  assert.equal(commands.count, 2)
  assert.equal(processes[0].actionCount, 3)
  assert.equal(processes[1].actions[0].successCount, 0)
  assert.equal(processes[1].actions[0].runningCount, 1)
  const output = JSON.stringify(blocks)
  for (const hidden of ['PRIVATE_COMMAND', 'PRIVATE_FAILURE', 'PRIVATE_OUTPUT', 'DUPLICATE_COMPLETION']) assert.equal(output.includes(hidden), false)
})

test('the first process or answer block of each turn leads the agent label', () => {
  const blocks = conversationTimeline([
    { seq: 1, type: 'operation', operation: 'message', text: 'Hello' },
    { seq: 2, type: 'thinking_delta', content: 'Thinking.' },
    { seq: 3, type: 'assistant_completed', content: 'Hi.' },
    { seq: 4, type: 'operation', operation: 'message', text: 'Again' },
    { seq: 5, type: 'assistant_completed', content: 'Sure.' },
  ], 'completed')
  assert.deepEqual(blocks.map(b => b.kind), ['user', 'process', 'assistant', 'user', 'assistant'])
  assert.deepEqual(blocks.map(b => Boolean(b.lead)), [false, true, false, false, true])
})

test('workspace output files attach to the turn after the answer', () => {
  const blocks = conversationTimeline([
    { seq: 1, type: 'operation', operation: 'message', text: 'Build a document.' },
    { seq: 2, type: 'assistant_started' },
    { seq: 3, type: 'assistant_completed', content: 'Created the file.' },
    { seq: 4, type: 'output_files', files: [{ path: 'reports/summary.docx', size: 1024 }] },
    { seq: 5, type: 'result', status: 'completed', output: 'Created the file.' },
  ], 'completed')
  assert.deepEqual(blocks.map(b => b.kind), ['user', 'assistant', 'files'])
  assert.deepEqual(blocks[2].files, [{ path: 'reports/summary.docx', size: 1024 }])
})

test('a finished session never renders its last turn as running', () => {
  const partial = [
    { seq: 1, type: 'operation', operation: 'message', text: 'Hello' },
    { seq: 2, type: 'status', status: 'running' },
    { seq: 3, type: 'assistant_started' },
    { seq: 4, type: 'thinking_delta', content: 'Partial history.' },
  ]
  const done = conversationTimeline(partial, 'completed')
  assert.equal(done[1].status, 'completed')
  assert.equal(done[1].active, false)
  const live = conversationTimeline(partial, 'running')
  assert.equal(live[1].status, 'running')
  assert.equal(live[1].active, true)
})
