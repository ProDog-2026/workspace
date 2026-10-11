# 로컬 검증 기록

자동 검사·데모 검증일: 2026-09-27. 실제 Google 연결 검증일: 2026-09-28.
환경: Windows, Python 3.13.5, `uv.lock`과 `package-lock.json`에 고정한 의존성.
아래 자동 검사와 데모 HTTP 검증은 합성 데이터로 수행했다.
이후 사용자 요청에 따라 Google Cloud 설정과 실제 계정 검증을 수행했다. 실제 메일 내용·주소·토큰·비밀값은 이 기록에 포함하지 않는다.

## 결과

| 검증 | 실행 | 결과 |
| --- | --- | --- |
| Python 테스트 | `uv run pytest -q` | 99개 통과 |
| Python 정적 검사 | `uv run ruff check src tests scripts` | 통과 |
| 프런트엔드 DOM 테스트 | `npm test` | 10개 통과 |
| JavaScript 문법 | `node --check src/prodog_gmail/static/app.js` | 통과 |
| 실제 로컬 HTTP 흐름 | 실행 중인 데모 서버에 `uv run python scripts/smoke_demo.py` | 정적 파일·보안 헤더·연결·페이지 이동·본문 3개·연결 해제 통과 |
| 실제 Google 동의·메일 조회 | 2026-09-28 로컬 Google 모드 | 사용자 동의 후 연결 완료, 받은편지함 20개·본문 1건 표시·다음 페이지 조회 성공 |
| 브라우저 시각 검증 | 2026-09-28 Paseo 브라우저 | 데스크톱 화면에서 데모 연결 상태·메일 3개 목록·HTML 데모 본문 표시 확인. 모바일 크기 검증은 미완료 |

Python 테스트에서 Starlette TestClient의 httpx 지원 종료 예정 경고 1개가 나왔다.
현재 고정한 의존성에서는 테스트가 통과했으며 런타임 오류는 발생하지 않았다.
의존성을 갱신할 때 TestClient 권장 전송 라이브러리로의 전환을 확인한다.

## Google Cloud 설정과 실제 검증 (2026-09-28)

- 전용 프로젝트 `ProDog Gmail Dev` (`prodog-gmail-dev`) 생성 완료.
- 해당 프로젝트에서 Gmail API 사용 설정 완료.
- OAuth 브랜드 생성 완료. External / Testing 상태로 테스트 사용자 1명 등록.
- 데이터 액세스에는 `https://www.googleapis.com/auth/gmail.readonly` 하나만 등록.
- Web application 클라이언트 `ProDog Local Web` 생성. callback은 `http://127.0.0.1:8765/oauth/google/callback`.
- 내려받은 클라이언트 JSON을 `scripts/configure_google.py`로 가져오고 로컬 서버를 `google` 모드로 시작. 기존 암호화 키 보존과 `configured: true` 확인.
- 사용자가 Google의 실제 읽기 권한 동의를 완료한 뒤 앱의 `연결됨` 상태와 받은편지함 20개 표시 확인.
- 메일 1건에서 본문, 분석 입력·HTML 원문 보기, 본문 해시 표시 확인. 다음 페이지에서도 20개 표시 확인.
- 새 HTTP 세션은 `authenticated: false`. 해당 세션의 `GET /api/mails?limit=1`은 `401`로 거부됨.
- DB의 Google 계정 토큰 1건이 Fernet 형식으로 암호화되어 있음을 확인. 검사한 메일 메타데이터에는 본문·RAW·분석 텍스트·토큰 필드가 없음.
- 실제 refresh token 요청 성공. 기존 `GoogleClient.access_token`의 거절된 access token 처리 경로를 호출해 갱신을 유도하고, 반환된 토큰으로 Gmail 프로필 조회가 같은 계정에 성공함을 확인. 저장된 refresh token 존재, 읽기 전용 단일 범위, 새 만료 시각도 확인.

