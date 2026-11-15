import { lazy, Suspense, useEffect, useState } from 'react'
import { BookOpenCheck, FlaskConical, LayoutDashboard, ScanSearch, Settings as SettingsIcon, ShieldCheck } from 'lucide-react'
import { gmail } from './api'
import Admin from './Admin'
import Dashboard from './Dashboard'
import Landing from './Landing'
import Login from './Login'
import Scan from './Scan'
import Settings from './Settings'
import States from './States'
import { DEFAULT_RULES } from './rules'
import { Logo, ThemeToggle } from './ui'

const Dynamic = lazy(() => import('./Dynamic'))
const MENU = [['', '대시보드', LayoutDashboard], ['states', '판정 안내', BookOpenCheck], ['scan', '메일 검사', ScanSearch], ['dynamic', '동적 검사', FlaskConical], ['settings', '설정', SettingsIcon], ['admin', '관리자', ShieldCheck]]
// 주소에 # 이 없으면(처음 들어왔을 때) 소개 화면.
// 단 Google OAuth 가 끝나면 Gmail 모듈이 /?connected=1 또는 /?error=코드 로 돌려보냄 -> 설정 화면으로
const currentRoute = () => location.hash !== '' ? location.hash.split('?')[0].replace(/^#\/?/, '')
  : /[?&](connected|error)=/.test(location.search) ? 'settings' : 'intro'

export default function App() {
  const [route, setRoute] = useState(currentRoute)
  // 관리자 페이지에서 바꾼 근거 탐지 룰이 메일 검사 데모에 바로 반영되도록 여기서 들고 있음
  const [rules, setRules] = useState(DEFAULT_RULES)
  const [email, setEmail] = useState(undefined)
  // 상단 연결 주소: 처음과 메뉴를 옮길 때마다 다시 읽음 (설정에서 연결·해제한 결과 반영)
  const loadEmail = () => gmail('/api/session').then((s) => setEmail(s.email)).catch(() => setEmail(null))
  useEffect(() => { loadEmail() }, [])

  useEffect(() => {
    const onChange = () => { setRoute(currentRoute()); loadEmail() }
    addEventListener('hashchange', onChange)
    return () => removeEventListener('hashchange', onChange)
  }, [])

  if (route === 'intro') return <Landing />
  if (route === 'login') return <Login />

  return (
    <div className="app">
      <aside className="sidebar">
        <a href="#/intro" className="brand" aria-label="ProDog"><span className="brand-mark"><Logo size={36} /></span></a>
        <nav className="sidenav" aria-label="메뉴">
          {MENU.map(([path, label, Icon]) => (
            <a key={path} href={`#/${path}`} aria-current={route === path ? 'page' : undefined}><Icon size={18} strokeWidth={1.75} aria-hidden="true" />{label}</a>
          ))}
        </nav>
        <div className="side-foot">
          <span className="side-user">{email === undefined ? '연결 확인 중' : email ?? 'Gmail 연결 안 됨'}</span>
          <div className="side-actions">
            {email === undefined ? null : email
              ? <a className="btn nav-cta" href="#/settings">연동 관리</a>
              : <a className="btn nav-cta" href="#/login">로그인</a>}
            <ThemeToggle />
          </div>
        </div>
      </aside>
      <main className="page">
        {route === 'scan' ? <Scan rules={rules} />
          : route === 'states' ? <States />
          : route === 'settings' ? <Settings />
          : route === 'admin' ? <Admin rules={rules} setRules={setRules} />
          : route === 'dynamic' ? <Suspense fallback={<p className="empty">동적 검사 페이지를 불러오는 중…</p>}><Dynamic /></Suspense>
          : <Dashboard />}
      </main>
    </div>
  )
}
