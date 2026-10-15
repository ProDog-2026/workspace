import { useEffect, useRef } from 'react'
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
  danger: '짚어낼 문장이 없어요. 탐지 룰에 걸린 숨긴 글자나 주석은 없고, 문맥 분석에서만 위험으로 나왔어요.',
  suspicious: '짚어낼 문장이 없어요. 탐지 룰에 걸린 숨긴 글자나 주석은 없고, 문맥 분석에서만 의심으로 나왔어요.',
  safe: '숨긴 글자, HTML 주석, 지시 문구, 외부 전송 링크를 찾지 못했어요. 모든 수법을 잡는다고 장담할 수는 없어요.',
  incomplete: '가져온 부분에선 숨은 명령을 찾지 못했어요. 빠진 부분은 검사하지 않았어요.',
  failed: '분석이 끝나지 않아 근거가 없어요. 안전하다는 뜻은 아니에요.',
  pending: '탐지기와 아직 연결되지 않아 검사 결과가 없어요. 안전하다는 뜻은 아니에요.',
}

export const typeName = (k) => TYPES[k] ?? k
export const shortType = (k) => typeName(k).replace(/\s*\(.*\)$/, '')

// 목록 한 줄 요약 ('일부만 검사'는 따로 태그로 붙임)
export function shortReason(r) {
  const state = stateOf(r)
  if (state === 'failed') return r.status_detail ?? '판정 결과 없음'
  if (state === 'pending') return r.status === 'incomplete' ? '검사 전 · 일부 못 가져옴' : '검사 전'
  if (r.evidence.length) return shortType(r.evidence[0].type) + (r.evidence.length > 1 ? ` 외 ${r.evidence.length - 1}곳` : '')
  if (state === 'incomplete') return `${r.missing_parts[0]} 못 가져옴`
  if (state === 'safe') return '숨은 명령 없음'
  return '문맥 분석으로만 탐지'
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
            const partial = r.status !== 'ok' && (s === 'danger' || s === 'suspicious')
            return (
              <tr key={r.id} onClick={() => onSelect(r)}>
                <td className="td-state"><Badge v={s} /></td>
                {/* 클릭은 행(tr)으로 올라감. 버튼은 키보드 접근용 */}
                <td className="td-subj"><button type="button" className="subj">{r.subject}</button></td>
                <td className="sender">{r.sender}</td>
                <td className="reason">{shortReason(r)}{partial && <span className="tag">일부만 검사</span>}</td>
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
  return (
    <>
      <dl className="scores">
        <div><dt>최종</dt><dd>{orMissing(s)}</dd></div>
        <div><dt>정적</dt><dd>{orMissing(r.static_score)}</dd></div>
        <div><dt>문맥</dt><dd>{orMissing(r.llm_score)}</dd></div>
      </dl>
      {s != null && (
        <div className="meter" role="img" aria-label={`최종 ${s}점, 의심 ${t.suspicious}점 이상, 위험 ${t.danger}점 이상`}>
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

  return (
    <div className="verdict">
      <div className={`band ${state}`}>
        <p className="band-state"><Mark v={state} size={20} />{STATES[state].label}</p>
        <p>{SUMMARY[state]}</p>
      </div>

      <table className="info">
        <tbody>
          {r.subject && <tr><th scope="row">제목</th><td><b>{r.subject}</b></td></tr>}
          {r.sender && <tr><th scope="row">보낸 사람</th><td className="mono">{r.sender}</td></tr>}
          {r.received_at && <tr><th scope="row">받은 시각</th><td>{fmtTime(r.received_at)}</td></tr>}
          {r.llm_reason && <tr><th scope="row">문맥 분석</th><td>{r.llm_reason}</td></tr>}
          {r.status === 'incomplete' && r.missing_parts.length > 0 && (
            <tr><th scope="row">검사 못 한 부분</th><td>
              <ul className="plain">{r.missing_parts.map((p) => <li key={p}>{p}</li>)}</ul>
              {partial && <p className="sub">검사한 부분에서 이미 {STATES[state].label} 근거가 나왔어요.</p>}
            </td></tr>
          )}
          {(state === 'failed' || r.status === 'failed') && <tr><th scope="row">원인</th><td>{r.status_detail ?? '판정 결과가 없어요.'}</td></tr>}
          <tr><th scope="row">점수</th><td><Score r={r} t={thresholds} /></td></tr>
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
        <p className="no-evidence">{NO_EVIDENCE[state]}</p>
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
