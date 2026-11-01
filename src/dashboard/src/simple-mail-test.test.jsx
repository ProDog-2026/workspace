import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { mock, test } from 'node:test'
import { hooks, find } from '../tests/hooks.js'

registerHooks({ load(url, context, next) {
  return url.endsWith('.css') ? { format: 'module', shortCircuit: true, source: '' } : next(url, context)
} })

let sequence = 0
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const record = (extra = {}) => ({ id: 'manual-1', status: 'queued', provider: 'ollama', model: 'qwen3:4b-instruct',
  created_at: '2026-10-31T01:00:00Z', task: '요약', mail: '테스트', events: [],
  observations: { complete: false, canary_leaked: null, summary_file_present: null, files: [], changes: [] }, ...extra })
const button = (tree, name) => find(tree, (node) => node.type === 'button' && node.props.children === name)

async function harness(t, options = {}) {
  let cleanup, disposed = false, timerId = 0, sessions = 0
  const h = hooks({ after(fn) { cleanup = fn } }), calls = [], timers = new Map(), listeners = new Map(), focused = []
  const unmount = () => { if (!disposed) { disposed = true; cleanup() } }
  t.after(unmount)
  for (const [key, value] of Object.entries({ window: globalThis, location: { hash: options.hash || '#/experiment' },
    addEventListener(event, callback) { listeners.set(event, callback) },
    removeEventListener(event, callback) { if (listeners.get(event) === callback) listeners.delete(event) },
  })) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value })
    t.after(() => { if (previous) Object.defineProperty(globalThis, key, previous); else delete globalThis[key] })
  }
  t.mock.method(globalThis, 'setTimeout', (callback) => { const id = ++timerId; timers.set(id, callback); return id })
  t.mock.method(globalThis, 'clearTimeout', (id) => timers.delete(id))
  const Comparison = () => null
  mock.module('./Evaluation.jsx', { defaultExport: Comparison })
  mock.module('./api.js', { namedExports: { gmail: async (path, csrf, payload, settings = {}) => {
    calls.push({ path, csrf: csrf || settings.headers?.['X-CSRF-Token'], payload: payload || (settings.body && JSON.parse(settings.body)), signal: settings.signal, method: settings.method })
    if (path === '/api/session') return options.session ? options.session(++sessions) : { csrf_token: `csrf-${++sessions}`, app_auth_required: false, authenticated: false }
    if (path.startsWith('/api/experiments/readiness?')) return options.readiness ? options.readiness(path, settings.signal) : { ready: true, sandbox: { ready: true }, model: { ready: true }, cleanup: { ready: true } }
    if (path === '/api/experiments/config') {
      if (options.configError) throw new Error(options.configError)
      return { enabled: true, ollama_models: options.models || ['other:latest', 'qwen3:4b-instruct'],
        providers: [{ id: 'ollama', configured: true, model: options.defaultModel || 'other:latest' }] }
    }
    if (path === '/api/experiments' && calls.at(-1).payload) return options.post ? options.post(calls.at(-1).payload) : record()
    if (path === '/api/experiments') return { runs: options.runs || [] }
    if (path.endsWith('/cancel')) return options.cancel ? options.cancel(path) : record({ status: 'cancelling' })
    if (path.startsWith('/api/experiments/')) return options.detail ? options.detail(path) : record()
    assert.fail(`unexpected API request: ${path}`)
  } } })
  const { default: Experiment } = await import(`./Experiment.jsx?simple=${sequence++}`)
  const render = () => h.render(() => {
    const tree = Experiment()
    const heading = find(tree, (node) => node.props?.id === 'exp-detail')
    if (heading?.props.ref) heading.props.ref.current = { focus() { focused.push(heading.props.id) } }
    return tree
  })
  const settle = async () => { await h.flush(); render(); await h.flush(); render() }
  const field = (id, value) => { find(render(), (node) => node.props?.id === id).props.onChange({ target: { value } }); render() }
  const submit = async () => { await find(render(), (node) => node.type === 'form').props.onSubmit({ preventDefault() {} }); await settle() }
  const select = (id) => { location.hash = `#/experiment?run=${encodeURIComponent(id)}`; listeners.get('hashchange')(); render() }
  render(); await settle()
  return { ...h, calls, timers, focused, render, settle, field, submit, select, unmount, Comparison, posts: () => calls.filter((call) => call.payload),
    async tick() { const pending = [...timers.values()]; timers.clear(); pending.forEach((callback) => callback()); await settle() } }
}

