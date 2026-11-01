# Gmail 수집·Supabase·대시보드 데이터 계약

이 문서는 실제 서버와 대시보드의 저장 계약이다. FastAPI는 Supabase Auth로 검증한 UUID에 Gmail OAuth 연결을 귀속하고, 로컬 SQLite outbox를 통해 수집·분석 결과를 Supabase에 저장한다. 브라우저는 scans를 SELECT한다. `src/dashboard/schema.sql`은 과거 필드 참고용이며 실행하지 않는다.

## 결정 요약

| 쟁점 | 결정 |
| --- | --- |
| Gmail 계정 식별 | 현재 `gmail.readonly` 권한을 유지한다. `users.getProfile`의 `emailAddress`를 연결 계정 키로 사용한다. 앱 사용자 귀속은 별도로 검증한 Supabase Auth `user_id`로 한다. |
| 해시 | DB 컬럼을 `body_hash`로 바꾸고, 수집 모듈과 똑같이 RFC 822 본문 바이트의 SHA-256을 저장한다. `analysis_text`의 해시가 아니다. |
| 경고 | `warnings text[]`에 수집 출력의 코드 목록을 그대로 저장한다. 화면용 문장은 별도 변환한다. |
| 수집 상태 | `fetch_status`는 아래 규칙으로 서버가 계산한다. 수집 모듈의 출력 필드인 것처럼 다루지 않는다. |
| 대시보드 | `src/dashboard/schema.sql`의 필드와 값(`safe`, `ok`, 점수, `evidence`)을 읽기 계약으로 채택한다. 저장 테이블 `analysis_results`와 메일 테이블을 결합한 `scans` 읽기 뷰가 그 계약을 제공한다. `scans` 테이블을 중복 생성하지 않는다. |

### Google 계정 ID의 한계

현재 수집 모듈은 ID 토큰을 받지 않는다. 따라서 이전 설계의 `google_id = sub`는 삭제한다. 연결 시 Gmail API `users.getProfile`에서 받은 이메일을 소문자 정규화해 `(user_id, email)`로 중복을 막는다. 이 값은 불변 Google 계정 ID가 아니므로 주소가 변경되면 기존 연결과 자동 병합하지 않고 재연결·이전 절차를 거친다. Supabase 로그인 사용자 ID와 Gmail 이메일만으로 타인 계정을 연결할 수 없도록 OAuth callback의 state/PKCE 및 현재 사용자 세션 검증이 필요하다. 장기적으로 불변 ID가 필요하면 Google OIDC `openid` 범위를 추가하고 검증된 ID 토큰의 `sub`로 마이그레이션한다.

근거: [Gmail users.getProfile](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile), [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect).

## 테이블과 필드

### `gmail_connections` — 사용자와 Gmail 연결

| 필드 | 입력 |
| --- | --- |
| `id` | 연결 UUID, DB 생성 |
| `user_id` | Supabase Auth에서 검증한 앱 사용자 UUID |
| `email` | Gmail `users.getProfile.emailAddress`, 소문자 정규화 |
| `status` | `active` 또는 `reconnect_required` |
| `connected_at` | 연결 시각 |

로컬 SQLite `accounts.id`는 로컬 프로토타입 ID이며 Supabase `user_id`가 아니다. 연결 해제 시 토큰 저장소의 자격 증명을 지우고 이 연결 행을 삭제한다.

### `mail_messages` — 메일 메타데이터와 수집 품질

| DB 필드 | 수집 모듈 출력 |
| --- | --- |
| `id`, `user_id`, `connection_id` | DB 메일 UUID, 앱 사용자 UUID, Gmail 연결 UUID |
| `gmail_id` | `id` |
| `subject`, `sender`, `received_at` | 동일 이름의 값 |
| `body_hash` | `body_hash`; 원본 RFC 822 본문 바이트의 SHA-256 |
| `attachments` | `attachments` 배열의 메타데이터만 JSON 배열로 저장. 파일 내용 제외 |
| `warnings` | `warnings` 코드 배열 그대로 저장 |
| `truncated` | `truncated` |
| `fetch_status` | 아래 계산 규칙에 따라 서버가 생성 |
| `collected_at` | Supabase에 수집 기록을 만든 시각; DB 기본값 |

메일 본문 `raw`, `text_plain`, `text_html`, `analysis_text`는 저장하지 않는다. 같은 `connection_id + gmail_id`의 재수집은 기존 행을 갱신한다. `body_hash`도 사용자와 연결될 수 있는 데이터이므로 연결 해제 시 같이 삭제한다.

### `analysis_results` — 분석 실행별 결과

| 필드 | 의미 |
| --- | --- |
| `id`, `user_id`, `mail_id` | 실행 UUID, 앱 사용자 UUID, 대상 메일 UUID |
| `status` | `pending`, `succeeded`, `failed` |
| `verdict` | `safe`, `suspicious`, `danger`; 미판정이면 NULL |
| `static_score`, `llm_score`, `final_score` | 대시보드 점수 0–100. 해당 단계가 실패했으면 NULL |
| `types` | 탐지 유형 코드 목록 |
| `evidence` | `[{rule,type,snippet}]` 형태의 근거. 제한된 원문 발췌이므로 개인정보로 취급 |
| `llm_reason` | 문맥 분석 설명. 근거 문장이 없을 수 있음 |
| `missing_parts` | 수집 경고·첨부 제외를 사용자 문장으로 바꾼 목록 |
| `status_detail` | 분석 실패 이유 등 사용자에게 보여 줄 안전한 설명 |
| `model` | 사용한 모델과 버전 |
| `analysis` | 최대 32KiB JSONB object. job id/status, 모델별 정적 점수·임계값, 동적 관측 요약과 안전한 오류. 원문·명령·토큰 제외 |
| `created_at` | 실행 생성 시각 |

