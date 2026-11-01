import { useEffect, useRef } from 'react'
import { DETECTOR_NAMES } from './api'
import { DEFAULT_THRESHOLDS, TYPES } from './rules'
import { Badge, CloseIcon, Mark, STATES, fmtShort, fmtTime, stateOf } from './ui'

// 항목별 한 줄 설명. "AI는 안 보이는 것도 읽는다"는 목록 위 한 줄(lead)에서 한 번만 말함
const EXPLAIN = {
  hidden_color: '글자색을 배경색과 같게 해서 안 보이게 했어요.',
  hidden_css: '화면에 안 나오게 숨겨 둔 영역이에요.',
  html_comment: '메일 화면엔 안 나오는 HTML 주석이에요.',
  zero_width: '안 보이는 문자를 끼워 넣어 단어 필터를 피했어요.',
  override: 'AI에게 원래 지시를 무시하라고 시키는 문장이에요.',
  exfil_link: 'AI가 이 주소를 불러오면 메일 내용이 밖으로 나갈 수 있어요.',
  encoded: 'Base64로 감춘 문장이에요. 풀면 AI에게 내리는 지시가 나와요.',
}

export const SUMMARY = {
  danger: '숨은 명령이 있어요. AI에게 맡기지 마세요.',
  suspicious: '공격일 수 있는 부분이 있어요. 확실하진 않아요.',
  safe: '검사한 범위에선 숨은 명령을 찾지 못했어요.',
  incomplete: '메일 일부를 못 가져와서 다 검사하지 못했어요.',
  failed: '오류가 나서 판정하지 못했어요.',
  pending: '메일은 가져왔고 아직 검사하지 않았어요.',
}

// 읽기 전용 앱: 사용자가 직접 할 일만 안내하고, 격리·차단 같은 기능은 약속하지 않음
export const ADVICE = {
  danger: ['AI에게 요약, 답장, 전달 맡기지 않기', '본문 링크와 이미지 열지 않기', '삭제나 스팸 신고는 Gmail에서 직접 하기'],
  suspicious: ['AI에게 맡기기 전에 찾은 부분 확인하기', '모르는 발신자면 요약 대신 원문 읽기'],
  safe: [],
  incomplete: ['검사 못 한 부분은 안전한지 알 수 없음', '특히 첨부파일은 AI에게 맡기지 않기'],
  failed: ['판정이 나올 때까지 AI에게 맡기지 않기'],
  pending: ['판정이 나올 때까지 AI에게 맡기지 않기'],
}

// 근거가 없을 때 문구. 상세의 '찾은 부분' 칸과 판정 안내 표가 같이 씀 (상태별 뜻 유지)
export const NO_EVIDENCE = {
  danger: '위험 판정이지만 문장별 근거는 제공되지 않았어요. 근거가 없다는 이유로 안전하다고 볼 수는 없어요.',
  suspicious: '의심 판정이지만 문장별 근거는 제공되지 않았어요. 메일을 AI에게 맡기기 전에 확인하세요.',
  safe: '검사한 범위에서 공격 근거를 찾지 못했어요. 모든 공격이 없다는 뜻은 아니에요.',
  incomplete: '가져온 부분에선 숨은 명령을 찾지 못했어요. 빠진 부분은 검사하지 않았어요.',
  failed: '분석이 끝나지 않아 근거가 없어요. 안전하다는 뜻은 아니에요.',
  pending: '아직 판정 결과가 없어요. 안전하다는 뜻은 아니에요.',
}

export const typeName = (k) => TYPES[k] ?? k
export const shortType = (k) => typeName(k).replace(/\s*\(.*\)$/, '')

const PIPELINE_STATUS = {
  static_queued: '정적 분석 대기', static_running: '정적 분석 중', static_error: '정적 분석 실패',
  skipped: '동적 분석 생략', dynamic_queued: '동적 분석 대기', dynamic_running: '동적 분석 중',
  completed: '분석 완료', error: '동적 분석 오류', cancelled: '분석 취소',
}
const DYNAMIC_STATUS = {
  queued: '대기', running: '실행 중', completed: '완료', error: '오류', failed: '실패', cancelled: '취소', skipped: '생략',
  timeout: '시간 초과', step_limit: '단계 제한 도달', cancelling: '중지 중',
}
const pipelineStatus = (a) => PIPELINE_STATUS[a?.status] ?? a?.status ?? '분석 결과 없음'
const points = (n) => n == null ? '측정 못 함' : `${Number(n.toFixed(2))}점`

