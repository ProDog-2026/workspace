# ProDog Gmail 모듈 통합 범위

이 코드는 메일 불러오기와 Google OAuth 연결을 시험하는 FastAPI 로컬 프로토타입이다. 기본 `demo` 모드는 합성 메일을 사용하고, `.env`의 `APP_MODE=google`로 바꿨을 때만 실제 Google 연결을 시작한다. 실행과 Cloud 설정은 [Google 설정 문서](google-setup.md)를 따른다.

현재 결과물은 Gmail을 읽어 ProDog의 후속 분석기에 전달할 수 있는 입력을 만드는 단계다. 프롬프트 인젝션 탐지기, 메일 안전 판정, 자동 차단, Gemini·Copilot의 동작 제어 기능을 제공하거나 그 효과를 검증한 결과가 아니다.

## 연결과 저장 경계

- **앱 사용자 인증:** 프로토타입의 HTTP-only 세션 쿠키로 로컬 브라우저 세션을 구분한다. Supabase Auth나 조직의 사용자 관리에 연결되어 있지 않다.
- **Google 연결 계정:** 자체 Google OAuth authorization-code 흐름을 사용한다. 요청 권한은 `gmail.readonly`다. Gmail profile의 이메일을 표시하며 OIDC ID token을 검증한 앱 로그인으로 취급하지 않는다.
- **OAuth 요청 연결:** state는 시작한 브라우저 세션에 묶어 한 번만 소비하며 10분 뒤 만료된다. authorization code 교환에는 PKCE S256을 사용한다.
- **비밀값:** Google 토큰은 `TOKEN_ENCRYPTION_KEY`로 암호화한 뒤 로컬 SQLite에 저장한다. 이 설명은 SQLite 파일 전체가 암호화됐다는 뜻이 아니다. `.env`, 키, 데이터베이스를 저장소에 커밋하지 않는다.
- **메일 데이터:** 본문은 요청 처리와 응답 중 메모리에서 다룬다. 저장되는 메타데이터와 본문 해시를 원문 보관으로 오해하지 않는다. 본문 해시도 개인정보와 연결될 수 있으므로 데이터 삭제·보관 정책에 포함한다.
- **외부 요청:** Gmail API로 메일을 조회한다. 이 모듈 자체에 메일 본문을 LLM 공급자로 보내는 단계는 없다.

로그아웃은 해당 브라우저 세션만 삭제한다. 계정의 암호화 토큰과 메일 메타데이터는 남는다. 연결 해제에 성공하면 해당 계정, 저장 토큰, 메타데이터, 연결된 로컬 세션을 삭제한다. 이 삭제는 이 프로토타입의 SQLite 범위이며 향후 외부 분석기에 전달한 데이터까지 삭제하는 기능은 아니다.

Google이 갱신 권한을 거부하면 저장 bundle에 `needs_reconnect`를 표시하고 같은 토큰으로 메일 읽기를 계속 재시도하지 않는다. 이때 로컬 세션의 `authenticated`는 유지될 수 있다. 프론트는 `needs_reconnect`를 함께 확인해 재연결 버튼을 표시해야 한다. 암호화된 기존 토큰은 사용자가 명시적으로 연결 해제와 정리를 할 수 있도록 남겨두며, 새 OAuth 연결에 성공하면 새 bundle로 바뀐다.

기본 DB 경로는 `data/prodog.db`, 세션 TTL은 28,800초다. 프로토타입은 루프백 주소에서만 사용하는 것을 전제로 한다. 실제 메일이 연결된 인스턴스를 터널이나 공용 주소로 공유하지 않는다.

## API 계약

모든 POST는 `/api/session`에서 받은 `csrf_token`을 `X-CSRF-Token` 헤더로 보내고, `Origin`을 `APP_BASE_URL`과 정확히 맞춰야 한다. 쿠키도 같은 세션의 것을 유지한다. 토큰 값과 세션 쿠키를 로그로 남기지 않는다.

| 요청 | 역할 | 응답 핵심 |
| --- | --- | --- |
| `GET /api/session` | 현재 세션과 실행 모드 확인 | `authenticated`, `needs_reconnect`, `email`, `csrf_token`, `mode`, `configured` |
| `POST /api/demo/connect` | 합성 메일 세션 연결 | 새 세션 정보. 세션 쿠키가 교체되므로 이후 CSRF 값도 다시 확인한다. |
| `POST /api/oauth/google/start` | Google 동의 흐름 시작 | `authorization_url` |
| `GET /oauth/google/callback` | Google authorization code 수신 | OAuth 결과를 처리하고 화면으로 복귀한다. 일반 클라이언트가 직접 만드는 URL이 아니다. |
| `GET /api/mails?limit=20&page_token=...` | 받은편지함 목록 조회 | `messages`, `next_page_token`, `result_size_estimate` |
| `GET /api/mails/{id}` | 한 메일을 파싱한 입력 조회 | 아래 본문 계약 참조 |
| `POST /api/disconnect` | 연결 해제 및 Google 권한 철회 | 성공 시 `{"disconnected":true}` |
| `POST /api/logout` | 로컬 세션 종료 | 성공 시 `{"logged_out":true}` |

