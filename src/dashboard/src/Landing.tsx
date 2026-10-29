import { useEffect, useState } from 'react'
import { motion, useAnimate } from 'motion/react'
import { ChevronRight } from 'lucide-react'
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import './landing.css'

const cn = (...c: ClassValue[]) => twMerge(clsx(c))

// 배경 장면: 비스듬히 누운 메일 타일 격자, 가운데 한 장이 위험으로 탐지돼 빨갛게 빛남 (영상 대신 코드로 그림)
const N = 7
const HOT = 3 * N + 3 // 가운데 타일

function Envelope({ alert }: { alert?: boolean }) {
  return (
    <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="6" y="11" width="36" height="26" rx="5" />
      <path d="M7.5 13.5 24 26l16.5-12.5" />
      {alert && (
        <g>
          <circle cx="41" cy="10" r="7" fill="#fff" stroke="#e5484d" strokeWidth="2" />
          <path d="M41 6.6v3.8" stroke="#e5484d" strokeWidth="2.4" />
          <circle cx="41" cy="13.4" r="1.2" fill="#e5484d" stroke="none" />
        </g>
      )}
    </svg>
  )
}

type Rect = { x: number; y: number; w: number; h: number }

function MailScene({ onOpen, disabled }: { onOpen: (r: Rect) => void; disabled: boolean }) {
  return (
    <div className="scene">
      <div className="scene-plane">
        {Array.from({ length: N * N }, (_, i) => {
          const r = Math.floor(i / N), c = i % N
          const d = Math.max(Math.abs(r - 3), Math.abs(c - 3)) // 가운데로부터 거리
          if (i === HOT) {
            // 빨간 메일 = 대시보드로 들어가는 버튼
            return (
              <button key={i} type="button" className="tile hot" aria-label="위험 메일 확인하기" disabled={disabled}
                onClick={(e) => { const b = e.currentTarget.getBoundingClientRect(); onOpen({ x: b.left, y: b.top, w: b.width, h: b.height }) }}>
                <Envelope alert />
              </button>
            )
          }
          return (
            <div key={i} className={`tile${d === 1 ? ' near' : ''}`} style={{ ['--d' as string]: `${((r + c) % 5) * -1.2}s` }}>
              <Envelope />
            </div>
          )
        })}
        <div className="scene-scan" />
      </div>
      <div className="scene-fade" />
    </div>
  )
}

// 남의 회사 로고를 넣으면 협력사처럼 보여서, 실제로 쓰는 기술 스택 로고로 채움
type Brand = { src: string; alt: string; gradient: { from: string; to: string } }
const LOGOS: Brand[] = [
  { src: 'https://svgl.app/library/gmail.svg', alt: 'Gmail', gradient: { from: '#fde2e1', to: '#fbbcb8' } },
  { src: 'https://svgl.app/library/google-cloud.svg', alt: 'Google Cloud', gradient: { from: '#e0f2fe', to: '#bae6fd' } },
  { src: 'https://svgl.app/library/supabase.svg', alt: 'Supabase', gradient: { from: '#dcfce7', to: '#bbf7d0' } },
  { src: 'https://svgl.app/library/react_light.svg', alt: 'React', gradient: { from: '#cffafe', to: '#a5f3fc' } },
  { src: 'https://svgl.app/library/vite.svg', alt: 'Vite', gradient: { from: '#ede9fe', to: '#fde68a' } },
  { src: 'https://svgl.app/library/python.svg', alt: 'Python', gradient: { from: '#dbeafe', to: '#fef08a' } },
  { src: 'https://svgl.app/library/fastapi.svg', alt: 'FastAPI', gradient: { from: '#ccfbf1', to: '#99f6e4' } },
  { src: 'https://svgl.app/library/tailwindcss.svg', alt: 'Tailwind CSS', gradient: { from: '#e0f2fe', to: '#a5f3fc' } },
]

const pill = 'bg-white px-5 py-2 rounded-full text-[12px] font-semibold text-[#0a1b33] border border-slate-200/60 shadow-sm hover:border-slate-300 transition-all'

function Marquee({ items, className }: { items: Brand[]; className?: string }) {
  return (
    <div
      className={cn('marquee relative w-full overflow-hidden', className)}
      style={{ maskImage: 'linear-gradient(to right, transparent, black 10%, black 90%, transparent)', WebkitMaskImage: 'linear-gradient(to right, transparent, black 10%, black 90%, transparent)' }}
    >
      <div className="marquee-track flex w-max gap-4 py-2">
        {[...items, ...items].map((logo, i) => (
          <div key={i} aria-hidden={i >= items.length || undefined} className="group relative h-24 w-40 shrink-0 flex items-center justify-center rounded-full bg-white border border-slate-200/60 shadow-sm hover:border-slate-300 transition-all overflow-hidden">
            <div
              className="absolute inset-0 scale-150 opacity-0 transition-all duration-500 group-hover:scale-100 group-hover:opacity-100"
              style={{ background: `linear-gradient(135deg, ${logo.gradient.from}, ${logo.gradient.to})` }}
            />
            <img src={logo.src} alt={logo.alt} loading="lazy" className="relative z-10 h-8 w-auto max-w-[96px] transition-all duration-300 group-hover:brightness-0 group-hover:invert" />
          </div>
        ))}
      </div>
    </div>
  )
}

// 빨간 메일을 누르면: 봉투가 가운데로 날아와 → 뚜껑이 열리고 → 편지지가 올라오고 → 편지지에서 화면이 퍼지며 대시보드로
const EASE = [0.22, 1, 0.36, 1] as const
const SOFT = [0.65, 0, 0.35, 1] as const

