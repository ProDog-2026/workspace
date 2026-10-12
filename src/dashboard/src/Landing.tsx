import { useEffect } from 'react'
import { motion } from 'motion/react'
import { ChevronRight } from 'lucide-react'
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { Logo } from './ui'
import './landing.css'

const cn = (...c: ClassValue[]) => twMerge(clsx(c))

const VIDEO = 'https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260505_101331_74f9b798-3f00-4e86-8a01-377aa16ffeaa.mp4'

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

export default function Landing() {
  useEffect(() => {
    const prev = document.title
    document.title = 'ProDog — 메일 속 숨은 AI 명령 탐지'
    return () => { document.title = prev }
  }, [])

  return (
    <div className="min-h-screen bg-[#f9fafb] px-4 py-6 md:px-8 md:py-10 font-sans antialiased">
      <section className="relative w-full max-w-[1400px] mx-auto rounded-[48px] bg-white border border-slate-200/50 shadow-[0_40px_100px_-20px_rgba(0,0,0,0.03)] overflow-hidden h-[600px] flex flex-col">
        <div className="absolute inset-0 pointer-events-none z-0 overflow-hidden select-none" aria-hidden="true">
          <video src={VIDEO} autoPlay loop muted playsInline className="w-full h-full object-cover scale-105 transition-transform duration-1000" />
        </div>

        <div className="relative z-20 flex-1 px-8 md:px-16 pt-12 md:pt-16 flex flex-col items-start">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }} className="flex flex-col items-start">
            <h1 className="m-0 font-display text-[42px] md:text-[56px] font-medium leading-[1.1] tracking-tight text-[#0a1b33] [word-break:keep-all]">
              AI가 읽기 전에<br />숨은 명령부터
            </h1>
            <p className="m-0 mt-5 max-w-[440px] font-sans text-[14px] md:text-[15px] leading-relaxed text-[#64748b] [word-break:keep-all]">
              흰 글씨, HTML 주석, 외부 전송 링크까지. Gmail 메일을 AI에게 맡기기 전에 숨겨진 지시문을 먼저 찾아 드려요.
            </p>
            <motion.a
              href="#/login"
              whileHover={{ scale: 1.04 }}
              whileTap={{ scale: 0.97 }}
              className="mt-8 inline-flex items-center bg-[#0a152d] text-white rounded-full px-6 py-3 text-[14px] font-semibold no-underline"
            >
              시작하기
            </motion.a>
          </motion.div>
        </div>

        <div className="absolute bottom-10 left-1/2 -translate-x-1/2 z-30">
          <motion.nav
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.35, ease: [0.22, 1, 0.36, 1] }}
            className="flex items-center bg-white/90 backdrop-blur-2xl px-1.5 py-1.5 rounded-full shadow-[0_12px_40px_rgba(0,0,0,0.08)] border border-slate-200/40"
            aria-label="메뉴"
          >
            <a href="#/intro" aria-label="ProDog 홈" className="w-9 h-9 shrink-0 flex items-center justify-center rounded-full bg-white border border-slate-100 shadow-sm text-[#0a1b33]">
              <Logo size={18} />
            </a>
            <a href="#/states" className="px-4 text-[12px] font-semibold text-slate-500 hover:text-[#0a1b33] no-underline whitespace-nowrap transition-colors">판정 안내</a>
            <a href="#/scan" className="px-4 text-[12px] font-semibold text-slate-500 hover:text-[#0a1b33] no-underline whitespace-nowrap transition-colors">메일 검사</a>
            <a href="#/login" className={cn(pill, 'inline-flex items-center gap-1 no-underline whitespace-nowrap')}>
              로그인 <ChevronRight className="w-3.5 h-3.5" />
            </a>
          </motion.nav>
        </div>
      </section>

      <Marquee items={LOGOS} className="mt-10 max-w-[1400px] mx-auto" />
    </div>
  )
}
