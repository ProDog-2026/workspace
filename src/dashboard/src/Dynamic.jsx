import { useEffect, useRef, useState } from 'react'
import { dynamic, gmail } from './api'
import { DynamicEvents, DynamicFlow, DynamicResult } from './DynamicDetails'
import { dynamicError, dynamicLabel, isActive } from './dynamicState'
import toolDefinitions from '../../agent-container/control-dashboard/backend/tool_definitions.json'
import mockCatalog from '../../agent-container/control-dashboard/backend/mock_tools/catalog.json'
import './Dynamic.css'

const DEFAULT = { auto_submit: true, base_url: 'http://host.docker.internal:11434', model: '',
  prompt_id: 'MAIL_SUMMARY_V2', temperature: 0.1,
  limits: { max_calls: 8, max_tokens: 2048, total_seconds: 600 } }
const SOURCE = { gmail: '받은 메일', gmail_manual: '선택한 메일', experiment: '수동 실험', imported: '기존 실험' }
const DEPLOY_ERRORS = {
  docker_unavailable: 'Docker Desktop을 실행한 뒤 다시 시도하세요.',
  deployment_failed: '이미지 빌드 또는 실행에 실패했습니다. Docker 상태와 네트워크, 포트 8000 사용 여부를 확인하세요.',
  connection_failed: '배포 후 서버 또는 수신 서비스에 연결하지 못했습니다. Docker 컨테이너 상태를 확인하세요.',
}
const CHECK_MESSAGES = { ready: '모델 연결과 도구 호출 확인 완료', limit_reached: '응답 토큰 한도 도달', tool_call_not_verified: '연결됐지만 도구 호출 미확인', tool_roundtrip_not_verified: '도구 결과 전달 뒤 최종 응답 미확인' }

const TOOL_INFO = {
  read_email: ['검사 메일 읽기', '검사 대상으로 전달된 메일을 읽습니다.', '검사 데이터'],
  save_summary: ['요약 저장', '작업 디렉터리에 요약 파일을 저장합니다.', '제한된 실행'],
  send_email: ['메일 전송', '전송 요청을 기록하지만 실제 메일은 보내지 않습니다.', '모의 실행'],
  write_file: ['파일 쓰기', '작업 디렉터리의 files 폴더에 파일을 씁니다.', '제한된 실행'],
  fetch_url: ['테스트 서버 요청', '등록된 테스트 수신 서버로만 HTTP 요청을 보냅니다.', '제한된 실행'],
}

function DynamicTools() {
  const tools = [
    ...toolDefinitions.map(({ function: tool }) => {
      const [label, description, mode] = TOOL_INFO[tool.name] ?? [tool.name, tool.description, '실행 방식 확인 필요']
      return { name: tool.name, label, description, mode, fields: tool.parameters.required ?? [] }
    }),
    ...mockCatalog.map((tool) => ({ ...tool, mode: '모의 실행' })),
  ]
  return <details className="panel dynamic-tools">
    <summary>에이전트 도구 목록 <span className="dynamic-badge">{tools.length}개</span></summary>
    <div className="panel-body">
      <p className="note">현재 웹 대시보드에 포함된 도구 정의입니다. 실행 중인 검사 이미지의 버전과 다를 수 있습니다. 모의 도구는 요청만 기록하며 실제 작업을 수행하지 않습니다.</p>
      <ul className="dynamic-tool-list">{tools.map((tool) => <li key={tool.name} className="dynamic-tool-item">
        <div className="dynamic-tool-heading"><strong>{tool.label}</strong><span className="dynamic-badge">{tool.mode}</span></div>
        <code>{tool.name}</code>
        <p>{tool.mode === '모의 실행' && tool.name !== 'send_email' ? '요청을 기록하는 모의 도구입니다.' : tool.description}</p>
        <small>입력값: {tool.fields.length ? tool.fields.join(', ') : '없음'}</small>
      </li>)}</ul>
    </div>
  </details>
}

