import { useEffect, useRef, useState } from 'react'
import { gmail, toRow } from './api'
import { TYPES } from './rules'
import { dailyCounts, readScans, SCAN_PAGE_SIZE } from './scans'
import { supabase } from './supabase'
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
const STRIP = ['flagged', 'danger', 'suspicious', 'incomplete', 'failed', 'pending', 'all']
const FILTERS = {
  flagged: { label: '위험·의심', states: ['danger', 'suspicious'] },
  ...Object.fromEntries(['danger', 'suspicious', 'incomplete', 'failed', 'pending'].map((s) => [s, { label: STATES[s].label, states: [s] }])),
  all: { label: '전체', states: Object.keys(STATES) },
}
// ponytail: 받은편지함 최신 20건만. 메일마다 Gmail 본문 조회가 1번씩 나가서 50건이면 Gmail 초당 한도에 걸림.
// 더 보려면 '더 불러오기'로 next_page_token 을 넘겨 다음 20건을 이어 받음
const LIMIT = 20
// 최대 128자 ID 50개와 쉼표 49개는 서버의 6,500자 제한 안에 들어간다.
const POLL_BATCH_SIZE = 50
const ACTIVE = new Set(['static_queued', 'static_running', 'dynamic_queued', 'dynamic_running'])

export default function Dashboard({ user, local = false }) {
  const title = local ? '로컬 메일 수집' : '대시보드'
  const pageSize = local ? LIMIT : SCAN_PAGE_SIZE
  const [reload, setReload] = useState(0)
  const [days, setDays] = useState(null) // null = 전체 기간
  const [filter, setFilter] = useState('all')
  const [selected, setSelected] = useState(null)
  const [scans, setScans] = useState(null)
  const [error, setError] = useState(null)
  const [next, setNext] = useState(null) // Gmail 다음 페이지 토큰. 없으면 끝
  const [more, setMore] = useState({ busy: false, error: '' })
  const [pollError, setPollError] = useState('')
  const lifetime = useRef(null)
  const loadingMore = useRef(false)

  const load = async (token, signal) => {
    if (!local) {
      const offset = token ?? 0
      const rows = await readScans(supabase, user?.id, offset, signal)
      if (signal.aborted) return
      setScans((prev) => [...new Map([...(prev ?? []), ...rows].map((r) => [r.id, r])).values()])
      setNext(rows.length === SCAN_PAGE_SIZE ? offset + SCAN_PAGE_SIZE : null)
      return
    }
    const session = await gmail('/api/session', undefined, undefined, { signal })
    if (signal.aborted) return
    if (!session.csrf_token) throw new Error('세션을 확인하지 못했어요. 다시 접속해 주세요.')
    const d = await gmail(`/api/mails?limit=${LIMIT}` + (token ? `&page_token=${encodeURIComponent(token)}` : ''), session.csrf_token, undefined, { signal })
    if (signal.aborted) return
    setScans((prev) => {
      const rows = new Map((prev ?? []).map((r) => [r.gmail_message_id, r]))
      d.messages.forEach((m) => rows.set(m.id, toRow(m)))
      return [...rows.values()]
    })
    setNext(d.next_page_token)
  }
  useEffect(() => {
    const controller = new AbortController()
    lifetime.current = controller
    setScans(null)
    setError(null)
    setNext(null)
    setSelected(null)
    setMore({ busy: false, error: '' })
    setPollError('')
    load(undefined, controller.signal).catch((e) => { if (!controller.signal.aborted) setError(e) })
    return () => controller.abort()
  }, [local, user?.id, reload])

  const messageIds = (scans ?? []).filter((r) => ACTIVE.has(r.analysis?.status)).map((r) => r.gmail_message_id).join(',')
  const active = local && !!messageIds
  useEffect(() => {
    if (!active || !messageIds) return
    const controller = new AbortController()
    const ids = messageIds.split(',')
    const batches = []
    for (let i = 0; i < ids.length; i += POLL_BATCH_SIZE) batches.push(ids.slice(i, i + POLL_BATCH_SIZE).join(','))
    let timer
    const poll = async () => {
      try {
        const results = await Promise.all(batches.map((batch) => gmail(`/api/analysis?message_ids=${encodeURIComponent(batch)}`, undefined, undefined, { signal: controller.signal })))
        if (controller.signal.aborted) return
        const updates = new Map(results.flatMap((data) => data.analyses).map((a) => [a.gmail_message_id, a]))
        setScans((prev) => prev?.map((r) => updates.has(r.gmail_message_id) ? toRow({ ...r.mail, analysis: updates.get(r.gmail_message_id) }) : r) ?? null)
        setPollError('')
      } catch (e) {
        if (!controller.signal.aborted) setPollError(e.message)
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 1000)
    }
    timer = setTimeout(poll, 1000)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [messageIds, active])

  const loadMore = async () => {
    if (loadingMore.current || !next) return
    loadingMore.current = true
    const signal = lifetime.current.signal
    setMore({ busy: true, error: '' })
    try {
      await load(next, signal)
      if (!signal.aborted) setMore({ busy: false, error: '' })
    } catch (e) {
      if (!signal.aborted) setMore({ busy: false, error: e.message })
    } finally { loadingMore.current = false }
  }

  if (error || !scans) {
    return (
      <>
        <header className="page-head"><h1>{title}</h1></header>
        <section className="panel"><p className="empty">
          {!error ? '검사 결과를 불러오는 중…'
            : error.code === 'authentication_required'
              ? <>연결된 Gmail이 없어요. <a href="#/settings">설정</a>에서 Gmail을 연결하세요.</>
              : <span role="alert">{error.message}</span>}
        </p>{error && <div className="panel-body"><button className="btn" onClick={() => setReload((v) => v + 1)}>다시 불러오기</button></div>}</section>
      </>
    )
  }

  // days = null 이면 전체 기간 = 지금까지 불러온 메일 전부 (더 오래된 메일은 '더 불러오기'로 이어 받음)
  const cur = days ? between(scans, dayStart(days - 1), dayStart(-1)) : scans
  const period = days ? `최근 ${days}일` : '전체 기간'
  // 그래프 칸 수: 전체면 가장 오래된 메일 날짜부터 오늘까지. ponytail: 막대가 너무 가늘어지지 않게 최대 90일
  const oldest = Math.min(Date.now(), ...scans.map((r) => Date.parse(r.received_at)).filter(Number.isFinite))
  const span = days ?? Math.min(90, Math.max(7, Math.ceil((dayStart(-1) - oldest) / 86400000)))
  const matched = cur.filter((r) => FILTERS[filter].states.includes(stateOf(r))).sort(bySeverity)
  const typeCounts = Object.keys(TYPES)
    .map((key) => ({ key, label: shortType(key), n: cur.filter((r) => r.types.includes(key)).length }))
    .filter((d) => d.n > 0)
    .sort((a, b) => b.n - a.n)
    .slice(0, 5)
  const daily = dailyCounts(cur, span)
  const flaggedTotal = daily.reduce((n, d) => n + d.danger + d.suspicious, 0)

  return (
    <>
      <header className="page-head">
        <h1>{title}</h1>
        <div className="ctl-row">
          <button className="btn" type="button" disabled={more.busy} onClick={() => setReload((v) => v + 1)}>새로고침</button>
          <span id="period-label">조회 기간</span>
          <div className="btn-group" role="group" aria-labelledby="period-label">
            {[7, 30, null].map((d) => (
              <button key={d ?? 'all'} type="button" aria-pressed={days === d} onClick={() => setDays(d)}>{d ? `최근 ${d}일` : '전체'}</button>
            ))}
          </div>
        </div>
      </header>

      <p className="note page-note">{local
        ? <>이 컴퓨터의 Gmail 연결로 메일을 수집하고 정적·동적 분석해요. 결과는 로컬에 저장되며 Supabase 대시보드와 별개예요. <a href="#/settings">자동 분석 설정</a></>
        : <>Supabase에 저장된 본인 검사 결과를 읽기 전용으로 표시해요. <a href="#/settings">Gmail 연결 관리</a> · <a href="#/mailbox">로컬 메일 수집·분석</a></>}</p>
      <p className="note">목록·집계·그래프는 현재 불러온 {scans.length}건 기준이에요. 일별 그래프는 받은 날짜 기준 최근 {span}일을 표시해요.</p>
      {active && <p className="note" role="status">분석 진행 상태를 1초마다 확인하고 있어요.</p>}
      {pollError && <p className="note" role="alert">분석 상태를 갱신하지 못했어요. 기존 목록을 유지하며 다시 확인해요. {pollError}</p>}

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

      <section className="chart-row">
        <div className="panel">
          <div className="panel-head">
            <h2>일별 탐지<span className="count">위험·의심 <b>{flaggedTotal}</b>건</span></h2>
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
              : <p className="muted">{cur.some((r) => r.analysis?.static) ? '정적 분류기는 숨김 수법별 근거를 제공하지 않아요.' : '아직 수법별 분석 근거가 없어요.'}</p>}
            <p className="note hb-note">수법별 근거가 제공된 경우에만 집계해요.</p>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{FILTERS[filter].label} 메일<span className="count">총 <b>{matched.length}</b>건</span></h2>
          <span className="note">불러온 검사 결과 {scans.length}건 중</span>
        </div>
        <MailTable
          rows={matched}
          onSelect={(r) => setSelected(r.id)}
          empty={filter === 'flagged' ? `${period} 동안 위험하거나 의심되는 메일이 없어요.` : '해당하는 메일이 없어요.'}
        />
        {next && (
          <div className="panel-body more">
            <button type="button" className="btn" disabled={more.busy} onClick={loadMore}>
              {more.busy ? '불러오는 중…' : `더 불러오기 (${pageSize}건)`}
            </button>
            {more.error && <p className="note" role="alert">{more.error}</p>}
          </div>
        )}
      </section>

      {selected && <MailDrawer r={scans.find((r) => r.id === selected)} onClose={() => setSelected(null)} />}
    </>
  )
}

