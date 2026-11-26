# Gmail 수집·Supabase·대시보드 데이터 계약

## #70 변경 사항

Google 모드의 로그인과 사용자별 저장 구현은 [사용자 분리 안내](../src/gmail/docs/user-isolation.md)를 따른다. 새 연결은 검증된 OIDC `sub`와 Supabase Auth `user_id`를 묶는다. 아래의 이메일 기반 로컬 프로토타입 설명과 "ID 토큰을 받지 않는다"는 설명은 이전 상태다. 신규 DB용 실제 SQL은 [supabase-gmail-schema.sql](supabase-gmail-schema.sql)이며, 기존 DB에 자동 적용하지 않는다.

이 문서는 이슈 #6의 저장 구조를 `src/gmail/docs/collect-api-contract.md` 및 `src/dashboard/schema.sql`과 맞춘다. 현재 Gmail 모듈은 로컬 SQLite와 자체 세션을 쓰는 프로토타입이다. 아래 매핑은 Supabase Auth와 수집 서버를 연결할 때 적용할 계약이며, 현재 모듈이 이미 Supabase에 쓰고 있다는 뜻이 아니다.

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
| `static_score` | 최종 정적 판정을 만든 모델 출력값의 표시용 백분율(`round(score * 100)`). 판정 기준이 아니며 실패 시 NULL |
| `llm_score`, `final_score` | 대시보드 점수 0–100. 해당 단계가 실패했으면 NULL |
| `types` | 탐지 유형 코드 목록 |
| `evidence` | `[{rule,type,snippet}]` 형태의 근거. 제한된 원문 발췌이므로 개인정보로 취급 |
| `llm_reason` | 문맥 분석 설명. 근거 문장이 없을 수 있음 |
| `missing_parts` | 수집 경고·첨부 제외를 사용자 문장으로 바꾼 목록 |
| `status_detail` | 분석 실패 이유 등 사용자에게 보여 줄 안전한 설명 |
| `model` | 사용한 모델과 버전 |
| `created_at` | 실행 생성 시각 |

메일 한 건을 여러 번 분석하면 실행별로 새 행을 만든다. `scans` 뷰는 메일별 최신 **완료 또는 실패** 결과 하나를 보여 준다. 진행 중인 행은 기존 완료 결과를 덮어 가리지 않는다.

## `fetch_status` 계산

다음 순서대로 판정한다. `attachment_content_not_analyzed`를 포함한 경고는 전체 메일을 분석하지 못했다는 뜻이므로 `incomplete`에 포함한다.

1. `failed`: Gmail 요청 자체가 실패했거나, 파서 출력의 `body_hash`가 NULL이라 RAW 본문을 받아들이지 못한 경우. 메일 ID를 알 수 없으면 메일 행을 만들지 않고 수집 작업 오류로 기록한다.
2. `incomplete`: `body_hash`가 있고, 본문이 잘렸거나 경고가 있거나 텍스트 추출에 성공하지 않은 첨부가 하나라도 있는 경우. 첨부의 PDF 상태는 `pdf_status=parsed`일 때만 텍스트 추출 성공으로 본다.
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

수집 실패 메일은 분석 행이 없어도 `failed`로 표시한다. 분석을 기다리는 정상 수집 메일은 아직 `scans`에 나타나지 않는다. `missing_parts`는 분석 파이프라인이 경고 코드를 사용자 문장으로 바꿔 넣는다. 값이 비었는데 수집 불완전이면 뷰가 일반 문구를 제공한다. 위험·의심 판정이 있으면 화면은 그 판정을 먼저 표시하고, 수집 불완전 안내도 함께 표시한다. 이 우선순위는 `src/dashboard/src/ui.jsx`와 같다.

`evidence.snippet`은 전체 본문이 아니어도 메일 원문 발췌이므로 개인정보다. 서버에서 건수·길이를 제한하고 화면에서는 HTML로 실행하지 않고 텍스트로만 표시한다. 연결 해제 시 분석 결과와 함께 삭제한다.

## 접근 및 삭제

세 저장 테이블은 RLS를 켜고 본인 `user_id` 행의 SELECT만 허용한다. `scans` 뷰는 `security_invoker=true`로 만들고 같은 사용자 권한으로 기초 테이블 RLS를 적용한다. 브라우저 쓰기는 허용하지 않는다. 서버의 서비스 키는 RLS를 우회하므로 매 요청에서 Supabase 사용자와 연결 소유권을 확인한다.

연결 삭제는 `gmail_connections → mail_messages → analysis_results` 연쇄 삭제로 이어진다. 별도 암호화 토큰 저장소의 토큰 삭제와 Google 권한 철회는 연결 해제 API가 처리해야 한다. 현재 로컬 Gmail 모듈의 SQLite 삭제와 Supabase 삭제는 자동으로 이어지지 않는다.

## 구현·확인 경계

지정된 Supabase 프로젝트에는 세 테이블과 `scans` 뷰를 적용했고, 구조·권한을 확인했다. 재현용 SQL은 [supabase-gmail-schema.sql](supabase-gmail-schema.sql)에 있다. 수집 모듈과 Supabase 사이의 서버 어댑터, 실제 Auth 연동, 경고 코드의 화면 문장 변환, 사용자 A/B 접근 검증은 후속 통합 작업이다. 대시보드의 현재 화면은 목업 데이터를 사용하므로 이 뷰를 읽는 프론트 연결도 후속 작업이다.
