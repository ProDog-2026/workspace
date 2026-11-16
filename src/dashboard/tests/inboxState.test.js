import assert from 'node:assert/strict'
import test from 'node:test'
import { UNREACHABLE, gmail } from '../src/api.js'
import { inboxState } from '../src/inboxState.js'

const error = (code, message = '') => Object.assign(new Error(message), { code })

test('each reason the inbox cannot be shown gets its own guidance and next action', () => {
  const states = [
    inboxState(error(UNREACHABLE)),
    inboxState(error('authentication_required')),
    inboxState(error('reconnect_required')),
    inboxState(error('rate_limited')),
    inboxState(error('supabase_error', '저장소 오류')),
    inboxState(null, null),
    inboxState(null, []),
  ]
  assert.deepEqual(states.map((s) => s.kind),
    ['unreachable', 'disconnected', 'reconnect', 'rate_limited', 'error', 'loading', 'empty'])
  assert.equal(new Set(states.map((s) => s.title)).size, states.length)
  assert.equal(new Set(states.map((s) => s.body)).size, states.length)
})

test('a server that is down is not presented as a missing Gmail connection', () => {
  const down = inboxState(error(UNREACHABLE))
  assert.equal(down.action.href, undefined) // retry, not a login link
  assert.equal(inboxState(error('authentication_required')).action.href, '#/login')
  assert.equal(inboxState(error('reconnect_required')).action.href, '#/settings')
})

test('loading offers no action and an empty inbox is not called safe', () => {
  assert.equal(inboxState(null, null).action, null)
  assert.match(inboxState(null, []).body, /안전하다는 뜻은 아니에요/)
})

test('unknown errors keep the server message and loaded mail shows the list', () => {
  assert.equal(inboxState(error('mystery', '서버가 알려 준 이유')).body, '서버가 알려 준 이유')
  assert.equal(inboxState(null, [{ id: 'mail' }]), null)
})

test('gmail() marks an unanswered request as unreachable but keeps server error codes', async (t) => {
  const real = globalThis.fetch
  t.after(() => { globalThis.fetch = real })

  globalThis.fetch = () => Promise.reject(new TypeError('fetch failed'))
  await assert.rejects(gmail('/api/mails'), { code: UNREACHABLE })

  // dev 프록시의 502: 본문이 JSON 이 아님
  globalThis.fetch = async () => new Response('Bad Gateway', { status: 502 })
  await assert.rejects(gmail('/api/mails'), { code: UNREACHABLE })

  globalThis.fetch = async () => Response.json({ error: { code: 'authentication_required', message: '로그인 필요' } }, { status: 401 })
  await assert.rejects(gmail('/api/mails'), { code: 'authentication_required', message: '로그인 필요' })
})
