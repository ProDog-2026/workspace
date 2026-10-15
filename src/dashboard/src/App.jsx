import { useEffect, useState } from 'react'
import Admin from './Admin'
import Dashboard from './Dashboard'
import Landing from './Landing'
import Login from './Login'
import Scan from './Scan'
import Settings from './Settings'
import States from './States'
import { scans } from './mock'
import { DEFAULT_RULES, DEFAULT_THRESHOLDS } from './rules'
import { Logo, fmtShort } from './ui'

const MENU = [['', '대시보드'], ['states', '판정 안내'], ['scan', '메일 검사'], ['settings', '설정'], ['admin', '관리자']]
// 주소에 # 이 없으면(처음 들어왔을 때) 소개 화면
const currentRoute = () => (location.hash === '' ? 'intro' : location.hash.replace(/^#\/?/, ''))

export default function App() {
  const [route, setRoute] = useState(currentRoute)
  // 관리자 페이지에서 바꾼 룰·기준이 메일 검사 데모에 바로 반영되도록 여기서 들고 있음 (새로고침하면 초기화)
  const [rules, setRules] = useState(DEFAULT_RULES)
  const [thresholds, setThresholds] = useState(DEFAULT_THRESHOLDS)

  useEffect(() => {
    const onChange = () => setRoute(currentRoute())
    addEventListener('hashchange', onChange)
    return () => removeEventListener('hashchange', onChange)
  }, [])

  if (route === 'intro') return <Landing />
  if (route === 'login') return <Login />

  return (
    <div className="app">
      <header className="topbar">
        <a href="#/intro" className="brand"><span className="brand-mark"><Logo size={18} /></span>ProDog</a>
        <nav className="pillnav" aria-label="메뉴">
          {MENU.map(([path, label]) => (
            <a key={path} href={`#/${path}`} aria-current={route === path ? 'page' : undefined}>{label}</a>
          ))}
        </nav>
        <div className="top-right">
          <span className="sync"><span className="dot" aria-hidden="true" />마지막 검사 {fmtShort(scans[0].received_at)}</span>
          <a className="btn" href="#/login">로그아웃</a>
        </div>
      </header>
      <main className="page">
        {route === 'scan' ? <Scan rules={rules} thresholds={thresholds} />
          : route === 'states' ? <States />
          : route === 'settings' ? <Settings />
          : route === 'admin' ? <Admin rules={rules} setRules={setRules} thresholds={thresholds} setThresholds={setThresholds} />
          : <Dashboard />}
      </main>
    </div>
  )
}
