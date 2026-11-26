import { UNREACHABLE } from './api.js'

export const isPromoted = (r) =>
  !r.pending && r.verdict === 'suspicious' && r.dynamic?.observation === 'observed'

// 위험·의심 근거가 있으면 수집이 불완전해도 그 판정을 먼저 보여 줌.
export function stateOf(r) {
  if (r.pending) return r.status === 'failed' ? 'failed' : 'pending'
  if (isPromoted(r)) return 'danger'
  if (r.verdict === 'danger' || r.verdict === 'suspicious') return r.verdict
  return r.status === 'ok' ? r.verdict ?? 'failed' : r.status
}

// 받은편지함 목록을 보여 줄 수 없을 때의 안내. null 이면 목록을 그대로 보여 줌.
// 상태마다 "무슨 일인지(title) + 무엇을 하면 되는지(body) + 다음 행동 하나(action)"를 다르게 줌.
// action.href 가 없으면 다시 불러오기 버튼
const RETRY = { label: '다시 시도' }

export function inboxState(error, scans) {
  if (error) {
    switch (error.code) {
      case UNREACHABLE:
        return { kind: 'unreachable', title: 'ProDog 서버에 연결하지 못했어요', action: RETRY,
          body: '검사 서버가 꺼져 있는 것 같아요. 워크스페이스 폴더의 run.cmd(macOS는 run.command)를 실행한 뒤 다시 시도하세요.' }
      case 'authentication_required':
        return { kind: 'disconnected', title: 'Gmail이 아직 연결되지 않았어요', action: { label: 'Gmail 연결하기', href: '#/login' },
          body: 'Google 계정으로 로그인하면 받은편지함을 검사해요. 메일 읽기 권한만 요청해요.' }
      case 'reconnect_required':
        return { kind: 'reconnect', title: 'Gmail 권한이 만료됐어요', action: { label: '다시 연결하기', href: '#/settings' },
          body: '설정에서 Gmail을 다시 연결하면 이어서 검사해요. 이전 판정이 지금도 맞는지는 알 수 없어요.' }
      case 'rate_limited':
        return { kind: 'rate_limited', title: '요청이 너무 많았어요', action: RETRY,
          body: '잠시 기다린 뒤 다시 시도하세요.' }
      default:
        return { kind: 'error', title: '받은편지함을 불러오지 못했어요', action: RETRY,
          body: error.message || '원인을 알 수 없어요. 다시 시도해도 같으면 서버 로그를 확인하세요.' }
    }
  }
  if (!scans) return { kind: 'loading', title: '받은편지함을 불러오는 중이에요', action: null, body: '메일마다 숨은 명령이 있는지 확인하고 있어요.' }
  if (!scans.length) {
    return { kind: 'empty', title: '받은편지함에 메일이 없어요', action: { label: '새로고침' },
      body: '새 메일이 오면 여기에서 판정을 볼 수 있어요. 검사한 메일이 없다는 뜻이지 안전하다는 뜻은 아니에요.' }
  }
  return null
}
