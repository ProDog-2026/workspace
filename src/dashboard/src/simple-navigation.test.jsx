import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { hooks, find } from '../tests/hooks.js'

let sequence = 0
function browser(t, hash) {
  const listeners = new Map()
  for (const [name, value] of Object.entries({
    window: globalThis, location: { hash, search: '' },
    addEventListener: (event, listener) => listeners.set(event, listener),
    removeEventListener: (event) => listeners.delete(event),
  })) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name)
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
    t.after(() => previous ? Object.defineProperty(globalThis, name, previous) : delete globalThis[name])
  }
  return listeners
}
const text = (tree) => {
  if (Array.isArray(tree)) return tree.map(text).join(' ')
  if (tree == null || typeof tree === 'boolean') return ''
  if (typeof tree !== 'object') return String(tree)
  return text(tree.props?.children)
}
const details = (tree, label) => find(tree, (node) => node.type === 'details' && find(node.props.children, (child) => child.type === 'summary' && text(child) === label))

test('readability changes preserve the original seven menu labels, order and routes', async (t) => {
  const h = hooks(t)
  const listeners = browser(t, '#/')
  t.mock.module('./auth.js', { namedExports: { useAuth: () => ({ loading: false, session: { user: { id: 'test-user' } } }) } })
  t.mock.module('./supabase.js', { namedExports: { supabase: null } })
  t.mock.module('./api.js', { namedExports: { gmail: async () => ({ email: 'test@example.invalid' }) } })
  for (const name of ['Admin', 'Dashboard', 'Experiment', 'Landing', 'Login', 'Scan', 'Settings', 'States']) {
    t.mock.module(`./${name}.${name === 'Landing' ? 'tsx' : 'jsx'}`, { defaultExport: () => <p>{name}</p> })
  }
  const { default: App } = await import(`./App.jsx?simple=${sequence++}`)
  h.render(App); await h.flush()
  const nav = find(h.render(App), (node) => node.type === 'nav')
  assert.deepEqual(nav.props.children.map(({ props }) => [props.href, props.children]), [
    ['#/', '대시보드'], ['#/states', '판정 안내'], ['#/mailbox', '로컬 수집'], ['#/scan', '메일 검사'],
    ['#/experiment', '동적 분석'], ['#/settings', '설정'], ['#/admin', '관리자'],
  ])
  for (const [route, component] of [
    ['mailbox', 'Dashboard'], ['experiment', 'Experiment'], ['settings', 'Settings'],
    ['scan', 'Scan'], ['states', 'States'], ['admin', 'Admin'],
  ]) {
    location.hash = `#/${route}`
    listeners.get('hashchange')()
    h.render(App)
    assert.match(h.markup(), new RegExp(`<p>${component}</p>`))
  }
})

async function settingsHarness(t, ready) {
  const h = hooks(t)
  browser(t, '#/settings')
  t.mock.module('./api.js', { namedExports: { DETECTOR_NAMES: {}, gmail: async (path) => {
    if (path === '/api/session') return {
      authenticated: true, configured: true, mode: 'google', email: 'test@example.invalid',
      user_id: 'test-user', csrf_token: 'test-csrf', cloud: { configured: true, pending: 0 },
    }
    if (path === '/api/analysis/settings') return {
      enabled: true, model: 'local-model', ollama_models: ['local-model'],
      sandbox: { ready, context: 'technical-context', message: 'sandbox evidence' },
      detector: { ready, model_id: 'technical-model-hash', components: ['technical-component'], message: 'detector evidence' },
    }
    assert.fail(`unexpected request: ${path}`)
  } } })
  const { default: Settings } = await import(`./Settings.jsx?simple=${sequence++}`)
  const render = () => h.render(Settings, { user: { email: 'test@example.invalid' } })
  render(); await h.flush(); render(); await h.flush(); render()
  return { ...h, render }
}

for (const ready of [false, true]) {
  test(`settings keep basics visible and technical evidence collapsed (ready=${ready})`, async (t) => {
    const h = await settingsHarness(t, ready)
    const tree = h.render()
    for (const label of ['연결 상태 자세히', '실행 환경 자세히']) assert.equal(details(tree, label).props.open, undefined)
    const runtimeTree = details(tree, '실행 환경 자세히')
    assert.equal(find(runtimeTree, (node) => node.type === 'select' || node.type === 'input' || node.type === 'button'), undefined)
    const runtime = renderToStaticMarkup(runtimeTree)
    for (const evidence of ['technical-context', 'technical-model-hash', 'technical-component', 'sandbox evidence', 'detector evidence']) assert.ok(runtime.includes(evidence))
    assert.equal(!!find(tree, (node) => node.props?.role === 'alert' && text(node).includes('분석 준비가 끝나지')), !ready)
    assert.ok(find(tree, (node) => node.type === 'select' && node.props.id === 'analysis-model'))
    assert.ok(find(tree, (node) => node.props?.role === 'switch'))
    assert.ok(find(tree, (node) => node.type === 'button' && text(node) === '설정 저장'))
    assert.equal(details(tree, '추가 도구'), undefined)
    const disconnect = find(tree, (node) => node.type === 'button' && text(node) === '연동 해제')
    assert.equal(disconnect.props['aria-describedby'], 'disconnect-warning')
    assert.match(text(find(tree, (node) => node.props?.id === 'disconnect-warning')), /검사 결과가 삭제/)
  })
}

test('mail collection shows pending or failed saves and clears the notice when saved', async (t) => {
  const h = hooks(t)
  t.mock.module('./supabase.js', { namedExports: { supabase: null } })
  const api = await import('./api.js')
  const status = { configured: true, pending: 2, error: 'retry evidence' }
  t.mock.module('./api.js', { namedExports: { ...api, gmail: async (path) => {
    if (path === '/api/session') return { csrf_token: 'test-csrf' }
    if (path.startsWith('/api/mails?')) return { messages: [], next_page_token: null }
    if (path.startsWith('/api/analysis')) return { analyses: [], cloud: { ...status } }
    assert.fail(`unexpected request: ${path}`)
  } } })
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { default: Dashboard } = await import(`./Dashboard.jsx?simple=${sequence++}`)
  const render = () => h.render(Dashboard, { local: true })
  render(); await h.flush(); render()
  assert.match(h.markup(), /저장 대기 2건.*retry evidence/)
  assert.doesNotMatch(h.markup(), /Supabase|UUID|읽기 전용/)
  assert.match(h.markup(), /불러온 0건 기준/)
  status.pending = 0; status.error = null
  t.mock.timers.tick(1000); await h.flush(); render()
  assert.doesNotMatch(h.markup(), /저장 대기|retry evidence/)
  assert.doesNotMatch(h.markup(), /분석·저장 상태를 확인/)
  const refresh = async () => {
    find(render(), (node) => node.type === 'button' && text(node) === '새로고침').props.onClick()
    render(); await h.flush(); render()
  }
  status.configured = false
  await refresh()
  assert.match(h.markup(), /role="alert"[^>]*><a href="#\/settings">결과 저장 설정/)
  status.configured = true; status.error = 'save failure at zero'
  await refresh()
  assert.match(h.markup(), /role="alert"[^>]*>저장 대기 0건.*save failure at zero/)
})
