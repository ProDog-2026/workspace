import { useEffect, useId, useState } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { gmail, toRow } from './api'
import { TYPES } from './rules'
import { Mark, STATES, bySeverity, stateOf } from './ui'
import { MailDrawer, MailTable, shortType } from './Verdict'
import useDynamicInbox from './useDynamicInbox'

const dayStart = (daysAgo) => {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - daysAgo)
  return d
}
const between = (rows, from, to) => rows.filter((r) => { const t = new Date(r.received_at); return t >= from && t < to })
const countOf = (rows, ...states) => rows.filter((r) => states.includes(stateOf(r))).length

// 판정 현황 줄 = 목록 필터 (스마트스토어센터 주문 현황처럼 숫자 칸을 누르면 아래 목록이 바뀜)
// 위험·의심 합계는 강조 카드(feature)로 따로 뺌. 나머지 6칸이 Stats Row
const STRIP = ['danger', 'suspicious', 'incomplete', 'failed', 'pending', 'all']
const FILTERS = {
  flagged: { label: '위험·의심', states: ['danger', 'suspicious'] },
  ...Object.fromEntries(['danger', 'suspicious', 'incomplete', 'failed', 'pending'].map((s) => [s, { label: STATES[s].label, states: [s] }])),
  all: { label: '전체', states: Object.keys(STATES) },
}
// ponytail: 받은편지함 최신 20건만. 메일마다 Gmail 본문 조회가 1번씩 나가서 50건이면 Gmail 초당 한도에 걸림.
// 더 보려면 '더 불러오기'로 next_page_token 을 넘겨 다음 20건을 이어 받음
const LIMIT = 20

export default function Dashboard() {
  const [days, setDays] = useState(null) // null = 전체 기간
  const [filter, setFilter] = useState('all')
  const [selected, setSelected] = useState(null)
  const [scans, setScans] = useState(null)
  const [error, setError] = useState(null)
  const [next, setNext] = useState(null) // Gmail 다음 페이지 토큰. 없으면 끝
  const [more, setMore] = useState({ busy: false, error: '' })
  const dynamicNotice = useDynamicInbox(scans, setScans)

  const load = (token) => gmail(`/api/mails?limit=${LIMIT}` + (token ? `&page_token=${encodeURIComponent(token)}` : ''))
    .then((d) => {
      setScans((prev) => [...new Map([...(prev ?? []), ...d.messages.map(toRow)].map((row) => [row.id, row])).values()])
      setNext(d.next_page_token)
    })
  useEffect(() => { load().catch(setError) }, [])
  const loadMore = () => {
    setMore({ busy: true, error: '' })
    load(next).then(() => setMore({ busy: false, error: '' }), (e) => setMore({ busy: false, error: e.message }))
  }

  if (error || !scans) {
    return (
      <>
        <header className="page-head"><h1>대시보드</h1></header>
        <section className="panel"><p className="empty">
          {!error ? '받은편지함을 불러오는 중…'
            : error.code === 'authentication_required'
              ? <>연결된 Gmail이 없어요. <a href="#/settings">설정</a>에서 Gmail을 연결하세요.</>
              : error.message}
        </p></section>
      </>
    )
  }

  // days = null 이면 전체 기간 = 지금까지 불러온 메일 전부 (더 오래된 메일은 '더 불러오기'로 이어 받음)
  const cur = days ? between(scans, dayStart(days - 1), dayStart(-1)) : scans
  const period = days ? `최근 ${days}일` : '전체 기간'
  // 그래프 칸 수: 전체면 가장 오래된 메일 날짜부터 오늘까지. ponytail: 막대가 너무 가늘어지지 않게 최대 90일
  const oldest = Math.min(...scans.filter((r) => r.received_at).map((r) => new Date(r.received_at)))
  const span = days ?? Math.min(90, Math.max(7, Math.ceil((dayStart(-1) - oldest) / 86400000)))
  const matched = cur.filter((r) => FILTERS[filter].states.includes(stateOf(r))).sort(bySeverity)
  const typeCounts = Object.keys(TYPES)
    .map((key) => ({ key, label: shortType(key), n: cur.filter((r) => r.types.includes(key)).length }))
    .filter((d) => d.n > 0)
    .sort((a, b) => b.n - a.n)
    .slice(0, 5)
  const daily = Array.from({ length: span }, (_, i) => {
    const from = dayStart(span - 1 - i)
    const rows = between(cur, from, dayStart(span - 2 - i))
    const last = i === span - 1
    return {
      key: from.toISOString(),
      label: last ? '오늘' : `${from.getMonth() + 1}월 ${from.getDate()}일`,
      short: last ? '오늘' : `${from.getMonth() + 1}/${from.getDate()}`,
      danger: countOf(rows, 'danger'),
      suspicious: countOf(rows, 'suspicious'),
    }
  })
  const flaggedTotal = countOf(cur, 'danger', 'suspicious')

  return (
    <>
      <header className="page-head">
        <div><p className="eyebrow">받은편지함 현황</p><h1>대시보드</h1></div>
        <div className="ctl-row">
          <span id="period-label">조회 기간</span>
          <div className="btn-group" role="group" aria-labelledby="period-label">
            {[7, 30, null].map((d) => (
              <button key={d ?? 'all'} type="button" aria-pressed={days === d} onClick={() => setDays(d)}>{d ? `최근 ${d}일` : '전체'}</button>
            ))}
          </div>
        </div>
      </header>

      {dynamicNotice && <p className="dynamic-notice" role="status">{dynamicNotice} <a href="#/dynamic">동적 검사 설정</a></p>}

      <section className="overview">
      <button type="button" className="feature" aria-pressed={filter === 'flagged'} onClick={() => setFilter('flagged')}>
        <span className="feature-label">{period} 위험·의심 메일</span>
        <span className="feature-num">{flaggedTotal}<small>건</small></span>
        <Sparkline data={daily} />
        <span className="feature-sub">누르면 아래 목록에 이 메일만 보여요</span>
      </button>
      <div className="stat-strip" role="group" aria-label="판정별 메일 수">
        {STRIP.map((k) => {
          const n = countOf(cur, ...FILTERS[k].states)
          return (
            <button key={k} type="button" className="stat" aria-pressed={filter === k} onClick={() => setFilter(k)}>
              <span className="stat-label">{STATES[k] && <Mark v={k} size={14} />}{FILTERS[k].label}</span>
              <span className={`stat-num${n > 0 && (k === 'danger' || k === 'suspicious') ? ` alert-${k}` : ''}`}>{n}<small>건</small></span>
              {k === 'all' && <span className="stat-sub">정상 {countOf(cur, 'safe')}건</span>}
            </button>
          )
        })}
      </div>
      </section>

      <section className="chart-row">
        <div className="panel">
          <div className="panel-head">
            <div><h2>일별 탐지</h2><p className="panel-desc">{period} 위험·의심 메일 추이</p></div>
            <div className="legend" aria-hidden="true"><span><i className="danger" />위험</span><span><i className="suspicious" />의심</span></div>
          </div>
          <div className="panel-body"><DailyChart data={daily} /></div>
        </div>
        <div className="panel">
          <div className="panel-head">
            <h2>자주 쓰인 숨김 수법</h2>
          </div>
          <div className="panel-body">
            {typeCounts.length
              ? <TypeBars data={typeCounts} />
              : <p className="muted">{period} 동안 찾은 숨김 수법이 없어요.</p>}
            <p className="note hb-note">메일 1건에 수법이 여러 개일 수 있어요.</p>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{FILTERS[filter].label} 메일<span className="count">총 <b>{matched.length}</b>건</span></h2>
          <span className="note">받은편지함 최신 {scans.length}건 중</span>
        </div>
        <MailTable
          rows={matched}
          onSelect={setSelected}
          empty={filter === 'flagged' ? `${period} 동안 위험하거나 의심되는 메일이 없어요.` : '해당하는 메일이 없어요.'}
        />
        {next && (
          <div className="panel-body more">
            <button type="button" className="btn" disabled={more.busy} onClick={loadMore}>
              {more.busy ? '불러오는 중…' : `더 불러오기 (${LIMIT}건)`}
            </button>
            {more.error && <p className="note" role="alert">{more.error}</p>}
          </div>
        )}
      </section>

      {selected && <MailDrawer r={scans.find((r) => r.id === selected.id) ?? selected} onClose={() => setSelected(null)} />}
    </>
  )
}

