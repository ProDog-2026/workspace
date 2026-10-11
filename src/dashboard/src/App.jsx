import { Fragment, useEffect, useState } from 'react'
import Admin from './Admin'
import Dashboard from './Dashboard'
import Login from './Login'
import Scan from './Scan'
import Settings from './Settings'
import States from './States'
import { scans } from './mock'
import { DEFAULT_RULES, DEFAULT_THRESHOLDS } from './rules'
import { Logo, fmtShort } from './ui'

const MENU = [
  ['메일', [['', '대시보드'], ['states', '판정 안내'], ['scan', '메일 검사']]],
  ['관리', [['settings', '설정'], ['admin', '관리자']]],
]
const ITEMS = MENU.flatMap(([group, items]) => items.map(([path, label]) => ({ group, path, label })))
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
  const here = ITEMS.find((m) => m.path === route) ?? ITEMS[0]

  return (
    <div className="app">
      <header className="gnb">
        <a href="#/" className="brand"><Logo />ProDog<span className="brand-sub">메일 보안</span></a>
        <div className="gnb-right">
          <span className="sync"><span className="dot" aria-hidden="true" />마지막 검사 {fmtShort(scans[0].received_at)}</span>
          <span className="gnb-user">demo@gmail.example</span>
          <a className="btn-gnb" href="#/login">로그아웃</a>
        </div>
      </header>
      <nav className="lnb" aria-label="메뉴">
        {MENU.map(([group, items]) => (
          <Fragment key={group}>
            <p className="lnb-group">{group}</p>
            {items.map(([path, label]) => (
              <a key={path} href={`#/${path}`} aria-current={route === path ? 'page' : undefined}>{label}</a>
            ))}
          </Fragment>
        ))}
      </nav>
      <main className="page">
        <ol className="crumbs" aria-label="현재 위치"><li>{here.group}</li><li aria-current="page">{here.label}</li></ol>
        {route === 'scan' ? <Scan rules={rules} thresholds={thresholds} />
          : route === 'states' ? <States />
          : route === 'settings' ? <Settings />
          : route === 'admin' ? <Admin rules={rules} setRules={setRules} thresholds={thresholds} setThresholds={setThresholds} />
          : <Dashboard />}
      </main>
    </div>
  )
}