메일 한 건을 새 설정/입력으로 분석하면 새 job UUID를 만든다. 같은 job의 상태 변경·재시도는 동일 id로 upsert한다. `scans`는 최신 완료/실패를 우선하며 완료 결과가 없으면 최신 pending 또는 분석 전 메타데이터도 표시한다. 진행 중인 행은 기존 완료 결과를 가리지 않는다. 동적 실패가 있어도 완료된 정적 위험 판정은 succeeded/verdict에 유지하고 동적 오류는 analysis에 기록한다. 점수 컬럼은 DB의 정수 0–100 규칙에 맞춰 반올림하며 정적 원래 확률·임계값은 analysis JSON에 남긴다. 화면은 이 원래 확률을 0–100으로 변환해 눈금·모델별 점수와 정밀도를 맞추고, JSON 값이 없는 기존 결과만 정수 컬럼을 사용한다. llm_score는 NULL이다.

## `fetch_status` 계산

다음 순서대로 판정한다. `attachment_content_not_analyzed`를 포함한 경고는 전체 메일을 분석하지 못했다는 뜻이므로 `incomplete`에 포함한다.

1. `failed`: Gmail 요청 자체가 실패했거나, 파서 출력의 `body_hash`가 NULL이라 RAW 본문을 받아들이지 못한 경우. 메일 ID를 알 수 없으면 메일 행을 만들지 않고 수집 작업 오류로 기록한다.
2. `incomplete`: `body_hash`가 있고 `truncated=true`, `warnings`가 비어 있지 않음, 또는 `attachments`가 비어 있지 않음 중 하나라도 참인 경우.
3. `complete`: 위 두 경우가 모두 아닌 경우. 수집 완료는 분석 성공이나 메일 안전을 뜻하지 않는다.

DB에는 `fetch_status` 기본값을 두지 않았다. 통합 서버가 위 규칙을 계산해 명시적으로 넣어야 하며, 누락되거나 해시·경고·첨부·잘림 값과 모순되면 INSERT가 실패한다.

수집 상태와 판정을 별도로 보관한다. 수집이 불완전해도 탐지된 위험·의심 근거는 `verdict`에 남긴다. 분석 실패를 `safe`로 채우지 않는다.

## 대시보드 `scans` 읽기 계약

`scans`는 `mail_messages`와 최신 `analysis_results`를 연결한 읽기 전용 뷰다. `gmail_message_id`, `sender`, `subject`, `received_at`는 메일 테이블에서, 점수·`verdict`·`evidence`는 분석 테이블에서 온다. `status`는 다음과 같이 변환한다.

| 조건 | `scans.status` |
| --- | --- |
| 메일 수집 실패 또는 분석 실패 | `failed` |
| 분석 성공이고 수집 불완전 | `incomplete` |
| 분석 성공이고 수집 완료 | `ok` |

수집 실패 메일은 분석 행이 없어도 failed로 표시한다. 최초 pending은 verdict=NULL이고 화면에서 검사 전이다. missing_parts는 현재 수집 경고 코드와 첨부/잘림 안내를 포함한다. 값이 비었는데 수집 불완전이면 뷰가 일반 문구를 제공한다. 위험·의심 판정이 있으면 화면은 그 판정을 먼저 표시하고 수집 불완전 안내도 함께 표시한다. 이 우선순위는 `src/dashboard/src/scans.js`의 stateOf와 같다.

`evidence.snippet`은 전체 본문이 아니어도 메일 원문 발췌이므로 개인정보다. 서버에서 건수·길이를 제한하고 화면에서는 HTML로 실행하지 않고 텍스트로만 표시한다. 연결 해제 시 분석 결과와 함께 삭제한다.

## 접근 및 삭제

세 저장 테이블은 RLS를 켜고 본인 `user_id` 행의 SELECT만 허용한다. `scans` 뷰는 `security_invoker=true`로 만들고 같은 사용자 권한으로 기초 테이블 RLS를 적용한다. 브라우저 쓰기는 허용하지 않는다. 서버의 서비스 키는 RLS를 우회하므로 매 요청에서 Supabase 사용자와 연결 소유권을 확인한다.

연결 해제 API는 Google 권한 철회 후 계정별 outbox 저장 lock 안에서 본인의 Supabase 연결을 삭제하고 로컬 계정·토큰·메타데이터·outbox를 삭제한다. 연결 삭제는 `gmail_connections → mail_messages → analysis_results`로 연쇄 삭제된다. 저장 중인 요청이 늦게 연결을 다시 생성하지 않게 직렬화한다. 과거 동적 실행 로그 파일은 추가 삭제하지 않는다.

## 구현·확인 경계

후속 변경은 [supabase-pipeline-integration.sql](supabase-pipeline-integration.sql)이며 지정 프로젝트의 기존 3개 테이블에 적용했다. 과거 문서가 링크한 기본 schema SQL은 이 checkout에 없다. 후속 SQL은 빈 DB bootstrap용이 아니며 기존 필드/제약/RLS 계약을 전제로 한다. 실제 REST 합성 저장·pending/완료 조회·upsert 중복 방지·삭제, authenticated role의 owner 조회·다른 UUID 차단·INSERT 불허를 확인했다. 합성 행은 cleanup/rollback했다. 실제 Gmail OAuth 후 브라우저에서20건 수집→본인 scans 목록/상세/그래프 표시와 reload 후 중복 방지도 확인했다. RLS를 SQL claims로 확인한 검증을 두 실제 Google 계정 로그인 검증으로 표현하지 않는다.