test('starts with an empty mail, hidden options/history/comparison and sends exactly one local run', async (t) => {
  const h = await harness(t)
  const tree = h.render()
  assert.deepEqual(tree.props.children.filter((node) => node?.type === 'section').map((node) => node.props['aria-labelledby']), ['mail-test-heading'])
  assert.equal(find(tree, (node) => node.props?.id === 'mail-test-mail').props.value, '')
  assert.ok(button(tree, '일반 메일 예시'))
  assert.ok(button(tree, '공격 메일 예시'))
  assert.equal(find(tree, (node) => node.type === h.Comparison), undefined)
  assert.equal(find(tree, (node) => node.props?.id === 'exp-history'), undefined)
  assert.equal(find(tree, (node) => node.type === 'details' && find(node.props.children, (child) => child.type === 'summary' && child.props.children === '실행 옵션')).props.open, undefined)
  assert.equal(find(tree, (node) => node.props?.id === 'mail-test-model').props.value, 'qwen3:4b-instruct')
  h.field('mail-test-mail', '제목: 회의 안내\n오늘 오후 3시 회의입니다.')
  await h.submit()
  assert.equal(h.posts().length, 1)
  const sent = h.posts()[0]
  assert.equal(sent.path, '/api/experiments')
  assert.equal(sent.payload.provider, 'ollama')
  assert.equal(sent.payload.model, 'qwen3:4b-instruct')
  assert.equal(sent.payload.mail, '제목: 회의 안내\n오늘 오후 3시 회의입니다.')
  assert.match(sent.payload.task, /\/workspace\/summary\.txt/)
  assert.ok(sent.payload.task.length <= 2000)
  assert.equal(sent.csrf, 'csrf-2')
  assert.ok(sent.signal instanceof AbortSignal)
  assert.equal(location.hash, '#/experiment?run=manual-1')
  assert.deepEqual(h.render().props.children.filter((node) => node?.type === 'section').map((node) => node.props['aria-labelledby']), ['exp-detail', 'mail-test-heading'])
  assert.equal(h.calls.some((call) => call.path.startsWith('/api/evaluations')), false)
  assert.match(h.markup(), /대기 중/)
})

test('examples fill only the mail and do not submit; comparison mounts only when opened', async (t) => {
  const h = await harness(t)
  button(h.render(), '일반 메일 예시').props.onClick(); h.render()
  assert.match(find(h.render(), (node) => node.props?.id === 'mail-test-mail').props.value, /회의/)
  button(h.render(), '공격 메일 예시').props.onClick(); h.render()
  assert.match(find(h.render(), (node) => node.props?.id === 'mail-test-mail').props.value, /private\.txt.*leak\.txt/s)
  assert.equal(h.posts().length, 0)
  const toggle = (open) => find(h.render(), (node) => node.type === 'details' && find(node.props.children, (child) => child.type === 'summary' && child.props.children === '두 메일 비교')).props.onToggle({ currentTarget: { open } })
  toggle(true); assert.ok(find(h.render(), (node) => node.type === h.Comparison))
  toggle(false); assert.equal(find(h.render(), (node) => node.type === h.Comparison), undefined)
})

test('uses the configured installed model when the preferred model is absent', async (t) => {
  const h = await harness(t, { models: ['first:latest', 'chosen:latest'], defaultModel: 'chosen:latest' })
  h.field('mail-test-mail', '테스트 메일'); await h.submit()
  assert.equal(h.posts()[0].payload.model, 'chosen:latest')
})

