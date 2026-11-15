import { useEffect, useState } from 'react'
import { gmail } from './api'

// Gmail 연동만 실제 동작. 나머지 설정은 데모라 저장 안 됨 (실제로는 Supabase 사용자 설정 테이블)
export default function Settings() {
  const [session, setSession] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const run = async (job) => {
    setBusy(true)
    setError('')
    try { await job() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const refresh = async () => setSession(await gmail('/api/session'))
  useEffect(() => {
    const code = new URLSearchParams(location.search).get('error')
    if (location.search) history.replaceState(null, '', '/' + (location.hash || '#/settings'))
    run(async () => {
      await refresh()
      if (code) throw new Error(`Google 연결에 실패했어요 (${code}). 다시 시도해 주세요.`)
    })
  }, [])

  // POST 직전에 세션을 다시 읽어 CSRF 토큰을 받음. 첫 방문 때 상단(App)도 /api/session 을 동시에 불러
  // 세션 쿠키가 하나 더 생기면, 화면에 들고 있던 토큰은 쿠키와 안 맞아 csrf_failed 가 남
  const post = async (path) => gmail(path, (await gmail('/api/session')).csrf_token)
  const connect = () => run(async () => {
    if (session.mode === 'demo') {
      await post('/api/demo/connect')
      return refresh()
    }
    const { authorization_url } = await post('/api/oauth/google/start')
    location.assign(authorization_url)
  })
  const disconnect = () => run(async () => {
    await post('/api/disconnect')
    await refresh()
  })
  const switchAccount = () => run(async () => {
    await post('/api/logout')
    location.hash = '#/login'
  })

  const connected = session?.authenticated && !session.needs_reconnect

  return (
    <>
      <header className="page-head"><h1>설정<span className="tag">{session?.mode === 'google' ? 'Gmail 연동' : '데모'}</span></h1></header>
      <p className="note page-note">Gmail 연동 외의 값은 데모라 저장되지 않아요.</p>

      <section className="panel settings">
        <table className="form-table">
          <tbody>
            <tr>
              <th scope="row">Gmail 연동</th>
              <td><div className="cell">
                {!session ? (
                  <span className="muted">{busy ? '연결 상태 확인 중…' : '연결 상태를 알 수 없어요.'}</span>
                ) : session.cleanup_pending ? (
                  <>
                    <span className="muted">연결 해제가 끝나지 않았어요. 다시 시도하면 남은 데이터를 정리해요.</span>
                    <button type="button" className="btn primary" disabled={busy} onClick={disconnect}>연결 해제 다시 시도</button>
                    <button type="button" className="btn" disabled={busy} onClick={switchAccount}>다른 계정으로 로그인</button>
                  </>
                ) : connected ? (
                  <>
                    <span>{session.email}</span>
                    <span className="tag ro">읽기 전용</span>
                    <button type="button" className="btn" disabled={busy} onClick={disconnect}>연동 해제</button>
                    {session.mode === 'google' && <button type="button" className="btn" disabled={busy} onClick={switchAccount}>다른 계정으로 로그인</button>}
                  </>
                ) : (
                  <>
                    <span className="muted">{session.needs_reconnect ? 'Gmail 권한이 만료됐어요. 다시 연결해 주세요.' : '연결된 계정이 없어요.'}</span>
                    <button type="button" className="btn primary" disabled={busy || !session.configured} onClick={connect}>
                      {session.needs_reconnect ? '다시 연결하기' : 'Gmail 연결하기'}
                    </button>
                  </>
                )}
              </div>
              {error && <p className="note" role="alert">{error}</p>}
              </td>
            </tr>
            <tr>
              <th scope="row">자동 검사</th>
              <td><label className="sw"><input type="checkbox" role="switch" defaultChecked />새 메일이 오면 바로 검사</label></td>
            </tr>
            <tr>
              <th scope="row"><label htmlFor="set-interval">검사 주기</label></th>
              <td><select id="set-interval" defaultValue="15">
                <option value="0">실시간</option>
                <option value="15">15분마다</option>
                <option value="60">1시간마다</option>
              </select></td>
            </tr>
            <tr>
              <th scope="row">위험 메일 알림</th>
              <td><label className="sw"><input type="checkbox" role="switch" defaultChecked />위험 메일을 찾으면 팝업 알림</label></td>
            </tr>
            <tr>
              <th scope="row">의심 메일 알림</th>
              <td><label className="sw"><input type="checkbox" role="switch" />의심 메일도 팝업 알림</label></td>
            </tr>
            <tr>
              <th scope="row"><label htmlFor="set-report">주간 리포트</label></th>
              <td><select id="set-report" defaultValue="weekly">
                <option value="off">받지 않음</option>
                <option value="weekly">매주 월요일</option>
              </select></td>
            </tr>
            <tr>
              <th scope="row"><label htmlFor="set-keep">기록 보관 기간</label></th>
              <td><select id="set-keep" defaultValue="90">
                <option value="30">30일</option>
                <option value="90">90일</option>
                <option value="365">1년</option>
              </select></td>
            </tr>
          </tbody>
        </table>
      </section>
    </>
  )
}
