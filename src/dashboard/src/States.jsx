import { useState } from 'react'
import { EXAMPLES } from './examples'
import { Badge, STATES } from './ui'
import { ADVICE, MailDrawer, MailTable, NO_EVIDENCE, SUMMARY } from './Verdict'

// 과제에 적힌 순서
const ORDER = ['safe', 'suspicious', 'danger', 'incomplete', 'failed']

export default function States() {
  const [sel, setSel] = useState(null)
  const openState = (s) => setSel(EXAMPLES.find((e) => e.id === `ex-${s}`))

  return (
    <>
      <header className="page-head"><h1>판정 안내</h1></header>

      <section className="panel">
        <div className="panel-head"><h2>판정별 안내</h2></div>
        <div className="table-scroll">
          <table className="wrap guide clickable">
            <colgroup><col className="w-state" /><col /><col /><col className="w-advice" /><col className="w-chev" /></colgroup>
            <thead>
              <tr><th>판정</th><th>뜻</th><th>근거가 없으면</th><th>할 일</th><th><span className="sr-only">예시</span></th></tr>
            </thead>
            <tbody>
              {ORDER.map((s) => (
                <tr key={s} onClick={() => openState(s)}>
                  <td className="c"><button type="button" className="subj" aria-label={`${STATES[s].label} 예시 메일 보기`}><Badge v={s} /></button></td>
                  <td>{SUMMARY[s]}</td>
                  <td>{NO_EVIDENCE[s]}</td>
                  <td>{ADVICE[s][0] ?? '없음'}</td>
                  <td className="chev" aria-hidden="true" />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>예시 메일<span className="count">총 <b>{EXAMPLES.length}</b>건</span></h2></div>
        <MailTable rows={EXAMPLES} onSelect={setSel} />
      </section>

      {sel && <MailDrawer r={sel} onClose={() => setSel(null)} />}
    </>
  )
}
