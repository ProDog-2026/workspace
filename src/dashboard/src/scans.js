export const SCAN_PAGE_SIZE = 100
export const stateOf = (r) =>
  r.verdict === 'danger' || r.verdict === 'suspicious' ? r.verdict
    : r.status === 'failed' ? 'failed'
      : r.pending || r.verdict == null ? 'pending'
        : r.status === 'incomplete' ? 'incomplete'
          : r.status === 'ok' && r.verdict === 'safe' ? 'safe' : 'failed'
const VERDICTS = ['safe', 'suspicious', 'danger']
const strings = (value) => Array.isArray(value) ? value.filter((v) => typeof v === 'string') : []
const score = (value) => Number.isFinite(value) && value >= 0 && value <= 100 ? value : null

// VITE 변수는 공개된다. 잘못 붙여 넣은 서버 키를 클라이언트에 사용하지 않는다.
export function browserKey(key) {
  try { return JSON.parse(atob(key.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).role === 'anon' }
  catch { return false }
}

export function scanRow(row) {
  const verdict = VERDICTS.includes(row.verdict) ? row.verdict : null
  const status = ['ok', 'incomplete', 'failed'].includes(row.status) ? row.status : 'failed'
  const missing = strings(row.missing_parts)
  return {
    ...row, verdict, status,
    pending: verdict === null && status !== 'failed',
    static_score: score(row.static_score), llm_score: score(row.llm_score), final_score: score(row.final_score),
    missing_parts: status === 'incomplete' && !missing.length ? ['메일 일부를 분석하지 못했어요. 빠진 부분의 안전 여부는 알 수 없어요.'] : missing,
    types: strings(row.types),
    evidence: Array.isArray(row.evidence) ? row.evidence.filter((e) => e && typeof e.snippet === 'string').map((e) => ({
      rule: typeof e.rule === 'string' ? e.rule : '', type: typeof e.type === 'string' ? e.type : '', snippet: e.snippet,
    })) : [],
    llm_reason: typeof row.llm_reason === 'string' ? row.llm_reason : null,
    source: 'supabase',
  }
}

export async function readScans(client, userId, offset = 0, signal) {
  if (!userId) throw new Error('로그인 후 검사 결과를 조회하세요.')
  let query = client.from('scans').select('id,user_id,gmail_message_id,sender,subject,received_at,scanned_at,status,status_detail,missing_parts,static_score,llm_score,final_score,verdict,types,evidence,llm_reason')
    .eq('user_id', userId).order('received_at', { ascending: false }).order('id', { ascending: false })
    .range(offset, offset + SCAN_PAGE_SIZE - 1)
  if (signal) query = query.abortSignal(signal)
  const { data, error } = await query
  if (error) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    throw new Error(error.code === '42501' ? '검사 결과 조회 권한을 확인해 주세요.' : '검사 결과를 불러오지 못했어요. 로그인 상태와 scans 뷰 설정을 확인하세요.')
  }
  if (!Array.isArray(data) || data.some((r) => r.user_id !== userId)) throw new Error('검사 결과의 사용자 정보를 확인하지 못했어요.')
  return data.map(scanRow)
}

export function dailyCounts(rows, span, now = new Date()) {
  return Array.from({ length: span }, (_, i) => {
    const from = new Date(now)
    from.setHours(0, 0, 0, 0)
    from.setDate(from.getDate() - (span - 1 - i))
    const to = new Date(from)
    to.setDate(to.getDate() + 1)
    const daily = rows.filter((r) => new Date(r.received_at) >= from && new Date(r.received_at) < to)
    const last = i === span - 1
    return {
      key: from.toISOString(), label: last ? '오늘' : `${from.getMonth() + 1}월 ${from.getDate()}일`,
      short: last ? '오늘' : `${from.getMonth() + 1}/${from.getDate()}`,
      danger: daily.filter((r) => r.verdict === 'danger').length,
      suspicious: daily.filter((r) => r.verdict === 'suspicious').length,
    }
  })
}
