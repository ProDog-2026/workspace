# ProDog Gmail 메일 연동: 진행 기록과 재현 가이드

작성 기준: 2026-09-28. 이 문서는 심재훈 담당 WBS `메일 불러오기 · Google OAuth 연동(9/24~9/30)`을 실제로 어떻게 진행했는지 기록하고, 같은 로컬 환경을 다시 만드는 순서를 정리한다. 화면 이름은 당시 Google Cloud 콘솔의 한국어 UI 기준이다.

## 1. 작업 범위와 출발점 확인

Vault의 `01_Projects/PromptShield/프로젝트-개요.md`에서 ProDog의 입력이 Gmail 메일이며, 이 담당 범위는 OAuth 연결과 메일 수집이라는 점을 확인했다. 연결할 팀 코드 저장소는 확인되지 않아 사용자가 지정한 대로 `D:\SJH_Data\SecurityLab\prodog-gmail`에 독립 실행 가능한 모듈을 만들었다. 팀 저장소로 옮기는 작업은 나중에 한다.

이번 모듈의 완료 기준은 Google 계정이 메일 **읽기 권한**을 부여하고, 앱에서 받은편지함 목록·본문·분석용 텍스트를 확인하는 것이다. 정적·동적 탐지기, 안전 판정, Gemini·Copilot의 메일 접근 차단은 이 모듈에 포함하지 않았다. 구조와 팀 통합 조건은 [통합 문서](integration.md)에 있다.

## 2. 로컬 프로젝트와 데모 준비

Python 3.11 이상, `uv`를 사용한다. 화면 검증용 Node.js와 npm은 개발 테스트를 실행할 때만 필요하다. 새 환경에서는 PowerShell에서 다음을 실행한다.

```powershell
Set-Location -LiteralPath 'D:\SJH_Data\SecurityLab\prodog-gmail'
uv sync --frozen --dev
uv run python scripts/setup_local.py
```

첫 명령은 `uv.lock`에 고정된 Python 의존성을 설치한다. 두 번째 명령은 `.env`가 **없을 때만** 데모 설정과 토큰 암호화 키를 만든다. 예상 결과는 `.env`와 `.venv`가 준비되는 것이다. 이미 연결해 둔 PC에서 `.env`를 지우거나 암호화 키를 다시 만들면 저장 토큰을 읽을 수 없으므로 기존 파일을 유지한다.

처음에는 합성 메일 3개를 넣은 `demo` 모드로 화면과 수집 흐름을 확인했다. 새 설치에서 서버를 직접 실행하려면 다음을 사용한다.

```powershell
uv run uvicorn prodog_gmail.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

`http://127.0.0.1:8765`에서 **데모 계정 연결**과 예시 메일 3개가 보여야 한다. `--no-access-log`는 OAuth callback의 인증 코드가 일반 접근 로그에 남지 않도록 사용했다. 현재 개발 PC는 아래 절차까지 마쳐 `google` 모드이므로 데모 버튼 대신 **Google 계정 연결**이 보이는 것이 정상이다.

## 3. 메일 연결·수집 코드 구현

| 파일 | 구현한 역할 | 확인할 점 |
| --- | --- | --- |
| `src/prodog_gmail/app.py` | 세션, OAuth 시작·callback, 목록·본문·연결 해제 API | 인증되지 않은 세션의 메일 요청은 거부된다. |
| `src/prodog_gmail/google.py` | Google authorization code 교환, Gmail API 호출, 토큰 갱신·철회 | 요청 범위는 `gmail.readonly` 하나다. |
| `src/prodog_gmail/store.py` | SQLite 계정·세션·메일 메타데이터, Fernet 토큰 암호화 | 본문·RAW·토큰 평문은 DB에 넣지 않는다. |
| `src/prodog_gmail/mail.py` | Gmail RAW와 MIME의 plain/HTML 파싱 | HTML 주석·숨김 문자열을 분석 입력에 남긴다. |
| `src/prodog_gmail/static/` | 계정 연결, 목록·페이지 이동, 본문 표시 | HTML은 실행하지 않고 텍스트로 표시한다. |

OAuth 시작에는 브라우저 세션에 묶인 일회성 `state`와 PKCE S256을 사용했다. 세션 쿠키는 HttpOnly이고 상태 변경 POST에는 Origin·CSRF 검사를 적용했다. access token과 refresh token은 서버에서 암호화해 저장한다. 메일 본문은 요청 중 메모리에서 파싱하고, DB에는 계정별 메타데이터와 본문 해시만 남긴다. 첨부 내용은 분석하지 않으며 `warnings`로 누락을 알린다.

화면의 `UNTRUSTED`는 외부에서 온 입력이라는 표시다. 탐지 결과나 안전 판정이 아니다. 분석기에 넘기는 필드와 수집 제한은 [통합 문서](integration.md)를 따른다.

## 4. 합성 데이터 검증

