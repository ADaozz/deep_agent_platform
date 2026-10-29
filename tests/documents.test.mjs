import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeDocument } from '../frontend/src/features/docx/state.js'
test('late snapshots and events never replace a newer revision', () => {
  const newest = mergeDocument(null, { document_id: 'report', revision: 7, pages: [] })
  assert.equal(mergeDocument(newest, { document_id: 'report', revision: 6 }), newest)
})
test('new commits invalidate old page reviews; same-version reviews merge', () => {
  const reviewed = { document_id: 'report', revision: 7, finalized: true, pages: [{ page: 1, status: 'passed' }] }
  const update = mergeDocument(reviewed, { document_id: 'report', revision: 8 })
  assert.deepEqual(update.pages, [])
  assert.equal(update.finalized, false)
  const review = mergeDocument(update, { document_id: 'report', revision: 8, pages: [{ page: 1, status: 'pending' }] })
  assert.equal(review.pages[0].status, 'pending')
})
