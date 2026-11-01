import { useState } from 'react'
import { stateOf } from './scans'
export { stateOf } from './scans'

export const STATES = {
  danger: { label: '위험' },
  suspicious: { label: '의심' },
  safe: { label: '정상' },
  incomplete: { label: '수집 불완전' },
  failed: { label: '분석 실패' },
  pending: { label: '검사 전' },
}

// 위험·의심 근거가 있으면 수집/분석이 덜 끝났어도 그 판정을 먼저 보여줌 (위험을 '확인 필요' 뒤에 숨기지 않기).
// 판정이 비어 있으면 절대 '정상'으로 떨어뜨리지 않음.
// pending = 수집만 됐고 탐지기를 아직 안 거침. 수집 실패는 그대로 실패로 보여줌
// 목록 정렬: 위험 → 의심 → 수집 불완전 → 분석 실패 → 정상, 같은 상태 안에서는 최신순
const RANK = { danger: 0, suspicious: 1, incomplete: 2, failed: 3, pending: 4, safe: 5 }
export const bySeverity = (a, b) => RANK[stateOf(a)] - RANK[stateOf(b)] || (b.received_at ?? '').localeCompare(a.received_at ?? '')

export const fmtTime = (iso) => new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

// 목록용 짧은 시각: '오늘 05:36' / '어제 23:10' / '10/9'
const pad = (n) => String(n).padStart(2, '0')
const dayOf = (d) => new Date(d).setHours(0, 0, 0, 0)
export const fmtShort = (iso) => {
  const d = new Date(iso)
  const ago = Math.round((dayOf(Date.now()) - dayOf(d)) / 86400000)
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return ago === 0 ? `오늘 ${hm}` : ago === 1 ? `어제 ${hm}` : `${d.getMonth() + 1}/${d.getDate()}`
}

// 판정 마크: 16px 손그림 SVG. 흑백에서도 모양만으로 구분 (채운 팔각형 / 빈 삼각형 / 체크 원 / 반 채운 원 / 점선 원+사선)
const SHAPES = {
  danger: (
    <>
      <path d="M5.3 1.5h5.4l3.8 3.8v5.4l-3.8 3.8H5.3l-3.8-3.8V5.3z" fill="currentColor" stroke="none" />
      <path className="cut" d="M4.8 8h6.4" strokeWidth="1.8" />
    </>
  ),
  suspicious: (
    <>
      <path d="M8 1.8 14.6 13.6H1.4z" />
      <path d="M8 6.4v3.4" strokeWidth="1.6" />
      <circle cx="8" cy="11.7" r=".9" fill="currentColor" stroke="none" />
    </>
  ),
  safe: (
    <>
      <circle cx="8" cy="8" r="6.25" />
      <path d="M5.3 8.2l1.8 1.8 3.6-3.9" strokeWidth="1.6" />
    </>
  ),
  incomplete: (
    <>
      <circle cx="8" cy="8" r="6.25" />
      <path d="M8 1.75a6.25 6.25 0 0 1 0 12.5z" fill="currentColor" stroke="none" />
    </>
  ),
  failed: (
    <>
      <circle cx="8" cy="8" r="6.25" strokeDasharray="2.2 2" />
      <path d="M4.8 11.2l6.4-6.4" />
    </>
  ),
  pending: (
    <>
      <circle cx="8" cy="8" r="6.25" strokeDasharray="2.2 2" />
      <path d="M8 4.8V8l2.2 1.4" />
    </>
  ),
}

export function Mark({ v, size = 16 }) {
  return (
    <svg className={`mark mk-${v}`} width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {SHAPES[v]}
    </svg>
  )
}

// 목록·표의 판정 라벨: 마크 + 글자. 위험만 빨간 채움, 나머지는 테두리만 (알약·옅은 배경 없음)
export function Badge({ v }) {
  return <span className={`badge ${v}`}><Mark v={v} size={12} />{STATES[v].label}</span>
}

// ProDog 로고: ProDog-2026 GitHub 조직 프로필 이미지
export function Logo({ size = 20 }) {
  return <img src="/prodog-logo.png" width={size} height={size} alt="" />
}

export function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false">
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  )
}

// 라이트/다크 전환. 처음엔 시스템 설정(index.html 이 정함)을 따르고, 누른 뒤로는 선택을 저장
export function ThemeToggle() {
  const [theme, setTheme] = useState(() => typeof document === 'undefined' ? 'light' : document.documentElement.dataset.theme)
  const next = theme === 'dark' ? 'light' : 'dark'
  const flip = () => {
    document.documentElement.dataset.theme = next
    try { localStorage.setItem('theme', next) } catch { /* 저장 못 해도 이번 화면에는 적용됨 */ }
    setTheme(next)
  }
  return (
    <button type="button" className="btn theme-toggle" onClick={flip} aria-label={next === 'dark' ? '다크 모드로 전환' : '라이트 모드로 전환'}>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        {theme === 'dark'
          ? <><circle cx="8" cy="8" r="3" /><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M12.6 3.4l-1 1M4.4 11.6l-1 1" /></>
          : <path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7z" />}
      </svg>
    </button>
  )
}