실제 계정 연결 전에 서버·MIME 파싱·화면 동작을 합성 응답으로 시험했다.

```powershell
uv run pytest -q
uv run ruff check src tests scripts
npm ci
npm test
node --check src/prodog_gmail/static/app.js
```

각 명령은 순서대로 Python 동작, Python 정적 검사, 화면 테스트 의존성 설치, 화면 DOM 동작, JavaScript 문법을 확인한다. 2026-09-27 기록은 Python 테스트 99개, 화면 테스트 10개 통과와 정적 검사 통과다. 이 숫자는 **그날의 실행 기록**이며 새 환경에서는 다시 실행해 결과를 확인해야 한다. 데모 서버에서 `uv run python scripts/smoke_demo.py`로 실제 로컬 HTTP 흐름도 확인했다. 이 스크립트는 `demo` 모드 전용이며 현재 `google` 모드에서는 실행하지 않는다. 상세 결과는 [검증 기록](verification.md)에 있다.

## 5. Google Cloud 프로젝트와 OAuth 앱 만들기

실제 연결에 사용한 전용 프로젝트는 `ProDog Gmail Dev`, 프로젝트 ID는 `prodog-gmail-dev`다. 재현할 때는 본인 프로젝트에서 다음 순서로 설정한다.

1. Google Cloud 콘솔에서 개발용 프로젝트를 선택하거나 새로 만든다. 이 프로젝트에서는 **Gmail API**를 사용 설정했다.
2. **Google 인증 플랫폼 → 시작하기**에서 앱 이름과 사용자 지원 이메일을 입력했다.
3. **대상**을 `외부`로 선택했다. 생성 후 게시 상태는 `테스트 중`으로 유지했다.
4. 개발자 연락처를 입력하고 Google의 사용자 데이터 정책 동의 단계를 완료해 OAuth 브랜드를 만들었다.
5. **대상 → 테스트 사용자 → 사용자 추가**에 실제로 연결할 Google 계정을 등록했다. 이번에는 1명을 등록했다.
6. **데이터 액세스 → 범위 추가 또는 삭제**에서 아래 범위만 추가하고 저장했다.

   ```text
   https://www.googleapis.com/auth/gmail.readonly
   ```

7. **클라이언트 → 클라이언트 만들기**에서 유형 `웹 애플리케이션`, 이름 `ProDog Local Web`을 선택했다. 승인된 리디렉션 URI에 다음 값을 정확히 입력했다. 이 서버는 백엔드에서 코드를 교환하므로 JavaScript 원본은 추가하지 않았다.

   ```text
   http://127.0.0.1:8765/oauth/google/callback
   ```

8. 클라이언트 생성 직후 **JSON 다운로드**를 눌러 로컬에 보관했다. 파일에는 비밀값이 들어 있으므로 채팅·문서·저장소에 내용을 붙여 넣지 않는다.

