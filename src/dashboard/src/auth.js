import { useEffect, useState } from 'react'
import { supabase, supabaseError, checkGoogleLogin } from './supabase'

let restoration
async function restoreSession() {
  if (!restoration) restoration = (async () => {
    const url = new URL(location.href)
    if (url.searchParams.has('auth_callback')) {
      const code = url.searchParams.get('code')
      const denied = url.searchParams.has('error')
      // OAuth 코드는 주소·방문 기록에 남겨 두지 않는다.
      history.replaceState(null, '', url.pathname + '#/login')
      if (denied || !code) throw new Error('Google 로그인이 취소됐거나 응답이 만료됐어요. 다시 로그인하세요.')
      const { error } = await supabase.auth.exchangeCodeForSession(code)
      if (error) throw new Error('로그인 응답을 확인하지 못했어요. 같은 브라우저에서 다시 로그인하세요.')
    }
  })()
  await restoration
  const { data, error } = await supabase.auth.getSession()
  if (error) throw new Error('로그인 상태를 확인하지 못했어요. 다시 로그인하세요.')
  return data.session
}

export function useAuth() {
  const [state, setState] = useState({ session: null, loading: !!supabase, error: supabaseError })
  useEffect(() => {
    if (!supabase) return
    let active = true, restoring = true, changed = false, latest
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (!active || event === 'INITIAL_SESSION') return
      changed = true
      latest = session
      if (!restoring) setState({ session, loading: false, error: null })
    })
    restoreSession().then((session) => {
      restoring = false
      if (active) setState({ session: changed ? latest : session, loading: false, error: null })
    }).catch((e) => {
      restoring = false
      if (active) setState({ session: null, loading: false, error: e.message })
    })
    return () => { active = false; subscription.unsubscribe() }
  }, [])
  return state
}

export async function signIn() {
  await checkGoogleLogin()
  const { error } = await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: location.origin + location.pathname + '?auth_callback=1' },
  })
  if (error) throw new Error('Google 로그인을 시작하지 못했어요. Supabase의 Google 로그인 설정을 확인하세요.')
}
