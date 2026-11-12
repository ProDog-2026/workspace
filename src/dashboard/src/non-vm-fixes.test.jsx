import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { mock, test } from 'node:test'
import { hooks, find } from '../tests/hooks.js'
import { canaryExposureObserved } from './api.js'

registerHooks({ load(url, context, next) {
  return url.endsWith('.css') ? { format: 'module', shortCircuit: true, source: '' } : next(url, context)
} })

let sequence = 0
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const button = (tree, name) => find(tree, (node) => node.type === 'button' && node.props.children === name)

function browser(t, hash) {
  const listeners = new Map()
  for (const [key, value] of Object.entries({ window: globalThis, location: { hash, search: '' },
    confirm() { assert.fail('unexpected confirmation') },
    addEventListener(event, handler) { listeners.set(event, handler) },
    removeEventListener(event, handler) { if (listeners.get(event) === handler) listeners.delete(event) },
  })) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value })
    t.after(() => { if (previous) Object.defineProperty(globalThis, key, previous); else delete globalThis[key] })
  }
  return listeners
}

async function settingsHarness(t, sessionOverrides = {}) {
  browser(t, '#/settings')
  const h = hooks(t), calls = []
  let connected = true
  mock.module('./api.js', { namedExports: { DETECTOR_NAMES: {}, gmail: async (path, csrf, payload, options) => {
    calls.push({ path, csrf, payload, signal: options?.signal })
    if (path === '/api/session') return { authenticated: connected, csrf_token: 'synthetic-csrf', email: 'synthetic@example.invalid', mode: 'demo', configured: true, ...(connected ? sessionOverrides : {}) }
    if (path === '/api/analysis/settings') return { enabled: false, model: '', ollama_models: [] }
    if (path === '/api/disconnect') { connected = false; return { disconnected: true } }
    assert.fail(`unexpected API request: ${path}`)
  } } })
  const { default: Settings } = await import(`./Settings.jsx?non-vm=${sequence++}`)
  const render = () => h.render(Settings, {})
  render(); await h.flush(); render(); await h.flush(); render()
  return { ...h, calls, render }
}

test('pending unlink remains retryable when Gmail needs reconnection', async (t) => {
  const h = await settingsHarness(t, { disconnect_pending: true, needs_reconnect: true })
  t.mock.method(globalThis, 'confirm', () => true)
  const retry = find(h.render(), (node) => node.type === 'button' && node.props.children === '해제 다시 시도')
  assert.ok(retry)
  assert.doesNotMatch(h.markup(), /다시 연결하기/)
  await retry.props.onClick()
  await h.flush(); h.render()
  assert.ok(h.calls.some(({ path }) => path === '/api/disconnect'))
  assert.doesNotMatch(h.markup(), /해제 다시 시도/)
})

test('unlink discloses stored inspection deletion and cancellation makes no request', async (t) => {
  const h = await settingsHarness(t)
  let warning
  t.mock.method(globalThis, 'confirm', (message) => { warning = message; return false })
  const before = h.calls.length
  await button(h.render(), '연동 해제').props.onClick()
  assert.match(warning ?? '', /저장.*검사.*삭제/)
  assert.equal(h.calls.length, before, 'cancel must not even refresh the session')
  assert.match(h.markup(), /synthetic@example.invalid/)
})

test('explicit unlink confirmation refreshes CSRF before deletion and shows disconnected state', async (t) => {
  const h = await settingsHarness(t)
  let confirmations = 0
  t.mock.method(globalThis, 'confirm', () => { confirmations++; return true })
  const before = h.calls.length
  await button(h.render(), '연동 해제').props.onClick()
  h.render()
  assert.equal(confirmations, 1)
  assert.deepEqual(h.calls.slice(before).map(({ path }) => path), ['/api/session', '/api/disconnect', '/api/session'])
  assert.equal(h.calls[before + 1].csrf, 'synthetic-csrf')
  assert.ok(h.calls[before + 1].signal instanceof AbortSignal)
  assert.ok(button(h.render(), 'Gmail 연결하기'))
  assert.doesNotMatch(h.markup(), /synthetic@example.invalid/)
})

async function experimentHarness(t, session) {
  let cleanup, disposed = false, sessionValid = false
  const h = hooks({ after(fn) { cleanup = fn } }), calls = [], timers = new Map()
  const unmount = () => { if (!disposed) { disposed = true; cleanup() } }
  t.after(unmount)
  const listeners = browser(t, '#/experiment?run=synthetic-run')
  let timerId = 0
  t.mock.method(globalThis, 'setTimeout', (callback) => { const id = ++timerId; timers.set(id, callback); return id })
  t.mock.method(globalThis, 'clearTimeout', (id) => timers.delete(id))
  mock.module('./Evaluation.jsx', { defaultExport: () => null })
  mock.module('./api.js', { namedExports: { canaryExposureObserved, gmail: async (path, _csrf, _payload, options) => {
    calls.push({ path, signal: options?.signal })
    if (path === '/api/session') {
      const initialized = await session()
      sessionValid = true
      return initialized
    }
    if (!sessionValid) throw new Error('synthetic session expired')
    if (path.startsWith('/api/experiments/readiness?')) return { ready: true, sandbox: { ready: true }, model: { ready: true }, cleanup: { ready: true } }
    if (path === '/api/experiments/config') return { enabled: true, ollama_models: ['synthetic'], providers: [{ id: 'ollama', configured: true, model: 'synthetic' }] }
    if (path === '/api/experiments') return { runs: [{ id: 'synthetic-run', status: 'completed', provider: 'ollama', model: 'synthetic' }] }
    if (path.startsWith('/api/experiments/')) return { id: path.split('/').at(-1), status: 'completed', provider: 'ollama', model: 'synthetic',
      events: [{ type: 'model', data: { message: { content: path.endsWith('/new-run') ? '새로 선택한 실행의 요약입니다.' : '처음 선택한 실행의 요약입니다.' } } }] }
    assert.fail(`unexpected API request: ${path}`)
  } } })
  const { default: Experiment } = await import(`./Experiment.jsx?non-vm=${sequence++}`)
  const render = () => h.render(Experiment)
  const settle = async () => { await h.flush(); render() }
  render(); await settle()
  return { ...h, calls, timers, render, settle, unmount, expireSession() { sessionValid = false }, select(id) {
    location.hash = `#/experiment?run=${id}`; listeners.get('hashchange')(); render()
  }, async tick() {
    const pending = [...timers.values()]; timers.clear(); pending.forEach((fn) => fn()); await settle()
  } }
}

