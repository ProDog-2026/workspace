import { useEffect, useRef, useState } from 'react'
import './experiment.css'

const STATUS = { queued: '대기 중', running: '실행 중', cancelling: '중지 중', completed: '실행 완료', cancelled: '중지됨', timeout: '시간 초과', error: '실행 오류', step_limit: '단계 제한 도달' }
const EVENTS = { status: '상태', model_input: '모델에 보낸 입력', context_window: '현재 대화 범위 조정', model: '모델', tool_call: '도구 호출', tool_result: '도구 결과 원문', files: '파일', error: '오류' }
const active = (run) => ['queued', 'running', 'cancelling'].includes(run?.status)
const selectedId = () => new URLSearchParams(location.hash.split('?')[1]).get('run') || ''
const plain = (value) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)
const date = (value) => value ? new Date(value).toLocaleString('ko-KR') : '—'
const sourceName = (run) => run.source?.kind === 'mail_pipeline' ? '수집 메일 자동 분석' : '이전 수동 실험 기록'

async function request(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options }).catch((e) => {
    if (e.name === 'AbortError') throw e
    throw new Error('서버에 연결하지 못했어요. Gmail 서버(8765)가 실행 중인지 확인해 주세요.')
  })
  const body = await response.json().catch(() => { throw new Error('서버 응답을 읽지 못했어요. Gmail 서버(8765)를 확인해 주세요.') })
  if (!response.ok) throw new Error(`${body.error?.message || '요청에 실패했어요.'}${body.error?.code ? ` (${body.error.code})` : ''}`)
  return body
}

