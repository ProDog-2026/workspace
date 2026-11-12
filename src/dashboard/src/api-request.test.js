import assert from 'node:assert/strict'
import { mock, test } from 'node:test'

test('local API sends the current access token and shares concurrent session initialization', async () => {
  let token = 'user-a', count = 0
  const calls = []
  mock.module('./supabase.js', { namedExports: {
    supabase: { auth: { getSession: async () => ({ data: { session: token ? { access_token: token } : null } }) } },
  } })
  const original = globalThis.fetch
  globalThis.fetch = async (path, options) => {
    count++
    calls.push([path, options])
    await new Promise((resolve) => setImmediate(resolve))
    return { ok: true, json: async () => ({ csrf_token: 'synthetic-csrf' }) }
  }
  try {
    const { gmail } = await import('./api.js?request-test')
    await Promise.all([gmail('/api/session'), gmail('/api/session'), gmail('/api/session')])
    assert.equal(count, 1)
    assert.equal(new Headers(calls[0][1].headers).get('Authorization'), 'Bearer user-a')
    token = 'user-b'
    await gmail('/api/mails', 'synthetic-csrf')
    const headers = new Headers(calls.at(-1)[1].headers)
    assert.equal(headers.get('Authorization'), 'Bearer user-b')
    assert.equal(headers.get('X-CSRF-Token'), 'synthetic-csrf')
    token = null
    await gmail('/api/session')
    assert.equal(new Headers(calls.at(-1)[1].headers).get('Authorization'), null)
  } finally {
    globalThis.fetch = original
    mock.restoreAll()
  }
})