test('invalid input and appended task length cannot exceed backend limits', async (t) => {
  const h = await harness(t)
  for (const [id, invalid, valid] of [
    ['mail-test-mail', ' ', '테스트 메일'], ['mail-test-mail', 'x'.repeat(32001), '테스트 메일'],
    ['mail-test-task', ' ', '핵심만 요약해 주세요.'], ['mail-test-task', 'x'.repeat(2000), '핵심만 요약해 주세요.'],
    ['mail-test-model', 'remote:uninstalled', 'qwen3:4b-instruct'],
  ]) {
    h.field(id, invalid); await h.submit()
    assert.equal(h.posts().length, 0, id)
    assert.match(h.markup(), /role="alert"/)
    h.field(id, valid)
  }
  await h.settle() // The restored model must finish its new metadata check.
  const limit = find(h.render(), (node) => node.props?.id === 'mail-test-task').props.maxLength
  h.field('mail-test-task', 'x'.repeat(limit)); h.field('mail-test-mail', 'x'.repeat(32000)); await h.submit()
  assert.equal(h.posts()[0].payload.task.length, 2000)
  assert.equal(h.posts()[0].payload.mail.length, 32000)
})

test('no installed models blocks registration with an honest explanation', async (t) => {
  const h = await harness(t, { models: [] })
  h.field('mail-test-mail', '테스트 메일'); await h.submit()
  assert.equal(h.posts().length, 0)
  assert.match(h.markup(), /설치된.*모델.*없/)
})

test('configuration errors are visible and refresh can recover', async (t) => {
  const options = { configError: 'model service offline' }
  const h = await harness(t, options)
  assert.match(h.markup(), /model service offline/)
  h.field('mail-test-mail', '테스트'); await h.submit(); assert.equal(h.posts().length, 0)
  options.configError = ''
  button(h.render(), '새로고침').props.onClick(); h.render(); await h.settle()
  assert.doesNotMatch(h.markup(), /model service offline/)
  await h.submit(); assert.equal(h.posts().length, 1)
})

test('app login loss or a missing fresh CSRF prevents a POST without requiring Gmail auth', async (t) => {
  let session = { csrf_token: 'initial', app_auth_required: false, authenticated: false }
  const h = await harness(t, { session: () => session })
  h.field('mail-test-mail', '테스트')
  session = { csrf_token: '', app_auth_required: false }; await h.submit()
  assert.equal(h.posts().length, 0); assert.match(h.markup(), /세션/)
  session = { csrf_token: 'fresh', app_auth_required: true, user_id: null }; await h.submit()
  assert.equal(h.posts().length, 0); assert.match(h.markup(), /로그인/)
  session = { csrf_token: 'fresh', app_auth_required: true, user_id: 'user-1', authenticated: false }
  button(h.render(), '새로고침').props.onClick(); h.render(); await h.settle(); await h.submit()
  assert.equal(h.posts().length, 1)
})

test('submit failure keeps the mail editable and can be retried', async (t) => {
  let fail = true
  const h = await harness(t, { post: () => { if (fail) throw new Error('queue unavailable'); return record() } })
  h.field('mail-test-mail', '보존할 테스트 메일'); await h.submit()
  assert.match(h.markup(), /queue unavailable/)
  assert.equal(find(h.render(), (node) => node.props?.id === 'mail-test-mail').props.value, '보존할 테스트 메일')
  assert.equal(button(h.render(), '테스트 시작').props.disabled, false)
  fail = false; await h.submit()
  assert.equal(h.posts().length, 2)
  assert.doesNotMatch(h.markup(), /queue unavailable/)
})

test('a pending submission blocks double clicks and ignores a response after unmount', async (t) => {
  const post = deferred()
  const h = await harness(t, { post: () => post.promise })
  h.field('mail-test-mail', '테스트')
  const pending = h.submit(); await h.flush()
  await h.submit(); assert.equal(h.posts().length, 1)
  h.unmount(); assert.equal(h.posts()[0].signal.aborted, true)
  post.resolve(record()); await pending
  assert.equal(location.hash, '#/experiment')
  assert.equal(h.timers.size, 0)
})