// 목록 한 줄 요약 ('일부만 검사'는 따로 태그로 붙임)
export function shortReason(r) {
  const state = stateOf(r)
  if (state === 'failed') return r.status_detail ?? '판정 결과 없음'
  if (r.analysis) {
    if (r.pending) return pipelineStatus(r.analysis)
    if (r.analysis.static) return `${r.analysis.static.mode === 'ensemble' ? 'ML + Transformer 앙상블' : '정적 분류기'} · ${STATES[r.verdict]?.label ?? '판정 없음'}`
  }
  if (state === 'pending') return r.status === 'incomplete' ? '검사 전 · 일부 못 가져옴' : '검사 전'
  if (r.evidence.length) return shortType(r.evidence[0].type) + (r.evidence.length > 1 ? ` 외 ${r.evidence.length - 1}곳` : '')
  if (state === 'incomplete') return `${r.missing_parts[0]} 못 가져옴`
  if (state === 'safe') return '숨은 명령 없음'
  return '문맥 분석으로만 탐지'
}

export function AutomaticAnalysis({ rows, onSelect }) {
  const flagged = rows.filter((row) => ['suspicious', 'danger'].includes(row.verdict))
  return <section className="panel" aria-labelledby="automatic-analysis-heading">
    <div className="panel-head"><h2 id="automatic-analysis-heading">수집 메일 자동 동적 분석<span className="count">의심·위험 <b>{flagged.length}</b>건</span></h2><a className="btn" href="#/settings">자동 분석 설정</a></div>
    <p className="panel-body note">자동 분석을 켜면 의심·위험 메일을 로컬 AI로 분석하고, 명령은 격리 환경에서 실행해요.</p>
    {!flagged.length ? <p className="empty">현재 불러온 메일에 의심·위험 판정이 없어요.</p>
      : <div className="table-scroll"><table className="wrap automatic-analysis-table">
        <thead><tr><th scope="col">메일</th><th scope="col">정적 판정</th><th scope="col">동적 분석</th><th scope="col">실행 모델</th><th scope="col">가짜 토큰</th><th scope="col">결과</th></tr></thead>
        <tbody>{flagged.map((row) => {
          const analysis = row.analysis, dynamic = analysis?.dynamic
          const exposure = dynamic?.observations?.canary_leaked
          const status = dynamic ? DYNAMIC_STATUS[dynamic.status] ?? dynamic.status
            : analysis?.status === 'skipped' ? '동적 분석 생략'
              : ['static_queued', 'static_running', 'dynamic_queued', 'dynamic_running', 'error', 'static_error', 'cancelled'].includes(analysis?.status) ? pipelineStatus(analysis) : '실행 기록 없음'
          return <tr key={row.id}>
            <td><button type="button" className="subj" onClick={() => onSelect(row)}>{row.subject || '(제목 없음)'}</button></td>
            <td><Badge v={row.verdict} /></td>
            <td>{status}{(dynamic?.error || analysis?.error) && <p className="note">{(dynamic?.error || analysis?.error).message}</p>}</td>
            <td>{dynamic?.model || analysis?.config?.model || '미기록'}</td>
            <td>{exposure === true ? '노출 관측' : exposure === false ? '관측 안 됨' : '미확인'}</td>
            <td>{dynamic?.id ? <a href={`#/experiment?run=${encodeURIComponent(dynamic.id)}`}>실행 기록</a> : '—'}</td>
          </tr>
        })}</tbody>
      </table></div>}
    {flagged.length > 0 && <p className="panel-body note">명령·AI 응답 상세는 분석을 실행한 PC의 실행 기록에서 확인해요. 동적 분석 결과가 정적 위험 판정을 낮추지는 않아요.</p>}
  </section>
}