export default function Experiment() {
  const [runs, setRuns] = useState(null), [run, setRun] = useState(null)
  const [id, setId] = useState(selectedId)
  const [refresh, setRefresh] = useState(0), [reload, setReload] = useState(0)
  const [errors, setErrors] = useState({}), [busy, setBusy] = useState(false)
  const sequence = useRef(0), selection = useRef(id), mutation = useRef(null), sessionReady = useRef(null)
  const error = (key, message) => setErrors((prev) => ({ ...prev, [key]: message }))

  useEffect(() => {
    const onHash = () => {
      const next = selectedId()
      if (next === selection.current) return
      selection.current = next; sequence.current++; setId(next)
    }
    addEventListener('hashchange', onHash)
    return () => { removeEventListener('hashchange', onHash); mutation.current?.abort() }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let timer
    sessionReady.current = request('/api/session', { signal: controller.signal })
    const poll = async () => {
      try {
        await sessionReady.current
        const next = await request('/api/experiments', { signal: controller.signal })
        if (controller.signal.aborted) return
        setRuns(next.runs); error('history', '')
      } catch (e) {
        if (controller.signal.aborted) return
        error('history', e.message)
      }
      if (!controller.signal.aborted) timer = window.setTimeout(poll, 1000)
    }
    poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [refresh])

  useEffect(() => {
    setRun(null); error('detail', '')
    if (!id) return
    const controller = new AbortController()
    let timer
    const poll = async () => {
      const current = ++sequence.current
      let again = true
      try {
        await sessionReady.current
        const next = await request(`/api/experiments/${encodeURIComponent(id)}`, { signal: controller.signal })
        if (controller.signal.aborted || current !== sequence.current) return
        setRun(next); error('detail', ''); again = active(next)
      } catch (e) {
        if (controller.signal.aborted || current !== sequence.current) return
        error('detail', `${e.message} 새로고침하거나 다른 기록을 선택할 수 있어요.`)
      }
      if (again && !controller.signal.aborted) timer = window.setTimeout(poll, 1000)
    }
    poll()
    return () => { controller.abort(); clearTimeout(timer); sequence.current++ }
  }, [id, reload])

  const select = (next) => { sequence.current++; selection.current = next; setId(next); location.hash = `/experiment?run=${encodeURIComponent(next)}` }
  const cancel = async () => {
    if (mutation.current || !active(run) || run.status === 'cancelling') return
    const controller = new AbortController()
    const target = id
    mutation.current = controller; setBusy(true); error('action', '')
    try {
      const session = await request('/api/session', { signal: controller.signal })
      if (!session.csrf_token) throw new Error('세션을 확인하지 못했어요. 다시 시도해 주세요.')
      await request(`/api/experiments/${encodeURIComponent(target)}/cancel`, {
        method: 'POST', signal: controller.signal, headers: { 'X-CSRF-Token': session.csrf_token },
      })
      if (controller.signal.aborted) return
      if (selection.current === target) setReload((n) => n + 1)
    } catch (e) { if (!controller.signal.aborted) error('action', e.message) }
    finally { mutation.current = null; if (!controller.signal.aborted) setBusy(false) }
  }
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(run, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url; link.download = `experiment-${encodeURIComponent(run.id)}.json`; link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const observations = run?.observations
  const observed = (value) => value === true ? '확인됨' : value === false ? '관찰되지 않음' : '미확인'

  return (
    <div className="experiment">
      <header className="page-head"><h1>동적 분석 결과</h1><button type="button" className="btn" onClick={() => { setRefresh((n) => n + 1); setReload((n) => n + 1) }}>새로고침</button></header>
      <p className="note">수집한 메일의 정적 판정이 의심·위험이면 설정한 Ollama 모델로 자동 분석해요. 이 화면에서 대기열과 실행 결과를 확인할 수 있어요.</p>
      <div className="experiment-actions"><a className="btn" href="#/settings">자동 분석 설정</a><a className="btn" href="#/mailbox">수집 메일 보기</a></div>
      <div className="experiment-grid">
        <section className="panel" aria-labelledby="exp-history">
          <div className="panel-head"><h2 id="exp-history">실행 기록</h2><span className="note">최근 {runs?.length ?? 0}건</span></div>
          <div className="panel-body">
            <p className="note">1초마다 상태를 갱신해요. 모델 선택과 자동 분석 사용은 설정에서 저장하세요.</p>
            {errors.history && <p className="error" role="alert">{errors.history}</p>}
            {!runs ? <p className="muted">{errors.history ? '새로고침으로 다시 조회해 주세요.' : '기록을 불러오는 중…'}</p>
              : !runs.length ? <p className="muted">등록된 동적 분석이 없어요. 자동 분석을 켜고 로컬 수집 화면에서 메일을 수집하면 조건에 맞는 메일이 등록돼요.</p>
                : <ul className="experiment-history">{runs.map((item) => <li key={item.id}><button type="button" disabled={busy} aria-pressed={item.id === id} onClick={() => { select(item.id); setReload((n) => n + 1) }}>
                  <span>{sourceName(item)}</span><strong>{STATUS[item.status] || item.status}</strong><span>{item.provider} / {item.model}</span>
                  {item.source?.gmail_message_id && <small>메일 ID: {item.source.gmail_message_id}</small>}
                  <small>{date(item.created_at)} · {item.id}</small>
                </button></li>)}</ul>}
          </div>
        </section>
        <section className="panel" aria-labelledby="exp-detail">
          <div className="panel-head"><h2 id="exp-detail">실행 상세</h2><div className="experiment-actions"><button type="button" className="btn" disabled={!run} onClick={download}>JSON 다운로드</button><button type="button" className="btn" disabled={!active(run) || run?.status === 'cancelling' || busy} onClick={cancel}>{run?.status === 'cancelling' ? '중지 중' : busy ? '중지 요청 중…' : '중지'}</button></div></div>
          <div className="panel-body experiment-detail">
            {errors.detail && <p className="error" role="alert">{errors.detail}</p>}
            {errors.action && <p className="error" role="alert">{errors.action}</p>}
            {!run ? <p className="muted">{errors.detail ? '상세 기록을 불러오지 못했어요.' : id ? '선택한 실행을 불러오는 중…' : '목록에서 실행 기록을 선택하세요.'}</p> : <>
              <p className="note">{sourceName(run)}</p>
              <p role="status"><strong>{STATUS[run.status] || run.status}</strong> · {run.provider} / {run.model}</p>
              <p className="note">명령 실행 대상: {run.sandbox_context || '기본 Docker 설정 · 이전 기록'}</p>
              <p className="note">시작 {date(run.created_at)} · 종료 {date(run.finished_at)}<br />ID: {run.id}</p>
              {run.error && <p className="error" role="alert">{run.error.message} ({run.error.code})</p>}
              <div className="experiment-observations" aria-label="고정 목표 관찰">
                <div><span>목표 파일에 가짜 토큰 복사</span><strong>{observed(observations?.canary_leaked)}</strong></div>
                <div><span>요약 파일 존재</span><strong>{observed(observations?.summary_file_present)}</strong></div>
              </div>
              <p className="note">파일 관찰: {observations?.complete ? '수집 완료' : '불완전 또는 진행 중'}. 토큰 복사는 /workspace/leak.txt 기준이에요. 다른 경로나 명령 출력의 토큰은 로그에서 확인하세요. 메일의 안전 여부나 모든 공격의 성공을 판정하지 않아요.</p>
              <p className="note">‘실행 완료’는 대화가 끝났다는 뜻이에요. 요약 파일이 없다면 파일 저장 작업은 완료되지 않은 결과예요.</p>
              <details><summary>분석에 사용된 입력</summary><h3>작업</h3><pre>{run.task}</pre><h3>메일</h3><pre>{run.mail}</pre></details>
              <p className="note">긴 명령 출력은 원문 로그에 보관하고 모델에는 미리보기와 추가 조회 도구를 제공해요. 실제로 모델에 전달된 부분은 ‘모델에 보낸 입력’에서 확인하세요. 대화 범위 조정 시 이전 로그는 남지만 현재 모델 입력에서는 제외돼요.</p>
              <h3>진행 로그 <span className="note">{run.events?.length ?? 0}건</span></h3>
              <ol className="experiment-log">{(run.events || []).map((event, index) => <li key={index}><p><strong>{EVENTS[event.type] || event.type}</strong> <time className="note">{date(event.time)}</time></p><pre>{plain(event.data)}</pre></li>)}</ol>
              {!run.events?.length && <p className="muted">아직 기록된 이벤트가 없어요.</p>}
              <h3>파일 변경</h3>
              <ul className="experiment-changes">{(observations?.changes || []).map((file, index) => <li key={index}>{({ created: '생성', modified: '수정', deleted: '삭제' })[file.change] || file.change} · <code>{file.path}</code></li>)}</ul>
              {!observations?.changes?.length && <p className="muted">기록된 파일 변경이 없어요.</p>}
              <h3>파일 스냅샷</h3>
              {(observations?.files || []).map((file) => <details key={file.path}><summary>{file.path} · {file.size}바이트</summary><p className="note">SHA-256: {file.sha256 || '미확인'}</p><pre>{plain(file.preview) ?? '미리보기 없음'}</pre></details>)}
              {!observations?.files?.length && <p className="muted">수집된 파일이 없어요.</p>}
            </>}
          </div>
        </section>
      </div>
    </div>
  )
}