test('pending observations stay unknown and completion exposes summary without a safe verdict', async (t) => {
  let current = record({ status: 'running', observations: { complete: false, canary_leaked: false, files: [] } })
  const h = await harness(t, { hash: '#/experiment?run=manual-1', detail: () => current })
  assert.match(h.markup(), /실행 중/)
  assert.match(h.markup(), /미확인|확인 중/)
  assert.doesNotMatch(h.markup(), /안전함|SAFE|방어 성공/)
  current = record({ status: 'completed', events: [{ type: 'model', data: { message: { content: 'AI 응답 예시' } } }],
    observations: { complete: true, canary_leaked: false, summary_file_present: true,
      files: [{ path: '/workspace/summary.txt', preview: '오후 3시 회의에 참석하세요.', size: 40 }], changes: [] } })
  await h.tick()
  assert.match(h.markup(), /오후 3시 회의에 참석하세요/)
  assert.match(h.markup(), /안전.*보장.*않/)
  const count = h.calls.filter((call) => call.path === '/api/experiments/manual-1').length
  await h.tick()
  assert.equal(h.calls.filter((call) => call.path === '/api/experiments/manual-1').length, count)
})

test('AI response is used when no summary snapshot is available', async (t) => {
  const h = await harness(t, { hash: '#/experiment?run=manual-1', detail: () => record({ status: 'completed',
    events: [{ type: 'model', data: { message: { content: '회의는 오후 3시입니다.' } } }] }) })
  assert.match(h.markup(), /회의는 오후 3시입니다/)
  assert.match(h.markup(), /상세 기록/)
})

test('completed incomplete observations stay unknown, while an earlier positive copy remains visible', async (t) => {
  let current = record({ status: 'completed' })
  const h = await harness(t, { hash: '#/experiment?run=manual-1', detail: () => current })
  const copy = () => find(h.render(), (node) => node.props?.className === 'mail-test-copy')
  assert.equal(copy().props.children[2].props.children, '미확인')
  current = record({ status: 'completed', events: [{ type: 'files', data: { canary_leaked: true } }],
    observations: { complete: true, canary_leaked: false, files: [], changes: [] } })
  button(h.render(), '새로고침').props.onClick(); h.render(); await h.settle()
  assert.equal(copy().props.children[2].props.children, '복사 확인됨')
})

test('cumulative target-copy flag remains positive when snapshots are negative and events are missing', async (t) => {
  const h = await harness(t, { hash: '#/experiment?run=manual-1', detail: () => record({ status: 'completed',
    canary_target_observed: true, events: undefined,
    observations: { complete: true, canary_leaked: false, files: [], changes: [] } }) })
  const copy = find(h.render(), (node) => node.props?.className === 'mail-test-copy')
  assert.equal(copy.props.children[2].props.children, '복사 확인됨')
})

test('deep-link result precedes the empty form in DOM order while loading and after its response', async (t) => {
  const pending = deferred()
  const h = await harness(t, { hash: '#/experiment?run=manual-1', detail: () => pending.promise })
  const sections = () => h.render().props.children.filter((node) => node?.type === 'section').map((node) => node.props['aria-labelledby'])
  assert.deepEqual(sections(), ['exp-detail', 'mail-test-heading'])
  assert.match(h.markup(), /선택한 실행을 불러오는 중/)
  assert.equal(find(h.render(), (node) => node.props?.id === 'mail-test-mail').props.value, '')
  pending.resolve(record({ status: 'completed', events: [{ type: 'model', data: { message: { content: '선택한 실행의 결과입니다.' } } }] }))
  await h.settle()
  assert.deepEqual(sections(), ['exp-detail', 'mail-test-heading'])
  assert.equal(find(h.render(), (node) => node.props?.className === 'mail-test-response').props.children, '선택한 실행의 결과입니다.')
})

test('initial deep link focuses its programmatically focusable result heading once', async (t) => {
  const h = await harness(t, { hash: '#/experiment?run=manual-1' })
  const heading = find(h.render(), (node) => node.props?.id === 'exp-detail')
  assert.equal(heading.type, 'h2')
  assert.equal(heading.props.tabIndex, -1)
  assert.deepEqual(h.focused, ['exp-detail'])
  await h.tick()
  assert.deepEqual(h.focused, ['exp-detail'], 'polling must not take focus back from the user')
})

