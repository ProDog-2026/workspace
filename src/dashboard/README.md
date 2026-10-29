# ProDog 대시보드

## 실행

Node.js 22.15 이상을 사용한다. Node 22.15와 24에서 테스트했다 (`npm test`는 Node 내장 모듈 모킹을 사용한다).

```sh
cd ~/Downloads/캡스톤/workspace/src/dashboard
npm ci
npm run dev
```

접속: `http://127.0.0.1:5173/#/`. `localhost`와 혼용하면 로그인 저장소·Gmail 쿠키가 달라지므로 개발 주소를 하나로 유지한다.

`.env.example`을 `.env.local`로 복사하고 프로젝트 URL과 **anon** 키를 입력한다. 이 작업 환경에는 연결된 프로젝트의 공개 anon 키만 `.env.local`에 설정했다. 이 파일은 Git에서 제외한다. 서버의 `service_role`·`sb_secret_…` 키는 `VITE_` 변수에 넣지 않는다. 잘못된 키는 개발 서버·빌드 시작 시 거절한다.

## Supabase Google 로그인 설정

2026-10-29 확인 시 연결 프로젝트의 Google 제공자는 비활성 상태였다. 프로젝트 관리자가 아래 설정을 해야 실제 로그인이 된다.

1. Supabase Authentication → Sign In / Providers → Google에서 Web OAuth 클라이언트 ID와 secret을 서버 설정으로 등록하고 활성화한다. **Google secret은 프론트 `.env.local`에 넣지 않는다.**
2. Google Cloud의 해당 OAuth 클라이언트에 Supabase가 보여 주는 callback 주소를 승인된 리디렉션 URI로 추가한다: `https://<project-ref>.supabase.co/auth/v1/callback`.
3. Supabase Authentication → URL Configuration에서 Site URL을 `http://127.0.0.1:5173`으로, 허용 Redirect URL에 `http://127.0.0.1:5173/?auth_callback=1`을 추가한다. 배포 시 실제 HTTPS 주소를 등록한다.
4. 앱의 Google 로그인 → PKCE 코드 교환 → 설정 → Gmail 읽기 권한 연결 순서로 진행한다. 앱 로그인에는 Gmail 권한을 추가하지 않는다. Google OAuth 테스트 모드라면 테스트 사용자 등록도 필요하다.

앱 로그인과 Gmail 수집 OAuth는 서로 다른 callback을 쓴다. 기존 수집기의 `/oauth/google/callback` 설정도 유지한다. [공식 Google 로그인 문서](https://supabase.com/docs/guides/auth/social-login/auth-google).

## 데이터와 권한

- 대시보드: `scans` 뷰를 `user_id = session.user.id`로 SELECT한다. `received_at`, `id` 순서를 고정해 100건씩 읽는다. 기본 정렬은 받은 날짜 내림차순이며 화면 목록은 위험도 순이다.
- 점수는 DB의 0–100 값을 그대로 사용한다. NULL은 미측정이다. DB에서 주지 않은 임계값을 화면에서 만들어 적용하지 않는다.
- 목록·상세·일별 그래프는 같은 조회 행을 사용한다. 집계는 현재 불러온 행 기준이며 그래프는 최대 90일이다. 새로고침하면 첫 페이지부터 다시 조회한다.
- 위험·의심 판정을 우선 표시하고 수집 불완전·실패 안내를 함께 표시한다. 미판정은 `검사 전`이며 정상으로 대체하지 않는다.
- `evidence.snippet`, 제목, 발신자, 설명은 React 텍스트로만 표시한다. 메일 HTML을 렌더링하거나 외부 이미지를 불러오지 않는다.
- DB 쓰기·서비스 키·임의 RPC는 없다. Auth의 로그인·로그아웃은 DB 결과 쓰기와 별개다.
- 보안 경계는 DB RLS다. `scans`는 `security_invoker=true`, 기초 3개 테이블은 `auth.uid() = user_id` SELECT 정책이어야 한다. 브라우저에 소유권 검사를 맡기지 않는다. 현재 연결 DB의 이 설정과 익명 SELECT 차단을 읽기 전용으로 확인했다.
- **`schema.sql`은 오래된 행 형식 참고용이다. 실제 DB에는 `scans` 뷰가 있으므로 `CREATE TABLE scans`를 실행하지 않는다.**

## 현재 통합 경계

기존 Gmail 수집 서버는 로컬 SQLite 세션을 사용한다. 아직 Supabase Auth 토큰 검증·`gmail_connections.user_id` 귀속·수집/분석 결과 Supabase 저장 어댑터가 없다. 앱에 로그인하고 Gmail을 연결하는 화면 순서는 이어지지만, 이것만으로 두 서버의 계정이 안전하게 결합되거나 결과가 Supabase에 저장되지는 않는다. 이메일 일치로 `user_id`를 추정하거나 프론트에서 연결 행을 쓰지 않는다.

`#/mailbox`(로컬 수집), 동적 분석·메일 검사·설정은 기존 로컬 프로토타입으로 유지한다. 이 화면은 이 브라우저의 Gmail 세션을 사용하고 Supabase 로그인/로그아웃과 독립적이다. 앱 로그아웃은 로컬 Gmail 연결을 해제하지 않는다. 공유 서비스로 배포하기 전 서버 사용자 귀속을 구현해야 한다.

현재 `scans` 뷰는 분석 대기 중인 새 메일을 제외한다. 따라서 UI는 NULL 판정 행의 `검사 전` 표시를 지원하지만, 실제 대기 메일을 보려면 저장 담당이 뷰 계약을 확장해야 한다. 로컬 수집 화면에서는 대기 상태가 계속 표시된다.

## 검증

```sh
npm test
npm run build
```

합성 데이터로 소유권 필터·조회 오류·페이지 범위·점수·일별 집계·3단계/검사 전·불완전 안내·HTML 이스케이프·PKCE 콜백과 로그인 상태 변경을 검사한다. 실제 사용자 로그인 및 사용자 A/B 데이터 접근 검증은 Google 제공자를 활성화한 후 수행한다. 실제 메일이나 테스트 계정을 운영 DB에 추가하지 않는다.
