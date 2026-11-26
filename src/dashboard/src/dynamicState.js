const ACTIVE = new Set(['queued', 'submitting', 'running', 'stopping', 'unreachable'])
const LABELS = { queued: '검사 대기', submitting: '실행 준비', running: '검사 중', stopping: '중지 처리 중',
  unreachable: '연결 확인 필요', stopped: '중지됨', failed: '검사 실패', incomplete: '검사 미완료',
  unavailable: '동적 검사 연결 필요', not_configured: '모델 미설정' }
export const isActive = (job) => ACTIVE.has(job?.status)
export const canStartDynamic = (row) => !row.pending && row.status !== 'failed' && ['suspicious', 'danger'].includes(row.verdict)
export const isLinkedDynamicJob = (job) => job?.source === 'gmail'
export const matchesDynamicJob = (row, job) => Boolean(row.body_hash && job && job.mail_id === row.id && job.body_hash === row.body_hash)

export function mergeDynamicJob(row, job) {
  if (!matchesDynamicJob(row, job)) return row
  const previous = row.dynamic
  if (previous && isLinkedDynamicJob(previous) !== isLinkedDynamicJob(job)) {
    return isLinkedDynamicJob(job) ? { ...row, dynamic: job } : row
  }
  if (previous && (previous.created_at > job.created_at || (previous.id === job.id && previous.updated_at >= job.updated_at))) return row
  return { ...row, dynamic: job }
}

export function dynamicLabel(job) {
  if (!job) return '대상 등록 전'
  if (job.observation === 'observed') return job.status === 'completed' ? '이상 행동 관찰' : '이상 행동 관찰 · 미완료'
  if (job.status === 'completed' && job.observation === 'not_observed') return '이상 행동 미관찰'
  return LABELS[job.status] ?? '결과 확인 필요'
}
// 메일 상세용 상태. 미실행·진행 중·실패·미완료 어느 것도 '안전'으로 읽히지 않게 문구를 고정함
const NOT_SAFE = '안전하다는 뜻은 아니에요.'
export function dynamicStatus(row) {
  const job = row.dynamic
  if (!job) {
    const runnable = canStartDynamic(row)
    const note = row.pending || row.status === 'failed' ? '1차 검사 결과가 없어 동적 검사를 시작하지 않았어요.'
      : runnable ? `아직 실행하지 않았어요. ${NOT_SAFE}`
      : '메일 본문 정적 검사에서 판정이 끝났어요. 동적 검사는 본문이 의심·위험인 메일에 실행해요.'
    return { phase: 'none', label: '실행 안 함', note, runnable }
  }
  const label = dynamicLabel(job)
  if (isActive(job)) return { phase: 'active', label, note: `검사가 끝날 때까지 AI에게 맡기지 마세요. 끝나면 결과가 여기에 나와요.` }
  if (job.status === 'completed') return { phase: 'done', label, note: job.observation === 'observed'
    ? '요약 외 도구 요청 또는 테스트 환경의 효과가 관찰됐어요. 메일 지시를 따랐다고 확정한 것은 아니에요.'
    : '이번 실행에선 이상 행동이 없었어요. 안전하다는 보장은 아니에요.' }
  if (job.status === 'incomplete') return { phase: 'incomplete', label, note: `시간·호출 한도나 오류로 검사가 끝나지 않았어요. ${NOT_SAFE}` }
  return { phase: 'failed', label, note: `동적 검사가 실패했거나 중지됐어요. ${NOT_SAFE}` }
}
export const dynamicError = (code) => ({
  dynamic_not_configured: '동적 검사 페이지에서 모델을 선택하고 설정을 저장하세요.',
  dynamic_unavailable: '동적 검사 서버에 연결할 수 없습니다. 서버를 실행한 뒤 다시 확인하세요.',
  submission_unknown: '실행 접수 여부가 불확실합니다. 실험 서버의 실행 기록을 먼저 확인하세요.',
  dynamic_incomplete: '시간·호출 한도 또는 오류로 검사가 끝나지 않았습니다.',
  dynamic_storage_error: '증거 저장에 실패했습니다. 실험 서버에서 컨테이너를 확인하세요.',
  docker_runtime_not_configured: 'Docker 실행 환경이 설정되지 않았습니다. 실험 서버를 준비한 뒤 다시 검사하세요.',
  prompt_changed: '대기 중 프롬프트가 바뀌었습니다. 현재 설정으로 다시 검사하세요.',
  mail_changed: '메일 내용이나 PDF 추출 결과가 달라졌습니다. 다시 검사하세요.',
  experiment_interrupted: '서버가 재시작돼 대기 중이던 실험 본문이 사라졌습니다. 다시 입력하세요.',
  run_not_found: '실험 서버에 이 실행이 없습니다. 서버 재시작·기록 삭제 여부를 확인하세요.',
  dynamic_invalid_response: '실험 서버의 결과를 해석할 수 없습니다.',
}[code] ?? (code ? '검사 상태를 확인하지 못했습니다. 설정과 실행 기록을 확인하세요.' : ''))
