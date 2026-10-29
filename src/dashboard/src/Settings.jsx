import { useEffect, useRef, useState } from 'react'
import { DETECTOR_NAMES, gmail } from './api'

export default function Settings({ user }) {
  const [session, setSession] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [settings, setSettings] = useState(null)
  const [enabled, setEnabled] = useState(false)
  const [model, setModel] = useState('')
  const [settingsError, setSettingsError] = useState('')
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const lifetime = useRef(null)
  const savingNow = useRef(false)
  const oauthError = useRef(new URLSearchParams(location.search).get('error'))

  const refresh = async (signal) => {
    const next = await gmail('/api/session', undefined, undefined, { signal })
    if (!signal.aborted) setSession(next)
  }
  useEffect(() => {
    const controller = new AbortController()
    lifetime.current = controller
    const code = oauthError.current
    if (location.search) history.replaceState(null, '', '/' + (location.hash || '#/settings'))
    setBusy(true)
    refresh(controller.signal).then(() => {
      if (code && !controller.signal.aborted) setError(`Google 연결에 실패했어요 (${code}). 다시 시도해 주세요.`)
    }).catch((e) => {
      if (!controller.signal.aborted) setError(e.message)
    }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [])

  const connected = !!session?.authenticated && !session.needs_reconnect
  useEffect(() => {
    setSettings(null)
    setSettingsError('')
    setSaved(false)
    setEnabled(false)
    setModel('')
    if (!connected) return
    const controller = new AbortController()
    gmail('/api/analysis/settings', undefined, undefined, { signal: controller.signal }).then((data) => {
      if (controller.signal.aborted) return
      setSettings(data)
      setEnabled(data.enabled)
      setModel(data.model)
    }).catch((e) => { if (!controller.signal.aborted) setSettingsError(e.message) })
    return () => controller.abort()
  }, [connected, session?.email])

  // 쓰기 직전에 세션을 다시 읽어 현재 쿠키와 맞는 CSRF 토큰을 받는다.
  const post = async (path, signal, body) => {
    const fresh = await gmail('/api/session', undefined, undefined, { signal })
    if (signal.aborted) return
    if (!fresh.csrf_token) throw new Error('세션을 확인하지 못했어요. 다시 접속해 주세요.')
    return gmail(path, fresh.csrf_token, body, { signal })
  }
  const run = async (job) => {
    const signal = lifetime.current.signal
    setBusy(true)
    setError('')
    try { await job(signal) }
    catch (e) { if (!signal.aborted) setError(e.message) }
    finally { if (!signal.aborted) setBusy(false) }
  }
  const connect = () => run(async (signal) => {
    if (session.mode === 'demo') {
      await post('/api/demo/connect', signal)
      if (!signal.aborted) await refresh(signal)
      return
    }
    const data = await post('/api/oauth/google/start', signal)
    if (!signal.aborted) location.assign(data.authorization_url)
  })
  const disconnect = () => run(async (signal) => {
    await post('/api/disconnect', signal)
    if (!signal.aborted) await refresh(signal)
  })
  const save = async (e) => {
    e.preventDefault()
    if (savingNow.current || !settings || !connected) return
    savingNow.current = true
    const signal = lifetime.current.signal
    setSaving(true)
    setSaved(false)
    setSettingsError('')
    try {
      await post('/api/analysis/settings', signal, { enabled, model })
      if (signal.aborted) return
      const data = await gmail('/api/analysis/settings', undefined, undefined, { signal })
      if (signal.aborted) return
      setSettings(data)
      setEnabled(data.enabled)
      setModel(data.model)
      setSaved(true)
    } catch (e) { if (!signal.aborted) setSettingsError(e.message) }
    finally {
      savingNow.current = false
      if (!signal.aborted) setSaving(false)
    }
  }

  return (
    <>
      <header className="page-head"><h1>설정{session && <span className="tag">{session.mode === 'google' ? 'Gmail 연동' : '데모'}</span>}</h1></header>
      <section className="panel settings">
        <div className="panel-head"><h2>앱 로그인 · Gmail 연결</h2></div>
        <div className="panel-body">
          {user ? <p>ProDog 로그인: {user.email} · <a href="#/">저장된 검사 결과 보기</a></p>
            : <p>Supabase 검사 결과를 보려면 <a href="#/login">Google 계정으로 앱에 로그인</a>하세요.</p>}
          <p className="note">앱 로그인 후 아래에서 Gmail 읽기 권한을 연결하세요. 현재 수집 서버는 이 컴퓨터의 로컬 연결을 사용해요. Supabase 사용자와 연결을 묶고 결과를 저장하는 서버 연동은 아직 적용되지 않았어요.</p>
          <p><a className="btn" href="#/mailbox">로컬 메일 수집·분석으로 이동</a></p>
        </div>
      </section>
      <section className="panel settings">
        <table className="form-table"><tbody><tr>
          <th scope="row">Gmail 연동</th>
          <td><div className="cell">
            {!session ? <span className="muted">{busy ? '연결 상태 확인 중…' : '연결 상태를 알 수 없어요.'}</span>
              : connected ? <>
                <span>{session.email}</span><span className="tag ro">읽기 전용</span>
                <button type="button" className="btn" disabled={busy || saving} onClick={disconnect}>연동 해제</button>
              </> : <>
                <span className="muted">{session.needs_reconnect ? 'Gmail 권한이 만료됐어요. 다시 연결해 주세요.' : '연결된 계정이 없어요.'}</span>
                <button type="button" className="btn primary" disabled={busy || !session.configured} onClick={connect}>{session.needs_reconnect ? '다시 연결하기' : 'Gmail 연결하기'}</button>
              </>}
          </div>{error && <p className="note" role="alert">{error}</p>}</td>
        </tr></tbody></table>
      </section>

      <section className="panel settings">
        <div className="panel-head"><h2>자동 분석</h2></div>
        {!connected ? <p className="panel-body muted">Gmail을 연결하면 계정별 자동 분석 설정을 사용할 수 있어요.</p>
          : <form onSubmit={save}>
            <table className="form-table"><tbody>
              <tr><th scope="row">명령 실행 환경</th><td>
                {!settings ? '확인 중…' : settings.sandbox ? <>
                  <span>{settings.sandbox.ready ? '준비됨' : '준비 안 됨'} · 실행 대상: {settings.sandbox.context}</span>
                  <p className="sub">{settings.sandbox.message}</p>
                </> : '실행 환경 정보를 받지 못했어요. 서버를 재시작하세요.'}
              </td></tr>
              <tr><th scope="row">정적 탐지기</th><td>
                {!settings ? settingsError ? '설정을 불러오지 못했어요.' : '준비 상태 확인 중…' : <>
                  <span>{settings.detector?.ready ? '준비됨' : '준비 안 됨'}</span>
                  {settings.detector?.mode && <p className="sub">{settings.detector.mode === 'ensemble' ? 'ML + Transformer 앙상블' : settings.detector.mode === 'lexical' ? '기존 ML(해시 특징) 단독' : settings.detector.mode}</p>}
                  {settings.detector?.components?.length > 0 && <p className="sub">구성: {settings.detector.components.map((id) => DETECTOR_NAMES[id] ?? id).join(', ')}</p>}
                  {settings.detector?.model_id && <p className="sub">{settings.detector.model_id}</p>}
                  {settings.detector?.message && <p className="note">{settings.detector.message}</p>}
                  <p className="sub">정적 탐지기는 모델 파일과 전용 실행 환경이 필요해요. 준비됨 표시는 실행 환경과 파일 확인 결과이며, 실제 모델 로딩과 추론 성공은 분석 시 확인해요. Ollama 모델 설치 상태와는 별개예요.</p>
                </>}
              </td></tr>
              <tr><th scope="row"><label htmlFor="analysis-model">Ollama 모델</label></th><td>
                <select id="analysis-model" value={model} disabled={!settings || busy || saving} required={enabled} onChange={(e) => { setModel(e.target.value); setSaved(false) }}>
                  <option value="">모델을 선택하세요</option>
                  {model && !settings?.ollama_models.includes(model) && <option value={model} disabled>{model} (설치 목록에 없음)</option>}
                  {(settings?.ollama_models ?? []).map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
                {settings && !settings.ollama_models.length && <p className="note">설치된 Ollama 모델이 없어요.</p>}
                <p className="sub">로컬 Qwen 등 도구 호출을 지원하는 모델을 선택하세요. 저장할 때 서버에서 지원 여부를 확인해요.</p>
              </td></tr>
              <tr><th scope="row">자동 분석 사용</th><td>
                <label className="sw"><input type="checkbox" role="switch" checked={enabled} disabled={!settings || busy || saving} onChange={(e) => { setEnabled(e.target.checked); setSaved(false) }} />의심·위험 메일을 Ollama로 자동 분석</label>
                <p className="sub">로컬 수집 화면에서 불러온 메일을 정적 분석한 뒤 최종 의심·위험 판정만 선택한 로컬 Ollama 모델로 자동 전달해요. 앙상블에서는 각 모델의 기준으로 판정한 뒤 가장 높은 위험 단계를 사용해요.</p>
              </td></tr>
              {settings?.task && <tr><th scope="row">분석 작업</th><td>{settings.task}</td></tr>}
            </tbody></table>
            <div className="panel-body">
              <button type="submit" className="btn primary" disabled={!settings || busy || saving || (enabled && (!model || !settings.ollama_models.includes(model)))}>{saving ? '저장 중…' : '설정 저장'}</button>
              {settingsError && <p className="note" role="alert">{settingsError}</p>}
              {saved && <p className="note" role="status">설정을 저장했어요. 다음 로컬 수집 화면 진입 시 메일을 불러오며 적용해요.</p>}
            </div>
          </form>}
      </section>
    </>
  )
}
