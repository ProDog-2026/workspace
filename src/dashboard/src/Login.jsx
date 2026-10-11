export default function Login() {
  return (
    <main className="login">
      <div className="card login-card">
        <h1>ProDog</h1>
        <p className="muted">AI 에이전트가 읽기 전에, 메일 속 숨은 명령을 먼저 찾아냅니다.</p>
        {/* 데모: 실제로는 Supabase Auth 의 Google OAuth (signInWithOAuth) 로 교체 */}
        <a className="btn primary" href="#/">Google 계정으로 로그인</a>
        <p className="muted small">Gmail 읽기 전용 권한(gmail.readonly)만 요청합니다.</p>
      </div>
    </main>
  )
}
