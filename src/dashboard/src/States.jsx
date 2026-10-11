import { EXAMPLES } from './mock'
import { MailTable, VerdictDetail } from './Verdict'

export default function States() {
  return (
    <>
      <header className="top">
        <div>
          <h1>판정 표시</h1>
          <p className="muted">정상·의심·위험·수집 불완전·분석 실패, 그리고 근거가 없을 때 목록과 상세 화면이 어떻게 보이는지 예시입니다.</p>
        </div>
      </header>

      <section className="card">
        <h2>메일 목록</h2>
        <p className="muted small">행을 누르면 아래 상세 예시로 이동합니다.</p>
        <MailTable rows={EXAMPLES} onSelect={(r) => document.getElementById(r.id).scrollIntoView({ behavior: 'smooth' })} />
      </section>

      <section className="states">
        {EXAMPLES.map((r) => (
          <article key={r.id} id={r.id} className="card">
            <p className="muted small">예시 · {r.title}</p>
            <h2>{r.subject}</h2>
            <p className="muted small">{r.sender}</p>
            <VerdictDetail r={r} />
          </article>
        ))}
      </section>
    </>
  )
}
