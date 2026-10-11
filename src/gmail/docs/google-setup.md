# Google OAuth 설정과 실제 Gmail 검증

이 문서는 로컬 ProDog Gmail 프로토타입을 실제 Gmail 계정에 연결하는 절차다. 새로 설치한 기본값인 `demo` 모드는 합성 메일만 사용한다. 현재 개발 PC에서는 2026-09-28 실제 계정 연결과 메일 조회를 확인했다. 실행한 항목과 남은 검증은 [검증 기록](verification.md)을 참고한다.

## 1. 로컬 실행 준비

PowerShell에서 프로젝트 폴더로 이동한 뒤 실행한다.

```powershell
uv sync --dev
uv run python scripts/setup_local.py
```

첫 명령은 실행·개발 의존성을 설치한다. 두 번째 명령은 로컬 `.env`와 토큰 암호화용 `TOKEN_ENCRYPTION_KEY`를 준비한다. `.env`와 `data/`는 Git에 포함하지 않는다. 암호화 키를 잃거나 바꾸면 기존 저장 토큰을 복호화할 수 없으므로 같은 데이터베이스를 쓰는 동안 키를 유지한다.

```powershell
uv run uvicorn prodog_gmail.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

브라우저에서 `http://127.0.0.1:8765`를 연다. `demo` 모드에서 데모 연결과 합성 메일 조회가 가능해야 한다. `--no-access-log`는 OAuth callback URL에 포함되는 인증 코드가 일반 접근 로그에 남는 것을 줄인다.

## 2. Google Cloud 설정

1. 팀의 개발용 Google Cloud 프로젝트를 선택하거나 만든다. 운영 프로젝트와 구분한다.
2. API 라이브러리에서 **Gmail API**를 사용 설정한다.
3. Google Auth Platform에서 앱 이름, 지원 이메일, 개발자 연락처를 설정한다.
4. 일반 Gmail 계정으로 시험한다면 Audience를 **External**, 게시 상태를 **Testing**으로 두고 사용할 계정을 테스트 사용자에 추가한다. 조직 내부 계정만 받는 Internal 설정은 해당 조직의 조건을 충족할 때 사용한다.
5. Data Access에 다음 범위만 추가한다.

   ```text
   https://www.googleapis.com/auth/gmail.readonly
   ```

6. OAuth 클라이언트 유형을 **Web application**으로 만들고 Authorized redirect URI를 아래 값과 정확히 일치시킨다.

   ```text
   http://127.0.0.1:8765/oauth/google/callback
   ```

7. 생성 화면에서 클라이언트 JSON을 내려받고 다음 절차로 로컬 `.env`에 가져온다. 채팅, 이슈, 스크린샷, 저장소에 비밀값을 보내지 않는다.