function Launch({ from }: { from: Rect }) {
  const [scope, animate] = useAnimate()

  useEffect(() => {
    const W = 300 // 봉투 너비 (CSS .env 와 같음)
    const dx = from.x + from.w / 2 - innerWidth / 2
    const dy = from.y + from.h / 2 - innerHeight / 2
    const s0 = (Math.min(from.w, from.h) * 0.62) / W
    let alive = true
    ;(async () => {
      animate('.env-bg', { opacity: [0, 1] }, { duration: 0.5, ease: 'easeOut' })
      await animate('.env', { x: [dx, 0], y: [dy, 0], scale: [s0, 1], rotate: [-14, 0], opacity: [0.6, 1] }, { duration: 0.65, ease: EASE })
      await animate('.env-flap', { rotateX: [0, 180] }, { duration: 0.45, ease: SOFT })
      if (!alive) return
      ;(scope.current.querySelector('.env-flap') as HTMLElement).style.zIndex = '0' // 열린 뚜껑은 편지지 뒤로
      await animate('.env-letter', { y: [0, -120] }, { duration: 0.5, ease: EASE })
      animate('.env-letter', { scale: [1, 1.08] }, { duration: 0.6, ease: SOFT })
      await animate('.env-reveal', { clipPath: ['circle(0% at 50% 38%)', 'circle(150% at 50% 38%)'] }, { duration: 0.7, ease: SOFT })
      if (alive) location.hash = '#/'
    })()
    return () => { alive = false }
  }, [])

  return (
    <div ref={scope} className="env-overlay" aria-hidden="true">
      <div className="env-bg" />
      <div className="env">
        <div className="env-back" />
        <div className="env-letter">
          <span className="env-chip">위험 메일 1건</span>
          <i /><i /><i className="short" />
        </div>
        <div className="env-front" />
        <div className="env-flap" />
      </div>
      <div className="env-reveal" />
    </div>
  )
}

export default function Landing({ loggedIn = false, checkingLogin = false }: { loggedIn?: boolean, checkingLogin?: boolean }) {
  const [launch, setLaunch] = useState<Rect | null>(null)
  // 로그인 상태면 '로그인' 대신 대시보드로 보냄 (다시 로그인하지 않게)
  const next = checkingLogin ? undefined : loggedIn ? '#/' : '#/login'
  const open = (r: Rect) => {
    if (checkingLogin) return
    if (!loggedIn) { location.hash = '#/login'; return }
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) { location.hash = '#/'; return }
    setLaunch(r)
  }

  useEffect(() => {
    const prev = document.title
    document.title = 'ProDog — 메일 속 숨은 AI 명령 탐지'
    return () => { document.title = prev }
  }, [])

  return (
    <div className="min-h-screen bg-[#f9fafb] px-4 py-6 md:px-8 md:py-10 font-sans antialiased">
      <section className="relative w-full max-w-[1400px] mx-auto rounded-[48px] bg-white border border-slate-200/50 shadow-[0_40px_100px_-20px_rgba(0,0,0,0.03)] overflow-hidden h-[600px] flex flex-col">
        <div className="absolute inset-0 pointer-events-none z-0 overflow-hidden select-none" aria-hidden="true">
          <MailScene onOpen={open} disabled={checkingLogin} />
        </div>

        <div className="relative z-20 flex-1 px-8 md:px-16 pt-12 md:pt-16 flex flex-col items-start pointer-events-none">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }} className="flex flex-col items-start pointer-events-auto">
            <h1 className="m-0 font-display text-[42px] md:text-[56px] font-medium leading-[1.1] tracking-tight text-[#0a1b33] [word-break:keep-all]">
              매일 오는 메일<br />일일이 확인하지 마세요
            </h1>
            <p className="m-0 mt-5 max-w-[440px] font-sans text-[14px] md:text-[15px] leading-relaxed text-[#64748b] [word-break:keep-all]">
              흰 글씨, HTML 주석, 외부 전송 링크까지. Gmail 메일을 AI에게 맡기기 전에 숨겨진 지시문을 먼저 찾아 드려요.
            </p>
            <motion.a
              href={next}
              aria-disabled={checkingLogin}
              whileHover={{ scale: 1.04 }}
              whileTap={{ scale: 0.97 }}
              className={cn('mt-8 inline-flex items-center bg-[#0a152d] text-white rounded-full px-6 py-3 text-[14px] font-semibold no-underline', checkingLogin && 'pointer-events-none opacity-60')}
            >
              {checkingLogin ? '로그인 확인 중…' : '시작하기'}
            </motion.a>
          </motion.div>
        </div>

        {/* 로그인: 히어로 오른쪽 위에 따로 */}
        <motion.a
          href={next}
          aria-disabled={checkingLogin}
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.3, ease: [0.22, 1, 0.36, 1] }}
          className={cn(pill, 'absolute top-8 right-8 md:top-10 md:right-12 z-30 inline-flex items-center gap-1 no-underline whitespace-nowrap', checkingLogin && 'pointer-events-none opacity-60')}
        >
          {checkingLogin ? '확인 중…' : loggedIn ? '대시보드' : '로그인'} <ChevronRight className="w-3.5 h-3.5" />
        </motion.a>
      </section>

      <Marquee items={LOGOS} className="mt-10 max-w-[1400px] mx-auto" />
      {launch && <Launch from={launch} />}
    </div>
  )
}
