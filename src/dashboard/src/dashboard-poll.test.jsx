import assert from 'node:assert/strict'
import { mock, test } from 'node:test'
import { hooks, find } from '../tests/hooks.js'

let sequence = 0
const mail = {
  id: 'cached-mail', body_hash: 'synthetic-hash', subject: 'Cached mail', sender: 'test@example.invalid',
  received_at: '2026-10-29T00:00:00Z', warnings: [], attachments: [],
  analysis: { status: 'completed', static: { status: 'completed', verdict: 'danger', score: 0.9 } },
}
const cloud = (pending, error = null) => ({ configured: true, pending, error })

async function harness(t, statuses, { next = null } = {}) {
  const h = hooks(t), calls = []
  mock.module('./supabase.js', { namedExports: { supabase: null } })
  const api = await import('./api.js')
  mock.module('./api.js', { namedExports: { ...api, gmail: async (path, csrf, _payload, options) => {
    calls.push({ path, csrf, signal: options?.signal })
    if (path === '/api/session') return { csrf_token: 'csrf', cloud: cloud(0) }
    if (path.startsWith('/api/mails?')) return { messages: [mail], next_page_token: next }
    if (path.startsWith('/api/analysis')) {
      const result = statuses.shift()
      if (result instanceof Error) throw result
      assert.ok(result, 'unexpected status request')
      return { analyses: [], cloud: result }
    }
    assert.fail(`unexpected API request: ${path}`)
  } } })
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { default: Dashboard } = await import(`./Dashboard.jsx?poll=${sequence++}`)
  const render = () => h.render(Dashboard, { local: true })
  render()
  await h.flush()
  render()
  return { ...h, calls, render, async tick() {
    t.mock.timers.tick(1000)
    await h.flush()
    render()
  } }
}

test('cached terminal recollection refreshes cloud after POST and polls backlog until drained', async (t) => {
  const h = await harness(t, [cloud(2, 'retry later'), new Error('offline'), cloud(0)])
  assert.deepEqual(h.calls.map(({ path }) => path), ['/api/session', '/api/mails?limit=20', '/api/analysis'])
  assert.equal(h.calls[1].csrf, 'csrf')
  assert.equal(h.calls[2].csrf, undefined)
  assert.ok(h.calls[2].signal instanceof AbortSignal)
  assert.match(h.markup(), /저장 대기 2건.*retry later/)
  await h.tick()
  assert.match(h.markup(), /offline/)
  assert.match(h.markup(), /Cached mail/)
  await h.tick()
  assert.doesNotMatch(h.markup(), /저장 대기/)
  assert.doesNotMatch(h.markup(), /offline/)
  const count = h.calls.length
  await h.tick()
  assert.equal(h.calls.length, count, 'stop polling after the backlog drains')
})

test('failed post-collection status read keeps mail visible and retries without active analyses', async (t) => {
  const h = await harness(t, [new Error('status unavailable'), cloud(1), cloud(0)])
  assert.match(h.markup(), /status unavailable/)
  assert.match(h.markup(), /Cached mail/)
  await h.tick()
  assert.match(h.markup(), /저장 대기 1건/)
  assert.doesNotMatch(h.markup(), /status unavailable/)
  await h.tick()
  assert.doesNotMatch(h.markup(), /저장 대기/)
})

test('unknown backlog stays visible and polling resumes after worker recovery', async (t) => {
  const h = await harness(t, [cloud(null, '자동 재시도 중'), cloud(0)])
  assert.match(h.markup(), /저장 상태 확인 중.*자동 재시도 중/)
  assert.doesNotMatch(h.markup(), /null건|저장 대기 0건/)
  await h.tick()
  assert.doesNotMatch(h.markup(), /저장 상태 확인 중|자동 재시도 중/)
  const count = h.calls.length
  await h.tick()
  assert.equal(h.calls.length, count)
})

test('load-more recollection also refreshes cloud and restarts backlog polling', async (t) => {
  const h = await harness(t, [cloud(0), cloud(1), cloud(0)], { next: 'next-page' })
  await find(h.render(), (node) => node.type === 'button' && String(node.props.children).includes('더 불러오기')).props.onClick()
  h.render()
  assert.match(h.markup(), /저장 대기 1건/)
  assert.deepEqual(h.calls.slice(-2).map(({ path }) => path), ['/api/mails?limit=20&page_token=next-page', '/api/analysis'])
  await h.tick()
  assert.doesNotMatch(h.markup(), /저장 대기/)
})
