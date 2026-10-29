// Keep the native journal intact; project each user turn into separate reasoning,
// ordered operations and final answers. No raw event objects reach the template.
export function conversationTimeline(events, status = 'idle') {
  const turns = []
  let turn, model
  const ensureTurn = key => {
    if (!turn) { turn = { key, models: [], actions: [], tools: new Map(), notes: [], result: '' }; turns.push(turn) }
    return turn
  }
  const ensureModel = key => {
    ensureTurn(key)
    if (!model) { model = { key, content: '', thought: '', hasTools: false }; turn.models.push(model) }
    return model
  }
  for (const event of events) {
    if (event.type === 'operation' && event.operation === 'message') {
      turn = null; model = null
      ensureTurn(event.seq)
      turn.user = { kind: 'user', key: `user-${event.seq}`, content: event.text, attachments: event.attachments || [] }
    } else if (event.type === 'operation' && ['approve', 'answer', 'resume'].includes(event.operation)) {
      ensureTurn(event.seq).status = 'running'
    } else if (event.type === 'status') {
      ensureTurn(event.seq).status = event.status
    } else if (event.type === 'assistant_started') {
      ensureTurn(event.seq)
      model = { key: event.seq, content: '', thought: '', hasTools: false }
      turn.models.push(model)
    } else if (event.type === 'thinking_delta') {
      // Native deltas contain the cumulative text for this model invocation.
      ensureModel(event.seq).thought = event.content || ''
    } else if (event.type === 'assistant_delta' || event.type === 'assistant_completed') {
      const current = ensureModel(event.seq)
      current.content = event.content || ''
      if (event.type === 'assistant_completed' && event.result?.thinking) current.thought = event.result.thinking
    } else if (event.type === 'tool_started' || event.type === 'tool_completed') {
      ensureTurn(event.seq)
      if (event.type === 'tool_started' && model) model.hasTools = true
      const identity = event.tool_call_id || `${event.name}-${event.seq}`
      let step = turn.tools.get(identity)
      if (!step) {
        step = { key: event.seq, name: event.name, status: 'running' }
        turn.tools.set(identity, step)
        turn.actions.push(step)
      }
      if (event.name && event.name !== 'tool') step.name = event.name
      if (event.type === 'tool_completed') {
        step.status = event.is_error ? 'failed' : 'completed'
      }
    } else if (event.type === 'result') {
      ensureTurn(event.seq).status = event.status
      if (event.output) turn.result = event.output
    } else if (event.type === 'error') {
      ensureTurn(event.seq).status = 'failed'
    } else if (event.type === 'task_progress') {
      // A progress message, if emitted by the kernel, stays separate from logs.
      ensureTurn(event.seq).progress = typeof event.content === 'string' ? event.content : ''
    } else if (event.type === 'output_files') {
      // Workspace files created or changed during this turn, safe for display.
      const turn = ensureTurn(event.seq)
      turn.outputFiles = Array.isArray(turn.outputFiles) ? turn.outputFiles : []
      for (const file of event.files || []) {
        if (!file?.path) continue
        const existing = turn.outputFiles.find(item => item.path === file.path)
        if (existing) existing.size = file.size
        else turn.outputFiles.push({ path: file.path, size: file.size })
      }
    } else if (event.type === 'steer' || event.type === 'follow_up') {
      ensureTurn(event.seq).notes.push({ kind: 'note', key: event.seq, type: event.type, content: event.content })
    }
  }
  return turns.flatMap((item, index) => {
    const blocks = []
    if (item.user) blocks.push(item.user)
    const last = index === turns.length - 1
    const finished = ['completed', 'failed', 'cancelled', 'interrupted'].includes(status)
    // A finished session is authoritative for its last turn: an incomplete
    // history payload must not make old work look like it is still running.
    const currentStatus = last && finished ? status : item.status || (last ? status : 'completed')
    const thoughts = item.models.flatMap(chunk => [chunk.thought, chunk.hasTools ? chunk.content : '']).filter(Boolean).join('\n\n')
    const active = ['queued', 'running'].includes(currentStatus)
    // Group by the exact tool within this turn; the original journal and call
    // identities remain separate, so retries and out-of-order finishes count once.
    const groups = new Map()
    for (const step of item.actions) {
      let group = groups.get(step.name)
      if (!group) {
        group = { key: step.key, name: step.name, count: 0, successCount: 0, failureCount: 0, runningCount: 0, status: currentStatus }
        groups.set(step.name, group)
      }
      group.count++
      if (step.status === 'completed') group.successCount++
      if (step.status === 'failed') group.failureCount++
      if (step.status === 'running' && active) group.runningCount++
    }
    for (const group of groups.values()) {
      if (group.runningCount) group.status = 'running'
      else if (group.failureCount) group.status = 'failed'
      else if (group.successCount === group.count) group.status = 'completed'
    }
    if (thoughts || item.actions.length || active || item.progress) blocks.push({
      kind: 'process', key: `process-${item.key}`, thoughts,
      actions: [...groups.values()], status: currentStatus, active,
      actionCount: item.actions.length,
    })
    const answers = item.models.filter(chunk => !chunk.hasTools && chunk.content)
    for (const chunk of answers) blocks.push({ kind: 'assistant', key: `answer-${chunk.key}`, content: chunk.content })
    if (item.result && !answers.some(chunk => chunk.content === item.result)) blocks.push({ kind: 'assistant', key: `result-${item.key}`, content: item.result })
    if (item.outputFiles?.length) blocks.push({ kind: 'files', key: `files-${item.key}`, files: item.outputFiles })
    blocks.push(...item.notes)
    // The first visible agent block of the turn carries the shared "Agent" label.
    const lead = blocks.find(block => block.kind === 'process' || block.kind === 'assistant')
    if (lead) lead.lead = true
    return blocks
  })
}
