// Gmail 수집 모듈 API (vite.config.js 프록시). 계약: src/gmail/docs/integration.md
const DOWN = 'Gmail 모듈에 연결하지 못했어요. 대시보드(npm run dev)와 수집 서버(src/gmail, 8765)가 둘 다 켜져 있는지 확인하세요.'

export async function gmail(path, csrf) {
  // fetch 자체 실패 = dev 서버가 꺼짐. 502 = dev 서버는 켜졌는데 수집 서버가 꺼짐
  const res = await fetch(path, csrf ? { method: 'POST', headers: { 'X-CSRF-Token': csrf } } : undefined).catch(() => { throw new Error(DOWN) })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw Object.assign(new Error(body.error?.message ?? DOWN), { code: body.error?.code })
  return body
}

// 이미 로그인(Gmail 연결)된 세션인지. 재연결이 필요하면 로그인 안 된 것으로 봄. 서버가 꺼져 있으면 false
export const isLoggedIn = () => gmail('/api/session').then((s) => s.authenticated && !s.needs_reconnect).catch(() => false)

export async function dynamic(path, { method = 'GET', body, signal } = {}) {
  const headers = {}
  if (method !== 'GET') {
    headers['X-CSRF-Token'] = (await gmail('/api/session')).csrf_token
    if (body !== undefined) headers['Content-Type'] = 'application/json'
  }
  const res = await fetch('/api/dynamic' + path, { method, headers, signal,
    body: body === undefined ? undefined : JSON.stringify(body) }).catch((error) => {
    if (error.name === 'AbortError') throw error
    throw new Error(DOWN)
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw Object.assign(new Error(data.error?.message ?? DOWN), { code: data.error?.code, status: res.status })
  return data
}

// 수집 메타데이터(GET /api/mails) -> 대시보드 행 모양(schema.sql 의 scans).
// m.detection 은 수집 서버가 정적 탐지기를 돌린 결과. null 이면 탐지기 미설정이라 검사 전(pending).
// 근거·LLM 점수는 아직 없음
export const toRow = (m) => {
  const d = m.detection
  const scanned = d?.status === 'ok'
  const missing = [
    ...m.warnings.filter((w) => w !== 'attachment_content_not_analyzed'), // 첨부는 아래 문장으로
    ...(m.attachments.length ? [`첨부파일 ${m.attachments.length}개 (내용 검사 안 함)`] : []),
  ]
  return {
    id: m.id,
    body_hash: m.body_hash,
    dynamic: m.dynamic ?? null,
    attachments_count: m.attachments.length,
    gmail_message_id: m.id,
    sender: m.sender,
    subject: m.subject,
    received_at: m.received_at,
    pending: !d,
    status: m.body_hash == null || d?.status === 'failed' ? 'failed' : m.truncated || missing.length ? 'incomplete' : 'ok',
    status_detail: m.body_hash == null ? '메일 본문을 가져오지 못했어요.' : d?.status === 'failed' ? '탐지 중 오류가 났어요.' : null,
    missing_parts: missing,
    static_score: scanned ? d.static_score : null, llm_score: null,
    final_score: scanned ? d.static_score : null, verdict: scanned ? d.verdict : null,
    types: [], evidence: [], llm_reason: null,
  }
}
