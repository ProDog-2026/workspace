import assert from 'node:assert/strict'
import { mock, test } from 'node:test'

let sequence = 0
async function harness({ code, denied, exchangeError, session = null } = {}) {
  const states = [], calls = []
  let listener, cleanup
  const client = { auth: {
    onAuthStateChange(fn) { listener = fn; return { data: { subscription: { unsubscribe() { calls.push('unsubscribe') } } } } },
    async getSession() { return { data: { session }, error: null } },
    async exchangeCodeForSession(value) { calls.push(['exchange', value]); return { error: exchangeError } },
    async signInWithOAuth(value) { calls.push(['signIn', value]); return { error: null } },
  } }
  globalThis.location = { href: `http://127.0.0.1:5173/${denied ? '?auth_callback=1&error=access_denied' : code !== undefined ? '?auth_callback=1&code=' + code : '#/'}`, origin: 'http://127.0.0.1:5173', pathname: '/' }
  globalThis.history = { replaceState(_a, _b, path) { calls.push(['clean-url', path]) } }
  mock.module('react', { namedExports: {
    useState(initial) { states.push(initial); return [initial, (value) => states.push(value)] },
    useEffect(fn) { cleanup = fn() },
  } })
  mock.module('./supabase.js', { namedExports: { supabase: client, supabaseError: null, checkGoogleLogin: async () => calls.push('check-provider') } })
  const auth = await import(`./auth.js?test=${sequence++}`)
  auth.useAuth()
  return { states, calls, auth, event: (s, name = 'SIGNED_IN') => listener(name, s), cleanup: () => cleanup(), flush: () => new Promise((resolve) => setImmediate(resolve)) }
}
test('PKCE callback exchanges code once and removes it from the URL first', async () => {
  const h = await harness({ code: 'synthetic-code', session: { user: { id: 'a' } } })
  await h.flush()
  assert.deepEqual(h.calls.slice(0, 2), [['clean-url', '/#/login'], ['exchange', 'synthetic-code']])
  assert.equal(h.states.at(-1).session.user.id, 'a')
  h.cleanup()
  assert.equal(h.calls.at(-1), 'unsubscribe')
  mock.restoreAll()
})
test('auth event wins over an older session read and signout clears the user', async () => {
  const h = await harness({ session: { user: { id: 'old' } } })
  h.event({ user: { id: 'new' } })
  await h.flush()
  assert.equal(h.states.at(-1).session.user.id, 'new')
  h.event(null)
  assert.equal(h.states.at(-1).session, null)
  h.cleanup(); mock.restoreAll()
})
test('failed callback remains signed out and Google sign in returns to connection settings flow', async () => {
  const h = await harness({ code: 'invalid-code', exchangeError: new Error('not logged') })
  await h.flush()
  assert.equal(h.states.at(-1).session, null)
  assert.match(h.states.at(-1).error, /다시 로그인/)
  await h.auth.signIn()
  assert.deepEqual(h.calls.at(-1), ['signIn', { provider: 'google', options: { redirectTo: 'http://127.0.0.1:5173/?auth_callback=1' } }])
  h.cleanup(); mock.restoreAll()
})
test('late INITIAL_SESSION does not erase a denied callback error', async () => {
  const h = await harness({ denied: true })
  await h.flush()
  h.event(null, 'INITIAL_SESSION')
  assert.match(h.states.at(-1).error, /취소/)
  assert.equal(h.states.at(-1).loading, false)
  h.cleanup(); mock.restoreAll()
})