export default function Dynamic() {
  const [form, setForm] = useState(DEFAULT)
  const [ready, setReady] = useState(false)
  const [staticReady, setStaticReady] = useState(false)
  const [models, setModels] = useState([])
  const [prompts, setPrompts] = useState([])
  const [jobs, setJobs] = useState([])
  const [selected, setSelected] = useState(() => new URLSearchParams(location.hash.split('?')[1] ?? '').get('job'))
  const [historyLimit, setHistoryLimit] = useState(30)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [deployment, setDeployment] = useState(null)
  const [modelCheck, setModelCheck] = useState('모델 선택 후 확인 가능')
  const deployedHere = useRef(false)
  const currentForm = useRef(form)
  currentForm.current = form
  const [email, setEmail] = useState({ subject: '회의 일정 확인', body: '11월 10일 오후 2시, 301호에서 회의합니다.' })
  const [mailList, setMailList] = useState(null)
  const [mailNext, setMailNext] = useState(null)
  const [selectedMail, setSelectedMail] = useState('')
  const [promptEdit, setPromptEdit] = useState({ id: '', description: '', text: '' })
  const selectedPrompt = prompts.find((p) => p.id === form.prompt_id)

  const loadPrompts = async () => {
    const data = await dynamic('/prompts')
    setPrompts(data.prompts)
  }
  const loadModels = async (address = form.base_url) => {
    const data = await dynamic('/models?base_url=' + encodeURIComponent(address))
    setModels(data.models)
  }
  const loadJobs = async () => {
    const data = await dynamic('/jobs')
    setJobs(data.jobs)
    return data.jobs
  }
  const loadMails = async (token = null) => {
    const data = await gmail('/api/mails?limit=20' + (token ? '&page_token=' + encodeURIComponent(token) : ''))
    setMailList((previous) => token ? [...(previous ?? []), ...data.messages] : data.messages)
    setMailNext(data.next_page_token)
    if (!token) setSelectedMail('')
  }
  const verifyConnections = async () => {
    const selected = currentForm.current
    const results = await Promise.allSettled([loadPrompts(), loadModels(selected.base_url)])
    if (results.some((result) => result.status === 'rejected')) {
      setModelCheck('모델 목록 또는 프롬프트를 불러오지 못했습니다.')
      return
    }
    if (!selected.model) { setModelCheck('모델을 선택하고 연결·도구 호출 확인을 누르세요.'); return }
    setModelCheck('모델 연결과 도구 호출 확인 중…')
    try {
      const result = await dynamic('/check', { method: 'POST', body: selected })
      if (selected.model !== currentForm.current.model || selected.base_url !== currentForm.current.base_url) return
      setModelCheck(CHECK_MESSAGES[result.status] ?? '연결 확인 결과: ' + result.status)
    } catch (error) {
      if (selected.model === currentForm.current.model && selected.base_url === currentForm.current.base_url) setModelCheck('모델 연결 확인 실패: ' + error.message)
    }
  }
  useEffect(() => {
    let disposed = false, timer
    const poll = async () => {
      try {
        const data = await dynamic('/jobs')
        if (!disposed) setJobs(data.jobs)
      } catch (e) { if (!disposed) setError(e.message) }
      if (!disposed) timer = setTimeout(poll, 2000)
    }
    gmail('/api/session').then(() => dynamic('/settings')).then(async (data) => {
      if (disposed) return
      setForm(data.settings); setStaticReady(data.static_configured); setReady(true)
      dynamic('/deployment').then((status) => { if (!disposed) setDeployment(status) }).catch((e) => { if (!disposed) setError(e.message) })
      const results = await Promise.allSettled([loadPrompts(), loadModels(data.settings.base_url)])
      if (!disposed) {
        const failed = results.find((r) => r.status === 'rejected')
        if (failed) setError(failed.reason.message)
        poll()
      }
    }).catch((e) => { if (!disposed) setError(e.message) })
    return () => { disposed = true; clearTimeout(timer) }
  }, [])
  useEffect(() => {
    if (deployment?.state !== 'running') return
    let stopped = false, timer
    const pollDeployment = async () => {
      try {
        const status = await dynamic('/deployment')
        if (stopped) return
        setDeployment(status)
        if (status.state === 'running') timer = setTimeout(pollDeployment, 2000)
        else if (status.ready && deployedHere.current) { deployedHere.current = false; setError(''); await verifyConnections() }
      } catch (error) { if (!stopped) { setError(error.message); timer = setTimeout(pollDeployment, 3000) } }
    }
    timer = setTimeout(pollDeployment, 2000)
    return () => { stopped = true; clearTimeout(timer) }
  }, [deployment?.state])
  useEffect(() => {
    if (selectedPrompt) setPromptEdit({ id: selectedPrompt.id, description: selectedPrompt.description, text: selectedPrompt.text })
  }, [selectedPrompt])

  const action = async (name, work) => {
    setBusy(name); setError(''); setNotice('')
    try { await work() } catch (e) { setError(e.message) } finally { setBusy('') }
  }
  const save = async () => {
    const data = await dynamic('/settings', { method: 'PUT', body: form })
    setForm(data.settings)
    setNotice(data.settings.auto_submit ? '설정을 저장했습니다. 받은편지함을 열면 의심·위험 메일을 자동으로 등록합니다.' : '설정을 저장했습니다.')
  }
  const selectJob = (job) => {
    setSelected(job.id)
    history.replaceState(null, '', '#/dynamic?job=' + encodeURIComponent(job.id))
  }
  const job = jobs.find((j) => j.id === selected)
  const active = jobs.filter(isActive).length
  const completed = jobs.filter((j) => j.status === 'completed').length
  const observed = jobs.filter((j) => j.observation === 'observed').length
  const patch = (key, value) => {
    if ((key === 'model' || key === 'base_url') && value !== currentForm.current[key]) setModelCheck('모델 또는 주소가 변경됐습니다. 다시 확인하세요.')
    setForm((old) => ({ ...old, [key]: value }))
  }
  const patchLimit = (key, value) => setForm((old) => ({ ...old, limits: { ...old.limits, [key]: Number(value) } }))

  return <div className="dynamic-page">
    <header className="page-head"><div><p className="dynamic-eyebrow">2차 분석 · 에이전트 동작 관찰</p><h1>동적 검사</h1></div>
      <div className="ctl-row"><span className="tag">로컬 실험 환경</span><button className="btn" disabled={!!busy} onClick={() => action('refresh', async () => { await Promise.all([loadJobs(), loadPrompts(), loadModels()]); setNotice('최신 상태를 불러왔습니다.') })}>상태 새로고침</button></div>
    </header>
    <p className="dynamic-lead">정적 검사에서 의심·위험으로 분류된 메일을 격리된 에이전트에 전달하고, 도구 요청과 확인된 효과를 살펴봅니다.</p>
    {error && <div className="dynamic-notice error" role="alert">{error}</div>}
    {notice && <div className="dynamic-notice" role="status">{notice}</div>}
    {ready && !staticReady && <div className="dynamic-notice">1차 분류 모델이 아직 연결되지 않았습니다. 자동 메일 검사는 모델 연결 후 시작되며, 아래 수동 실험은 별도로 사용할 수 있습니다.</div>}
    <div className="dynamic-metrics overview">
      <div><span>대기·진행 중</span><strong>{active}<small>건</small></strong></div>
      <div><span>검사 완료</span><strong>{completed}<small>건</small></strong></div>
      <div><span>이상 행동 관찰</span><strong>{observed}<small>건</small></strong></div>
      <div><span>전체 실행 기록</span><strong>{jobs.length}<small>건</small></strong></div>
    </div>

    <section className="panel">
      <div className="panel-head"><h2>동적 검사 실행 환경</h2><span className="dynamic-badge">{deployment?.state === 'running' ? '준비 중' : deployment?.ready ? '연결됨' : '준비 필요'}</span></div>
      <div className="panel-body dynamic-stack">
        <p className="note">이미지가 없으면 Docker 이미지를 빌드하고 검사 서버와 수신 서버를 실행합니다. 완료 후 연결 상태를 확인합니다.</p>
        {deployment?.checks && <div className="dynamic-environment" role="status">
          <span>Docker: {deployment.checks.docker ? '연결됨' : '확인 필요'}</span>
          <span>이미지: {deployment.checks.images ? '준비됨' : '준비 필요'}</span>
          <span>검사 서버: {deployment.checks.control ? '연결됨' : '연결 안 됨'}</span>
          <span>수신 서버: {deployment.checks.receiver ? '정상' : '확인 필요'}</span>
        </div>}
        {deployment?.state === 'running' && <p className="note" role="status">이미지 빌드 및 서버 실행 중입니다. 처음에는 몇 분 걸릴 수 있습니다.</p>}
        {deployment?.error && <p className="dynamic-notice error" role="alert">{DEPLOY_ERRORS[deployment.error] ?? '실행 환경을 준비하지 못했습니다.'}</p>}
        <p className="note" role="status">Ollama: {modelCheck}</p>
        <div className="ctl-row">
          <button className="btn primary" disabled={!!busy || !ready || deployment?.state === 'running'} onClick={() => action('deploy', async () => {
            deployedHere.current = true
            const status = await dynamic('/deployment', { method: 'POST' }); setDeployment(status)
            if (status.ready) { deployedHere.current = false; await verifyConnections() }
            else if (status.state !== 'running') deployedHere.current = false
          })}>{deployment?.state === 'running' ? '배포 및 실행 중…' : '이미지 배포 및 서버 실행'}</button>
          <button className="btn" disabled={!!busy || deployment?.state === 'running'} onClick={() => action('environment', async () => { setDeployment(await dynamic('/deployment')) })}>실행 환경 확인</button>
        </div>
      </div>
    </section>

    <section className="panel">
      <div className="panel-head"><h2>모델 연결 및 자동 검사</h2><label className="dynamic-switch"><input type="checkbox" checked={form.auto_submit} onChange={(e) => patch('auto_submit', e.target.checked)} />의심·위험 메일 자동 전달</label></div>
      <form className="panel-body dynamic-stack" onSubmit={(e) => { e.preventDefault(); action('save', save) }}>
        <div className="dynamic-form-grid">
          <label>Ollama 주소<input required value={form.base_url} onChange={(e) => patch('base_url', e.target.value)} /></label>
          <label>모델<input list="dynamic-models" placeholder="설치된 모델명 선택 또는 입력" value={form.model} onChange={(e) => patch('model', e.target.value)} /><datalist id="dynamic-models">{models.map((model) => <option key={model} value={model} />)}</datalist></label>
          <label>시스템 프롬프트<select value={form.prompt_id} onChange={(e) => patch('prompt_id', e.target.value)}>
            {!prompts.length && <option value={form.prompt_id}>{form.prompt_id}</option>}
            {prompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.id} — {prompt.description}</option>)}
          </select></label>
          <label>Temperature<input type="number" min="0" max="2" step="0.1" required value={form.temperature} onChange={(e) => patch('temperature', Number(e.target.value))} /></label>
          <label>최대 LLM 호출<input type="number" min="1" max="100" required value={form.limits.max_calls} onChange={(e) => patchLimit('max_calls', e.target.value)} /></label>
          <label>응답 토큰 한도<input type="number" min="1" max="32768" required value={form.limits.max_tokens} onChange={(e) => patchLimit('max_tokens', e.target.value)} /></label>
          <label>전체 실행 시간 (초)<input type="number" min="1" max="3600" required value={form.limits.total_seconds} onChange={(e) => patchLimit('total_seconds', e.target.value)} /></label>
        </div>
        <p className="note">{selectedPrompt?.description ?? '동적 검사 서버를 연결하면 기존 프롬프트 목록을 불러옵니다.'} · 동시 실행은 1건입니다.</p>
        <div className="ctl-row">
          <button className="btn primary" type="submit" disabled={!ready || !!busy}>{busy === 'save' ? '저장 중…' : '설정 저장'}</button>
          <button className="btn" type="button" disabled={!ready || !!busy} onClick={() => action('models', () => loadModels())}>모델 목록 새로고침</button>
          <button className="btn" type="button" disabled={!ready || !!busy || !form.model} onClick={() => action('check', async () => {
            const result = await dynamic('/check', { method: 'POST', body: form })
            if (form.model !== currentForm.current.model || form.base_url !== currentForm.current.base_url) return
            const message = CHECK_MESSAGES[result.status] ?? '연결 확인 결과: ' + result.status
            setModelCheck(message); setNotice(message)
          })}>{busy === 'check' ? '연결·도구 호출 확인 중…' : '연결·도구 호출 확인'}</button>
        </div>
      </form>
      <details className="dynamic-prompt-editor"><summary>프롬프트 보기·수정</summary>
        <div className="dynamic-stack">
          <div className="dynamic-form-grid"><label>프롬프트 ID<input maxLength="40" value={promptEdit.id} onChange={(e) => setPromptEdit((p) => ({ ...p, id: e.target.value }))} /></label>
            <label>설명<input maxLength="200" value={promptEdit.description} onChange={(e) => setPromptEdit((p) => ({ ...p, description: e.target.value }))} /></label></div>
          <label>시스템 프롬프트 내용<textarea rows="7" maxLength="4000" value={promptEdit.text} onChange={(e) => setPromptEdit((p) => ({ ...p, text: e.target.value }))} /></label>
          <p className="note">기본 프롬프트와 기존 실험실의 프롬프트는 새 ID로 복사해 사용하세요. 여기서 만든 프롬프트는 수정·삭제할 수 있습니다.</p>
          <div className="ctl-row">
            <button className="btn" disabled={!!busy || !promptEdit.id || !promptEdit.text.trim()} onClick={() => action('prompt', async () => { const saved = await dynamic('/prompts', { method: 'POST', body: promptEdit }); await loadPrompts(); patch('prompt_id', saved.id); setNotice('새 프롬프트를 저장했습니다. 자동 검사에 쓰려면 설정도 저장하세요.') })}>새 프롬프트로 저장</button>
            <button className="btn" disabled={!!busy || selectedPrompt?.builtin || !selectedPrompt?.editable} onClick={() => action('prompt', async () => { await dynamic('/prompts/' + encodeURIComponent(form.prompt_id), { method: 'PUT', body: promptEdit }); await loadPrompts(); setNotice('프롬프트를 수정했습니다.') })}>수정 저장</button>
            <button className="btn" disabled={!!busy || selectedPrompt?.builtin || !selectedPrompt?.editable} onClick={() => {
              if (confirm(form.prompt_id + ' 프롬프트를 삭제할까요?')) action('prompt', async () => { await dynamic('/prompts/' + encodeURIComponent(form.prompt_id), { method: 'DELETE' }); patch('prompt_id', 'MAIL_SUMMARY_V2'); await loadPrompts(); setNotice('프롬프트를 삭제했습니다. 기본 프롬프트로 설정을 다시 저장하세요.') })
            }}>삭제</button>
          </div>
        </div>
      </details>
    </section>

    <DynamicTools />
    <details className="panel dynamic-experiment"><summary>수동 실험 · 내 메일 선택 또는 직접 입력</summary>
      <div className="panel-body dynamic-stack">
        <div><h3>연동된 받은편지함에서 선택</h3><p className="note">1차 분류 없이 선택한 메일 한 건을 동적 검사합니다. 메일 내용과 검사 결과가 로컬 실험 서버에 기록될 수 있습니다.</p></div>
        <div><button type="button" className="btn" disabled={!!busy} onClick={() => action('mail-list', () => loadMails())}>{busy === 'mail-list' ? '불러오는 중…' : '내 메일 목록 불러오기'}</button></div>
        {mailList && (mailList.length ? <>
          <label>검사할 메일<select value={selectedMail} onChange={(e) => setSelectedMail(e.target.value)}>
            <option value="">메일을 선택하세요</option>
            {mailList.map((mail) => <option key={mail.id} value={mail.id} disabled={!mail.body_hash}>{mail.subject || '(제목 없음)'} — {mail.sender || '발신자 없음'}{!mail.body_hash ? ' (가져오기 실패)' : ''}</option>)}
          </select></label>
          {mailNext && <div><button type="button" className="btn" disabled={!!busy} onClick={() => action('mail-more', () => loadMails(mailNext))}>메일 더 불러오기</button></div>}
          <div><button type="button" className="btn primary" disabled={!ready || !!busy || !form.model || !selectedMail} onClick={() => action('manual-mail', async () => {
            await save(); const data = await dynamic('/manual-mail/' + encodeURIComponent(selectedMail), { method: 'POST' }); await loadJobs(); selectJob(data.job); setNotice('선택한 메일을 동적 검사 대기열에 등록했습니다.')
          })}>{busy === 'manual-mail' ? '등록 중…' : '선택한 메일 검사'}</button></div>
        </> : <p className="note">받은편지함에 메일이 없습니다.</p>)}
        <p className="note">Gmail이 연결되지 않았다면 <a href="#/settings">설정</a>에서 먼저 연결하세요. 모델을 선택하고 설정을 저장해야 검사를 실행할 수 있습니다.</p>
      </div>
      <form className="panel-body dynamic-stack" onSubmit={(e) => { e.preventDefault(); action('experiment', async () => { await save(); const data = await dynamic('/experiments', { method: 'POST', body: email }); await loadJobs(); selectJob(data.job); setNotice('실험을 대기열에 등록했습니다.') }) }}>
        <h3>직접 입력</h3><p className="note">직접 입력한 메일은 1차 분류를 거치지 않으며 받은편지함 기록과 구분됩니다.</p>
        <label>제목<input value={email.subject} maxLength="4096" onChange={(e) => setEmail((p) => ({ ...p, subject: e.target.value }))} /></label>
        <label>메일 본문<textarea rows="7" required maxLength="300000" value={email.body} onChange={(e) => setEmail((p) => ({ ...p, body: e.target.value }))} /></label>
        <div><button className="btn primary" disabled={!ready || !!busy || !form.model || !email.body.trim()}>{busy === 'experiment' ? '등록 중…' : '실험 실행'}</button></div>
      </form>
    </details>

    <section className="panel">
      <div className="panel-head"><h2>실행 내역</h2><button className="btn" disabled={!!busy || !ready} onClick={() => action('import', async () => {
        const imported = await dynamic('/import', { method: 'POST' }); await loadJobs(); setNotice(`기존 실험 기록 ${imported.imported}건을 연결했습니다.`)
      })}>기존 실험 기록 불러오기</button></div>
      {!jobs.length ? <p className="empty">아직 실행 기록이 없습니다. 의심·위험 메일을 검사하거나 수동 실험을 시작하면 여기에 표시됩니다.</p>
        : <div className="table-scroll"><table className="list dynamic-history"><thead><tr><th>출처</th><th>메일·실험</th><th>동적 검사</th><th>등록 시각</th></tr></thead><tbody>
          {jobs.slice(0, historyLimit).map((entry) => <tr key={entry.id} className={selected === entry.id ? 'selected' : ''}>
            <td><span className="tag">{SOURCE[entry.source] ?? entry.source}</span></td><td><button className="dynamic-text-btn" onClick={() => selectJob(entry)}>{entry.subject || '(제목 없음)'}</button></td>
            <td><span className={`dynamic-badge ${entry.observation === 'observed' ? 'alert' : ''}`}>{dynamicLabel(entry)}</span></td><td className="time">{new Date(entry.created_at * 1000).toLocaleString('ko-KR')}</td>
          </tr>)}
        </tbody></table></div>}
      {jobs.length > historyLimit && <div className="panel-body"><button className="btn" onClick={() => setHistoryLimit((n) => n + 30)}>실행 기록 더 보기</button></div>}
    </section>
    {job && <DynamicRun key={job.id} job={job} onChange={loadJobs} onSelect={selectJob} />}
    <p className="note dynamic-footer">메일과 모델 출력은 텍스트로 표시합니다. 실행 로그·요약에는 메일 내용이 포함될 수 있으며 로컬 실험 서버에 보관됩니다.</p>
  </div>
}

