import { useEffect, useState } from 'react'
import { scans } from './mock'
import { TYPES } from './rules'
import { STATES, fmtTime, stateOf } from './ui'
import { MailTable, VerdictDetail } from './Verdict'

const dayStart = (daysAgo) => {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - daysAgo)
  return d
}
const between = (rows, from, to) => rows.filter((r) => { const t = new Date(r.received_at); return t >= from && t < to })
const countOf = (rows, ...states) => rows.filter((r) => states.includes(stateOf(r))).length
const FILTERS = {
  flagged: { label: '탐지됨', states: ['danger', 'suspicious'] },
  danger: { label: '위험', states: ['danger'] },
  suspicious: { label: '의심', states: ['suspicious'] },
  attention: { label: '확인 필요', states: ['incomplete', 'failed'] },
  all: { label: '전체', states: Object.keys(STATES) },
}

export default function Dashboard() {
  const [days, setDays] = useState(7)
  const [filter, setFilter] = useState('flagged')
  const [selected, setSelected] = useState(null)

  const tomorrow = dayStart(-1)
  const cur = between(scans, dayStart(days - 1), tomorrow)
  const prev = between(scans, dayStart(days * 2 - 1), dayStart(days - 1))

  const daily = Array.from({ length: days }, (_, i) => {
    const from = dayStart(days - 1 - i)
    const rows = between(cur, from, dayStart(days - 2 - i))
    return { label: `${from.getMonth() + 1}/${from.getDate()}`, danger: countOf(rows, 'danger'), suspicious: countOf(rows, 'suspicious') }
  })

  const typeCounts = Object.entries(TYPES)
    .map(([key, label]) => ({ key, label, n: cur.filter((r) => r.types.includes(key)).length }))
    .sort((a, b) => b.n - a.n)

  const tableRows = cur
    .filter((r) => FILTERS[filter].states.includes(stateOf(r)))
    .slice(0, 50) // ponytail: 최근 50건만, 페이지네이션은 실데이터 붙이고 필요하면

  return (
    <>
      <header className="top">
        <div>
          <h1>대시보드</h1>
          <p className="muted">마지막 검사 {fmtTime(scans[0].received_at)}</p>
        </div>
        <div className="seg" role="group" aria-label="기간">
          {[7, 30].map((d) => (
            <button key={d} aria-pressed={days === d} onClick={() => setDays(d)}>최근 {d}일</button>
          ))}
        </div>
      </header>

      <section className="kpis">
        <Kpi label="검사한 메일" value={cur.length} prev={prev.length} />
        {['danger', 'suspicious', 'safe'].map((v) => (
          <Kpi key={v} label={`${STATES[v].icon} ${STATES[v].label}`} value={countOf(cur, v)} prev={countOf(prev, v)} upIsBad={v !== 'safe'} status={v} />
        ))}
        <Kpi label="◐ 확인 필요" value={countOf(cur, 'incomplete', 'failed')} prev={countOf(prev, 'incomplete', 'failed')} upIsBad status="attention" />
      </section>

      <section className="grid">
        <div className="card">
          <h2>일별 탐지</h2>
          <Legend />
          <DailyChart data={daily} />
        </div>
        <div className="card">
          <h2>인젝션 유형</h2>
          <p className="muted small">탐지 메일 1건에 유형이 여러 개일 수 있음</p>
          <TypeBars data={typeCounts} />
        </div>
      </section>

      <section className="card">
        <div className="row">
          <h2>메일 목록</h2>
          <div className="seg" role="group" aria-label="판정 필터">
            {Object.entries(FILTERS).map(([k, { label }]) => (
              <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{label}</button>
            ))}
          </div>
        </div>
        <MailTable rows={tableRows} onSelect={setSelected} />
      </section>

      {selected && <Detail r={selected} onClose={() => setSelected(null)} />}
    </>
  )
}

function Kpi({ label, value, prev, upIsBad, status }) {
  const diff = value - prev
  const tone = !upIsBad || diff === 0 ? '' : diff > 0 ? 'bad' : 'good'
  return (
    <div className={`card kpi ${status ?? ''}`}>
      <div className="muted">{label}</div>
      <div className="kpi-value">{value.toLocaleString()}</div>
      <div className={`small delta ${tone}`}>
        {diff > 0 ? '▲' : diff < 0 ? '▼' : '–'} {Math.abs(diff)} <span className="muted">이전 기간 대비</span>
      </div>
    </div>
  )
}

function Legend() {
  return (
    <div className="legend small">
      <span><i className="sw danger" /> 위험</span>
      <span><i className="sw suspicious" /> 의심</span>
    </div>
  )
}

function DailyChart({ data }) {
  const max = Math.max(2, ...data.map((d) => d.danger + d.suspicious))
  const top = Math.ceil(max / 2) * 2
  return (
    <div className="chart">
      <div className="ticks">
        {[top, top / 2, 0].map((t) => <span key={t}>{t}</span>)}
      </div>
      <div className="plot">
        {data.map((d, i) => (
          <div key={d.label} className="col" tabIndex={0}>
            <div className="stack" style={{ height: `${((d.danger + d.suspicious) / top) * 100}%` }}>
              {d.danger > 0 && <div className="seg-bar danger" style={{ flex: d.danger }} />}
              {d.suspicious > 0 && <div className="seg-bar suspicious" style={{ flex: d.suspicious }} />}
            </div>
            <div className="tip">
              <b>{d.label}</b><br />위험 {d.danger} · 의심 {d.suspicious}
            </div>
            <span className="x">{data.length <= 7 || i % 5 === 0 || i === data.length - 1 ? d.label : ''}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function TypeBars({ data }) {
  const max = Math.max(1, ...data.map((d) => d.n))
  return (
    <ul className="hbars">
      {data.map((d) => (
        <li key={d.key}>
          <span className="small">{d.label}</span>
          <div className="hbar-track"><div className="hbar" style={{ width: `${(d.n / max) * 100}%` }} /></div>
          <span className="num small">{d.n}</span>
        </li>
      ))}
    </ul>
  )
}

function Detail({ r, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()} aria-label="메일 상세">
        <div className="row">
          <h2>{r.subject}</h2>
          <button className="close" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        <p className="muted small">{r.sender} · {fmtTime(r.received_at)}</p>
        <VerdictDetail r={r} />
      </aside>
    </div>
  )
}