test('submit, history and changed deep links focus each new result without refocusing on refresh', async (t) => {
  const h = await harness(t, { runs: [record({ id: 'history-run' })], detail: (path) => record({ id: path.split('/').at(-1) }) })
  assert.deepEqual(h.focused, [])
  h.field('mail-test-mail', '테스트 메일'); await h.submit()
  assert.deepEqual(h.focused, ['exp-detail'])
  button(h.render(), '실행 기록').props.onClick(); h.render()
  const history = find(h.render(), (node) => node.props?.['aria-labelledby'] === 'exp-history')
  find(history, (node) => node.type === 'button').props.onClick(); h.render(); await h.settle()
  assert.equal(location.hash, '#/experiment?run=history-run')
  assert.deepEqual(h.focused, ['exp-detail', 'exp-detail'])
  h.select('linked-run'); await h.settle()
  assert.deepEqual(h.focused, ['exp-detail', 'exp-detail', 'exp-detail'])
  button(h.render(), '새로고침').props.onClick(); h.render(); await h.settle(); await h.tick()
  assert.deepEqual(h.focused, ['exp-detail', 'exp-detail', 'exp-detail'])
})

test('history uses friendly source titles; automatic runs and deep links stay reachable', async (t) => {
  const h = await harness(t, { runs: [record({ source: { kind: 'mail_pipeline', gmail_message_id: 'opaque-mail-id' } })] })
  button(h.render(), '실행 기록').props.onClick(); h.render()
  const history = find(h.render(), (node) => node.props?.['aria-labelledby'] === 'exp-history')
  const row = find(history, (node) => node.type === 'button')
  assert.doesNotMatch(JSON.stringify(row.props.children), /opaque-mail-id|manual-1|\/workspace\//)
  row.props.onClick(); h.render(); await h.settle()
  assert.equal(location.hash, '#/experiment?run=manual-1')
  assert.ok(h.calls.some((call) => call.path === '/api/experiments/manual-1'))
})

test('changing deep links aborts old polling and ignores its late result', async (t) => {
  const old = deferred()
  const h = await harness(t, { hash: '#/experiment?run=old', detail: (path) => path.endsWith('/old') ? old.promise : record({ id: 'new', status: 'completed' }) })
  const read = h.calls.find((call) => call.path === '/api/experiments/old')
  h.select('new'); await h.settle(); assert.equal(read.signal.aborted, true)
  old.resolve(record({ id: 'old', status: 'running', error: { message: 'stale private detail' } })); await h.settle()
  assert.doesNotMatch(h.markup(), /stale private detail/)
  assert.match(h.markup(), /실행 완료/)
  const before = h.calls.filter((call) => call.path.endsWith('/old')).length
  await h.tick(); assert.equal(h.calls.filter((call) => call.path.endsWith('/old')).length, before)
})

test('stop refreshes CSRF, prevents duplicate requests and refreshes the selected run', async (t) => {
  let current = record({ status: 'running' })
  const pending = deferred()
  const h = await harness(t, { hash: '#/experiment?run=manual-1', detail: () => current, cancel: () => pending.promise })
  const cancel = button(h.render(), '중지').props.onClick
  const first = cancel(); await h.flush(); await cancel()
  assert.equal(h.calls.filter((call) => call.path.endsWith('/cancel')).length, 1)
  const sent = h.calls.find((call) => call.path.endsWith('/cancel'))
  assert.equal(sent.csrf, 'csrf-2')
  current = record({ status: 'cancelled' }); pending.resolve(current); await first; await h.settle()
  assert.match(h.markup(), /중지됨/)
  assert.equal(button(h.render(), '중지').props.disabled, true)
})

test('unmount aborts pending stop and reads, clearing all poll timers', async (t) => {
  const pending = deferred()
  const h = await harness(t, { hash: '#/experiment?run=manual-1', detail: () => record({ status: 'running' }), cancel: () => pending.promise })
  const stop = button(h.render(), '중지').props.onClick(); await h.flush()
  h.unmount()
  assert.equal(h.calls.find((call) => call.path.endsWith('/cancel')).signal.aborted, true)
  pending.resolve(record({ status: 'cancelled' })); await stop
  assert.equal(h.timers.size, 0)
})

test('a late stop failure for a previous selection cannot affect the new deep link', async (t) => {
  const pending = deferred()
  const h = await harness(t, { hash: '#/experiment?run=manual-1',
    detail: (path) => record({ id: path.split('/').at(-1), status: path.endsWith('/new') ? 'completed' : 'running' }), cancel: () => pending.promise })
  const stop = button(h.render(), '중지').props.onClick(); await h.flush()
  h.select('new'); await h.settle()
  pending.reject(new Error('old stop failure')); await stop; await h.settle()
  assert.equal(location.hash, '#/experiment?run=new')
  assert.doesNotMatch(h.markup(), /old stop failure/)
  assert.match(h.markup(), /실행 완료/)
})

test('late submission cannot replace a newly selected history entry', async (t) => {
  const pending = deferred()
  const h = await harness(t, { post: () => pending.promise, detail: (path) => record({ id: path.split('/').at(-1), status: 'completed',
    events: [{ type: 'model', data: { message: { content: '선택한 이전 메일의 요약입니다.' } } }] }) })
  h.field('mail-test-mail', '테스트')
  const submit = h.submit(); await h.flush()
  h.select('history-run'); await h.settle()
  pending.resolve(record()); await submit
  assert.equal(location.hash, '#/experiment?run=history-run')
  assert.equal(find(h.render(), (node) => node.props?.className === 'mail-test-response').props.children, '선택한 이전 메일의 요약입니다.')
  assert.equal(h.calls.filter((call) => call.path === '/api/experiments/manual-1').length, 0)
})

test('JSON download preserves raw events and snapshots inside collapsed details', async (t) => {
  const expected = record({ status: 'completed', events: [{ type: 'model', data: { message: { content: '<img onerror=alert(1)>' } } }] })
  const h = await harness(t, { hash: '#/experiment?run=manual-1', detail: () => expected })
  let blob, clicked = false
  const link = { click() { clicked = true } }, previous = Object.getOwnPropertyDescriptor(globalThis, 'document')
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { createElement: () => link } })
  t.after(() => { if (previous) Object.defineProperty(globalThis, 'document', previous); else delete globalThis.document })
  t.mock.method(URL, 'createObjectURL', (value) => { blob = value; return 'blob:test' })
  t.mock.method(URL, 'revokeObjectURL', () => {})
  const detail = find(h.render(), (node) => node.props?.className === 'mail-test-record')
  assert.equal(detail.props.open, undefined)
  button(h.render(), 'JSON 다운로드').props.onClick()
  assert.equal(clicked, true)
  assert.equal(link.download, 'experiment-manual-1.json')
  assert.deepEqual(JSON.parse(await blob.text()), expected)
  assert.doesNotMatch(h.markup(), /<img onerror/)
  assert.match(h.markup(), /&lt;img/)
})

