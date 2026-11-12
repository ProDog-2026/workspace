// 데모용 근거 탐지. 실제 판정과 static_score는 ML 정적 탐지기가 만든다.

export const TYPES = {
  hidden_color: '흰 글씨 (글자색 = 배경색)',
  hidden_css: 'CSS 숨김 (display:none 등)',
  html_comment: 'HTML 주석',
  zero_width: '제로폭 문자',
  override: '지시 무시 문구',
  exfil_link: '외부 전송 링크 (이미지 포함)',
  encoded: '인코딩 은닉 (Base64 등)',
}

export const DEFAULT_RULES = [
  { id: 'R01', type: 'hidden_color', name: 'color:#fff 또는 white 글자 (배경 지정 없음)', on: true },
  { id: 'R02', type: 'hidden_css', name: 'display:none, font-size:0, opacity:0', on: true },
  { id: 'R03', type: 'html_comment', name: 'HTML 주석 안 명령형 문장', on: true },
  { id: 'R04', type: 'zero_width', name: '제로폭 문자 5개 이상', on: true },
  { id: 'R05', type: 'override', name: '"ignore previous instructions" 계열 문구', on: true },
  { id: 'R06', type: 'exfil_link', name: '마크다운 이미지, 템플릿 쿼리 URL', on: true },
  { id: 'R07', type: 'encoded', name: 'Base64 디코딩 결과에 명령문', on: true },
]

const OVERRIDE = /ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts)|disregard\s+(all\s+)?(previous|prior)|you are now|이전\s*(지시|명령|지침)\S*\s*(무시|잊)|지금부터\s*너는/i
const COMMAND = /assistant|\bAI\b|ignore|forward|send|reply|요약|전달|보내|출력|무시/i
const ZERO_WIDTH = /[​-‍⁠﻿]/g
const WHITE_TEXT = /(^|;)\s*color\s*:\s*(#fff\b|#ffffff\b|white\b|rgba?\(\s*255\s*,\s*255\s*,\s*255)/i
const HIDDEN_CSS = /display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(px|pt|em)?\s*(;|$)|opacity\s*:\s*0(\.0+)?\s*(;|$)/i
const EXFIL = /!\[[^\]]*\]\(\s*https?:\/\/[^)]+\)|https?:\/\/[^\s"'<>]+\?[^\s"'<>]*(\{\{|%7B%7B|\$\{)/gi
const BASE64 = /[A-Za-z0-9+/]{24,}={0,2}/g

const clip = (s) => s.replace(/\s+/g, ' ').trim().slice(0, 160)

export function detect(html, rules) {
  // DOMParser 는 스크립트를 실행하지 않고, 결과를 화면에 붙이지도 않음
  const body = new DOMParser().parseFromString(html, 'text/html').body
  const found = {}
  const add = (type, text) => (found[type] ??= []).push(clip(text))

  for (const el of body.querySelectorAll('[style]')) {
    const style = el.getAttribute('style')
    const text = el.textContent.trim()
    if (!text) continue
    // ponytail: 자기 style 만 봄. 부모 배경색까지 계산하려면 getComputedStyle 필요(서버에선 헤드리스 브라우저)
    if (WHITE_TEXT.test(style) && !/background/i.test(style)) add('hidden_color', text)
    else if (HIDDEN_CSS.test(style)) add('hidden_css', text)
  }

  const comments = [...html.matchAll(/<!--([\s\S]*?)-->/g)].map((m) => m[1])
  comments.filter((c) => COMMAND.test(c)).forEach((c) => add('html_comment', c))

  // AI 가 읽는 텍스트 = 숨김 요소 + 주석까지 전부
  const aiText = [body.textContent, ...comments].join('\n')
  const zw = aiText.match(ZERO_WIDTH)?.length ?? 0
  if (zw >= 5) add('zero_width', `제로폭 문자 ${zw}개`)
  const clean = aiText.replace(ZERO_WIDTH, '')

  const m = clean.match(OVERRIDE)
  if (m) add('override', clean.slice(Math.max(0, m.index - 20), m.index + 100))

  for (const [url] of html.matchAll(EXFIL)) add('exfil_link', url)

  for (const [b64] of clean.matchAll(BASE64)) {
    try {
      const decoded = new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)))
      if (COMMAND.test(decoded)) add('encoded', `${b64.slice(0, 20)}… → "${decoded}"`)
    } catch { /* base64 아님 */ }
  }

  const hits = rules.filter((r) => r.on && found[r.type]).map((r) => ({ ...r, snippets: found[r.type] }))
  return { hits }
}
