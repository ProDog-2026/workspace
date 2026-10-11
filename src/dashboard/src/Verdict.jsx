import { TYPES } from './rules'
import { Badge, ScoreBar, fmtTime, stateOf } from './ui'

// 사용자용 설명: "왜 사람한텐 안 보이는데 AI한텐 위험한가"
const EXPLAIN = {
  hidden_color: '글자색을 배경색과 같게 해서 사람 눈에는 안 보이지만, AI는 이 글자를 그대로 읽습니다.',
  hidden_css: '화면에 표시되지 않게 숨긴 영역입니다. AI는 숨김 여부와 상관없이 읽습니다.',
  html_comment: 'HTML 주석은 화면에 나오지 않지만, AI에게 원문을 넘기면 함께 전달됩니다.',
  zero_width: '눈에 안 보이는 문자를 끼워 넣어 단어 필터를 피하려는 문장입니다.',
  override: 'AI에게 원래 지시를 무시하고 다른 일을 하라고 시키는 문장입니다.',
  exfil_link: 'AI가 이 링크나 이미지를 출력하면 주소에 담긴 정보가 외부 서버로 전송될 수 있습니다 (EchoLeak 방식).',
  encoded: 'Base64로 인코딩해 숨긴 문장입니다. 풀어 보면 AI에게 내리는 지시가 나옵니다.',
}

const SUMMARY = {
  danger: 'AI 에이전트에게 맡기면 숨은 명령을 따를 수 있는 메일입니다.',
  suspicious: '숨은 요소가 있지만 공격인지 확실하지 않습니다.',
  safe: '검사한 범위에서 숨은 명령을 찾지 못했습니다.',
  incomplete: '메일 일부를 가져오지 못해 전부 검사하지 못했습니다.',
  failed: '분석 중 오류가 나서 판정하지 못했습니다.',
}

// 읽기 전용 앱: 사용자가 직접 할 일만 안내하고, 격리·차단 같은 기능은 약속하지 않음
const ADVICE = {
  danger: ['이 메일의 요약·답장·전달을 AI 에이전트에게 맡기지 마세요.', '본문 속 링크나 이미지를 열지 마세요.', '삭제나 스팸 신고는 Gmail에서 직접 하세요.'],
  suspicious: ['AI에게 맡기기 전에 아래 근거를 직접 확인하세요.', '모르는 발신자라면 AI 요약 대신 원문을 읽으세요.'],
  safe: [],
  incomplete: ['검사하지 못한 부분은 안전한지 알 수 없습니다.', '그 부분(특히 첨부파일)까지 AI에게 맡기지 마세요.'],
  failed: ['판정이 나오기 전까지 이 메일을 AI 에이전트에게 맡기지 마세요.'],
}

const NO_EVIDENCE = {
  danger: '정적 룰에 걸린 숨은 내용은 없습니다. LLM 분석만 위험으로 판단했고, 문제 문장을 특정하지 못했습니다.',
  suspicious: '정적 룰에 걸린 숨은 내용은 없습니다. LLM 분석만 의심으로 판단했고, 문제 문장을 특정하지 못했습니다.',
  safe: '숨은 글자, 주석, 지시 문구를 찾지 못했습니다. 다만 모든 공격을 잡는다고 보장하지는 않습니다.',
  incomplete: '가져온 부분에서는 숨은 명령을 찾지 못했습니다. 빠진 부분은 검사하지 않았습니다.',
  failed: '판정 근거가 없습니다. 분석이 끝나지 않았기 때문이며, 안전하다는 뜻이 아닙니다.',
}

// 목록 한 줄 요약
export function shortReason(r) {
  const state = stateOf(r)
  const partial = r.status !== 'ok' && state !== r.status ? ' · 일부만 검사' : ''
  if (state === 'failed') return r.status_detail
  if (r.evidence.length) return TYPES[r.evidence[0].type] + (r.evidence.length > 1 ? ` 외 ${r.evidence.length - 1}건` : '') + partial
  if (state === 'incomplete') return `${r.missing_parts[0]} 미수집`
  if (state === 'safe') return '숨은 명령 없음'
  return 'LLM 판단 · 근거 문장 없음' + partial
}

export function MailTable({ rows, onSelect }) {
  return (
    <div className="table-wrap">
      <table className="clickable">
        <thead>
          <tr>
            <th>판정</th><th>수신</th><th>발신자</th><th>제목</th><th>근거</th>
            <th className="num">정적</th><th className="num">LLM</th><th className="num">최종</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} onClick={() => onSelect(r)}>
              <td><Badge v={stateOf(r)} /></td>
              <td className="nowrap">{fmtTime(r.received_at)}</td>
              <td>{r.sender}</td>
              <td><button className="link" onClick={() => onSelect(r)}>{r.subject}</button></td>
              <td className="small">{shortReason(r)}</td>
              <td className="num">{r.static_score ?? '—'}</td>
              <td className="num">{r.llm_score ?? '—'}</td>
              <td className="num strong">{r.final_score ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <p className="muted empty">해당 메일 없음</p>}
    </div>
  )
}

export function VerdictDetail({ r, thresholds }) {
  const state = stateOf(r)
  const partial = r.status !== 'ok' && state !== r.status

  return (
    <div className="verdict">
      <div className={`verdict-head ${state}`}>
        <Badge v={state} />
        <p>{SUMMARY[state]}</p>
      </div>
      {partial && <p className="notice small">◐ 메일 일부를 검사하지 못했지만, 검사한 부분만으로도 아래 근거가 나왔습니다.</p>}

      <h3>왜 이렇게 판정했나요?</h3>
      {r.evidence.length === 0 && <p className="no-evidence small">{NO_EVIDENCE[state]}</p>}
      {r.evidence.map((e, i) => (
        <div key={i} className="evidence">
          <div className="row small"><b>{TYPES[e.type]}</b><span className="muted">{e.rule}</span></div>
          <p className="small">{EXPLAIN[e.type]}</p>
          {/* 반드시 텍스트로만 표시. 메일 HTML 을 렌더링하면 대시보드가 XSS 에 뚫림 */}
          <pre className="hidden-text">{e.snippet}</pre>
        </div>
      ))}
      {r.llm_reason && <p className="small"><b>LLM 의견</b> · {r.llm_reason}</p>}

      {r.status === 'incomplete' && (
        <>
          <h3>검사하지 못한 부분</h3>
          <ul className="rules small">{r.missing_parts.map((m) => <li key={m}>{m}</li>)}</ul>
        </>
      )}
      {r.status === 'failed' && (
        <>
          <h3>실패 원인</h3>
          <p className="small">{r.status_detail}</p>
        </>
      )}

      <h3>점수</h3>
      <ScoreBar neutral={state === 'failed'} label="정적 분석" score={r.static_score} thresholds={thresholds} />
      <ScoreBar neutral={state === 'failed'} label="LLM 분석" score={r.llm_score} thresholds={thresholds} />
      <ScoreBar neutral={state === 'failed'} label="최종" score={r.final_score} thresholds={thresholds} />
      {(r.llm_score == null || r.final_score == null) && <p className="muted small">— 표시는 측정하지 못한 점수입니다.</p>}

      {ADVICE[state].length > 0 && (
        <>
          <h3>이렇게 하세요</h3>
          <ul className="advice small">{ADVICE[state].map((a) => <li key={a}>{a}</li>)}</ul>
        </>
      )}
      <p className="muted small readonly-note">ProDog은 Gmail 읽기 전용 권한만 씁니다. 메일을 옮기거나 지우거나 차단하지 않습니다.</p>
    </div>
  )
}
