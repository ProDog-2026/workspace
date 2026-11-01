import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readScans, scanRow, browserKey, dailyCounts, stateOf } from './scans.js'

const own = { id: 'scan-1', user_id: 'user-a', gmail_message_id: 'mail-1', status: 'ok', verdict: 'safe', final_score: 12, received_at: '2026-10-29T03:00:00Z' }
test('pipeline scores retain DB JSON precision across a rounded integer danger boundary', () => {
  const result = { status: 'completed', verdict: 'danger', score: 0.943566,
    suspicious_threshold: 0.939322, danger_threshold: 0.942692 }
  const row = scanRow({ ...own, verdict: 'danger', static_score: 94, final_score: 94,
    analysis: { static: result } })
  assert.equal(row.static_score, result.score * 100)
  assert.equal(row.final_score, result.score * 100)
  assert.ok(row.final_score >= row.static_thresholds.danger)
  for (const value of [null, '0.943566', true, 1.5]) {
    assert.equal(scanRow({ ...own, verdict: 'danger', final_score: 94,
      analysis: { static: { ...result, score: value } } }).final_score, 94)
  }
  assert.equal(scanRow({ ...own, analysis: { static: {
    ...result, verdict: 'safe', score: 0,
  } } }).final_score, 0)
})
test('stored pipeline model details and dynamic observations remain available', () => {
  const row = scanRow({ ...own, verdict: 'danger', analysis: { status: 'completed', static: {
    mode: 'ensemble', suspicious_threshold: 0.5, danger_threshold: 0.8,
    detectors: { transformer: { verdict: 'danger', score: 0.9, suspicious_threshold: 0.5, danger_threshold: 0.8 } },
  }, dynamic: { observations: { canary_leaked: false } } } })
  assert.equal(row.static_detectors[0].label, 'Transformer')
  assert.equal(row.static_detectors[0].score, 90)
  assert.deepEqual(row.static_thresholds, { suspicious: 50, danger: 80 })
  assert.equal(row.analysis.dynamic.observations.canary_leaked, false)
})
function client(data = [own], error = null) {
  const calls = []
  const query = Object.fromEntries(['select', 'eq', 'order', 'range', 'abortSignal'].map((name) => [name, (...args) => { calls.push([name, ...args]); return query }]))
  query.then = (resolve, reject) => Promise.resolve({ data, error }).then(resolve, reject)
  return { calls, from: (table) => { calls.push(['from', table]); return query } }
}
test('scans uses SELECT, own UUID filter, stable paging and abort signal', async () => {
  const c = client(), signal = new AbortController().signal
  const rows = await readScans(c, 'user-a', 100, signal)
  assert.equal(rows[0].final_score, 12)
  assert.deepEqual(c.calls.filter(([name]) => ['from', 'eq', 'range', 'order'].includes(name)), [
    ['from', 'scans'], ['eq', 'user_id', 'user-a'], ['order', 'received_at', { ascending: false }], ['order', 'id', { ascending: false }], ['range', 100, 199],
  ])
  assert.equal(c.calls.at(-1)[1], signal)
})
test('no anonymous query, no other user rows, and database errors are not empty success', async () => {
  const c = client()
  await assert.rejects(readScans(c, null, 0), /로그인/)
  assert.equal(c.calls.length, 0)
  await assert.rejects(readScans(client([{ ...own, user_id: 'user-b' }]), 'user-a', 0), /사용자/)
  await assert.rejects(readScans(client(null, { code: '42501', message: 'private database details' }), 'user-a', 0), /권한/)
})
test('null verdict is pending, failed stays failed, scores remain 0–100 and HTML stays text', () => {
  assert.equal(scanRow({ ...own, verdict: null }).pending, true)
  assert.equal(scanRow({ ...own, verdict: null, status: 'failed' }).pending, false)
  assert.equal(scanRow({ ...own, final_score: 0 }).final_score, 0)
  assert.equal(scanRow({ ...own, final_score: 101 }).final_score, null)
  assert.deepEqual(scanRow({ ...own, evidence: [{ rule: 'R01', type: 'html_comment', snippet: '<script>attack()</script>' }] }).evidence[0].snippet, '<script>attack()</script>')
  assert.equal(scanRow({ ...own, status: 'incomplete', missing_parts: [] }).missing_parts.length, 1)
})
test('verdict has priority, incomplete never appears safe, unscanned stays pending', () => {
  for (const verdict of ['danger', 'suspicious']) {
    for (const status of ['ok', 'incomplete', 'failed']) assert.equal(stateOf(scanRow({ ...own, verdict, status })), verdict)
  }
  assert.equal(stateOf(scanRow({ ...own, status: 'incomplete' })), 'incomplete')
  assert.equal(stateOf(scanRow({ ...own, verdict: null })), 'pending')
  assert.equal(stateOf(scanRow({ ...own, verdict: null, status: 'failed' })), 'failed')
})
test('browser accepts only anon JWTs and rejects privileged, malformed or secret keys', () => {
  const key = (role) => `e30.${Buffer.from(JSON.stringify({ role })).toString('base64url')}.sig`
  assert.equal(browserKey(key('anon')), true)
  for (const value of [key('service_role'), key('authenticated'), 'sb_secret_test', 'garbage', '']) assert.equal(browserKey(value), false)
})
test('daily graph uses the same rows and preserves dangerous incomplete verdicts', () => {
  const date = new Date(2026, 9, 29, 12)
  const at = new Date(2026, 9, 29, 9).toISOString()
  const points = dailyCounts([
    scanRow({ ...own, received_at: at, verdict: 'danger', status: 'incomplete' }),
    scanRow({ ...own, received_at: at, verdict: 'suspicious' }),
    scanRow({ ...own, received_at: at, verdict: 'safe' }),
    scanRow({ ...own, received_at: 'invalid', verdict: 'danger' }),
  ], 7, date)
  assert.equal(points.length, 7)
  assert.equal(points.at(-1).danger, 1)
  assert.equal(points.at(-1).suspicious, 1)
})