test('failed initial session retries on polling and shares the retry across history, detail and configuration', async (t) => {
  let offline = true
  const retry = deferred()
  const h = await experimentHarness(t, () => {
    if (offline) throw new Error('initial session offline')
    return retry.promise
  })
  assert.deepEqual(h.calls.map(({ path }) => path), ['/api/session'])
  assert.match(h.markup(), /initial session offline/)
  offline = false
  await h.tick()
  assert.equal(h.calls.filter(({ path }) => path === '/api/session').length, 2)
  assert.equal(h.calls.length, 2, 'history, detail and configuration must wait for the same initialization')
  retry.resolve({ csrf_token: 'synthetic' }); await h.settle()
  assert.deepEqual(h.calls.slice(2).map(({ path }) => path).filter((path) => !path.startsWith('/api/experiments/readiness?')).sort(), ['/api/experiments', '/api/experiments/config', '/api/experiments/synthetic-run'])
  assert.doesNotMatch(h.markup(), /initial session offline/)
  assert.match(h.markup(), /실행 완료/)
  await h.tick()
  assert.equal(h.calls.filter(({ path }) => path === '/api/session').length, 3)
})

test('manual refresh restores an expired session and shares initialization for history, detail and configuration', async (t) => {
  const h = await experimentHarness(t, () => ({ csrf_token: 'synthetic' }))
  const before = h.calls.length
  h.expireSession()
  button(h.render(), '새로고침').props.onClick(); h.render(); await h.settle()
  assert.deepEqual(h.calls.slice(before).map(({ path }) => path).filter((path) => !path.startsWith('/api/experiments/readiness?')), [
    '/api/session', '/api/experiments', '/api/experiments/synthetic-run', '/api/experiments/config',
  ])
  assert.doesNotMatch(h.markup(), /synthetic session expired/)
  assert.match(h.markup(), /실행 완료/)
})

test('polling restores an expired session automatically after a previous successful initialization', async (t) => {
  const h = await experimentHarness(t, () => ({ csrf_token: 'synthetic' }))
  const before = h.calls.length
  h.expireSession()
  await h.tick()
  assert.deepEqual(h.calls.slice(before).map(({ path }) => path), ['/api/session', '/api/experiments'])
  assert.doesNotMatch(h.markup(), /synthetic session expired/)
  assert.match(h.markup(), /실행 완료/)
})

test('unmount while initializing aborts and never starts history, detail or configuration reads after a late success', async (t) => {
  const initial = deferred()
  const h = await experimentHarness(t, () => initial.promise)
  assert.equal(h.calls.length, 1)
  const before = h.markup()
  h.unmount()
  assert.equal(h.calls[0].signal.aborted, true)
  initial.resolve({ csrf_token: 'synthetic' }); await h.flush()
  assert.equal(h.calls.length, 1)
  assert.equal(h.markup(), before)
  assert.equal(h.timers.size, 0)
})

test('refresh while initializing keeps one live session and drops the aborted history continuation', async (t) => {
  const initial = deferred()
  const h = await experimentHarness(t, () => initial.promise)
  button(h.render(), '새로고침').props.onClick(); h.render()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].signal.aborted, false, 'refresh must not abort shared initialization')
  initial.resolve({ csrf_token: 'synthetic' }); await h.settle()
  assert.equal(h.calls.filter(({ path }) => path === '/api/experiments').length, 1)
  assert.equal(h.calls.filter(({ path }) => path === '/api/experiments/synthetic-run').length, 1)
  assert.equal(h.calls.filter(({ path }) => path === '/api/experiments/config').length, 1)
  assert.ok(h.calls.every(({ signal }) => !signal.aborted))
})

test('changing selection during initialization never reads the stale selection', async (t) => {
  const initial = deferred()
  const h = await experimentHarness(t, () => initial.promise)
  h.select('new-run')
  assert.equal(h.calls.length, 1)
  initial.resolve({ csrf_token: 'synthetic' }); await h.settle()
  assert.deepEqual(h.calls.map(({ path }) => path).filter((path) => !path.startsWith('/api/experiments/readiness?')), ['/api/session', '/api/experiments', '/api/experiments/config', '/api/experiments/new-run'])
  assert.equal(h.calls.filter(({ path }) => path === '/api/experiments/synthetic-run').length, 0)
  assert.equal(find(h.render(), (node) => node.props?.className === 'mail-test-response').props.children, '새로 선택한 실행의 요약입니다.')
  assert.doesNotMatch(h.markup(), /처음 선택한 실행의 요약입니다/)
  assert.ok(h.calls.every(({ signal }) => !signal.aborted))
})
