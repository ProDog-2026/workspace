import { useState } from 'react'
import { scanFiles } from './fileScan.js'
import { pdfResultLabel } from './api'
import './FileScan.css'

const labels = { safe: '정상', suspicious: '의심', danger: '위험' }
const pdfStatus = {
  encrypted: '암호화되어 내용 검사 불가', invalid: '잘못된 PDF', no_text: '추출할 텍스트 없음',
  limit: '처리 한도 초과',
}

function FileResult({ mail }) {
  const d = mail.detection
  const warnings = (mail.warnings || []).filter((warning) => !['received_at_from_date_header', 'date_timezone_missing_assumed_utc', 'internal_date_invalid', 'date_header_invalid'].includes(warning))
  const partial = mail.truncated || warnings.length || mail.attachments?.some((a) => a.pdf_status !== 'parsed')
    || d?.status !== 'ok' || d?.pdf_results?.some((r) => r.status !== 'ok')
  return <div className="file-scan-result" aria-live="polite">
    <h3>파일 검사 결과</h3>
    {partial && <p className="file-scan-warning">일부 내용을 검사하지 못했거나 잘렸습니다. 아래 상태를 확인하세요.</p>}
    <p><strong>메일 본문 정적 검사</strong> · {d?.status === 'ok' && labels[d.verdict]
      ? `${labels[d.verdict]} · ${d.static_score}점` : '검사 실패'}</p>
    <h4>첨부파일</h4>
    {mail.attachments?.length ? <ul>{mail.attachments.map((file, i) => <li key={i}>
      {file.filename || '(이름 없음)'} · {file.pdf_status === 'parsed' ? pdfResultLabel(d?.pdf_results, i)
        : pdfStatus[file.pdf_status] || '내용 검사 제외'}
    </li>)}</ul> : <p>검사할 첨부파일이 없습니다.</p>}
    {!!warnings.length && <p className="file-scan-warning">메일을 읽는 과정에서 일부 내용이 제한되거나 올바르게 해석되지 않았습니다.</p>}
  </div>
}

export default function FileScan() {
  const [eml, setEml] = useState(null)
  const [pdf, setPdf] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  const submit = async (event) => {
    event.preventDefault()
    setError(''); setResult(null)
    if (!eml || !eml.name.toLowerCase().endsWith('.eml')) { setError('EML 파일을 선택해 주세요.'); return }
    if (eml.size > 2 * 1024 * 1024) { setError('EML 파일은 2 MB 이하여야 합니다.'); return }
    if (pdf && !pdf.name.toLowerCase().endsWith('.pdf')) { setError('PDF 파일을 선택해 주세요.'); return }
    if (pdf && pdf.size > 1024 * 1024) { setError('PDF 파일은 1 MB 이하여야 합니다.'); return }
    setBusy(true)
    try { const data = await scanFiles(eml, pdf); setResult(data.mail) }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  return <section className="panel file-scan">
    <div className="panel-head"><h2>EML 파일 정적 검사</h2></div>
    <form className="file-scan-form" onSubmit={submit}>
      <p>EML에 포함된 PDF도 검사하며, 별도 PDF를 추가할 수 있습니다.</p>
      <label>메일 파일 (필수)<input type="file" accept=".eml,message/rfc822" disabled={busy} onChange={(e) => { setEml(e.target.files?.[0] || null); setResult(null); setError('') }} /></label>
      <label>PDF 첨부 (선택)<input type="file" accept=".pdf,application/pdf" disabled={busy} onChange={(e) => { setPdf(e.target.files?.[0] || null); setResult(null); setError('') }} /></label>
      <p className="note">EML 최대 2 MB · PDF 각각 최대 1 MB, 20페이지 · 총 4개, 추출 텍스트 합계 4만 자. EML에 같은 PDF가 있으면 한 번만 검사합니다.</p>
      {error && <p className="file-scan-error" role="alert">{error}</p>}
      <div><button className="btn primary" type="submit" disabled={busy || !eml}>{busy ? '검사 중…' : '파일 검사하기'}</button></div>
    </form>
    {result && <FileResult mail={result} />}
  </section>
}
