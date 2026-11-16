import assert from 'node:assert/strict'
import test from 'node:test'
import { dynamicLabel, isActive, eligible } from '../src/dynamicState.js'
import { toRow } from '../src/api.js'
import { createInboxDispatcher } from '../src/inboxDispatcher.js'

test('negative dynamic observation does not erase a dangerous static verdict', () => {
  const row = toRow({ id: 'mail', body_hash: 'hash', warnings: [], attachments: [],
    detection: { status: 'ok', verdict: 'danger', static_score: 91 },
    dynamic: { id: 'job', status: 'completed', observation: 'not_observed' } })
  assert.equal(row.verdict, 'danger')
  assert.equal(row.final_score, 91)
  assert.equal(dynamicLabel(row.dynamic), '이상 행동 미관찰')
})
test('incomplete execution is not described as an all-clear', () => {
  assert.equal(dynamicLabel({ status: 'incomplete', observation: 'unknown' }), '검사 미완료')
  assert.equal(isActive({ status: 'unreachable' }), true)
  assert.equal(isActive({ status: 'completed' }), false)
})
test('automatic handoff requires a successful suspicious or danger result', () => {
  assert.equal(eligible({ detection: { status: 'ok', verdict: 'suspicious' } }), true)
  assert.equal(eligible({ detection: { status: 'failed', verdict: 'danger' } }), false)
  assert.equal(eligible({ detection: { status: 'ok', verdict: 'safe' } }), false)
  assert.equal(eligible({ detection: null }), false)
})

test('inbox updates during submission do not drop later mails or duplicate the first', async () => {
  const calls = [], delivered = []
  let release
  const gate = new Promise((resolve) => { release = resolve })
  const queue = createInboxDispatcher(async (row) => {
    calls.push(row.id)
    if (row.id === 'a') await gate
    return { job: { id: row.id } }
  }, (_, result) => delivered.push(result.job.id), () => {})
  const a = { id: 'a', body_hash: '1' }, b = { id: 'b', body_hash: '2' }
  queue.add([a, b])
  queue.add([a, b, { id: 'c', body_hash: '3' }])
  release()
  await queue.finished()
  assert.deepEqual(calls, ['a', 'b', 'c'])
  assert.deepEqual(delivered, ['a', 'b', 'c'])
})

test('score reason explains the verdict with the model thresholds', async () => {
  const { scoreReason } = await import('../src/api.js')
  const base = { id: 'm', body_hash: 'h', warnings: [], attachments: [] }
  const row = (verdict, probability, dynamic) => toRow({ ...base, dynamic,
    detection: { status: 'ok', verdict, probability, static_score: Math.round(probability * 100), thresholds: { suspicious: 5, danger: 41.4 } } })
  assert.equal(scoreReason(row('suspicious', 0.08)), '정적 8% · 의심 기준 5% 이상, 위험 기준 41.4% 미만이라 의심이에요.')
  assert.equal(scoreReason(row('danger', 0.9)), '정적 90% · 위험 기준 41.4% 이상이라 위험이에요.')
  assert.equal(scoreReason(row('safe', 0.046)), '정적 4.6% · 의심 기준 5% 미만이라 정상이에요.')
  assert.match(scoreReason(row('suspicious', 0.08, { status: 'completed', observation: 'not_observed' })), /동적 검사 미관찰은 이 판정을 바꾸지 않아요/)
  assert.equal(scoreReason(toRow({ ...base, detection: null })), null)
})
