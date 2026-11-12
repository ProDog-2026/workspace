import { useEffect, useState } from 'react'
import { signIn } from './auth'
import { supabase } from './supabase'
import { Logo } from './ui'

// Google 공식 Sign-In 브랜딩의 'G' (색·모양 변경 금지)
function GoogleG() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  )
}

function LockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" focusable="false">
      <rect x="3" y="7" width="10" height="7" rx="1.5" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
    </svg>
  )
}

export default function Login({ auth }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (auth.session) location.hash = '#/settings'
  }, [auth.session])
  const connect = async () => {
    setBusy(true)
    setError('')
    try { await signIn() }
    catch (e) { setError(e.message); setBusy(false) }
  }

  return (
    <main className="login">
      <div className="login-wrap">
        <a className="login-logo" href="#/intro"><Logo size={28} />ProDog</a>
        <div className="login-box">
          <h1>로그인</h1>
          <p className="login-sub">Google 계정으로 검사 결과를 확인하세요.</p>
          <button type="button" className="gbtn" disabled={auth.loading || busy || !supabase} onClick={connect}>
            <GoogleG />{auth.loading ? '로그인 상태 확인 중…' : busy ? 'Google로 이동 중…' : 'Google 계정으로 로그인'}
          </button>
          {(error || auth.error) && <p className="note error" role="alert">{error || auth.error}</p>}
          <p className="fine"><LockIcon />Gmail 읽기 권한은 로그인 후 별도로 동의해요.</p>
        </div>
        <p className="fine"><a href="#/mailbox">로컬 수집</a></p>
        <p className="copy"><span>이용약관</span><b>개인정보처리방침</b><span>© 2026 ProDog 캡스톤 팀</span></p>
      </div>
    </main>
  )
}