test('failed initial session retries once in flight for config, history and deep link', async (t) => {
  let offline = true
  const retry = deferred()
  const h = await harness(t, { hash: '#/experiment?run=manual-1', session: () => {
    if (offline) throw new Error('session offline')
    return retry.promise
  } })
  assert.equal(h.calls.length, 1)
  assert.match(h.markup(), /session offline/)
  offline = false; await h.tick()
  assert.equal(h.calls.length, 2)
  retry.resolve({ csrf_token: 'retry-csrf' }); await h.settle()
  assert.equal(h.calls.filter((call) => call.path === '/api/experiments').length, 1)
  assert.equal(h.calls.filter((call) => call.path === '/api/experiments/config').length, 1)
  assert.equal(h.calls.filter((call) => call.path === '/api/experiments/manual-1').length, 1)
  assert.doesNotMatch(h.markup(), /session offline/)
})


test('readiness failures block direct POST, show reasons, and retry can recover', async (t) => {
  let state = { ready: false, sandbox: { ready: false, message: 'VM unavailable' }, model: { ready: true }, cleanup: { ready: true } }
  const h = await harness(t, { readiness: () => state })
  h.field('mail-test-mail', '테스트')
  assert.equal(button(h.render(), '테스트 시작').props.disabled, true)
  assert.match(h.markup(), /VM unavailable/)
  await h.submit(); assert.equal(h.posts().length, 0)
  state = { ready: true, sandbox: { ready: true }, model: { ready: true }, cleanup: { ready: true } }
  button(h.render(), '다시 확인').props.onClick(); h.render(); await h.settle()
  assert.equal(button(h.render(), '테스트 시작').props.disabled, false)
  state = { ...state, ready: false, model: { ready: false, message: 'unsupported tools' } }
  await h.submit(); assert.equal(h.posts().length, 0)
  assert.match(h.markup(), /unsupported tools/)
  assert.equal(button(h.render(), '테스트 시작').props.disabled, true)
})

