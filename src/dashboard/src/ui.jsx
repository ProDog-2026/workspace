import { DEFAULT_THRESHOLDS, verdictOf } from './rules'

export const STATES = {
  danger: { label: '위험', icon: '⛔' },
  suspicious: { label: '의심', icon: '⚠' },
  safe: { label: '정상', icon: '✓' },
  incomplete: { label: '수집 불완전', icon: '◐' },
  failed: { label: '분석 실패', icon: '⊘' },
}

// 위험·의심 근거가 있으면 수집/분석이 덜 끝났어도 그 판정을 먼저 보여줌 (위험을 '확인 필요' 뒤에 숨기지 않기).
// 판정이 비어 있으면 절대 '정상'으로 떨어뜨리지 않음.
export const stateOf = (r) =>
  r.verdict === 'danger' || r.verdict === 'suspicious' ? r.verdict
    : r.status !== 'ok' ? r.status
      : r.verdict ?? 'failed'

export const fmtTime = (iso) => new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

export function Badge({ v }) {
  return <span className={`badge ${v}`}>{STATES[v].icon} {STATES[v].label}</span>
}

// neutral: 판정이 안 났을 때 부분 점수를 초록(정상)처럼 보이지 않게 회색으로
export function ScoreBar({ label, score, thresholds = DEFAULT_THRESHOLDS, neutral }) {
  return (
    <div className="meter-row">
      <span className="small">{label}</span>
      <div className="meter">{score != null && <div className={neutral ? 'neutral' : verdictOf(score, thresholds)} style={{ width: `${score}%` }} />}</div>
      <span className="num small strong">{score ?? '—'}</span>
    </div>
  )
}
