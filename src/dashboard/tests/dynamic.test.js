import assert from 'node:assert/strict'
import test from 'node:test'
import { canStartDynamic, dynamicLabel, dynamicStatus, isActive, mergeDynamicJob } from '../src/dynamicState.js'
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

test('only a current job for the same message body updates the dynamic result', () => {
  const row = toRow({ id: 'mail', body_hash: 'hash', warnings: [], attachments: [],
    detection: { status: 'ok', verdict: 'danger', static_score: 91 } })
  const job = { id: 'job', mail_id: 'mail', body_hash: 'hash', status: 'failed', created_at: 2, updated_at: 3 }
  assert.equal(mergeDynamicJob(row, { ...job, mail_id: 'other' }), row)
  assert.equal(mergeDynamicJob(row, { ...job, body_hash: 'old' }), row)
  const merged = mergeDynamicJob(row, job)
  assert.equal(merged.verdict, 'danger')
  assert.equal(merged.static_score, 91)
  assert.equal(dynamicStatus(merged).phase, 'failed')
  assert.equal(mergeDynamicJob(merged, { ...job, id: 'old', created_at: 1 }), merged)
  assert.equal(mergeDynamicJob(merged, { ...job, status: 'running', updated_at: 2 }), merged)
})

test('a standalone experiment cannot replace a linked result for the same mail', () => {
  const row = { id: 'mail', body_hash: 'hash' }
  const linked = { id: 'linked', source: 'gmail', mail_id: 'mail', body_hash: 'hash', created_at: 1 }
  const manual = { ...linked, id: 'manual', source: 'gmail_manual', created_at: 2 }
  const both = mergeDynamicJob({ ...row, dynamic: manual }, linked)
  assert.equal(both.dynamic, linked)
  assert.equal(mergeDynamicJob(both, manual), both)
})

test('only excluded attachments make the static result incomplete', async () => {
  const { scoreReason } = await import('../src/api.js')
  const pdf = { filename: 'agenda.pdf', pdf_status: 'parsed' }
  const mail = { id: 'mail', body_hash: 'hash', warnings: ['attachment_content_not_analyzed'],
    attachments: [pdf], detection: { status: 'ok', verdict: 'suspicious', source: 'pdf', probability: 0.08,
      static_score: 8, thresholds: { suspicious: 5, danger: 41.4 } } }
  const parsed = toRow(mail)
  assert.equal(parsed.status, 'ok')
  assert.deepEqual(parsed.attachments, [pdf])
  assert.match(scoreReason(parsed), /^PDF 정적 8%/)
  const partial = toRow({ ...mail, attachments: [pdf, { filename: 'notes.pdf', pdf_status: 'encrypted' }, { filename: 'notes.zip' }] })
  assert.equal(partial.status, 'incomplete')
  assert.equal(partial.verdict, 'suspicious')
  assert.deepEqual(partial.missing_parts, ['첨부파일 2개 (내용 검사 안 함)'])
})
test('incomplete execution is not described as an all-clear', () => {
  assert.equal(dynamicLabel({ status: 'incomplete', observation: 'unknown' }), '검사 미완료')
  assert.equal(isActive({ status: 'unreachable' }), true)
  assert.equal(isActive({ status: 'completed' }), false)
})
test('linked dynamic checks require a successful suspicious or danger result', () => {
  const row = { verdict: 'suspicious', status: 'ok', pending: false }
  assert.equal(canStartDynamic(row), true)
  assert.equal(canStartDynamic({ ...row, verdict: 'safe' }), false)
  assert.equal(canStartDynamic({ ...row, verdict: 'danger' }), true)
  assert.equal(canStartDynamic({ ...row, status: 'failed' }), false)
  assert.equal(canStartDynamic({ ...row, pending: true }), false)
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
  assert.match(dynamicStatus(row('safe')).note, /정적 검사에서 판정이 끝났어요/)
  assert.match(dynamicStatus(row('danger')).note, /아직 실행하지 않았어요/)
  assert.match(dynamicStatus(row('suspicious')).note, /아직 실행하지 않았어요/)
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
