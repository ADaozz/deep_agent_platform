export function previewKind(path = '') {
  const ext = path.split('.').pop().toLowerCase()
  if (['docx', 'pptx'].includes(ext)) return ext
  if (['md', 'markdown'].includes(ext)) return 'markdown'
  if (['txt', 'yaml', 'yml', 'json', 'csv', 'log', 'toml', 'ini', 'xml'].includes(ext)) return 'text'
  if (ext === 'pdf') return 'pdf'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'bmp', 'svg'].includes(ext)) return 'image'
  return null
}
export function previewFeature(path) {
  const kind = previewKind(path)
  return ['docx', 'pptx'].includes(kind) ? kind : kind ? 'file' : null
}
export function fileUrl(sessionId, path, preview = false) {
  return `/api/agent/sessions/${encodeURIComponent(sessionId)}/${preview ? 'file-preview' : 'files'}/${path.split('/').map(encodeURIComponent).join('/')}`
}
