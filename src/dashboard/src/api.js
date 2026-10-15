// Gmail 수집 모듈 API (vite.config.js 프록시). 계약: src/gmail/docs/integration.md
const DOWN = 'Gmail 모듈에 연결하지 못했어요. 대시보드(npm run dev)와 수집 서버(src/gmail, 8765)가 둘 다 켜져 있는지 확인하세요.'

export async function gmail(path, csrf) {
  // fetch 자체 실패 = dev 서버가 꺼짐. 502 = dev 서버는 켜졌는데 수집 서버가 꺼짐
  const res = await fetch(path, csrf ? { method: 'POST', headers: { 'X-CSRF-Token': csrf } } : undefined).catch(() => { throw new Error(DOWN) })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw Object.assign(new Error(body.error?.message ?? DOWN), { code: body.error?.code })
  return body
}

// 수집 메타데이터(GET /api/mails) -> 대시보드 행 모양(schema.sql 의 scans).
// 탐지기 연동 전이라 판정·점수·근거는 비어 있음 (pending)
export const toRow = (m) => {
  const missing = [
    ...m.warnings.filter((w) => w !== 'attachment_content_not_analyzed'), // 첨부는 아래 문장으로
    ...(m.attachments.length ? [`첨부파일 ${m.attachments.length}개 (내용 검사 안 함)`] : []),
  ]
  return {
    id: m.id,
    gmail_message_id: m.id,
    sender: m.sender,
    subject: m.subject,
    received_at: m.received_at,
    pending: true,
    status: m.body_hash == null ? 'failed' : m.truncated || missing.length ? 'incomplete' : 'ok',
    status_detail: m.body_hash == null ? '메일 본문을 가져오지 못했어요.' : null,
    missing_parts: missing,
    static_score: null, llm_score: null, final_score: null, verdict: null,
    types: [], evidence: [], llm_reason: null,
  }
}
