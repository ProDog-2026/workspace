const ACTIVE = new Set(['queued', 'submitting', 'running', 'stopping', 'unreachable'])
const LABELS = { queued: '검사 대기', submitting: '실행 준비', running: '검사 중', stopping: '중지 처리 중',
  unreachable: '연결 확인 필요', stopped: '중지됨', failed: '검사 실패', incomplete: '검사 미완료',
  unavailable: '동적 검사 연결 필요', not_configured: '모델 미설정' }
export const isActive = (job) => ACTIVE.has(job?.status)
export const eligible = (mail) => mail.detection?.status === 'ok' && ['suspicious', 'danger'].includes(mail.detection.verdict)
export function dynamicLabel(job) {
  if (!job) return '대상 등록 전'
  if (job.observation === 'observed') return job.status === 'completed' ? '이상 행동 관찰' : '이상 행동 관찰 · 미완료'
  if (job.status === 'completed' && job.observation === 'not_observed') return '이상 행동 미관찰'
  return LABELS[job.status] ?? '결과 확인 필요'
}
export const dynamicError = (code) => ({
  dynamic_not_configured: '동적 검사 페이지에서 모델을 선택하고 설정을 저장하세요.',
  dynamic_unavailable: '동적 검사 서버에 연결할 수 없습니다. 서버를 실행한 뒤 다시 확인하세요.',
  submission_unknown: '실행 접수 여부가 불확실합니다. 실험 서버의 실행 기록을 먼저 확인하세요.',
  dynamic_incomplete: '시간·호출 한도 또는 오류로 검사가 끝나지 않았습니다.',
  dynamic_storage_error: '증거 저장에 실패했습니다. 실험 서버에서 컨테이너를 확인하세요.',
  docker_runtime_not_configured: 'Docker 실행 환경이 설정되지 않았습니다. 실험 서버를 준비한 뒤 다시 검사하세요.',
  prompt_changed: '대기 중 프롬프트가 바뀌었습니다. 현재 설정으로 다시 검사하세요.',
  experiment_interrupted: '서버가 재시작돼 대기 중이던 실험 본문이 사라졌습니다. 다시 입력하세요.',
  run_not_found: '실험 서버에 이 실행이 없습니다. 서버 재시작·기록 삭제 여부를 확인하세요.',
  dynamic_invalid_response: '실험 서버의 결과를 해석할 수 없습니다.',
}[code] ?? (code ? '검사 상태를 확인하지 못했습니다. 설정과 실행 기록을 확인하세요.' : ''))
