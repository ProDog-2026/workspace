import assert from 'node:assert/strict'
import { mock, test } from 'node:test'
import { hooks } from '../tests/hooks.js'
import * as scans from './scans.js'

test('dashboard refreshes automatic mail results without collecting or starting an experiment', async (t) => {
  const h = hooks(t), reads = []
  let status = 'running', delayNext = false, release
  mock.module('./supabase.js', { namedExports: { supabase: null } })
  const api = await import('./api.js')
  mock.module('./api.js', { namedExports: { ...api, gmail: () => assert.fail('read-only dashboard must not collect or execute') } })
  mock.module('./scans.js', { namedExports: { ...scans, readScans: async (_client, userId, offset, signal) => {
    reads.push({ userId, offset, signal })
    const rows = [scans.scanRow({ id: 'mail', user_id: userId, subject: `자동 수집 메일 ${userId}`, verdict: 'danger', status: 'ok',
      analysis: { status: status === 'running' ? 'dynamic_running' : 'completed',
        static: { status: 'completed', verdict: 'danger', score: 0.9 },
        dynamic: { id: 'auto-run', status, model: 'local-ai', provider: 'ollama',
          observations: { canary_leaked: status === 'completed' ? true : null } } } })]
    if (delayNext) {
      delayNext = false
      return new Promise((resolve) => { release = () => resolve(rows) })
    }
    return rows
  } } })
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { default: Dashboard } = await import('./Dashboard.jsx?automatic-poll')
  const render = () => h.render(Dashboard, { user: { id: 'owner-a' } })
  render(); await h.flush(); render()
  assert.match(h.markup(), /수집 메일 자동 동적 분석/)
  assert.match(h.markup(), /local-ai/)
  assert.match(h.markup(), /실행 중/)
  status = 'completed'
  t.mock.timers.tick(5000); await h.flush(); render()
  assert.equal(reads.length, 2)
  assert.ok(reads.every((read) => read.userId === 'owner-a' && read.offset === 0))
  assert.match(h.markup(), /노출 관측/)
  assert.match(h.markup(), /#\/experiment\?run=auto-run/)

  delayNext = true
  t.mock.timers.tick(5000); await h.flush()
  const previousRead = reads.at(-1)
  h.render(Dashboard, { user: { id: 'owner-b' } }); await h.flush()
  h.render(Dashboard, { user: { id: 'owner-b' } })
  assert.equal(previousRead.signal.aborted, true)
  release(); await h.flush()
  h.render(Dashboard, { user: { id: 'owner-b' } })
  assert.match(h.markup(), /자동 수집 메일 owner-b/)
  assert.doesNotMatch(h.markup(), /자동 수집 메일 owner-a/)
})