const SERIES = [['danger', '위험', 'var(--danger)'], ['suspicious', '의심', 'var(--warn)']]

function ChartTip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="chart-tip">
      <b>{d.label}</b>
      {SERIES.map(([k, name, color]) => <div key={k}><i style={{ background: color }} />{name}<span>{d[k]}건</span></div>)}
    </div>
  )
}

// 일별 위험·의심 누적 영역 (shadcn Area Chart - Stacked 형태). 표 대신 읽을 수 있게 aria-label 에 합계를 둠
function DailyChart({ data }) {
  const id = useId().replace(/:/g, '')
  const total = (k) => data.reduce((n, d) => n + d[k], 0)
  return (
    <div className="area-chart" role="img" aria-label={`일별 탐지: 위험 ${total('danger')}건, 의심 ${total('suspicious')}건`}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
          <defs>
            {SERIES.map(([k, , color]) => (
              <linearGradient key={k} id={`${id}-${k}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.8} />
                <stop offset="95%" stopColor={color} stopOpacity={0.1} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="short" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={24} />
          <Tooltip content={<ChartTip />} cursor={{ strokeDasharray: '3 3' }} />
          {[...SERIES].reverse().map(([k, , color]) => (
            <Area key={k} dataKey={k} type="monotone" stackId="a" stroke={color} strokeWidth={2} fill={`url(#${id}-${k})`} isAnimationActive={false} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// 강조 카드 안 작은 추이선 (흰색 60%). 장식이라 스크린리더에서 숨김
function Sparkline({ data }) {
  const points = data.map((d) => ({ n: d.danger + d.suspicious }))
  return (
    <div className="feature-spark" aria-hidden="true">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 2, right: 0, left: 0, bottom: 2 }}>
          <Area dataKey="n" type="monotone" stroke="#fff" strokeWidth={2} fill="#fff" fillOpacity={0.15} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
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
