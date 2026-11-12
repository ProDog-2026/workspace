# ProDog Gmail 메일 연동 모듈

`메일 불러오기 · Google OAuth 연동(9/24~9/30)`의 로컬 구현이다.
Google 계정을 연결하고 받은편지함을 조회한 뒤, 메일 본문을 후속 분석기에 넘길 수 있는 형태로 추출한다.
탐지 결과나 차단 효과를 만들어 표시하지 않는다.

처음부터 진행한 순서와 재현 방법은 [단계별 진행 가이드](docs/implementation-guide.md)에 정리했다.
최종 판단 방식의 비교 기준과 미측정 항목은 [Jev 비교 보고서](docs/jev-comparison-report.md)에, 공개 가능한 개인 기여 서술 초안은 [포트폴리오 사례](docs/portfolio-case-study.md)에 정리했다.

## 바로 실행

Python 3.11 이상과 [uv](https://docs.astral.sh/uv/)가 필요하다.
PowerShell에서 다음 명령을 실행한다.

```powershell
Set-Location -LiteralPath 'D:\SJH_Data\SecurityLab\prodog-gmail'
.\start.ps1
```

`http://127.0.0.1:8765`에 접속한다. 새로 설치한 기본 `demo` 모드에서는 **데모 계정 연결**을 누른다.
정상 한국어 메일, 숨김 문구를 넣은 HTML 메일, 첨부파일이 있는 메일 3개로 전체 흐름을 확인할 수 있다.
데모 모드는 합성 자료만 사용하며 실제 Gmail이나 외부 AI에 접속하지 않는다.
현재 이 PC의 로컬 설정은 `google` 모드이며 실제 Gmail 계정 연결을 완료했다. 새 브라우저 세션에서는 **Google 계정 연결**을 사용한다.
서버를 종료하려면 실행한 터미널에서 `Ctrl+C`를 누른다.
터미널을 닫아도 실행을 유지하려면 `.\start-background.ps1`을 사용한다.
이 스크립트는 현재 사용자 계정의 Windows 작업 스케줄러에 `ProDog-Gmail-Local` 작업을 등록하고 서버를 시작한다. 로그인한 상태에서 필요할 때만 실행되며, PC를 재시작한 뒤에는 다시 시작해야 한다.
백그라운드 서버는 `.\stop.ps1`로 종료한다. 시작 오류는 `artifacts/server.background.log`에서 확인할 수 있다.
종료 스크립트는 이 폴더의 ProDog 프로세스인지 확인하고 다른 프로그램은 종료하지 않는다.

스크립트 실행 대신 직접 시작할 수도 있다.

```powershell
uv sync --frozen --dev
uv run python scripts/setup_local.py
uv run uvicorn prodog_gmail.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

`setup_local.py`는 `.env`가 없을 때만 새 암호화 키를 생성한다.
이미 있는 설정과 키를 덮어쓰지 않으며 비밀값을 출력하지 않는다.

## 실제 Google 연결

2026-09-28 전용 프로젝트 `prodog-gmail-dev`에서 Google Cloud 설정과 실제 계정 동의를 완료했다.
받은편지함 20개, 본문 표시, 페이지 이동, 실제 토큰 갱신, 별도 세션의 접근 차단을 확인했다. 상세 결과와 미검증 항목은 [검증 기록](docs/verification.md)에 남겼다.
앱은 External / Testing 상태다. 다른 계정은 해당 프로젝트의 테스트 사용자로 추가해야 한다.

다른 환경에서 처음 설정하는 절차는 다음과 같다.

1. [Google 설정 문서](docs/google-setup.md)에 따라 Gmail API와 테스트 사용자를 설정한다.
2. Web application OAuth 클라이언트의 redirect URI를 `http://127.0.0.1:8765/oauth/google/callback`으로 등록한다.
3. 로컬 `.env`에 client ID와 client secret을 입력하고 `APP_MODE=google`로 바꾼다. 기존 `TOKEN_ENCRYPTION_KEY`는 유지한다.
4. 서버를 다시 시작하고 **Google 계정 연결**에서 본인 계정으로 동의한다.
5. 설정 문서의 실제 계정 검증표를 수행한다.

Google에서 Web application 클라이언트 JSON을 내려받았다면 직접 값을 복사하는 대신 로컬에서 가져올 수 있다.
callback URI가 맞는 Web 클라이언트인지 검사하고 기존 암호화 키는 보존한다.

```powershell
uv run python scripts/configure_google.py 'C:\경로\client_secret.json'
```

비밀값을 채팅이나 저장소에 올릴 필요는 없다.
현재 코드 폴더를 옮길 때 `.env`와 `data/`는 소스와 분리해 취급한다.
기존 로컬 연결을 그대로 유지하려면 DB와 암호화 키가 함께 필요하다.

## 구현 범위

| 기능 | 동작 |
| --- | --- |
| Google OAuth | `gmail.readonly`, offline token, PKCE S256, 브라우저 세션에 묶인 일회성 state |
| 세션 | HttpOnly·SameSite=Lax 쿠키, 연결 후 회전, 만료, POST Origin·CSRF 확인 |
| 메일 목록 | 받은편지함, 페이지 이동, 페이지당 1~50개, 계정별 요청 제한 |
| 본문 추출 | Gmail RAW, 한글 charset, MIME plain/HTML, 숨김 문구·주석 보존 |
| 후속 분석 입력 | `trust=UNTRUSTED`, `analysis_text`, 원문 본문 SHA-256, 불완전 수집 경고 |
| 토큰 | 서버에서 암호화 저장·자동 갱신, 갱신 실패 시 재연결, 프런트엔드 미노출 |
| 로컬 DB | 계정별 메타데이터 upsert. 본문·RAW·첨부 내용은 저장하지 않음 |
| 연결 해제 | Google revoke 성공 후 로컬 토큰·계정·메일 메타데이터·세션 삭제 |
| 표시 | 외부 이미지 요청과 HTML 실행 없이 텍스트로 표시 |

원문은 최대 2 MiB, 추출 텍스트는 최대 300,000자, MIME part는 100개, 중첩은 20단계까지 처리한다.
첨부 내용은 분석하지 않으며 누락을 경고로 표시한다.
제한·경고가 있는 입력을 전체 메일의 안전 판정으로 바꾸면 안 된다.
본문 해시는 RFC 822 본문 바이트를 기준으로 하며 MIME 경계와 인코딩된 첨부 데이터도 포함한다.

## 팀 코드에 연결

[통합 문서](docs/integration.md)에 API와 데이터 계약, Supabase 전환 조건을 정리했다.
`GET /api/mails/{id}`가 반환하는 `analysis_text`, `text_plain`, `text_html`을 분석기에 전달할 수 있다.
메일 제목·발신자·본문은 모두 공격자가 쓸 수 있는 데이터다.
분석기는 `warnings`, `truncated`, `attachments`도 함께 받아 수집 누락과 분석 결과를 구분해야 한다.

현재 모듈은 독립 실행 검증을 위해 SQLite와 로컬 세션을 사용한다.
팀의 Supabase Auth·DB 구현을 대체하는 최종 구조는 아니다.
공유 서버로 옮길 때 앱 사용자 ID에 Gmail 연결을 묶고 RLS·HTTPS·키 관리·공유 세션을 적용해야 한다.
개발용 서버는 루프백에만 바인딩한다.

## 검증

```powershell
uv sync --frozen --dev
uv run pytest -q
uv run ruff check src tests scripts
```

OAuth와 Gmail API는 테스트에서 가짜 HTTP 응답으로 대체한다.
실제 비밀값이나 메일이 필요하지 않다.
CSRF·state 재사용·세션 만료·사용자 격리·갱신/철회·암호화·본문 미저장·MIME 제한을 시험한다.
프런트엔드 DOM 테스트와 이번 실행 기록은 [검증 기록](docs/verification.md)에 정리한다.

## 파일 구성

```text
src/prodog_gmail/
  app.py       HTTP API, 세션 연결, CSRF, 메일 조회
  google.py    Google OAuth, Gmail API, 토큰 갱신·철회
  store.py     암호화 토큰, 세션, 계정별 메타데이터
  mail.py      실행하지 않는 MIME 파싱과 분석 입력 추출
  demo.py      합성 Gmail 응답 3개
  static/      연결·메일 조회 화면
tests/         서버·Google 응답·파싱·화면 동작 테스트
docs/          실제 계정 설정, 팀 통합 계약, 검증 기록
```

프로젝트 방향에 대한 의견은 [설계 검토](docs/project-review.md)에 정리했다.
