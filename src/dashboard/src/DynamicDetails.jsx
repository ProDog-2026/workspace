import { useEffect, useId, useState } from 'react'
import '../../agent-container/control-dashboard/frontend/presentation.js'
import { dynamicLabel, dynamicError } from './dynamicState'
import { containerBounds, flowEdgePath, flowLayout, graphWidth } from './flowGeometry'

const view = globalThis.DashboardPresentation
export const format = (value) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)
export function Raw({ title = '원본 데이터 보기', value }) {
  return <details className="dynamic-raw"><summary>{title}</summary><pre>{format(value)}</pre></details>
}
function Fields({ fields }) {
  return <dl className="dynamic-fields">{fields.map((item, i) => <div key={i}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>
}

export function DynamicResult({ job, result, onEvidence }) {
  const data = result ? view.describeResult(result) : null
  return <section className="panel">
    <div className="panel-head"><h2>분석 결과</h2><span className={`dynamic-badge ${job.observation === 'observed' ? 'alert' : ''}`}>{dynamicLabel(job)}</span></div>
    <div className="panel-body dynamic-stack">
      {job.static_detection && <p className="dynamic-notice">1차 판정: <b>{job.static_detection.verdict === 'danger' ? '위험' : '의심'}</b> · {job.static_detection.static_score}점. 동적 검사 결과와 별도로 유지합니다.</p>}
      {job.source === 'gmail_manual' && <p className="dynamic-notice">사용자가 선택한 메일의 수동 검사입니다. 1차 분류는 수행하지 않았습니다.</p>}
      {(job.truncated || job.attachments_count > 0 || job.warnings?.length > 0) && <p className="dynamic-notice">일부만 검사: {job.truncated ? '본문 잘림 · ' : ''}{job.attachments_count ? `첨부파일 ${job.attachments_count}개 내용 제외 · ` : ''}{job.warnings?.join(', ')}</p>}
      {job.error_code && <p role="status" className="dynamic-notice">{dynamicError(job.error_code)}</p>}
      {!data ? <p className="muted">검사가 끝나면 요약과 확인된 증거가 여기에 표시됩니다.</p> : <>
        <div className="dynamic-metrics">
          <div><span>실행 상태</span><strong>{data.status}</strong></div>
          <div><span>작업과 무관한 도구 요청</span><strong>{data.deviations?.length ?? '미확인'}<small>건</small></strong></div>
          <div><span>증거 검증</span><strong>{data.verification}</strong></div>
        </div>
        <div><h3>메일 요약</h3><p className="dynamic-summary">{result.summary || '저장된 요약이 없습니다.'}</p></div>
        <p className="muted">이상 행동 미관찰은 이번 실행의 관찰 결과이며, 메일이 안전하다는 보장은 아닙니다.</p>
        <div><h3>작업과 무관한 도구 요청</h3>{data.deviations?.length ? data.deviations.map((item, i) => <div key={i} className="dynamic-evidence">
          <b>{view.toolLabel(item.tool)}</b><pre>{format(item.arguments)}</pre>
          <Evidence item={item} onEvidence={onEvidence} />
        </div>) : <p className="muted">{data.deviationText}</p>}</div>
        <div className="dynamic-effects">{data.effects.map((effect) => <div key={effect.key}>
          <h3>{effect.title}</h3><p>{effect.text}</p>
          {effect.entries.map((item, i) => <div key={i}>{item.path && <p className="mono">{item.path}</p>}<Evidence item={item} onEvidence={onEvidence} /></div>)}
          {effect.key === 'simulated_send_requests' && <p className="note">모의 실행이며 실제 메일 전송이 아닙니다.</p>}
        </div>)}</div>
        <p className="note">{data.systemMonitor} · {data.networkIsolation} · {result.runtime_mode === 'host_process_no_docker' ? 'Docker 없이 호스트에서 실행한 기록' : result.runtime_mode}</p>
        {data.verificationErrors.length > 0 && <p className="dynamic-notice">{data.verificationErrors.join(' · ')}</p>}
        {data.cleanupError && <p className="dynamic-notice">{data.cleanupError}</p>}
        <details className="dynamic-raw"><summary>실행 설정과 프롬프트</summary>
          <Fields fields={[
            { label: '모델', value: result.connection?.model ?? '-' },
            { label: '시작 시각', value: view.formatTime(result.created_at) },
            { label: '소요 시간', value: `${result.elapsed_seconds ?? '-'}초` },
            { label: 'LLM 호출', value: `${result.calls ?? '-'}회` },
            { label: '실행 한도', value: format(result.limits) },
            { label: 'Temperature', value: result.temperature },
            { label: '프롬프트', value: result.prompt_version },
          ]} /><pre>{result.system_prompt}</pre>
        </details>
        <Raw title="원본 결과 보기" value={result} />
      </>}
    </div>
  </section>
}

function Evidence({ item, onEvidence }) {
  return <p className="note">출처: {view.sourceLabel(item.source)} {item.event_id && <button className="dynamic-text-btn" onClick={() => onEvidence(item.event_id)}>근거 로그 보기</button>}</p>
}

export function DynamicEvents({ events, selectedEvent }) {
  const [filter, setFilter] = useState('all')
  const [limit, setLimit] = useState(80)
  useEffect(() => {
    if (!selectedEvent) return
    setFilter('all'); setLimit(events.length)
    const timer = requestAnimationFrame(() => document.getElementById(`event-${selectedEvent}`)?.scrollIntoView({ block: 'center' }))
    return () => cancelAnimationFrame(timer)
  }, [selectedEvent, events.length])
  const filtered = events.filter((event) => filter === 'all' || event.source === filter)
  return <section className="panel">
    <div className="panel-head"><h2>이벤트 로그 <span className="count">{filtered.length}건</span></h2>
      <select aria-label="이벤트 출처" value={filter} onChange={(e) => setFilter(e.target.value)}>
        <option value="all">모든 출처</option>{['manager', 'agent', 'receiver', 'verifier', 'sensor'].map((s) => <option key={s} value={s}>{view.sourceLabel(s)}</option>)}
      </select>
    </div>
    <div className="panel-body dynamic-events">
      {!filtered.length && <p className="muted">기록된 이벤트가 없습니다.</p>}
      {filtered.slice(0, limit).map((event) => {
        const item = view.describeEvent(event)
        return <details key={event.event_id} id={`event-${event.event_id}`} className="dynamic-event" open={selectedEvent === event.event_id || undefined}>
          <summary><span>{item.title}</span><small>{view.sourceLabel(event.source)} · {view.formatTime(event.occurred_at)}</small></summary>
          <Fields fields={item.fields} /><Raw title="원본 이벤트" value={event} />
        </details>
      })}
      {filtered.length > limit && <button className="btn" onClick={() => setLimit((n) => n + 100)}>이벤트 더 보기</button>}
    </div>
  </section>
}

export function DynamicFlow({ flow, runtimeMode }) {
  const [selected, setSelected] = useState(null)
  const arrowId = `dynamic-arrow-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  if (!flow) return <section className="panel"><div className="panel-head"><h2>데이터 흐름</h2></div><p className="empty">실행이 시작되면 단계별 흐름을 표시합니다.</p></section>
  const step = flow.steps.find((s) => s.index === selected) ?? flow.steps.at(-1)
  const nodes = new Map(flow.nodes.map((node) => [node.id, node]))
  const { positions, height } = flowLayout(flow.nodes)
  const pick = (id) => setSelected(flow.steps.findLast((s) => s.node === id)?.index ?? null)
  const areaTitle = runtimeMode === 'docker' ? '작업 컨테이너' : '작업 실행 영역'
  const areaCaption = runtimeMode === 'host_process_no_docker' ? 'Docker 없이 호스트에서 실행된 기록'
    : runtimeMode === 'docker' ? '에이전트 · 도구 실행' : '실행 환경 확인 중'
  return <section className="panel dynamic-flow">
    <div className="panel-head"><h2>데이터 흐름 <span className="tag">LangGraph</span></h2><span className="note">{flow.outcome?.label} · {flow.steps.length}단계</span></div>
    <div className="panel-body">
      {flow.anomalies?.length > 0 && <p className="dynamic-notice">{flow.anomalies.join(' · ')}</p>}
      <div className="dynamic-flow-key"><span><i className="dynamic-key-boundary" aria-hidden="true" /> {areaTitle}</span><span><i className="dynamic-key-outside" aria-hidden="true" /> 제어 서버 · 외부 LLM</span><span><i className="dynamic-key-visited" aria-hidden="true" /> 통과한 흐름</span></div>
      <p className="dynamic-flow-help">{runtimeMode === 'host_process_no_docker' ? '이 기록은 Docker 없이 호스트에서 실행되었습니다. LLM 요청은 제어 서버를 거쳐 Ollama로 전달됩니다.' : 'LLM 요청은 제어 서버를 거쳐 Ollama로 전달됩니다. 경계는 실행 구조를 나타내며, 네트워크 격리 검증 결과는 별도입니다.'}</p>
      <div className="dynamic-graph" role="region" aria-label="데이터 흐름 그래프, 가로로 스크롤 가능" tabIndex={0}><svg viewBox={`0 0 ${graphWidth} ${height}`} role="group" aria-label="동적 검사 데이터 흐름">
        <defs><marker id={arrowId} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7" fill="context-stroke" /></marker></defs>
        <g aria-hidden="true">
          <text x="30" y="72" className="dynamic-graph-section">01 · 실행 요청</text>
          <text x="30" y="255" className="dynamic-graph-section">02 · 외부 모델 연결</text>
          <text x="30" y="400" className="dynamic-graph-section">03 · 증거 수집 및 판정</text>
          <text x="30" y="550" className="dynamic-graph-section">04 · 결과 전달</text>
          <rect className="dynamic-container-boundary" x={containerBounds.x} y={containerBounds.y} width={containerBounds.width} height={containerBounds.height} rx="22" />
          {runtimeMode === 'docker' && <g className="dynamic-container-icon" transform="translate(737 73)"><rect x="1" y="7" width="15" height="12" rx="3" /><path d="M4 7V5a4.5 4.5 0 0 1 9 0v2M8.5 12v2" /></g>}
          <text x="763" y="89" className="dynamic-container-title">{areaTitle}</text>
          <text x="1098" y="89" textAnchor="end" className="dynamic-container-caption">{areaCaption}</text>
          <line x1="982" y1="123" x2="982" y2="478" className="dynamic-container-divider" />
          <text x="1000" y="151" className="dynamic-container-aside-title">실행 준비</text>
          <text x="1000" y="171" className="dynamic-container-aside">작업 생성</text>
          <text x="1000" y="316" className="dynamic-container-aside-title">도구 동작</text>
          <text x="1000" y="336" className="dynamic-container-aside">요청 · 처리</text>
          <text x="1000" y="466" className="dynamic-container-aside-title">종료 보고</text>
          <text x="1000" y="486" className="dynamic-container-aside">결과 전달</text>
        </g>
        {flow.edges.map((edge, i) => {
          const path = flowEdgePath(edge, positions)
          return path && <path key={i} d={path} markerEnd={`url(#${arrowId})`} className={`dynamic-edge ${edge.count ? 'visited' : ''} ${edge.abnormal ? 'abnormal' : ''}`}><title>{edge.label || `${nodes.get(edge.source)?.title} → ${nodes.get(edge.target)?.title}`} · 통과 {edge.count ?? 0}회</title></path>
        })}
        {flow.nodes.map((node) => {
          const [x, y] = positions.get(node.id), current = flow.current === node.id
          return <g key={node.id} role="button" tabIndex={0} aria-label={`${node.title}, ${node.visits}회${current ? ', 진행 중' : ''}`} onClick={() => pick(node.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(node.id) } }}
            aria-pressed={step?.node === node.id} className={`dynamic-node ${node.visits ? 'visited' : ''} ${current ? 'current' : ''} ${step?.node === node.id ? 'selected' : ''}`}>
            <rect x={x} y={y} width="176" height="64" rx="12" /><text x={x + 88} y={y + 27}>{node.title}</text><text className="dynamic-node-meta" x={x + 88} y={y + 48}>{current ? '진행 중' : node.visits ? `${node.visits}회 통과` : '미도달'}</text>
          </g>
        })}
      </svg></div>
      <details className="dynamic-flow-edges"><summary>단계 연결 목록 보기</summary><ul>{flow.edges.map((edge, index) => <li key={index}>{nodes.get(edge.source)?.title ?? edge.source} → {nodes.get(edge.target)?.title ?? edge.target}{edge.label ? ` (${edge.label})` : ''} · {edge.count ?? 0}회 통과</li>)}</ul></details>
      <div className="dynamic-flow-details">
        <ol aria-label="실행 단계">{flow.steps.map((s) => <li key={s.index}><button className="btn" aria-pressed={s.index === step?.index} onClick={() => setSelected(s.index)}>{s.index}. {nodes.get(s.node)?.title ?? s.node}<span>{s.outcome?.label}</span></button></li>)}</ol>
        <div>{step && <><h3>{nodes.get(step.node)?.title ?? step.node} · {step.outcome?.label}</h3><p className="note">{step.outcome?.detail}</p><Raw title="이 단계에서 바뀐 데이터" value={step.delta} /><pre>{format(step.state)}</pre></>}
          {selected !== null && <button className="btn" onClick={() => setSelected(null)}>최신 단계 따라가기</button>}
        </div>
      </div>
    </div>
  </section>
}