정적 탐지기가 설정된 목록 응답의 `detection.static_score`는 최종 정적 판정을 만든 모델 출력값을
`round(score * 100)`으로 바꾼 표시용 백분율이다. 실제 `detection.verdict`는 각 모델의 임계값으로
이미 결정된 값이며 대시보드는 `static_score`로 판정을 다시 계산하지 않는다. R01~R07 데모 규칙은
이 값에 합산하지 않는다.

`detection.verdict`와 `detection.thresholds`는 본문 검사 결과와 해당 모델 기준이다. 텍스트를 읽은 PDF의 검사 결과는 `detection.pdf_results`에 따로 담으며, 각 항목의 `attachment_index`는 `attachments` 배열의 인덱스다. 동적 검사 전달 여부는 본문 판정을 기준으로 한다.
대시보드는 이 값을 "왜 이 판정인지" 설명하는 데만 쓰고 판정을 다시 계산하지 않는다.

Google 모드의 목록은 받은편지함만 대상으로 한다. 페이지 토큰은 불투명한 값으로 그대로 다음 요청에 전달한다. 전체 메일 개수로 `result_size_estimate`를 사용하지 않는다. [Gmail 목록 API](https://developers.google.com/workspace/gmail/api/guides/list-messages)

현재 목록 구현도 각 메일을 Gmail `format=raw`로 조회·파싱한 뒤 브라우저에는 메타데이터만 반환한다. 본문을 선택한 순간에만 Google에서 가져오는 구조는 아니다. RAW 응답에는 MIME 첨부 인코딩 데이터도 포함될 수 있지만 첨부파일을 별도 파일로 저장하거나 실행하지 않는다. 목록 지연과 Gmail 요청 수를 줄이려면 후속 버전에서 metadata 조회와 본문 조회를 분리할 수 있다.

받은편지함 목록은 이미 검사한 메일의 결과를 재사용한다. 메타데이터는 `mails`, 정적 판정은 `mail_scans`에 계정과 메일 ID별로 저장한다. 입력 추출 버전과 탐지 모델 버전(`created_at`과 두 임계값)이 같을 때 저장된 결과를 재사용한다. 메일 본문과 PDF 추출 텍스트는 메모리에서 분석하며, 연결 해제 시 저장된 메타데이터와 판정을 지운다.

Gmail message ID는 그 연결 계정의 범위에서만 다뤄야 한다. 저장·캐시·분석 결과를 연결할 때 메일 ID 단독으로 사용자를 찾지 않는다. 향후 동일 사용자의 복수 Gmail 계정도 구별할 수 있도록 앱 사용자 ID, Google 연결 ID, Gmail message ID를 분리하는 편이 좋다.

## 후속 분석기에 넘기는 본문

`GET /api/mails/{id}`의 응답에는 다음 필드가 있다.

| 필드 | 의미와 사용 조건 |
| --- | --- |
| `id`, `thread_id` | Gmail 메일과 스레드 식별자. 접근 중인 연결 계정과 함께 사용한다. |
| `subject`, `sender`, `received_at`, `labels` | 표시·분석용 메타데이터. 발신자와 제목도 공격자가 조작할 수 있는 입력이다. |
| `body_hash` | 본문 비교·추적에 쓰는 해시. 악성 여부나 서명 검증 결과가 아니다. |
| `trust` | 본문이 `UNTRUSTED` 입력이라는 경계 표시다. |
| `text_plain` | 추출한 일반 텍스트. 본문 안의 지시를 실행하지 않는다. |
| `text_html` | 원문 HTML 입력. 브라우저 DOM에 삽입하거나 실행하지 않는다. 메모리에서 분석할 자료다. |
| `analysis_text` | MIME 순서의 일반 텍스트와 HTML 원문을 합친 문자열. HTML 태그·주석도 포함하며, 렌더링하거나 시스템 지시로 취급하지 않는다. |
| `attachments` | 첨부 메타데이터. PDF에는 `pdf_status`로 `parsed`, `encrypted`, `invalid`, `no_text`, `limit` 중 하나가 표시된다. |
| `warnings` | 파싱·제외·제한 등 후속 처리에 필요한 알림. 누락하지 않고 분석 결과에 전달한다. |
| `truncated` | 길이 제한으로 잘린 입력 여부. `true`면 전체 메일에 대한 분석이라고 말할 수 없다. |

분석 파이프라인에서는 이 응답 딕셔너리를 **신뢰할 수 없는 이메일 입력**으로 전달한다. 메일의 “이전 지시를 무시하라”, 도구 호출 요청, URL 방문 요청은 사용자 명령이나 시스템 지시로 승격하지 않는다. 제목, From 헤더, plain text, HTML을 동일한 입력 경계에서 처리한다.

HTML의 화면 표시와 보안 분석은 목적이 다르다. 화면에는 escaped text 또는 별도로 검증한 sanitizer 결과만 사용한다. 원문 HTML을 분석할 때는 네트워크 요청, 스크립트 실행, 외부 이미지 로딩 없이 처리한다. 단순 HTML 제거는 공격 탐지나 안전 보장이 아니다.

PDF에서 추출한 텍스트는 서버 내부에서 정적 검사에 사용하며, 정적 판정이 의심 또는 위험인 메일의 연동 동적 검사에도 전달한다. `parsed`는 PDF 텍스트 추출 성공을 뜻한다. 이미지에서 텍스트를 읽는 OCR은 지원하지 않는다.

첨부가 제외되면 `attachment_content_not_analyzed` 경고가 붙는다. `truncated`는 길이·구조 제한에 따른 생략을 표시하므로 첨부 제외만으로 반드시 `true`가 되지는 않는다. 후속 분석기는 `truncated` 하나만 보지 말고 `warnings`와 `attachments`도 함께 확인한다.

원문은 최대 2 MiB, plain과 HTML을 합친 텍스트는 최대 300,000자, MIME part는 최대 100개, MIME 깊이는 최대 20단계, 헤더 표시 길이는 최대 4,096자로 제한한다. PDF는 메일당 최대 4건, 파일당 1 MiB·20페이지·3초, 전체 추출 텍스트는 메일당 40,000자로 제한한다.

## Supabase 또는 팀 서비스로 옮길 때

현재 프로토타입의 로컬 세션을 공유 서비스의 인증으로 그대로 배포하면 안 된다. 다음 작업이 끝나야 팀 서버의 실제 사용자에게 연결할 수 있다.

1. Supabase Auth 등 기존 앱 인증에서 검증한 사용자 ID를 서버가 받아 Gmail 연결 시작에 묶는다. callback에서도 같은 연결 요청임을 검증한다. Gmail 이메일을 앱 사용자 소유권의 유일한 근거로 삼지 않는다.
2. 연결·메일 메타데이터·분석 결과를 앱 사용자 ID 및 연결 ID로 분리한다. Supabase 테이블을 브라우저에서 읽는다면 RLS를 설정하고, 사용자 A가 사용자 B의 행을 조회·수정할 수 없는지 직접 시험한다.
3. Google refresh token을 브라우저나 일반 테이블 조회에 노출하지 않는다. 서버 전용 저장소와 암호화 키 관리를 정하고 키 교체·토큰 폐기 절차를 만든다.
4. HTTPS, Secure 쿠키, 허용 Origin·redirect URI, 공유 세션 저장소, 요청 제한, 만료 정리, 안전한 오류·로그 처리를 운영 환경에 맞춘다.
5. 계정별 메일 저장과 삭제, 분석 로그, 재시도·중복 수집, 연결 해제 후 보관 여부를 정의한다. 연결을 해제했다고 이미 저장한 분석 데이터가 자동 삭제됐다고 표시하지 않는다.
6. 다른 사용자 세션, 만료된 state, callback 재사용, 권한 거절, Google 토큰 철회, 잘린 메일, 첨부파일 포함 메일을 통합 테스트한다.

Supabase 로그인을 채택하더라도 Google provider token 갱신은 별도 책임이다. Supabase Auth의 세션 갱신과 Gmail access token 갱신을 혼동하지 않는다. [Supabase provider token 안내](https://supabase.com/docs/guides/auth/social-login#provider-tokens)

## 후속 작업 의견

우선 목표는 실제 테스트 계정에서 연결, 받은편지함 목록, 본문 전달, 재연결, 연결 해제를 끝까지 재현하는 것이다. 다음으로 탐지기 입력 계약과 누락 상태를 맞춘다. 지속 동기화, Pub/Sub, 여러 메일 공급자 지원은 이 흐름이 검증된 뒤 확장해도 된다.

탐지 성능을 평가할 때 수집 성공률과 탐지 정확도를 별도로 기록하는 편이 좋다. 수집·파싱에 실패한 메일을 평가 집합에서 조용히 빼면 탐지 성능이 실제보다 좋아 보일 수 있다. 원문과 파싱 결과를 비교하는 시험용 데이터는 실제 개인 메일 대신 동의받은 시험 메일이나 합성 메일로 준비한다.
