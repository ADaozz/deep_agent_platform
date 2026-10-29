// Versions are monotonic; a late fetch/event cannot replace a newer document.
export function mergeDocument(previous, update) {
  if (!update?.document_id || !Number.isInteger(update.revision)) return previous
  if (previous && update.revision < previous.revision) return previous
  const newer = !previous || update.revision > previous.revision
  return { ...(previous || {}), ...(newer ? { pages: [], finalized: false, render_error: null } : {}), ...update }
}
export function previewKey(sessionId, documentId) { return `docx-preview:${sessionId}:${documentId}` }
