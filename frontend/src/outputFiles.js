// One current file list per session. An explicit empty snapshot also removes
// deleted files instead of resurrecting them from earlier execution events.
export function sessionOutputFiles(session) {
  if (!session) return []
  const files = new Map()
  const root = `/api/agent/sessions/${encodeURIComponent(session.id)}`
  const current = session.output_files || []
  for (const file of current) {
    if (!file.path) continue
    const url = `${root}/files/${file.path.split('/').map(encodeURIComponent).join('/')}`
    files.set(url, { ...file, url })
  }
  for (const path of session.artifacts || []) {
    const url = `${root}/artifacts/${encodeURIComponent(path)}`
    files.set(url, { path, url })
  }
  return [...files.values()].sort((a, b) => a.path.localeCompare(b.path))
}
