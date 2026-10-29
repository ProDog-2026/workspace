import { createClient } from '@supabase/supabase-js'
import { browserKey } from './scans'

const env = import.meta.env ?? {}
const url = env.VITE_SUPABASE_URL
const key = env.VITE_SUPABASE_ANON_KEY
export const supabaseError = !url || !key
  ? 'Supabase 설정이 필요해요. 대시보드의 .env.local에 VITE_SUPABASE_URL과 VITE_SUPABASE_ANON_KEY를 입력하세요.'
  : !browserKey(key) ? '브라우저에는 Supabase anon 키만 사용할 수 있어요. 서비스 키를 넣었다면 제거하고 키를 교체하세요.'
    : !/^https:\/\/[^/]+$/.test(url) ? 'Supabase 프로젝트의 HTTPS URL을 확인하세요.' : null
export const supabase = supabaseError ? null : createClient(url, key, {
  auth: { flowType: 'pkce', detectSessionInUrl: false, persistSession: true, autoRefreshToken: true },
})

export async function checkGoogleLogin() {
  if (supabaseError) throw new Error(supabaseError)
  const response = await fetch(url + '/auth/v1/settings', { headers: { apikey: key } })
  if (!response.ok) throw new Error('Supabase 로그인 설정을 확인하지 못했어요. 잠시 후 다시 시도하세요.')
  const settings = await response.json()
  if (!settings.external?.google) throw new Error('Supabase에서 Google 로그인이 꺼져 있어요. 프로젝트 관리자가 Authentication → Sign In / Providers → Google을 활성화해야 해요.')
}
