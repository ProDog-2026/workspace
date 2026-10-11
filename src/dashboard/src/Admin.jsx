import { DEFAULT_RULES, DEFAULT_THRESHOLDS } from './rules'

// 데모용 목업. 실제로는 Supabase 에서 관리자 권한으로 조회
const SYSTEM = [
  { name: 'Gmail API', status: 'good', text: '정상' },
  { name: 'Supabase', status: 'good', text: '정상' },
  { name: 'LLM API', status: 'warning', text: '느림 2.4초' },
  { name: '탐지 워커', status: 'good', text: '대기열 0건' },
]

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
      <header className="page-head">
        <h1>관리자<span className="tag">데모</span></h1>
        <button type="button" className="btn" onClick={() => { setRules(DEFAULT_RULES); setThresholds(DEFAULT_THRESHOLDS) }}>기본값으로 되돌리기</button>
      </header>
      <p className="note page-note">데모 화면이라 바꾼 값은 새로고침하면 처음으로 돌아가요.</p>

      <section className="sys-strip" aria-label="시스템 상태">
        {SYSTEM.map((s) => (
          <div key={s.name} className="sys-cell">
            <span className="sys-name">{s.name}</span>
            <span className={`sys-val ${s.status}`}><span className={`dot${s.status === 'good' ? '' : ' warn'}`} aria-hidden="true" />{s.text}</span>
          </div>
        ))}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>판정 기준</h2>
          <span className="note">최종 점수는 걸린 룰의 가중치를 더한 값이에요 (최대 100점).</span>
        </div>
        <table className="form-table">
          <tbody>
            <tr>
              <th scope="row"><label htmlFor="th-danger">위험 기준</label></th>
              <td><div className="cell">
                <input id="th-danger" type="number" min="1" max="100" value={thresholds.danger} onChange={(e) => setThreshold('danger', e.target.value)} />
                <span>점 이상</span>
              </div></td>
            </tr>
            <tr>
              <th scope="row"><label htmlFor="th-suspicious">의심 기준</label></th>
              <td><div className="cell">
                <input id="th-suspicious" type="number" min="0" max="99" value={thresholds.suspicious} onChange={(e) => setThreshold('suspicious', e.target.value)} />
                <span>점 이상</span>
                {invalid && <span className="error">의심 기준은 위험 기준보다 낮아야 해요.</span>}
              </div></td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>탐지 룰<span className="count">총 <b>{rules.length}</b>개</span></h2></div>
        <div className="table-scroll">
          <table className="wrap rules">
            <colgroup><col className="w-sw" /><col className="w-id" /><col /><col className="w-weight" /></colgroup>
            <thead><tr><th>사용</th><th>ID</th><th>룰</th><th className="num">가중치</th></tr></thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.id} className={r.on ? undefined : 'off'}>
                  <td className="c"><input type="checkbox" role="switch" checked={r.on} onChange={(e) => setRule(r.id, { on: e.target.checked })} aria-label={`${r.id} 사용`} /></td>
                  <td className="c"><span className="tag mono">{r.id}</span></td>
                  <td>{r.name}</td>
                  <td className="num"><input type="number" min="0" max="100" value={r.weight} onChange={(e) => setRule(r.id, { weight: Number(e.target.value) })} aria-label={`${r.id} 가중치`} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>사용자<span className="count">총 <b>{USERS.length}</b>명</span></h2></div>
        <div className="table-scroll">
          <table className="wrap users">
            <colgroup><col /><col className="w-role" /><col className="w-conn" /><col className="w-n" /><col className="w-n" /><col className="w-sync" /></colgroup>
            <thead><tr><th>이메일</th><th>권한</th><th>Gmail 연동</th><th className="num">검사</th><th className="num">위험</th><th>마지막 동기화</th></tr></thead>
            <tbody>
              {USERS.map((u) => (
                <tr key={u.email}>
                  <td>{u.email}</td>
                  <td className="c">{u.role}</td>
                  <td className="c">{u.gmail
                    ? <span className="conn"><span className="dot" aria-hidden="true" />연결됨</span>
                    : <span className="conn error"><span className="dot err" aria-hidden="true" />토큰 만료</span>}</td>
                  <td className="num">{u.scans.toLocaleString()}</td>
                  <td className="num">{u.danger}</td>
                  <td className="c time">{u.synced}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}
