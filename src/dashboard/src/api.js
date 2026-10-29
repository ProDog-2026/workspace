// Gmail 수집 모듈 API (vite.config.js 프록시). 계약: src/gmail/docs/integration.md
const DOWN = 'Gmail 모듈에 연결하지 못했어요. 대시보드(npm run dev)와 수집 서버(src/gmail, 8765)가 둘 다 켜져 있는지 확인하세요.'

export async function gmail(path, csrf, payload, options = {}) {
  // fetch 자체 실패 = dev 서버가 꺼짐. 502 = dev 서버는 켜졌는데 수집 서버가 꺼짐
  const res = await fetch(path, {
    ...options, credentials: 'same-origin', mode: 'same-origin',
    ...(csrf ? {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrf, ...(payload !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      ...(payload !== undefined ? { body: JSON.stringify(payload) } : {}),
    } : {}),
  }).catch((e) => { if (e.name === 'AbortError') throw e; throw new Error(DOWN) })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw Object.assign(new Error(body.error?.message ?? DOWN), { code: body.error?.code })
  return body
}

export const DETECTOR_NAMES = { tfidf: '기존 ML(해시 특징)', lexical: '기존 ML(해시 특징)', transformer: 'Transformer' }
const thresholdsOf = (r) => Number.isFinite(r.suspicious_threshold) && Number.isFinite(r.danger_threshold)
  ? { suspicious: r.suspicious_threshold * 100, danger: r.danger_threshold * 100 } : null

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
    static_thresholds: completed ? thresholdsOf(result) : null,
    // 대표 점수와 판정은 서버 값을 유지한다. 서로 다른 모델의 점수는 비교하지 않는다.
    static_detectors: completed ? ['tfidf', 'transformer'].filter((id) => result.detectors?.[id]).map((id) => {
      const detector = result.detectors[id]
      return {
        id, label: DETECTOR_NAMES[id],
        score: Number.isFinite(detector.score) ? detector.score * 100 : null,
        verdict: ['safe', 'suspicious', 'danger'].includes(detector.verdict) ? detector.verdict : null,
        thresholds: thresholdsOf(detector),
      }
    }) : [],
    types: [], evidence: [], llm_reason: null,
  }
}
