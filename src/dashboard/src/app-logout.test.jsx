import assert from 'node:assert/strict'
import { mock, test } from 'node:test'
import { hooks, find } from '../tests/hooks.js'

let sequence = 0
async function harness(t, { failure, signOutError, throws = false } = {}) {
  const h = hooks(t), calls = [], listeners = new Map()
  const auth = { loading: false, session: { user: { id: 'synthetic-user', email: 'test@example.invalid' } } }
  const globals = { location: { hash: '#/mailbox', search: '' },
    addEventListener: (name, listener) => listeners.set(name, listener),
    removeEventListener: (name) => listeners.delete(name) }
  for (const [name, value] of Object.entries(globals)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name)
    Object.defineProperty(globalThis, name, { value, writable: true, configurable: true })
    t.after(() => previous ? Object.defineProperty(globalThis, name, previous) : delete globalThis[name])
  }
  mock.module('./auth.js', { namedExports: { useAuth: () => auth } })
  mock.module('./api.js', { namedExports: { gmail: async (path, csrf) => {
    calls.push([path, csrf])
    if (path === failure) throw new Error('connector offline')
    return { csrf_token: 'csrf', email: 'test@example.invalid' }
  } } })
  mock.module('./supabase.js', { namedExports: { supabase: { auth: { signOut: async (options) => {
    calls.push(['signOut', options])
    if (throws) throw new Error('auth unavailable')
    if (!signOutError) auth.session = null
    return { error: signOutError }
  } } } } })
  for (const name of ['Admin', 'Dashboard', 'Experiment', 'Landing', 'Scan', 'Settings', 'States']) {
    mock.module(`./${name}.${name === 'Landing' ? 'tsx' : 'jsx'}`, { defaultExport: () => null })
  }
  mock.module('./Login.jsx', { defaultExport: () => <main>로그인 화면</main> })
  const { default: App } = await import(`./App.jsx?logout=${sequence++}`)
  const render = () => h.render(App)
  render()
  await h.flush()
  const button = find(render(), (node) => node.type === 'button' && node.props.children === '앱 로그아웃')
  calls.length = 0
  return { ...h, calls, auth, render, logout: () => button.props.onClick(), navigate() { listeners.get('hashchange')() } }
}

for (const failure of ['/api/session', '/api/logout']) {
  test(`connector cleanup failure at ${failure} still signs out locally and warns on login`, async (t) => {
    const h = await harness(t, { failure })
    await h.logout()
    assert.deepEqual(h.calls.at(-1), ['signOut', { scope: 'local' }])
    assert.equal(h.auth.session, null)
    assert.equal(location.hash, '#/login')
    // Auth can rerender before the hashchange event arrives.
    h.render()
    assert.match(h.markup(), /role="alert"/)
    assert.match(h.markup(), /Gmail.*백그라운드.*계속될 수/)
    assert.match(h.markup(), /connector offline/)
    h.navigate()
    await h.flush()
    h.render()
    assert.match(h.markup(), /로그인 화면/)
    assert.match(h.markup(), /Gmail.*백그라운드.*계속될 수/)
  })
}

test('successful connector cleanup uses CSRF and signs out without a warning', async (t) => {
  const h = await harness(t)
  await h.logout()
  assert.deepEqual(h.calls, [['/api/session', undefined], ['/api/logout', 'csrf'], ['signOut', { scope: 'local' }]])
  h.navigate()
  await h.flush()
  h.render()
  assert.match(h.markup(), /로그인 화면/)
  assert.doesNotMatch(h.markup(), /role="alert"/)
})

for (const throws of [false, true]) {
  test(`signout ${throws ? 'exception' : 'error'} retains both failures and permits retry`, async (t) => {
    const h = await harness(t, { failure: '/api/logout', signOutError: new Error('auth unavailable'), throws })
    await h.logout()
    assert.equal(location.hash, '#/mailbox')
    h.render()
    assert.match(h.markup(), /connector offline/)
    assert.match(h.markup(), /로그아웃하지 못/)
    assert.ok(find(h.render(), (node) => node.type === 'button' && node.props.children === '앱 로그아웃'))
  })
}