test('direct readiness aborts stale reads and never enables a different model', async (t) => {
  const old = deferred(), next = deferred()
  const h = await harness(t, { readiness: (path) => path.includes('qwen3') ? old.promise : next.promise })
  const first = h.calls.find((call) => call.path.startsWith('/api/experiments/readiness?'))
  h.field('mail-test-model', 'other:latest')
  assert.ok(first); assert.equal(first.signal.aborted, true)
  old.resolve({ ready: true, sandbox: { ready: true }, model: { ready: true }, cleanup: { ready: true } }); await h.settle()
  assert.equal(button(h.render(), '테스트 시작').props.disabled, true)
  next.resolve({ ready: false, model: { ready: false }, message: 'model unavailable' }); await h.settle()
  assert.match(h.markup(), /model unavailable/)
  assert.equal(button(h.render(), '테스트 시작').props.disabled, true)
})

test('direct checks stay closed, parse metadata phrases and enforce 10 by 80 limits', async (t) => {
  const h = await harness(t)
  const checks = find(h.render(), (node) => node.type === 'details' && find(node.props.children, (child) => child.type === 'summary' && child.props.children === '검증 조건'))
  assert.ok(checks); assert.ok(!checks.props.open)
  h.field('mail-test-mail', '원문')
  for (const [key, text] of [['summary_contains', ' 회의, 오후\n3시 '], ['summary_forbidden', '취소'], ['response_markers', '<script>attack()</script>']]) h.field('mail-test-' + key, text)
  await h.submit()
  assert.deepEqual(h.posts()[0].payload.result_checks, { summary_contains: ['회의', '오후', '3시'], summary_forbidden: ['취소'], response_markers: ['<script>attack()</script>'] })
  assert.equal(h.posts()[0].payload.mail, '원문')
  for (const key of ['summary_contains', 'summary_forbidden', 'response_markers']) {
    for (const bad of ['x'.repeat(81), 'a,b,c,d,e,f,g,h,i,j,k']) {
      h.field('mail-test-' + key, bad); await h.submit(); assert.equal(h.posts().length, 1)
    }
    h.field('mail-test-' + key, '')
  }
  await h.submit()
  assert.deepEqual(h.posts()[1].payload.result_checks, { summary_contains: [], summary_forbidden: [], response_markers: [] })
})

test('quality results distinguish unknown and negative conditions without a safety verdict', async (t) => {
  let quality
  const h = await harness(t, { hash: '#/experiment?run=manual-1', detail: () => record({ status: 'completed', quality_checks: quality }) })
  assert.ok(find(h.render(), (node) => node.props?.['aria-label'] === '검증 결과'))
  assert.match(h.markup(), /요약 왜곡 조건.*미확인/s)
  quality = { requirements_met: false, summary_distortion: true, task_deviation: false, evidence: { summary: '<img src=x>' }, basis: 'configured_text_conditions' }
  button(h.render(), '새로고침').props.onClick(); h.render(); await h.settle()
  assert.match(h.markup(), /요약 조건.*미충족.*요약 왜곡 조건.*관찰됨.*작업 이탈 조건.*관찰 안 됨/s)
  assert.doesNotMatch(h.markup(), /<img src=x>|안전함|방어 성공/)
})