권한 철회와 로그아웃은 실제 연결을 유지하기 위해 이 계정에서 수행하지 않았다. 해당 경로는 합성 응답 테스트로 검증했다. 자연 만료를 기다린 자동 갱신, 테스트 모드의 7일 만료 후 재동의, 조직 관리자 정책이 다른 계정, 모바일 레이아웃은 아직 실계정에서 검증하지 않았다.

## 시험한 핵심 동작

- **OAuth:** 읽기 전용 권한, offline 요청, PKCE challenge/verifier, 브라우저에 묶인 state, 만료·재사용·다른 세션·동의 거절 처리.
- **토큰:** 암호화 저장과 계정 귀속, 만료 시 갱신, 동시 갱신 중복 방지, 유효한 refresh token 유지, 무효 토큰 재사용 방지, 갱신 실패 후 재연결 상태 유지.
- **실패 처리:** Google 401·403·429·5xx, 요청 크기 제한, 잘못된 token 응답, revoke 실패 후 재시도, 비밀값을 넣지 않는 오류 메시지.
- **접근 경계:** 익명 요청 차단, 세션 회전·만료, CSRF·Origin·Host 검사, 동일 message ID를 쓰는 두 계정의 격리.
- **동시 요청:** 연결 해제 중 늦게 도착한 token refresh·본문 조회의 401 처리, 로그아웃 이후 늦은 callback이 새 세션을 만들지 않음.
- **데이터:** 메타데이터 중복 upsert, 본문·RAW 미저장, 연결 해제 시 해당 계정의 토큰·메타데이터·세션 제거.
- **파싱:** 한글 charset, plain/HTML, HTML 주석·숨김 문구 유지, MIME 첨부·중첩, 깨진 base64·charset·날짜·헤더, 원문·본문·파트 수·깊이 제한.
- **화면 코드:** 연결 후 CSRF 회전, 페이지 토큰 이동, HTML·첨부명 텍스트 표시, 만료 후 재연결, 로그아웃 후 이전 본문 제거, 늦은 본문 응답 무시, 첨부 미분석 표시.
- **설정 가져오기:** Web 클라이언트 JSON 검사, callback 불일치 거부, 기존 암호화 키·기타 설정 보존, 비밀값이 없는 오류.

## 검증 중 발견하고 고친 문제

1. 만료된 Google 권한에도 재연결 버튼이 숨겨지던 문제를 서버의 `needs_reconnect` 상태와 프런트엔드 처리로 고쳤다.
2. 연결 해제 중 완료된 요청이 삭제된 계정에 기록하려다 500을 내던 문제를 트랜잭션 내 계정 확인으로 고쳤다.
3. 로그아웃 뒤 OAuth callback이 세션을 되살리던 문제를 callback 저장 시점의 세션 재검증으로 고쳤다.
4. 첨부 내용 제외가 경고에 드러나지 않던 문제를 `attachment_content_not_analyzed`와 첨부 정보 표시로 고쳤다.
5. 무효 refresh token이 재연결 응답에 재사용되던 문제를 고쳤다. 이때 새 refresh token이 없으면 성공으로 처리하지 않는다.

## 재현

```powershell
uv sync --frozen --dev
uv run pytest -q
uv run ruff check src tests scripts
npm ci
npm test
node --check src/prodog_gmail/static/app.js
```

Node/npm은 프런트엔드 테스트를 실행할 때만 필요하다.
일반 데모 서버는 Python으로 실행한다.
서버를 `demo` 모드로 실행한 뒤 별도 터미널에서 다음 명령으로 실제 HTTP 흐름을 검사한다.

```powershell
uv run python scripts/smoke_demo.py
```

이 스크립트는 데모 연결과 해제를 수행한다.
같은 데모 계정을 사용 중인 브라우저가 있다면 합성 메일 연결이 해제되므로 다시 연결하면 된다.
`google` 모드에서는 실행을 중단하여 실제 계정에는 접근하지 않는다.

DOM 테스트는 브라우저 렌더링·레이아웃을 검증한 결과가 아니다.
실제 Google API 성공 여부도 MockTransport 테스트만으로 보장하지 않는다.
실제 계정 준비 후 [Google 설정 문서](google-setup.md)의 검증표를 수행한다.
