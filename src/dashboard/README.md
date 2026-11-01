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

2026-10-30 연결 프로젝트의 앱 Google 로그인을 확인했다. 새 프로젝트에서는 아래 설정이 필요하다.

1. Supabase Authentication → Sign In / Providers → Google에서 Web OAuth 클라이언트 ID와 secret을 서버 설정으로 등록하고 활성화한다. **Google secret은 프론트 `.env.local`에 넣지 않는다.**
2. Google Cloud의 해당 OAuth 클라이언트에 Supabase가 보여 주는 callback 주소를 승인된 리디렉션 URI로 추가한다: `https://<project-ref>.supabase.co/auth/v1/callback`.
3. Supabase Authentication → URL Configuration에서 Site URL을 `http://127.0.0.1:5173`으로, 허용 Redirect URL에 `http://127.0.0.1:5173/?auth_callback=1`을 추가한다. 배포 시 실제 HTTPS 주소를 등록한다.
4. 앱의 Google 로그인 → PKCE 코드 교환 → 설정 → Gmail 읽기 권한 연결 순서로 진행한다. 앱 로그인에는 Gmail 권한을 추가하지 않는다. Google OAuth 테스트 모드라면 테스트 사용자 등록도 필요하다.

앱 로그인과 Gmail 수집 OAuth는 서로 다른 callback을 쓴다. 기존 수집기의 `/oauth/google/callback` 설정도 유지한다. [공식 Google 로그인 문서](https://supabase.com/docs/guides/auth/social-login/auth-google).

## 데이터와 권한

- 대시보드: `scans` 뷰를 `user_id = session.user.id`로 SELECT한다. `received_at`, `id` 순서를 고정해 100건씩 읽는다. 기본 정렬은 받은 날짜 내림차순이며 화면 목록은 위험도 순이다.
- 파이프라인 점수는 DB `analysis.static.score`의 원래 확률을 0–100으로 변환해 임계값과 같은 정밀도로 표시한다. 기존 정수 점수 컬럼은 호환용이며 원래 확률이 없으면 그 값을 사용한다. NULL은 미측정이다. 화면에서 임계값을 만들지 않는다.
- 목록·상세·일별 그래프는 같은 조회 행을 사용한다. 집계는 현재 불러온 행 기준이며 그래프는 최대 90일이다. 새로고침하면 첫 페이지부터 다시 조회한다.
- 위험·의심 판정을 우선 표시하고 수집 불완전·실패 안내를 함께 표시한다. 미판정은 `검사 전`이며 정상으로 대체하지 않는다.
- `evidence.snippet`, 제목, 발신자, 설명은 React 텍스트로만 표시한다. 메일 HTML을 렌더링하거나 외부 이미지를 불러오지 않는다.
- DB 쓰기·서비스 키·임의 RPC는 없다. Auth의 로그인·로그아웃은 DB 결과 쓰기와 별개다.
- 보안 경계는 DB RLS다. `scans`는 `security_invoker=true`, 기초 3개 테이블은 `auth.uid() = user_id` SELECT 정책이어야 한다. 브라우저에 소유권 검사를 맡기지 않는다. 현재 연결 DB의 이 설정과 익명 SELECT 차단을 읽기 전용으로 확인했다.
- **`schema.sql`은 오래된 행 형식 참고용이다. 실제 DB에는 `scans` 뷰가 있으므로 `CREATE TABLE scans`를 실행하지 않는다.**

## 현재 통합 경계

기존 메뉴(대시보드·판정 안내·로컬 수집·메일 검사·동적 분석·설정·관리자)를 유지한다.
**대시보드 → 수집 메일 자동 동적 분석**에서 의심·위험 메일의 정적 판정, 실행 상태, 로컬 모델과 관측 결과를 확인한다.
저장 결과는 5초마다 읽기 전용으로 갱신하며, 조회가 메일 수집이나 재실행을 시작하지 않는다.
**동적 분석 → 수집 메일 자동 분석**은 자동 실행 기록을 기본으로 펼쳐 보여 준다. 선택하면 기존 상세 기록을 연다.
같은 화면의 **메일 직접 테스트**는 메일 하나를 입력하고 실행하는 로컬 Ollama 실험이며 정적 판정 단계는 거치지 않는다. 모델·작업은
**실행 옵션**, 정상/공격 비교는 **두 메일 비교**, 원문 로그·파일·JSON은 **상세 기록**에서 연다.
새 수동 테스트는 앱 로그인 UUID에 귀속해 Gmail 연결 여부·쿠키 교체 후에도 기록을 조회한다.
선택한 모델과 실행 환경을 확인한 뒤 실행 버튼을 활성화하고 등록 직전 서버에서도 다시 검사한다.
**실행 옵션 → 검증 조건**에는 필수 요약 내용·잘못된 내용·공격 목표 문구를 선택적으로 지정한다.
결과는 문구 조건 관찰이며, 미설정·불완전한 관찰은 미확인으로 남긴다.

Gmail 서버의 `.env`에 동일 프로젝트의 `SUPABASE_URL`, `SUPABASE_ANON_KEY`, 서버 전용 `SUPABASE_SERVICE_KEY`를 설정한다. 프론트 API helper가 현재 access token을 전달하고 서버가 Supabase Auth `/auth/v1/user`로 검증한다. 검증된 UUID를 Gmail OAuth state/PKCE 세션에 묶고 실제 연결 owner를 저장한다. 고정 `SUPABASE_USER_ID`는 사용하지 않는다.

`#/mailbox`는 POST 수집 후 정적·동적 분석을 등록하고, 서버 outbox가 메타데이터·분석 결과를 본인의 Supabase에 upsert한다. 저장 중 실패는 대기열에 남겨 재시도하며 화면에 대기/오류를 표시한다. 본문·토큰·명령 원문은 Supabase에 보내지 않는다. `#/`는 저장된 scans를 읽고, 동적 실행 상세는 owner가 일치하는 로컬 서버에서 읽는다.

앱 로그인과 Gmail 읽기 권한은 별도다. 이전 owner 없는 연결은 이메일이 같아도 자동 귀속하지 않으므로 처음 한 번 Gmail을 재연결한다. 이후 쿠키 만료/refresh 시 검증된 사용자 자신의 연결을 복원한다. 앱 로그아웃은 로컬 세션과 진행 작업을 종료하지만 Google grant는 철회하지 않는다. Gmail 연동 해제는 별도 버튼으로 처리한다.

현재 프로젝트의 기존 3개 테이블에 `arch/supabase-pipeline-integration.sql` 변경을 적용했다. 이 SQL은 기존 스키마용이며 빈 DB를 만드는 bootstrap SQL은 아니다. 필드·RLS 계약은 `arch/SUPABASE_TABLE.md`를 따른다. scans는 최초 pending을 `검사 전`으로 표시하고, 기존 완료 결과가 있으면 새 pending보다 우선 표시한다. `analysis` JSON에는 정적 모델별 점수/임계값과 동적 관측 요약이 포함된다. shared service 배포를 위한 HTTPS·공유 세션 구성은 별도다.

## 검증

```sh
npm test
npm run build
```

합성 데이터로 소유권 필터·조회 오류·페이지 범위·점수·일별 집계·3단계/검사 전·불완전 안내·HTML 이스케이프·PKCE 콜백과 로그인 상태 변경을 검사한다. 실제 DB에서 합성 저장/조회/cleanup과 RLS owner·타 UUID 차단을 확인했다. 사용자 허용으로 기존 Gmail OAuth를 재연결하고 실제20건 수집→Supabase 목록/상세/그래프 표시, 사용자 귀속과 reload 후 중복 방지를 확인했다. 합성 검증 행은 제거했으며 테스트 Auth 계정은 만들지 않았다.
