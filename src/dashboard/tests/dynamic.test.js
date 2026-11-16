import assert from 'node:assert/strict'
import test from 'node:test'
import { dynamicLabel, dynamicStatus, isActive, eligible } from '../src/dynamicState.js'
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

test('mail detail separates not run, running, failed and never reads as safe', () => {
  const row = (verdict, dynamic = null) => ({ verdict, pending: false, status: 'ok', dynamic })
  assert.equal(dynamicStatus(row('safe')).phase, 'none')
  assert.match(dynamicStatus(row('safe')).note, /대상이 아니에요/)
  assert.match(dynamicStatus(row('danger')).note, /아직 실행하지 않았어요/)
  assert.match(dynamicStatus({ ...row('danger'), status: 'failed' }).note, /1차 검사 결과가 없어/)
  assert.equal(dynamicStatus(row('danger', { status: 'running' })).phase, 'active')
  assert.equal(dynamicStatus(row('danger', { status: 'unreachable' })).phase, 'active')
  for (const status of ['failed', 'stopped', 'unavailable', 'not_configured', 'mystery']) {
    const s = dynamicStatus(row('danger', { status }))
    assert.equal(s.phase, 'failed')
    assert.match(s.note, /안전하다는 뜻은 아니에요/)
  }
  assert.equal(dynamicStatus(row('danger', { status: 'incomplete', observation: 'unknown' })).phase, 'incomplete')
  const clean = dynamicStatus(row('suspicious', { status: 'completed', observation: 'not_observed' }))
  assert.equal(clean.phase, 'done')
  assert.match(clean.note, /보장은 아니에요/)
  const seen = dynamicStatus(row('danger', { status: 'completed', observation: 'observed' }))
  assert.equal(seen.phase, 'done')
  assert.match(seen.note, /요약 외 도구 요청 또는 테스트 환경의 효과/)
  assert.doesNotMatch(seen.note, /지시를 따라/)
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
