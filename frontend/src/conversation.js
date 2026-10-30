// The journal stays immutable. Each model summary and tool-call ID owns one
// timeline row; stream events update that row without regrouping repeated tools.
export const terminalStatuses = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
const liveStatuses = new Set(['running', 'queued'])
export function eventTime(event) {
  const value = event.occurred_at ?? event.timestamp ?? event.created_at
  if (typeof value === 'number' && Number.isFinite(value)) return value < 1e12 ? value * 1000 : value
  if (typeof value === 'string') { const ms = Date.parse(value); if (Number.isFinite(ms)) return ms }
  return null
}
export function rawText(value) {
  if (value == null) return ''
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}
export function shortResult(value) {
  const text = rawText(value)
  let brief = text
  try {
    const parsed = JSON.parse(text)
    if (parsed && typeof parsed === 'object') brief = rawText(parsed.message ?? parsed.error ?? parsed.summary ?? parsed.content ?? parsed.status ?? text)
  } catch { /* Plain stdout remains plain text. */ }
  return Array.from(brief.replace(/\s+/g, ' ').trim()).slice(0, 120).join('')
}
function timeSpan(start, end) { return start != null && end != null ? Math.max(0, end - start) : null }

// Steering notes wait at the end of the turn until the run reads them; the
// read point (steering_applied) is where they belong in the timeline.
function steeringText(value) { return rawText(value).split('\n[Presentation feedback:')[0].split('\n[Document feedback:')[0] }

