import { useEffect, useState } from 'react'
import Admin from './Admin'
import Dashboard from './Dashboard'
import Login from './Login'
import Scan from './Scan'
import Settings from './Settings'
import States from './States'
import { DEFAULT_RULES, DEFAULT_THRESHOLDS } from './rules'

const NAV = [['', '대시보드'], ['scan', '메일 검사'], ['states', '판정 표시'], ['settings', '설정'], ['admin', '관리자']]
const currentRoute = () => location.hash.replace(/^#\/?/, '')

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

  if (route === 'login') return <Login />

  return (
    <>
      <nav className="nav">
        <a href="#/" className="brand">ProDog</a>
        {NAV.map(([path, label]) => (
          <a key={path} href={`#/${path}`} aria-current={route === path ? 'page' : undefined}>{label}</a>
        ))}
        <span className="nav-right small">demo@gmail.example · <a href="#/login">로그아웃</a></span>
      </nav>
      <main className="page">
        {route === 'scan' ? <Scan rules={rules} thresholds={thresholds} />
          : route === 'states' ? <States />
          : route === 'settings' ? <Settings />
          : route === 'admin' ? <Admin rules={rules} setRules={setRules} thresholds={thresholds} setThresholds={setThresholds} />
          : <Dashboard />}
      </main>
    </>
  )
}
