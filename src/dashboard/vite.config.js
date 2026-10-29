import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { browserKey } from './src/scans.js'

// Gmail 수집 모듈(src/gmail). 그 모듈은 Host·Origin 이 APP_BASE_URL 과 같아야 받아 줌
const GMAIL = 'http://127.0.0.1:8765'
// 쿠키는 포트를 가리지 않으므로 대시보드도 127.0.0.1 로 띄워야 Gmail 세션 쿠키를 같이 씀
const HOST = '127.0.0.1'
const PORT = 5173

const toGmail = {
  target: GMAIL,
  changeOrigin: true, // Host 를 127.0.0.1:8765 로
  configure: (proxy) => proxy.on('proxyReq', (req, incoming) => {
    // 이 dev 서버에서 온 요청만 Origin 을 바꿈. 다른 Origin 은 그대로 넘겨 Gmail 모듈이 거절하게 둠
    if (incoming.headers.origin === `http://${HOST}:${PORT}`) req.setHeader('origin', GMAIL)
  }),
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  if (env.VITE_SUPABASE_ANON_KEY && !browserKey(env.VITE_SUPABASE_ANON_KEY)) {
    throw new Error('VITE_SUPABASE_ANON_KEY에는 anon 키만 허용됩니다. 서버 키를 제거하세요.')
  }
  return {
    plugins: [react(), tailwindcss()],
    server: { host: HOST, port: PORT, strictPort: true, proxy: { '/api': toGmail, '/oauth': toGmail } },
  }
})
