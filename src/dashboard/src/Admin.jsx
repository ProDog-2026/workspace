import { DEFAULT_RULES, DEFAULT_THRESHOLDS, TYPES } from './rules'

// 데모용 목업. 실제로는 Supabase 에서 관리자 권한으로 조회
const SYSTEM = [
  { name: 'Gmail API', status: 'good', text: '정상' },
  { name: 'Supabase', status: 'good', text: '정상' },
  { name: 'LLM API', status: 'warning', text: '응답 지연 2.4s' },
  { name: '탐지 워커', status: 'good', text: '대기열 0건' },
]
const STATUS_ICON = { good: '✓', warning: '⚠', critical: '⛔' }

const USERS = [
  { email: 'demo@gmail.example', role: '관리자', gmail: true, scans: 614, danger: 47, synced: '10. 1. 오후 12:14' },
  { email: 'kim@gmail.example', role: '사용자', gmail: true, scans: 382, danger: 12, synced: '10. 1. 오후 12:10' },
  { email: 'park@gmail.example', role: '사용자', gmail: true, scans: 905, danger: 31, synced: '10. 1. 오전 11:58' },
  { email: 'yoon@gmail.example', role: '사용자', gmail: false, scans: 120, danger: 3, synced: '9. 24. 오후 3:02' },
  { email: 'lee@gmail.example', role: '사용자', gmail: true, scans: 271, danger: 9, synced: '10. 1. 오후 12:01' },
]

export default function Admin({ rules, setRules, thresholds, setThresholds }) {
  const setRule = (id, patch) => setRules(rules.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const setThreshold = (key, value) => setThresholds({ ...thresholds, [key]: Number(value) })
  const invalid = thresholds.suspicious >= thresholds.danger

  return (
    <>
      <header className="top">
        <div>
          <h1>관리자</h1>
          <p className="muted">데모: 여기서 바꾼 룰·판정 기준은 메일 검사 페이지에 바로 반영됩니다 (새로고침하면 초기화).</p>
        </div>
        <button className="btn" onClick={() => { setRules(DEFAULT_RULES); setThresholds(DEFAULT_THRESHOLDS) }}>기본값으로</button>
      </header>

      <section className="kpis">
        {SYSTEM.map((s) => (
          <div key={s.name} className={`card kpi ${s.status}`}>
            <div className="muted">{s.name}</div>
            <div className="strong">{STATUS_ICON[s.status]} {s.text}</div>
          </div>
        ))}
      </section>

      <section className="grid">
        <div className="card">
          <h2>탐지 룰</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>사용</th><th>ID</th><th>룰</th><th>유형</th><th className="num">가중치</th></tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id} className={r.on ? '' : 'off'}>
                    <td><input type="checkbox" checked={r.on} onChange={(e) => setRule(r.id, { on: e.target.checked })} aria-label={`${r.id} 사용`} /></td>
                    <td className="strong">{r.id}</td>
                    <td>{r.name}</td>
                    <td className="small muted">{TYPES[r.type]}</td>
                    <td className="num">
                      <input type="number" min="0" max="100" value={r.weight} onChange={(e) => setRule(r.id, { weight: Number(e.target.value) })} aria-label={`${r.id} 가중치`} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card form">
          <h2>판정 기준</h2>
          <p className="muted small">최종 점수(0~100)로 판정. 걸린 룰 가중치의 합, 최대 100.</p>
          <label>⛔ 위험 (이상)
            <input type="number" min="1" max="100" value={thresholds.danger} onChange={(e) => setThreshold('danger', e.target.value)} />
          </label>
          <label>⚠ 의심 (이상)
            <input type="number" min="0" max="99" value={thresholds.suspicious} onChange={(e) => setThreshold('suspicious', e.target.value)} />
          </label>
          {invalid && <p className="error small">의심 기준은 위험 기준보다 낮아야 합니다.</p>}
        </div>
      </section>

      <section className="card">
        <h2>사용자</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>이메일</th><th>권한</th><th>Gmail 연동</th><th className="num">검사</th><th className="num">위험</th><th>마지막 동기화</th></tr>
            </thead>
            <tbody>
              {USERS.map((u) => (
                <tr key={u.email}>
                  <td>{u.email}</td>
                  <td>{u.role}</td>
                  <td>{u.gmail ? '✓ 연결됨' : <span className="error">✕ 토큰 만료</span>}</td>
                  <td className="num">{u.scans.toLocaleString()}</td>
                  <td className="num">{u.danger}</td>
                  <td className="nowrap muted">{u.synced}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}
