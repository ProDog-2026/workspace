import { useEffect, useState } from 'react'
import { gmail } from './api'
import { useAuth } from './auth'
import { supabase } from './supabase'
import Admin from './Admin'
import Dashboard from './Dashboard'
import Experiment from './Experiment'
import Landing from './Landing'
import Login from './Login'
import Scan from './Scan'
import Settings from './Settings'
import States from './States'
import { DEFAULT_RULES } from './rules'
import { Logo, ThemeToggle } from './ui'

const MENU = [['', '대시보드'], ['states', '판정 안내'], ['mailbox', '로컬 수집'], ['scan', '메일 검사'], ['experiment', '동적 분석'], ['settings', '설정'], ['admin', '관리자']]
// 주소에 # 이 없으면(처음 들어왔을 때) 소개 화면.
// 단 Google OAuth 가 끝나면 Gmail 모듈이 /?connected=1 또는 /?error=코드 로 돌려보냄 -> 설정 화면으로
const currentRoute = () => new URLSearchParams(location.search).has('auth_callback') ? 'login' : location.hash !== '' ? location.hash.replace(/^#\/?/, '').split('?')[0]
  : /[?&](connected|error)=/.test(location.search) ? 'settings' : 'intro'

export default function App() {
  const auth = useAuth()
  const [logoutError, setLogoutError] = useState('')
  const [route, setRoute] = useState(currentRoute)
  // 관리자 페이지에서 바꾼 근거 탐지 룰이 메일 검사 데모에 바로 반영되도록 여기서 들고 있음
  const [rules, setRules] = useState(DEFAULT_RULES)
  const [email, setEmail] = useState(undefined)
  // 상단 연결 주소: 처음과 메뉴를 옮길 때마다 다시 읽음 (설정에서 연결·해제한 결과 반영)
  const loadEmail = () => gmail('/api/session').then((s) => setEmail(s.email)).catch(() => setEmail(null))
  useEffect(() => { if (!auth.loading) loadEmail() }, [auth.loading, auth.session?.user.id])

  useEffect(() => {
    const onChange = () => { setRoute(currentRoute()); loadEmail() }
    addEventListener('hashchange', onChange)
    return () => removeEventListener('hashchange', onChange)
  }, [])

  const login = <>{logoutError && <p className="note" role="alert">{logoutError}</p>}<Login auth={auth} /></>
  if (route === 'intro') return <Landing loggedIn={!!auth.session} checkingLogin={auth.loading} />
  if (route === 'login') return login
  if (auth.loading) return <main className="page"><p role="status">앱 로그인 상태를 확인하는 중…</p></main>
  const localRoute = ['mailbox', 'experiment', 'scan', 'states', 'admin', 'settings'].includes(route)
  if (!localRoute && (auth.loading || !auth.session)) return login
  const logout = async () => {
    setLogoutError('')
    let cleanupError = ''
    try {
      const session = await gmail('/api/session')
      await gmail('/api/logout', session.csrf_token)
    } catch (e) {
      cleanupError = `Gmail 연결 정리에 실패했어요. 백그라운드 수집·분석이 계속될 수 있어요. ${e.message}`
      setLogoutError(cleanupError)
    }
    try {
      const { error } = await supabase.auth.signOut({ scope: 'local' })
      if (error) throw error
      location.hash = '#/login'
    } catch {
      setLogoutError(`${cleanupError}${cleanupError ? ' ' : ''}앱 로그아웃하지 못했어요. 다시 시도하세요.`)
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <a href="#/intro" className="brand" aria-label="ProDog"><span className="brand-mark"><Logo size={40} /></span></a>
        <nav className="pillnav" aria-label="메뉴">
          {MENU.map(([path, label]) => (
            <a key={path} href={`#/${path}`} aria-current={route === path ? 'page' : undefined}>{label}</a>
          ))}
        </nav>
        <div className="top-right">
          <ThemeToggle />
          {auth.session && <button className="btn" onClick={logout}>앱 로그아웃</button>}
          <span className="gnb-user">{email === undefined ? '연결 확인 중' : email ?? 'Gmail 연결 안 됨'}</span>
          {email === undefined ? null : email
            ? <a className="btn" href="#/settings">연동 관리</a>
            : auth.session ? <a className="btn" href="#/settings">Gmail 연결</a> : <a className="btn" href="#/login">앱 로그인</a>}
        </div>
      </header>
      <main className="page" key={auth.session?.user.id ?? "local"}>
        {logoutError && <p className="note" role="alert">{logoutError}</p>}
        {route === 'scan' ? <Scan rules={rules} />
          : route === 'experiment' ? <Experiment />
          : route === 'states' ? <States />
          : route === 'settings' ? <Settings user={auth.session?.user} />
          : route === 'admin' ? <Admin rules={rules} setRules={setRules} />
          : route === 'mailbox' ? <Dashboard key="local-mailbox" local user={auth.session?.user} />
          : <Dashboard key={auth.session.user.id} user={auth.session.user} />}
      </main>
    </div>
  )
}