export function conversationTimeline(events, sessionStatus = 'idle') {
  const turns = [], seen = new Set(), queued = [], waiting = new Map()
  let turn, model
  const ensureTurn = (event, index) => {
    if (!turn) {
      turn = { key: event.seq ?? index, models: [], steps: [], tools: new Map(), notes: [], result: '', startedAt: eventTime(event), endedAt: null, status: null }
      turns.push(turn)
    }
    return turn
  }
  const ensureModel = (event, index) => {
    ensureTurn(event, index)
    if (!model) {
      model = { key: event.message_id || event.seq || index, content: '', thought: '', hasTools: false, completed: false, startedAt: eventTime(event) }
      turn.models.push(model)
    }
    return model
  }
  const thinking = (current, event, index) => {
    if (!current.reasoning) {
      current.reasoning = { key: `reasoning-${current.key}`, kind: 'reasoning', name: 'reasoning', order: index, status: current.completed ? 'completed' : 'running', content: '', startedAt: current.startedAt ?? eventTime(event), endedAt: current.endedAt ?? null }
      turn.steps.push(current.reasoning)
    }
    current.reasoning.content = current.thought
    return current.reasoning
  }
  const promoteSummary = current => {
    if (!current?.content || current.summary) return
    current.summary = { key: `summary-${current.key}`, kind: 'summary', name: 'summary', order: current.contentOrder, content: current.content, status: current.completed ? 'completed' : 'running', startedAt: current.contentAt, endedAt: current.endedAt ?? null }
    turn.steps.push(current.summary)
  }
  const toolStep = (event, index) => {
    ensureTurn(event, index)
    const identity = event.tool_call_id || `unidentified-${event.seq ?? index}`
    let step = turn.tools.get(identity)
    if (!step) {
      step = { key: `tool-${turn.key}-${identity}`, kind: 'tool', name: event.name || 'tool', order: index, status: 'pending', arguments: null, streamedOutput: '', finalOutput: '', streams: [], startedAt: eventTime(event), endedAt: null, durationMs: null }
      turn.tools.set(identity, step); turn.steps.push(step)
    }
    if (event.name && event.name !== 'tool') step.name = event.name
    return step
  }
  const finish = (step, status, event) => {
    step.status = status; step.endedAt = eventTime(event) ?? step.endedAt
    const duration = event.duration_ms ?? event.result?.duration_ms
    step.durationMs = typeof duration === 'number' && duration >= 0 ? duration : timeSpan(step.startedAt, step.endedAt)
  }
  for (let index = 0; index < events.length; index++) {
    const event = events[index]
    if (event.seq != null) { if (seen.has(event.seq)) continue; seen.add(event.seq) }
    if (event.type === 'operation' && event.operation === 'message') {
      turn = null; model = null
      const current = ensureTurn(event, index)
      current.user = { kind: 'user', key: `user-${current.key}`, content: event.text, attachments: event.attachments || [] }
      current.status = 'running'
    } else if (event.type === 'operation' && ['approve', 'answer', 'resume'].includes(event.operation)) {
      ensureTurn(event, index).status = 'running'
      for (const step of turn.steps) if (step.status === 'pending') step.status = 'running'
    } else if (event.type === 'status') {
      ensureTurn(event, index).status = event.status
      if (terminalStatuses.has(event.status)) turn.endedAt = eventTime(event)
    } else if (event.type === 'assistant_started') {
      ensureTurn(event, index)
      model = { key: event.message_id || event.seq || index, content: '', thought: '', hasTools: false, completed: false, startedAt: eventTime(event) }
      turn.models.push(model)
    } else if (event.type === 'thinking_delta') {
      const current = ensureModel(event, index)
      // Native reasoning deltas are snapshots; tool stdout events are chunks.
      current.thought = event.content_mode === 'append' ? current.thought + rawText(event.content) : rawText(event.content)
      thinking(current, event, index)
    } else if (event.type === 'assistant_delta' || event.type === 'assistant_completed') {
      const current = ensureModel(event, index)
      if (current.contentOrder == null) { current.contentOrder = index; current.contentAt = eventTime(event) }
      if (event.content) current.content = rawText(event.content)
      if (event.type === 'assistant_completed') {
        current.completed = true; current.endedAt = eventTime(event)
        if (event.result?.thinking) { current.thought = rawText(event.result.thinking); thinking(current, event, index) }
        if (current.reasoning) finish(current.reasoning, 'completed', event)
      }
      if (current.hasTools) promoteSummary(current)
      if (current.summary) { current.summary.content = current.content; if (current.completed) finish(current.summary, 'completed', event) }
    } else if (['tool_pending', 'tool_started', 'tool_output_delta', 'tool_completed', 'tool_failed', 'tool_cancelled'].includes(event.type)) {
      if (model) { model.hasTools = true; promoteSummary(model) }
      const step = toolStep(event, index)
      if (event.arguments && Object.keys(event.arguments).length) step.arguments = event.arguments
      if (event.type === 'tool_started' && !terminalStatuses.has(step.status)) step.status = 'running'
      else if (event.type === 'tool_output_delta') {
        if (!terminalStatuses.has(step.status)) step.status = 'running'
        const content = rawText(event.content)
        step.streamedOutput += content
        step.streams.push({ stream: event.stream || 'stdout', content })
      } else if (['tool_completed', 'tool_failed', 'tool_cancelled'].includes(event.type)) {
        const finalOutput = event.content != null ? rawText(event.content) : rawText(event.result)
        if (terminalStatuses.has(step.status)) { if (!step.finalOutput) step.finalOutput = finalOutput; continue }
        step.finalOutput = finalOutput
        const cancelled = event.type === 'tool_cancelled' || event.status === 'cancelled' || event.result?.status === 'cancelled'
        finish(step, cancelled ? 'cancelled' : event.type === 'tool_failed' || event.is_error ? 'failed' : 'completed', event)
      }
    } else if (event.type === 'result') {
      ensureTurn(event, index).status = event.status
      if (terminalStatuses.has(event.status)) turn.endedAt = eventTime(event)
      else if (['waiting_confirmation', 'waiting_human', 'paused', 'runtime_config_boundary'].includes(event.status)) {
        for (const step of turn.steps) if (!terminalStatuses.has(step.status)) step.status = 'pending'
      }
      if (event.output) turn.result = event.output
    } else if (event.type === 'error' || event.type === 'run_cancelled') {
      ensureTurn(event, index).status = event.type === 'run_cancelled' ? 'cancelled' : 'failed'
      turn.endedAt = eventTime(event)
    } else if (event.type === 'task_progress' && event.content) {
      ensureTurn(event, index).steps.push({ key: `progress-${event.seq ?? index}`, kind: 'progress', name: 'progress', order: index, content: rawText(event.content), status: 'completed', startedAt: eventTime(event), endedAt: eventTime(event) })
    } else if (event.type === 'steering_queued') {
      if (event.result?.id) queued.push(event.result.id)
    } else if (event.type === 'steer' || event.type === 'follow_up') {
      const note = { kind: 'note', key: event.seq ?? index, type: event.type, content: event.content }
      ensureTurn(event, index).notes.push(note)
      const id = queued.shift()
      if (id) waiting.set(id, { note, turn })
    } else if (event.type === 'steering_applied') {
      const entry = waiting.get(event.result?.id)
      if (entry) waiting.delete(event.result.id)
      const owner = entry?.turn || ensureTurn(event, index)
      if (entry) owner.notes.splice(owner.notes.indexOf(entry.note), 1)
      model = null
      owner.steps.push({ key: `steer-${event.seq ?? index}`, kind: 'steer', name: entry?.note.type || 'steer', order: index,
        content: entry?.note.content ?? steeringText(event.result?.content ?? event.content), status: 'completed',
        startedAt: eventTime(event), endedAt: eventTime(event), durationMs: 0 })
    }
  }
  return turns.flatMap((item, index) => {
    const blocks = []; if (item.user) blocks.push(item.user)
    const last = index === turns.length - 1
    const currentStatus = last && terminalStatuses.has(sessionStatus) ? sessionStatus : item.status || (last ? sessionStatus : 'interrupted')
    const ended = terminalStatuses.has(currentStatus)
    const steps = item.steps.sort((a, b) => a.order - b.order).map(step => {
      const projected = { ...step }
      if (ended && !terminalStatuses.has(projected.status)) {
        projected.status = currentStatus === 'completed' ? 'interrupted' : currentStatus === 'cancelled' ? 'cancelled' : currentStatus
        projected.endedAt = item.endedAt ?? projected.endedAt
      }
      projected.durationMs ??= timeSpan(projected.startedAt, projected.endedAt)
      projected.resultSummary = shortResult(projected.finalOutput || projected.streamedOutput || projected.content)
      return projected
    })
    if (steps.length || liveStatuses.has(currentStatus)) blocks.push({
      kind: 'process', key: `process-${item.key}`, steps, status: currentStatus,
      active: liveStatuses.has(currentStatus), finished: ended && steps.every(step => terminalStatuses.has(step.status)),
      stepCount: steps.filter(step => step.kind !== 'steer').length, actionCount: steps.filter(step => step.kind === 'tool').length,
      startedAt: item.startedAt, endedAt: item.endedAt, durationMs: timeSpan(item.startedAt, item.endedAt),
    })
    const answers = item.models.filter(chunk => !chunk.hasTools && chunk.content)
    for (const chunk of answers) blocks.push({ kind: 'assistant', key: `answer-${chunk.key}`, content: chunk.content })
    if (item.result && !answers.some(chunk => chunk.content === item.result)) blocks.push({ kind: 'assistant', key: `result-${item.key}`, content: item.result })
    blocks.push(...item.notes)
    const lead = blocks.find(block => block.kind === 'process' || block.kind === 'assistant')
    if (lead) lead.lead = true
    return blocks
  })
}