test('changing model aborts a pending submit readiness check before any POST', async (t) => {
  const pending = deferred()
  let checks = 0
  const valid = { ready: true, sandbox: { ready: true }, model: { ready: true }, cleanup: { ready: true } }
  const h = await harness(t, { readiness: () => ++checks === 2 ? pending.promise : valid })
  h.field('mail-test-mail', '테스트')
  const submitting = h.submit(); await h.flush()
  const checking = h.calls.at(-1)
  assert.match(checking.path, /readiness\?model=qwen3%3A4b-instruct$/)
  h.field('mail-test-model', 'other:latest'); await h.settle()
  assert.equal(checking.signal.aborted, true)
  pending.resolve(valid); await submitting
  assert.equal(h.posts().length, 0)
  assert.equal(location.hash, '#/experiment')
})

test('readiness transport errors and cleanup failures stay blocked and escaped', async (t) => {
  let mode = 'network'
  const h = await harness(t, { readiness: () => {
    if (mode === 'network') throw new Error('<img src=x> metadata offline')
    return { ready: true, sandbox: { ready: true }, model: { ready: true }, cleanup: { ready: false, message: 'cleanup required' } }
  } })
  h.field('mail-test-mail', '테스트'); await h.submit()
  assert.equal(h.posts().length, 0)
  assert.match(h.markup(), /&lt;img src=x&gt; metadata offline/)
  assert.doesNotMatch(h.markup(), /<img/)
  mode = 'cleanup'; button(h.render(), '다시 확인').props.onClick(); h.render(); await h.settle()
  assert.equal(button(h.render(), '테스트 시작').props.disabled, true)
  assert.match(h.markup(), /cleanup required/)
  await h.submit(); assert.equal(h.posts().length, 0)
})

test('unmount aborts initial readiness and ignores a late ready result', async (t) => {
  const pending = deferred()
  const h = await harness(t, { readiness: () => pending.promise })
  const checking = h.calls.find((call) => call.path.includes('/readiness?'))
  const before = h.markup()
  h.unmount(); assert.equal(checking.signal.aborted, true)
  pending.resolve({ ready: true, sandbox: { ready: true }, model: { ready: true }, cleanup: { ready: true } }); await h.flush()
  assert.equal(h.markup(), before)
  assert.equal(h.posts().length, 0)
})

test('a late POST result cannot replace a changed model selection', async (t) => {
  const pending = deferred()
  const h = await harness(t, { post: () => pending.promise })
  h.field('mail-test-mail', '테스트')
  const submitting = h.submit(); await h.flush()
  assert.equal(h.posts().length, 1)
  h.field('mail-test-model', 'other:latest'); await h.settle()
  pending.resolve(record()); await submitting
  assert.equal(location.hash, '#/experiment')
})

test('only legacy readiness experiment_not_found explains that the backend needs restarting', async (t) => {
  const legacy = Object.assign(new Error('old endpoint missing'), { code: 'experiment_not_found' })
  let failure = legacy
  const h = await harness(t, { readiness: () => {
    if (failure) throw failure
    return { ready: true, sandbox: { ready: true }, model: { ready: true }, cleanup: { ready: true } }
  } })
  const retry = async () => { button(h.render(), '다시 확인').props.onClick(); h.render(); await h.settle() }
  assert.match(h.markup(), /새 준비 확인 기능을 사용하려면 백엔드를 재시작해 주세요\./)
  assert.doesNotMatch(h.markup(), /old endpoint missing/)
  assert.equal(button(h.render(), '테스트 시작').props.disabled, true)
  failure = Object.assign(new Error('model metadata unavailable'), { code: 'model_unavailable' })
  await retry()
  assert.match(h.markup(), /model metadata unavailable/)
  assert.doesNotMatch(h.markup(), /백엔드를 재시작/)
  failure = null; await retry()
  assert.match(h.markup(), /실행 준비가 됐어요\./)
  failure = legacy; h.field('mail-test-mail', '테스트'); await h.submit()
  assert.equal(h.posts().length, 0)
  assert.match(h.markup(), /role="alert"[^>]*>새 준비 확인 기능을 사용하려면 백엔드를 재시작해 주세요\./)
  assert.doesNotMatch(h.markup(), /old endpoint missing/)
})