function DynamicRun({ job, onChange, onSelect }) {
  const [events, setEvents] = useState([]), [result, setResult] = useState(null), [flow, setFlow] = useState(null)
  const [error, setError] = useState(''), [selectedEvent, setSelectedEvent] = useState(null), [busy, setBusy] = useState(false)
  const cursor = useRef(0)
  useEffect(() => {
    if (!job.run_id) return
    let alive = true
    const controller = new AbortController()
    const get = (part) => dynamic(`/jobs/${job.id}/${part}`, { signal: controller.signal })
    const load = async () => {
      const [log, trace, output] = await Promise.allSettled([
        get('events?after=' + cursor.current), get('flow'),
        !isActive(job) ? get('result') : Promise.resolve(null),
      ])
      if (!alive) return
      if (log.status === 'fulfilled') { setEvents((prev) => [...prev, ...log.value.events]); cursor.current = log.value.next }
      if (trace.status === 'fulfilled') setFlow(trace.value)
      if (output.status === 'fulfilled' && output.value) setResult(output.value)
      const failure = [log, trace, output].find((item) => item.status === 'rejected' && item.reason.status !== 404 && item.reason.name !== 'AbortError')
      setError(failure ? failure.reason.message : '')
    }
    load()
    return () => { alive = false; controller.abort() }
  }, [job.id, job.run_id, job.updated_at, job.status])
  const stop = async () => { setBusy(true); try { await dynamic(`/jobs/${job.id}/stop`, { method: 'POST' }); await onChange() } catch (e) { setError(e.message) } finally { setBusy(false) } }
  const retry = async () => { setBusy(true); try { const path = job.source === 'gmail_manual' ? `/manual-mail/${encodeURIComponent(job.mail_id)}` : `/mail/${encodeURIComponent(job.mail_id)}?retry=true`; const data = await dynamic(path, { method: 'POST' }); await onChange(); if (data.job) onSelect(data.job); else setError('동적 재검사는 현재 정적 판정이 의심 또는 위험인 메일에서 가능합니다.') } catch (e) { setError(e.message) } finally { setBusy(false) } }
  return <div className="dynamic-run">
    <div className="dynamic-run-heading"><div><p className="dynamic-eyebrow">선택한 실행</p><h2>{job.subject || '(제목 없음)'}</h2><p className="note mono">{job.run_id || '실행 ID 발급 대기'}{job.calls != null ? ` · LLM ${job.calls}회` : ''}{job.elapsed_seconds != null ? ` · ${job.elapsed_seconds}초` : ''}</p></div>
      <div className="ctl-row">{isActive(job) && <button className="btn" onClick={stop} disabled={busy}>검사 중지</button>}
        {['gmail', 'gmail_manual'].includes(job.source) && !isActive(job) && job.error_code !== 'submission_unknown' && <button className="btn" onClick={retry} disabled={busy}>현재 설정으로 재검사</button>}</div>
    </div>
    {error && <p className="dynamic-notice error" role="alert">{error}</p>}
    {job.error_code && <p className="note">{dynamicError(job.error_code)}</p>}
    <DynamicResult job={job} result={result} onEvidence={setSelectedEvent} />
    <DynamicFlow flow={flow} runtimeMode={result?.runtime_mode ?? job.runtime_mode} />
    <DynamicEvents events={events} selectedEvent={selectedEvent} />
  </div>
}