백엔드가 authorization code를 교환하는 구조이므로 브라우저용 Google JavaScript SDK 설정은 필요하지 않다. `localhost`와 `127.0.0.1`은 다른 호스트다. 포트, 경로, 끝의 슬래시까지 redirect URI가 일치해야 한다. Google의 현재 설정 기준은 [웹 서버 OAuth 문서](https://developers.google.com/identity/protocols/oauth2/web-server)를 참고한다.

## 3. 실제 Google 모드로 전환

프로젝트 폴더에서 내려받은 Web application 클라이언트 JSON 경로를 지정한다.

```powershell
uv run python scripts/configure_google.py 'C:\경로\client_secret.json'
```

스크립트는 클라이언트 유형과 callback URI를 검사하고 `APP_MODE=google`로 전환한다. 기존 암호화 키와 나머지 설정을 유지하며 비밀값을 출력하지 않는다. 처음 실행할 때 이전 설정을 `.env.before-google.bak`에 보관한다. JSON 파일도 비밀값을 포함하므로 소스 저장소에 넣지 않는다.

직접 설정할 경우의 형식은 다음과 같다. 실제 비밀값을 이 문서에 적지 않는다.

```dotenv
APP_MODE=google
APP_BASE_URL=http://127.0.0.1:8765
GOOGLE_CLIENT_ID=발급받은-client-id
GOOGLE_CLIENT_SECRET=발급받은-client-secret
DATABASE_PATH=data/prodog.db
SESSION_TTL_SECONDS=28800
# TOKEN_ENCRYPTION_KEY는 setup_local.py가 만든 값을 유지한다.
```

서버를 재시작한 뒤 위 주소를 다시 연다. 기존 데모 세션이 있으면 로그아웃하고 연결을 시작한다. Google에서 사용할 테스트 계정을 선택한 뒤 Gmail 읽기 권한에 동의한다. 이 프로토타입은 Google OIDC 로그인 대신 Gmail `users.getProfile`의 이메일로 연결 계정을 표시한다. Supabase 앱 사용자 인증은 구현 범위에 포함하지 않는다.

이 서버는 루프백 접속을 전제로 한다. `--host 0.0.0.0`, 터널, 공용 호스팅으로 공유하지 않는다. 팀 서버에 통합할 조건은 [통합 문서](integration.md)를 참고한다.

## 4. 실제 계정에서 확인할 항목

브라우저 개발자 도구의 Network에서 상태 코드와 응답 구조를 확인한다. 인증 코드, 쿠키, CSRF 값, 토큰, 실제 메일 내용을 복사해 공유하지 않는다. 아래 표는 수행할 검증과 기대 결과이며 실행 완료 기록이 아니다.

| 확인 | 수행 방법 | 기대 결과 |
| --- | --- | --- |
| 모드와 설정 | 연결 전 `GET /api/session` 확인 | `mode`가 `google`, `configured`가 `true`, `authenticated`와 `needs_reconnect`가 `false`다. |
| 연결 시작 | 화면에서 Google 연결 실행 | `POST /api/oauth/google/start`가 `authorization_url`을 반환하고 Google 동의 화면으로 이동한다. |
| 연결 완료 | 테스트 계정에서 Gmail 읽기 동의 | callback 후 `GET /api/session`의 `authenticated`가 `true`, `needs_reconnect`가 `false`가 되고 `email`이 선택한 Gmail 계정과 일치한다. |
| 세션 격리 | 연결 후 별도 시크릿 창에서 앱 열기 | 별도 창의 `GET /api/session`은 `authenticated: false`다. 기존 창의 연결을 공유하지 않는다. |
| 받은편지함 | `GET /api/mails?limit=20` 확인 | `messages` 배열을 반환한다. 최대 20개이며 빈 받은편지함이면 빈 배열도 정상이다. 항목에 본문·Google 토큰이 포함되지 않는다. |
| 다음 페이지 | `next_page_token`이 있을 때 다음 페이지 조회 | `page_token`을 전달해 이어서 조회한다. 토큰이 없으면 다음 페이지가 없다. `result_size_estimate`는 추정값이다. |
| 본문 | 메일 하나를 열어 `GET /api/mails/{id}` 확인 | `id`, `thread_id`, `subject`, `sender`, `received_at`, `labels`, `body_hash`, `trust`, `text_plain`, `text_html`, `analysis_text`, `attachments`, `warnings`, `truncated`를 확인한다. `trust`는 신뢰할 수 없는 입력임을 나타낸다. |
| HTML 처리 | HTML 본문과 외부 이미지를 포함한 시험 메일 열기 | HTML 코드가 실행되거나 외부 이미지가 자동 요청되지 않는다. 첨부파일을 별도 파일로 저장하거나 실행하지 않는다. Gmail RAW 응답에는 첨부 인코딩 데이터가 포함될 수 있다. |
| 갱신 | Google access token 만료 이후 같은 연결에서 다시 조회 | refresh token이 유효하면 다시 동의하지 않고 메일을 조회한다. Google이 갱신 권한 만료를 반환하면 메일 조회가 `401` 및 `reconnect_required`로 실패하고 `/api/session`의 `needs_reconnect`가 `true`가 된다. 재연결 버튼으로 동의를 다시 시작할 수 있어야 한다. |
| 동의 취소 | 새 연결에서 Google 동의 화면을 취소 | 취소한 연결이 인증 완료로 처리되지 않는다. |
| CSRF | 유효한 세션에서 POST의 `X-CSRF-Token`을 빠뜨리거나 다른 Origin 사용 | 상태 변경 요청이 거부된다. GET은 정상이라고 POST까지 허용하면 안 된다. |
| 로그아웃 | 화면에서 로그아웃 | `POST /api/logout` 응답은 `{"logged_out":true}`다. 새 `GET /api/session`은 `authenticated: false`다. Google 권한, 저장 토큰, 메일 메타데이터는 유지된다. |
| 연결 해제 | 다시 연결한 뒤 연결 해제 실행 | `POST /api/disconnect` 성공 응답은 `{"disconnected":true}`다. Google 권한 철회 후 해당 로컬 계정의 토큰·메타데이터·세션이 삭제된다. 같은 계정으로 연결했던 다른 창에서도 메일을 계속 읽을 수 없다. |

실제 계정 검증 결과를 남길 때는 실행 날짜, 모드, 성공·실패한 항목, 민감정보를 제거한 오류 종류만 기록한다. 위 표를 확인하지 않고 “Google 연동 검증 완료”라고 표시하지 않는다.

## 5. 자주 만나는 문제와 운영 조건

- `redirect_uri_mismatch`: Google Cloud 등록값과 `APP_BASE_URL`에서 만들어지는 callback을 비교한다. 호스트와 포트가 하나라도 다르면 실패한다.
- 테스트 사용자 제한: 실제 동의한 이메일이 해당 프로젝트의 테스트 사용자 목록에 있는지 확인한다. 조직 관리자가 Gmail 범위를 제한한 계정은 별도로 거부될 수 있다.
- 갱신 토큰 만료: External + Testing에서 Gmail 범위를 요청하면 동의와 refresh token이 **7일 뒤 만료**된다. 재연결 경로를 시험한다. 테스트 사용자는 최대 100명이다. [Google Audience 안내](https://support.google.com/cloud/answer/15549945?hl=en)
- 검증 범위: `gmail.readonly`는 restricted scope다. 공개 서비스에서 이 데이터를 서버에 저장하거나 전송한다면 OAuth 검증과 보안평가 범위를 확인해야 한다. 개인용·개발·테스트 등의 예외도 있으므로 로컬 학생 프로젝트에 곧바로 운영 서비스 기준 비용을 단정하지 않는다. [Gmail 범위](https://developers.google.com/workspace/gmail/api/auth/scopes), [검증 예외](https://support.google.com/cloud/answer/13464323?hl=en)
- 연결 해제 영향: Google revoke는 같은 Google Cloud 프로젝트에 승인된 다른 범위와 클라이언트 토큰에도 영향을 줄 수 있다. 다른 Google 기능을 같은 프로젝트에서 연결한다면 해제 화면과 설계에 반영한다. [토큰 철회](https://developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke)

Google 설정과 계정 동의는 사용자 계정에서 마무리해야 한다. client secret을 전달받아 대신 붙여넣는 절차는 필요하지 않다.