export function MailTable({ rows, onSelect, empty = '해당하는 메일이 없어요.' }) {
  if (!rows.length) return <p className="empty">{empty}</p>
  return (
    <div className="table-scroll">
      <table className="list clickable">
        <colgroup><col className="w-state" /><col /><col className="w-from" /><col className="w-reason" /><col className="w-time" /></colgroup>
        <thead>
          <tr><th>판정</th><th>제목</th><th>보낸 사람</th><th>탐지 근거</th><th>받은 시각</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const s = stateOf(r)
            const partial = r.status === 'incomplete'
            return (
              <tr key={r.id} onClick={() => onSelect(r)}>
                <td className="td-state"><Badge v={s} /></td>
                {/* 클릭은 행(tr)으로 올라감. 버튼은 키보드 접근용 */}
                <td className="td-subj"><button type="button" className="subj">{r.subject}</button></td>
                <td className="sender">{r.sender}</td>
                <td className="reason">{shortReason(r)}{partial && <span className="tag">수집 불완전</span>}{r.status === 'failed' && s !== 'failed' && <span className="tag">분석·수집 실패</span>}</td>
                <td className="time">{r.received_at ? fmtShort(r.received_at) : '-'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const orMissing = (v) => v ?? '측정 못 함'

function Score({ r, t }) {
  const s = r.final_score
  const ensemble = r.analysis?.static?.mode === 'ensemble'
  const scoreLabel = ensemble ? '판정 결정 모델 점수' : '최종'
  return (
    <>
      <dl className="scores">
        <div><dt>{scoreLabel}</dt><dd>{orMissing(s == null ? null : Number(s.toFixed(2)))}</dd></div>
        <div><dt>정적</dt><dd>{orMissing(r.static_score == null ? null : Number(r.static_score.toFixed(2)))}</dd></div>
        <div><dt>문맥</dt><dd>{orMissing(r.llm_score)}</dd></div>
      </dl>
      {s != null && t && (
        <div className="meter" role="img" aria-label={`${scoreLabel} ${s}점, 의심 ${t.suspicious}점 이상, 위험 ${t.danger}점 이상`}>
          <div className="meter-track">
            <span className="meter-zone warn" style={{ left: `${t.suspicious}%`, width: `${t.danger - t.suspicious}%` }} />
            <span className="meter-zone danger" style={{ left: `${t.danger}%`, right: 0 }} />
            <span className="meter-pin" style={{ left: `${s}%` }} />
          </div>
          <div className="meter-ticks" aria-hidden="true">
            <span style={{ left: `${t.suspicious}%` }}>의심 {t.suspicious}</span>
            <span style={{ left: `${t.danger}%` }}>위험 {t.danger}</span>
          </div>
        </div>
      )}
      {r.analysis && <p className="sub">{ensemble
        ? '점수와 눈금은 최종 판정을 결정한 탐지기의 값이며, 모델 점수를 합친 확률이 아니에요. 각 모델의 기준으로 판정한 뒤 가장 높은 위험 단계를 최종 판정으로 사용해요. 모델 간 점수 크기는 비교하지 않아요.'
        : '최종 점수는 정적 분류기 점수예요.'} 동적 분석은 이 점수와 판정을 낮추지 않아요.</p>}
      {r.status === 'incomplete' && !r.pending && <p className="sub">가져온 부분만 검사한 점수예요.</p>}
    </>
  )
}

// 순서: ① 판정 띠(상태색은 여기와 라벨에만) → ② 정보 표 → ③ 할 일 → ④ 찾은 부분
export function VerdictDetail({ r, thresholds = DEFAULT_THRESHOLDS }) {
  const state = stateOf(r)
  const partial = r.status !== 'ok' && (state === 'danger' || state === 'suspicious')
  const advice = partial && r.status === 'incomplete' ? [...ADVICE[state], ADVICE.incomplete[1]] : ADVICE[state]
  const n = r.evidence.length
  const analysis = r.analysis
  const dynamic = analysis?.dynamic

  return (
    <div className="verdict">
      <div className={`band ${state}`}>
        <p className="band-state"><Mark v={state} size={20} />{STATES[state].label}</p>
        <p>{analysis && r.verdict && ['danger', 'suspicious', 'safe'].includes(state)
          ? `정적 분류기 판정: ${STATES[r.verdict].label}. ${state === 'safe' ? '검사한 범위의 결과이며 모든 공격이 없다는 뜻은 아니에요.' : 'AI에게 맡기기 전에 확인하세요.'}`
          : analysis && state === 'pending' ? pipelineStatus(analysis) : SUMMARY[state]}</p>
      </div>

      {r.status === 'incomplete' && <p className="note" role="note">수집 불완전: 메일 일부만 검사했어요. 빠진 부분의 안전 여부는 알 수 없어요.</p>}

      <table className="info">
        <tbody>
          {r.subject && <tr><th scope="row">제목</th><td><b>{r.subject}</b></td></tr>}
          {r.sender && <tr><th scope="row">보낸 사람</th><td className="mono">{r.sender}</td></tr>}
          {r.received_at && <tr><th scope="row">받은 시각</th><td>{fmtTime(r.received_at)}</td></tr>}
          {analysis && <>
            <tr><th scope="row">분석 진행</th><td>{pipelineStatus(analysis)}{analysis.error && <p className="note">{analysis.error.message} ({analysis.error.code})</p>}</td></tr>
            <tr><th scope="row">정적 분석</th><td>
              {analysis.static ? <>
                <span>{r.verdict ? STATES[r.verdict].label : '판정 없음'} · {analysis.static.mode === 'ensemble' && '판정 결정 모델 점수 '}{points(r.static_score)}</span>
                {analysis.static.mode && <p className="sub">{analysis.static.mode === 'ensemble' ? 'ML + Transformer 앙상블' : analysis.static.mode === 'lexical' ? '기존 ML(해시 특징) 단독' : analysis.static.mode}</p>}
                {r.verdict && analysis.static.decided_by?.length > 0 && <p className="sub">판정 결정: {analysis.static.decided_by.map((id) => DETECTOR_NAMES[id] ?? id).join(', ')}</p>}
                {analysis.static.model_id && <p className="sub">분류기: {analysis.static.model_id}</p>}
                {analysis.static.error && <p className="note">{analysis.static.error.message} ({analysis.static.error.code})</p>}
              </> : '아직 정적 분석 결과가 없어요.'}
            </td></tr>
            {(r.static_detectors ?? []).map((detector) => <tr key={detector.id}>
              <th scope="row">{detector.label}</th><td>
                <span>{detector.verdict ? STATES[detector.verdict].label : '판정 없음'} · {points(detector.score)}</span>
                <p className="sub">{detector.thresholds
                  ? `의심 ${points(detector.thresholds.suspicious)} 이상 · 위험 ${points(detector.thresholds.danger)} 이상`
                  : '판정 기준이 제공되지 않았어요.'}</p>
              </td>
            </tr>)}
            <tr><th scope="row">동적 분석</th><td>
              {dynamic ? <>
                <span>{DYNAMIC_STATUS[dynamic.status] ?? PIPELINE_STATUS[dynamic.status] ?? dynamic.status}</span>
                {dynamic.error && <p className="note">{dynamic.error.message} ({dynamic.error.code})</p>}
                {dynamic.observations && <ul className="plain">
                  <li>파일 수집: {dynamic.observations.complete == null ? '확인 안 됨' : dynamic.observations.complete ? '수집 완료' : '일부만 수집'}</li>
                  <li>가짜 토큰 노출{'canary_present_at_last_snapshot' in dynamic.observations ? ' 누적 관측' : ''}: {dynamic.observations.canary_leaked == null ? '확인 안 됨' : dynamic.observations.canary_leaked ? '관측됨' : '관측 안 됨'}</li>
                  {'canary_present_at_last_snapshot' in dynamic.observations && <li>마지막 파일 수집에서 토큰 존재: {dynamic.observations.canary_present_at_last_snapshot == null ? '확인 안 됨' : dynamic.observations.canary_present_at_last_snapshot ? '있음' : '없음'}</li>}
                  <li>요약 파일: {dynamic.observations.summary_file_present == null ? '확인 안 됨' : dynamic.observations.summary_file_present ? '있음' : '없음'}</li>
                </ul>}
                {dynamic.id && <p><a href={`#/experiment?run=${encodeURIComponent(dynamic.id)}`}>실행 명령·파일·JSON 상세 보기</a></p>}
              </> : analysis.status === 'skipped' ? '동적 분석이 생략됐어요. 자동 분석 설정과 정적 판정을 확인하세요.' : '아직 동적 실행이 없어요.'}
            </td></tr>
          </>}
          {r.llm_reason && <tr><th scope="row">문맥 분석</th><td>{r.llm_reason}</td></tr>}
          {r.missing_parts.length > 0 && (
            <tr><th scope="row">검사 못 한 부분</th><td>
              <ul className="plain">{r.missing_parts.map((p) => <li key={p}>{p}</li>)}</ul>
              {partial && <p className="sub">검사한 부분에서 이미 {STATES[state].label} 근거가 나왔어요.</p>}
            </td></tr>
          )}
          {(state === 'failed' || r.status === 'failed') && <tr><th scope="row">원인</th><td>{r.status_detail ?? '판정 결과가 없어요.'}</td></tr>}
          <tr><th scope="row">점수</th><td><Score r={r} t={analysis ? r.static_thresholds : r.source === 'supabase' ? null : thresholds} /></td></tr>
        </tbody>
      </table>

      {advice.length > 0 && (
        <>
          <h3 className="sec-title">할 일</h3>
          <ol className="todo">{advice.map((a) => <li key={a}>{a}</li>)}</ol>
        </>
      )}

      <h3 className="sec-title" id="evidence">찾은 부분{n > 0 && <span className="n">{n}곳</span>}</h3>
      {n === 0 ? (
        <p className="no-evidence">{analysis
          ? '정적 분류기는 문장별 근거를 제공하지 않아요. 동적 관측 결과는 위의 동적 분석 항목에서 확인할 수 있어요.'
          : r.mail ? '아직 분석 결과가 없어요. 안전하다는 뜻은 아니에요.' : NO_EVIDENCE[state]}</p>
      ) : (
        <>
          <p className="lead">화면엔 안 보이지만 AI는 그대로 읽는 내용이에요.</p>
          <div className="ev">
            {r.evidence.map((e, i) => (
              <details key={i} open={i === 0}>
                <summary>{shortType(e.type)}<span className="tag mono">{e.rule}</span></summary>
                {EXPLAIN[e.type] && <p className="why">{EXPLAIN[e.type]}</p>}
                <figure className="specimen">
                  <figcaption>숨겨진 원문</figcaption>
                  {/* 반드시 텍스트로만 표시. 메일 HTML 을 렌더링하면 대시보드가 XSS 에 뚫림 */}
                  <pre className="hidden-text">{e.snippet}</pre>
                </figure>
              </details>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// 대시보드와 판정 안내가 같이 쓰는 상세 드로어. 네이티브 <dialog> 라 포커스 가두기·되돌리기·Esc 가 기본으로 됨
export function MailDrawer({ r, onClose }) {
  const ref = useRef(null)
  useEffect(() => { ref.current.showModal() }, [])
  // close 이벤트(비동기)에 기대지 않고 바로 닫음. close() 가 열기 전 포커스를 되돌려 줌
  const close = () => { ref.current.close(); onClose() }
  return (
    <dialog ref={ref} className="drawer" aria-labelledby="drawer-title" onCancel={(e) => { e.preventDefault(); close() }}
      onClick={(e) => e.target === e.currentTarget && e.clientX < e.currentTarget.getBoundingClientRect().left + 1 && close()}>
      <header className="drawer-head">
        <h2 id="drawer-title">메일 상세</h2>
        {r.title && <><span className="tag">예시</span><span className="drawer-note">{r.title}</span></>}
        <button type="button" className="icon-btn" onClick={close} aria-label="닫기" autoFocus><CloseIcon /></button>
      </header>
      <div className="drawer-body"><VerdictDetail r={r} /></div>
    </dialog>
  )
}
