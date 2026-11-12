import { DEFAULT_RULES } from './rules'

export default function Admin({ rules, setRules }) {
  const setRule = (id, patch) => setRules(rules.map((r) => (r.id === id ? { ...r, ...patch } : r)))

  return (
    <>
      <header className="page-head">
        <h1>관리자<span className="tag">데모</span></h1>
        <button type="button" className="btn" onClick={() => setRules(DEFAULT_RULES)}>기본값으로 되돌리기</button>
      </header>
      <p className="note page-note">데모 화면이라 바꾼 값은 새로고침하면 처음으로 돌아가요.</p>

      <section className="panel">
        <div className="panel-head"><h2>탐지 룰<span className="count">총 <b>{rules.length}</b>개</span></h2></div>
        <p id="weight-note" className="note">가중치는 0~100 사이의 숫자만 적용해요. 빈 값이나 잘못된 입력은 이전 가중치를 유지해요.</p>
        <div className="table-scroll">
          <table className="wrap rules">
            <colgroup><col className="w-sw" /><col className="w-id" /><col /></colgroup>
            <thead><tr><th>사용</th><th>ID</th><th>근거 탐지 룰</th></tr></thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.id} className={r.on ? undefined : 'off'}>
                  <td className="c"><input type="checkbox" role="switch" checked={r.on} onChange={(e) => setRule(r.id, { on: e.target.checked })} aria-label={`${r.id} 사용`} /></td>
                  <td className="c"><span className="tag mono">{r.id}</span></td>
                  <td>{r.name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

    </>
  )
}
