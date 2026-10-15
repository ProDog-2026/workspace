import { DEFAULT_RULES, DEFAULT_THRESHOLDS } from './rules'

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

    </>
  )
}