`127.0.0.1`과 `localhost`, 포트, 경로가 다르면 callback이 일치하지 않는다. Google의 [웹 서버 OAuth 안내](https://developers.google.com/identity/protocols/oauth2/web-server)는 Web application 클라이언트와 정확한 리디렉션 URI 등록을 안내한다. [Gmail 권한 목록](https://developers.google.com/workspace/gmail/api/auth/scopes)에서 `gmail.readonly`는 읽기 용도이자 제한된 범위로 분류된다.

## 6. 내려받은 클라이언트를 로컬 앱에 연결

다운로드한 **Web application JSON의 실제 경로**를 인자로 넣는다. 다음 경로는 형식 예시다.

```powershell
Set-Location -LiteralPath 'D:\SJH_Data\SecurityLab\prodog-gmail'
uv run python scripts/configure_google.py 'C:\Users\사용자\Downloads\client_secret_예시.json'
```

스크립트는 JSON에 `web` 클라이언트 정보와 위 callback 주소가 있는지 검사한다. 일치하면 `.env`의 client ID·secret을 설정하고 `APP_MODE=google`로 바꾼다. 기존 `TOKEN_ENCRYPTION_KEY`는 보존하고 첫 가져오기 전 설정을 `.env.before-google.bak`으로 보관한다. 예상 출력은 `Google settings imported into local .env. Restart the server. No secrets printed.`이다. 실패하면 callback과 JSON 유형을 먼저 확인하고, 비밀값은 화면에 출력하지 않는다.

서버를 재시작한 뒤 민감값을 출력하지 않는 다음 확인을 할 수 있다.

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/session' |
    Select-Object mode, configured, authenticated
```

`mode=google`, `configured=True`가 나와야 한다. 새 HTTP 세션의 `authenticated=False`는 정상이며 브라우저에서 연결해야 참이 된다.

## 7. Google 계정 동의와 실메일 조회

브라우저에서 `http://127.0.0.1:8765`를 열고 **Google 계정 연결**을 눌렀다. 테스트 사용자로 등록한 계정을 선택하고 Google 화면의 **이메일 메시지 및 설정 보기** 권한을 확인한 뒤 사용자가 직접 **허용**했다. 앱으로 돌아오면 계정이 `연결됨`으로 표시되고 받은편지함을 불러온다.

2026-09-28 실제 확인 결과는 다음과 같다.

| 확인 | 당시 결과 |
| --- | --- |
| OAuth 동의·callback | 실제 계정 연결 성공. |
| 받은편지함 | 첫 페이지 20개 표시, 다음 페이지 20개 조회. |
| 메일 본문 | 1건에서 본문·분석 입력·HTML 원문 보기와 본문 해시 표시 확인. |
| 세션 격리 | 별도 새 HTTP 세션은 인증되지 않았고 메일 API가 `401`로 거부됨. |
| 토큰 갱신 | 실제 refresh token 요청 성공. 갱신된 토큰으로 같은 Gmail 계정의 프로필 조회 성공. |
| 저장소 | 토큰은 암호화 형식으로 저장. 확인한 메타데이터에 본문·RAW·분석 텍스트 없음. |

본문 내용과 토큰·쿠키·인증 코드는 검증 기록에 남기지 않았다. 실제 연결을 유지하기 위해 이 계정에서는 로그아웃과 권한 철회를 수행하지 않았다. 해당 기능은 합성 응답 테스트로 검증했다. 자연 만료 후 자동 갱신과 7일 뒤 재동의는 아직 실계정에서 확인하지 않았다.

## 8. 서버를 켜고 끄는 방법

개발 중 터미널을 계속 열어 둘 때는 `.\start.ps1`을 사용한다. 해당 터미널을 닫으면 서버도 종료된다. 이 때문에 임시 실행 세션에서 시작한 서버가 대화 종료 후 꺼진 적이 있었다. 백그라운드 실행이 필요해 아래 스크립트를 추가했다.

```powershell
Set-Location -LiteralPath 'D:\SJH_Data\SecurityLab\prodog-gmail'
.\start-background.ps1
```

예상 출력은 `ProDog is running at http://127.0.0.1:8765.`이다. 현재 사용자 계정의 Windows 작업 스케줄러에 `ProDog-Gmail-Local` 작업을 등록해 서버를 실행한다. 새 터미널이나 대화가 끝나도 서버가 유지된다. **PC 재시작 뒤에는 다시 실행해야 한다.** 시작 실패는 `artifacts/server.background.log`를 확인한다.

```powershell
.\stop.ps1
```

이 명령은 등록된 ProDog 작업과 이 폴더의 서버만 종료한다. 2026-09-28에는 작업 상태 `Running`, `http://127.0.0.1:8765/` 응답 `200`, 세션 API의 `mode=google`·`configured=True`를 확인했다.

## 9. 자주 막히는 지점과 다음 작업

- **`redirect_uri_mismatch`**: Google Cloud의 승인된 리디렉션 URI와 로컬 callback의 호스트·포트·경로를 비교한다.
- **테스트 사용자 접근 실패**: OAuth 앱이 `테스트 중`이면 연결할 계정을 프로젝트의 테스트 사용자에 추가했는지 확인한다. 조직 관리자의 Gmail 접근 정책도 영향을 줄 수 있다.
- **일주일쯤 뒤 다시 인증 필요**: 현재 `외부 / 테스트 중` 설정에서 Gmail 권한은 기본 프로필 범위 예외에 해당하지 않으며, Google 안내상 테스트 동의와 offline refresh token은 7일 뒤 만료된다. [Google 대상 안내](https://support.google.com/cloud/answer/15549945?hl=en)를 확인하고 앱에서 재연결한다.
- **포트 8765가 열리지 않음**: `.\start-background.ps1`을 다시 실행하고 `artifacts/server.background.log`를 확인한다. 다른 프로그램이 포트를 사용하면 해당 프로그램을 확인한 뒤 충돌을 해결한다.
- **메일이 누락되거나 잘림**: `warnings`, `truncated`, `attachments`를 먼저 본다. 첨부 내용 분석과 전체 메일 안전 판정은 이 수집 모듈의 결과가 아니다.

팀 서비스에 옮길 때는 Supabase 앱 사용자 ID와 Gmail 연결을 묶고, 계정별 데이터 격리·RLS·HTTPS·키 관리를 다시 구현해야 한다. 현재 코드의 SQLite 세션을 공유 서버의 로그인으로 그대로 사용하지 않는다. 또한 목록 조회는 현재 페이지 메일의 RAW를 각각 읽어 파싱하므로, 메일이 많아지면 목록 메타데이터 조회와 본문 조회를 분리하는 편이 좋다. 세부 설계는 [통합 문서](integration.md), 실제/합성 검증 경계는 [검증 기록](verification.md), 프로젝트 방향 의견은 [설계 검토](project-review.md)에 적었다.