// 일별 위험·의심 누적 막대. 위쪽 칸이 위험, 마우스·키보드 포커스로 그날 숫자를 보여 줌
function DailyChart({ data }) {
  const max = Math.max(4, ...data.map((d) => d.danger + d.suspicious))
  const top = Math.ceil(max / 4) * 4
  const every = data.length > 7 ? Math.ceil(data.length / 7) : 1
  return (
    <div className="dchart">
      <div className="dchart-y" aria-hidden="true">{[top, top / 2, 0].map((t) => <span key={t}>{t}</span>)}</div>
      <div className={`dchart-plot${data.length > 7 ? ' dense' : ''}`}>
        {data.map((d, i) => {
          const n = d.danger + d.suspicious
          const showX = i % every === 0 || i === data.length - 1
          return (
            <div key={d.key} className="dcol" tabIndex={0} aria-label={`${d.label} 위험 ${d.danger}건, 의심 ${d.suspicious}건`}>
              <div className="dbar" style={{ height: `${(n / top) * 100}%` }}>
                {d.danger > 0 && <i className="danger" style={{ flex: d.danger }} />}
                {d.suspicious > 0 && <i className="suspicious" style={{ flex: d.suspicious }} />}
              </div>
              <span className="dtip" aria-hidden="true"><b>{d.label}</b><span>위험 {d.danger}건</span><span>의심 {d.suspicious}건</span></span>
              {showX && <span className={`dx${i === data.length - 1 ? ' today' : ''}`} aria-hidden="true">{d.short}</span>}
            </div>
          )
        })}
      </div>
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
