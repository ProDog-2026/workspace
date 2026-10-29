import assert from 'node:assert/strict'
import { test } from 'node:test'
import { toRow } from './api.js'

const mail = { id: 'synthetic-mail', body_hash: 'hash', warnings: [], attachments: [] }
const tfidf = { score: 0.9, verdict: 'suspicious', suspicious_threshold: 0.8, danger_threshold: 0.95 }
const transformer = { score: 0.6, verdict: 'danger', suspicious_threshold: 0.3, danger_threshold: 0.5 }
const result = {
  status: 'completed', mode: 'ensemble', ...transformer,
  detectors: { tfidf, transformer }, decided_by: ['transformer'],
}
const rowOf = (value = result, status = 'completed') => toRow({ ...mail, analysis: { status, static: value } })

test('develop detection results remain visible without replacing pipeline results', () => {
  const detection = { status: 'ok', verdict: 'danger', static_score: 90 }
  const legacy = toRow({ ...mail, detection })
  assert.equal(legacy.pending, false)
  assert.equal(legacy.verdict, 'danger')
  assert.equal(legacy.final_score, 90)
  assert.equal(legacy.static_thresholds, null)
  assert.equal(toRow({ ...mail, detection: { status: 'failed' } }).status, 'failed')
  const queued = toRow({ ...mail, detection, analysis: { status: 'static_queued' } })
  assert.equal(queued.pending, true)
  assert.equal(queued.verdict, null)
  const current = toRow({ ...mail, detection, analysis: { status: 'completed', static: { status: 'completed', ...tfidf } } })
  assert.equal(current.verdict, 'suspicious')
  assert.equal(current.final_score, 90)
})

test('ensemble preserves the deciding detector score, even when another score is higher', () => {
  const row = rowOf()
  assert.equal(row.verdict, 'danger')
  assert.equal(row.static_score, 60)
  assert.equal(row.final_score, 60)
  assert.deepEqual(row.static_thresholds, { suspicious: 30, danger: 50 })
  assert.deepEqual(row.static_detectors, [
    { id: 'tfidf', label: '기존 ML(해시 특징)', score: 90, verdict: 'suspicious', thresholds: { suspicious: 80, danger: 95 } },
    { id: 'transformer', label: 'Transformer', score: 60, verdict: 'danger', thresholds: { suspicious: 30, danger: 50 } },
  ])
  assert.deepEqual(row.analysis.static.decided_by, ['transformer'])
})

test('equal risk stages retain the server representative and both deciding models', () => {
  const row = rowOf({ ...result, ...tfidf, detectors: { tfidf, transformer: { ...transformer, verdict: 'suspicious', danger_threshold: 0.7 } }, decided_by: ['tfidf', 'transformer'] })
  assert.equal(row.verdict, 'suspicious')
  assert.equal(row.final_score, 90)
  assert.deepEqual(row.analysis.static.decided_by, ['tfidf', 'transformer'])
})

test('legacy results and lexical-only results still map without ensemble fields', () => {
  const legacy = rowOf({ status: 'completed', ...tfidf })
  assert.equal(legacy.verdict, 'suspicious')
  assert.equal(legacy.final_score, 90)
  assert.deepEqual(legacy.static_detectors, [])
  const lexical = rowOf({ status: 'completed', mode: 'lexical', ...tfidf, detectors: { tfidf }, decided_by: ['tfidf'] })
  assert.equal(lexical.static_detectors.length, 1)
  assert.equal(lexical.static_detectors[0].label, '기존 ML(해시 특징)')
})

test('dynamic errors preserve static results; static failures do not expose detector scores', () => {
  assert.equal(rowOf(result, 'error').verdict, 'danger')
  const failed = rowOf({ ...result, status: 'error', error: { message: 'synthetic failure' } }, 'static_error')
  assert.equal(failed.status, 'failed')
  assert.equal(failed.verdict, null)
  assert.deepEqual(failed.static_detectors, [])
  assert.deepEqual(toRow(mail).static_detectors, [])
})

test('metadata results are supported and missing component numbers stay unknown', () => {
  const row = toRow({ ...mail, metadata: { analysis: { static: {
    ...result, detectors: { tfidf: { verdict: 'safe' }, transformer: null },
  } } } })
  assert.deepEqual(row.static_detectors, [
    { id: 'tfidf', label: '기존 ML(해시 특징)', score: null, verdict: 'safe', thresholds: null },
  ])
})
