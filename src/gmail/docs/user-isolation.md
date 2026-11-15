# Google 로그인과 사용자별 데이터

로그인은 Google 한 번으로 Gmail 읽기 권한까지 요청한다. 서버는 Google 공개 키로 ID 토큰의 서명, 발급자, 대상 앱, 만료, nonce, access token 해시와 Gmail 이메일을 검증한다. 이메일 주소 대신 불변 Google `sub`로 로컬 연결을 식별한다.

Supabase 설정이 있으면 동일 토큰을 Auth `id_token` grant로 교환하고, 응답의 Google identity가 검증한 `sub`와 같은지 확인한다. 이때 받은 `auth.users.id`를 해당 연결에 저장한다. 서비스 키는 서버에만 두며, 저장과 삭제는 매번 세션 연결의 `user_id`를 사용한다. `SUPABASE_USER_ID`는 합성 데모 전용이며 Google 모드에서는 사용하지 않는다. 설정이 없으면 로컬 전용으로 동작하며 `google:sub`를 사용한다.

## 직접 확인하는 순서

1. Google A 계정으로 로그인하고 받은편지함을 확인한다.
2. 설정에서 **다른 계정으로 로그인**을 누른다. A의 저장 기록은 유지하고 브라우저 세션만 끝낸다.
3. Google B 계정으로 로그인한다. 상단 계정과 받은편지함이 B로 바뀌는지 확인한다.
4. **연동 해제**를 누른다. B의 토큰, 연결, 메일과 분석 기록을 정리한다. A 계정으로 다시 로그인하면 A의 연결과 기록을 사용할 수 있다.
5. 외부 삭제가 실패하면 **연결 해제 다시 시도**가 표시된다. 완료할 때까지 메일 조회·동기화·동적 검사는 차단된다.

자동 검증에는 합성 Google 서명 토큰과 가짜 Supabase만 사용한다. 실제 메일과 실계정은 자동 테스트에서 사용하지 않는다. 이 문서의 절차는 실제 계정 두 개로 검증했다는 뜻이 아니다.

## 적용 전 준비와 이전 데이터

- 현재 서버는 로컬 단일 프로세스 구성이다. 동기화와 연결 해제를 계정별 잠금으로 직렬화한다. 다중 프로세스·여러 서버 배포에는 분산 잠금 또는 DB 작업 큐가 추가로 필요하다.
- Supabase Auth의 Google provider를 켜고 현재 Google OAuth client ID를 허용한다. Google 콘솔 동의 범위에는 `openid`, `email`, `gmail.readonly`가 포함된다.
- 신규 DB는 `arch/supabase-gmail-schema.sql`을 사용한다. 기존 테이블이 있으면 이 SQL은 실패하며, 기존 데이터를 덮어쓰지 않는다. 기존 배포는 `auth.users` 외래 키, 사용자까지 포함하는 복합 외래 키, RLS와 연쇄 삭제를 별도로 확인해야 한다.
- 서비스 시작 전 로컬 DB를 백업한다. 예전 Google 계정은 `legacy-google`로 보관하고 예전 세션을 무효화한다. 새 로그인에 예전 토큰이나 기록을 자동 귀속하지 않는다. 원본은 보존되며 새 연결은 다시 로그인해서 만든다. 예전 기록 정리는 소유자 확인 후 별도로 수행한다.
- 기존 Supabase의 공통 사용자 행도 새 로그인 사용자에게 자동 이전하지 않는다. 관리자 검토로 실제 소유자를 확인하기 전에는 격리한다.
- 로컬 전용으로 이미 로그인했다가 Supabase를 새로 활성화하려면 기존 연결을 먼저 해제한 후 로그인한다. 사용자 ID가 바뀌는 연결을 묵시적으로 병합하지 않는다.
- 동적 검사 컨테이너도 함께 갱신해야 한다. 새 삭제 API가 없으면 연결 해제가 완료되지 않고 재시도 상태로 유지된다. 소유자를 알 수 없는 과거 동적 실험의 가져오기는 Google 모드에서 차단한다.
- 제어 서버와 Ollama는 로컬 내부 서비스로 유지한다. 이 변경으로 인터넷 배포에 필요한 TLS, 서비스 간 인증, 분산 실행 제어까지 제공하는 것은 아니다.

근거: [Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect), [Supabase Auth ID token grant 구현](https://github.com/supabase/auth/blob/master/internal/api/token_oidc.go).
