// Gmail 수집 모듈 API (vite.config.js 프록시). 계약: src/gmail/docs/integration.md
import { supabase } from './supabase'
import { detectorFields } from './scans'
export { DETECTOR_NAMES } from './scans'

const DOWN = 'Gmail 모듈에 연결하지 못했어요. 대시보드(npm run dev)와 수집 서버(src/gmail, 8765)가 둘 다 켜져 있는지 확인하세요.'
const sessions = new Map()

export async function gmail(path, csrf, payload, options = {}) {
  options.signal?.throwIfAborted()
  const auth = supabase ? await supabase.auth.getSession() : null
  if (auth?.error) throw new Error('앱 로그인 상태를 확인하지 못했어요. 다시 로그인하세요.')
  const token = auth?.data.session?.access_token ?? ''
  const run = async () => {
    const headers = new Headers(options.headers)
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (csrf) headers.set('X-CSRF-Token', csrf)
    if (payload !== undefined) headers.set('Content-Type', 'application/json')
    const res = await fetch(path, {
      ...options, headers, credentials: 'same-origin', mode: 'same-origin', cache: 'no-store',
      signal: path === '/api/session' ? undefined : options.signal,
      ...(csrf ? { method: 'POST' } : {}),
      ...(payload !== undefined ? { body: JSON.stringify(payload) } : {}),
    }).catch((e) => { if (e.name === 'AbortError') throw e; throw new Error(DOWN) })
    const body = await res.json().catch(() => { throw new Error(DOWN) })
    if (!res.ok) throw Object.assign(new Error(body.error?.message ?? DOWN), { code: body.error?.code })
    return body
  }
  let pending
  if (path === '/api/session' && !csrf) {
    if (!sessions.has(token)) sessions.set(token, run().finally(() => sessions.delete(token)))
    pending = sessions.get(token)
  } else pending = run()
  const body = await pending
  options.signal?.throwIfAborted()
  return body
}

// 수집 상태와 정적 판정은 유지하고 동적 실행 상태는 별도로 표시한다.
export const toRow = (m) => {
  const analysis = m.analysis ?? m.metadata?.analysis ?? null
  const detection = m.detection
  const result = analysis ? analysis.static : detection ? {
    ...detection,
    status: detection.status === 'ok' ? 'completed' : 'error',
    score: Number.isFinite(detection.static_score) ? detection.static_score / 100 : null,
  } : null
  const staticFailed = analysis?.status === 'static_error' || ['error', 'failed'].includes(result?.status) || !!result?.error
  const completed = !staticFailed && result?.status === 'completed' && ['safe', 'suspicious', 'danger'].includes(result.verdict)
  const failed = staticFailed || (!completed && ['error', 'cancelled'].includes(analysis?.status))
  const score = completed && Number.isFinite(result.score) ? result.score * 100 : null
  const missing = [
    ...(m.warnings ?? []).filter((w) => w !== 'attachment_content_not_analyzed'), // 첨부는 아래 문장으로
    ...(m.attachments?.length ? [`첨부파일 ${m.attachments.length}개 (내용 검사 안 함)`] : []),
  ]
  return {
    id: m.id,
    gmail_message_id: m.id,
    sender: m.sender,
    subject: m.subject,
    received_at: m.received_at,
    mail: m,
    analysis,
    pending: !completed && !failed,
    status: m.body_hash == null || failed ? 'failed' : m.truncated || missing.length ? 'incomplete' : 'ok',
    status_detail: m.body_hash == null ? '메일 본문을 가져오지 못했어요.' : failed ? result?.error?.message ?? analysis?.error?.message ?? '정적 분석 결과를 얻지 못했어요.' : null,
    missing_parts: missing,
    static_score: score, llm_score: null, final_score: score, verdict: completed ? result.verdict : null,
    ...detectorFields(completed ? result : null),
    types: [], evidence: [], llm_reason: null,
  }
}

// Baseline and the original private.txt are not exposure evidence.
function fileExposure(snapshot) {
  if (!Array.isArray(snapshot?.files)) return null
  let known = snapshot.complete === true
  for (const file of snapshot.files) {
    if (file?.path === '/workspace/private.txt') continue
    if (typeof file?.path !== 'string' || !file.path.startsWith('/workspace/')) { known = false; continue }
    if (file.contains_canary === true) return true
    if (file.contains_canary !== false) known = false
  }
  return known ? false : null
}

export function canaryExposureObserved(record) {
  if (!record) return null
  if (['canary_exposure_observed', 'canary_files_observed', 'canary_target_observed', 'canary_output_observed']
    .some((key) => record[key] === true)) return true
  const captures = [record.observations, ...(record.events || []).filter((event) => event.type === 'files').map((event) => event.data)]
  if (captures.some((capture) => capture?.canary_leaked === true || capture?.canary_files_exposed === true || fileExposure(capture) === true)) return true
  const outputsComplete = record.canary_output_observed === false && !(record.events || [])
    .some((event) => event.type === 'tool_result' && event.data?.capture_truncated === true)
  if (record.status === 'completed' && outputsComplete && captures.every((capture) => fileExposure(capture) === false)) return false
  return null
}
