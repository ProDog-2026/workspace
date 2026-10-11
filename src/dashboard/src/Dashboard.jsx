import { useState } from 'react'
import { scans } from './mock'
import { TYPES } from './rules'
import { Mark, STATES, bySeverity, stateOf } from './ui'
import { MailDrawer, MailTable, shortType } from './Verdict'

const dayStart = (daysAgo) => {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - daysAgo)
  return d
}
const between = (rows, from, to) => rows.filter((r) => { const t = new Date(r.received_at); return t >= from && t < to })
const countOf = (rows, ...states) => rows.filter((r) => states.includes(stateOf(r))).length

// 판정 현황 줄 = 목록 필터 (스마트스토어센터 주문 현황처럼 숫자 칸을 누르면 아래 목록이 바뀜)
const STRIP = ['flagged', 'danger', 'suspicious', 'incomplete', 'failed', 'all']
const FILTERS = {
  flagged: { label: '위험·의심', states: ['danger', 'suspicious'] },
  ...Object.fromEntries(['danger', 'suspicious', 'incomplete', 'failed'].map((s) => [s, { label: STATES[s].label, states: [s] }])),
  all: { label: '전체', states: Object.keys(STATES) },
}
const LIMIT = 50 // ponytail: 위험도 순 50건만, 페이지네이션은 실데이터 붙이고 필요하면

export default function Dashboard() {
  const [days, setDays] = useState(7)
  const [filter, setFilter] = useState('flagged')
  const [selected, setSelected] = useState(null)

  const cur = between(scans, dayStart(days - 1), dayStart(-1))
  const matched = cur.filter((r) => FILTERS[filter].states.includes(stateOf(r))).sort(bySeverity)
  const typeCounts = Object.keys(TYPES)
    .map((key) => ({ key, label: shortType(key), n: cur.filter((r) => r.types.includes(key)).length }))
    .filter((d) => d.n > 0)
    .sort((a, b) => b.n - a.n)
    .slice(0, 5)

  return (
    <>
      <header className="page-head">
        <h1>대시보드</h1>
        <div className="ctl-row">
          <span id="period-label">조회 기간</span>
          <div className="btn-group" role="group" aria-labelledby="period-label">
            {[7, 30].map((d) => (
              <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)}>최근 {d}일</button>
            ))}
          </div>
        </div>
      </header>

      <section className="stat-strip" aria-label="판정별 메일 수">
        {STRIP.map((k) => {
          const n = countOf(cur, ...FILTERS[k].states)
          return (
            <button key={k} type="button" className="stat" aria-pressed={filter === k} onClick={() => setFilter(k)}>
              <span className="stat-label">{STATES[k] && <Mark v={k} size={14} />}{FILTERS[k].label}</span>
              <span className={`stat-num${k === 'danger' && n > 0 ? ' alert' : ''}`}>{n}<small>건</small></span>
              {k === 'all' && <span className="stat-sub">정상 {countOf(cur, 'safe')}건</span>}
            </button>
          )
        })}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{FILTERS[filter].label} 메일<span className="count">총 <b>{matched.length}</b>건</span></h2>
          {matched.length > LIMIT && <span className="note">위험한 순서로 {LIMIT}건까지 보여요.</span>}
        </div>
        <MailTable
          rows={matched.slice(0, LIMIT)}
          onSelect={setSelected}
          empty={filter === 'flagged' ? `최근 ${days}일 동안 위험하거나 의심되는 메일이 없어요.` : '해당하는 메일이 없어요.'}
        />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>자주 쓰인 숨김 수법</h2>
          <span className="note">메일 1건에 수법이 여러 개일 수 있어요.</span>
        </div>
        <div className="panel-body">
          {typeCounts.length
            ? <TypeBars data={typeCounts} />
            : <p className="muted">최근 {days}일 동안 찾은 숨김 수법이 없어요.</p>}
        </div>
      </section>

      {selected && <MailDrawer r={selected} onClose={() => setSelected(null)} />}
    </>
  )
}

function TypeBars({ data }) {
  const max = data[0].n
  return (
    <ul className="hbars">
      {data.map((d) => (
        <li key={d.key}>
          <span>{d.label}</span>
          <div className="hbar-track"><div className="hbar" style={{ width: `${(d.n / max) * 100}%` }} /></div>
          <span className="num">{d.n}건</span>
        </li>
      ))}
    </ul>
  )
}
