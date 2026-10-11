# Gmail 메일 분석 DB 구조

## 한눈에 보기

`auth.users`(Supabase가 관리하는 로그인 사용자) → `gmail_connections`(연결한 Gmail 계정) → `mail_messages`(수집한 메일) → `analysis_results`(AI 분석 기록)

앱에서 직접 만든 테이블은 세 개다. 사용자 한 명이 Gmail을 여러 개 연결할 수 있고, Gmail 연결마다 메일이 여러 건 있으며, 메일 한 건을 여러 번 분석할 수 있다.

| 테이블 | 용도 | 주요 내용 |
|---|---|---|
| `gmail_connections` | 사용자가 연결한 Gmail 계정 | 앱 사용자, Google 계정 ID, 표시용 이메일, 연결 상태 |
| `mail_messages` | 수집한 메일과 수집 품질 | Gmail 메일 ID, 제목·발신자·수신 시각, 해시, 첨부·잘림·파싱·수집 상태 |
| `analysis_results` | 메일별 분석 실행 기록 | 분석 상태, 위험도, 판정 이유, 모델, 실패 이유 |

## 1. `gmail_connections` — 누가 어떤 Gmail을 연결했는가

| 컬럼 | 뜻 | 넣는 값 |
|---|---|---|
| `id` | 이 연결의 고유 번호 | DB가 자동 생성하는 UUID |
| `user_id` | 앱 사용자 | 로그인한 Supabase Auth의 `auth.users.id` |
| `google_id` | Google 계정 고유 ID | 검증한 Google ID 토큰의 `sub`. 이메일 주소로 대신하지 않음 |
| `email` | 화면에 보여줄 Gmail 주소 | 예: `sample.user@example.com` |
| `status` | 연결 상태 | `active`(사용 가능), `reconnect_required`(재연결 필요) |
| `connected_at` | 처음 연결한 시각 | DB가 자동 기록 |

Gmail 인증이 끝난 뒤 서버가 한 행을 만든다. 동일한 사용자가 같은 Google 계정을 중복 등록할 수 없게 했다. **Google 토큰은 이 테이블에 저장하지 않는다.**

## 2. `mail_messages` — 어떤 메일을 수집했는가

| 컬럼 | 뜻 | 넣는 값 |
|---|---|---|
| `id` | DB 안에서 쓰는 메일 번호 | DB가 자동 생성하는 UUID |
| `user_id` | 메일 소유자인 앱 사용자 | 연결 행의 `user_id`와 같은 값 |
| `connection_id` | 어느 Gmail 연결에서 왔는가 | `gmail_connections.id` |
| `gmail_id` | Gmail 자체의 메일 ID | Gmail API의 `message.id` |
| `subject` | 제목 | 파싱한 제목. 없으면 비워 둠 |
| `sender` | 발신자 | 파싱한 발신자. 없으면 비워 둠 |
| `received_at` | 메일을 받은 시각 | Gmail 메일 시각. 모르면 비워 둠 |
| `content_hash` | 분석에 쓴 텍스트의 지문 | 분석 입력 텍스트의 SHA-256, 소문자 64자리. 분석 입력을 못 만들면 비워 둠 |
| `has_attachment` | 첨부파일이 있는가 | `true` 또는 `false`; 첨부 내용은 저장하지 않음 |
| `is_truncated` | 본문이 잘렸는가 | `true` 또는 `false` |
| `parse_warning` | 메일 파싱 중 생긴 문제 | 예: `HTML 부분만 읽음`; 문제가 없으면 비워 둠 |
| `fetch_status` | 수집 상태 | `complete`(완료), `incomplete`(일부만 수집), `failed`(실패) |

수집기가 메일별로 한 행을 만든다. 같은 Gmail 연결에서 같은 `gmail_id`는 중복 저장되지 않는다. `fetch_status='failed'`여도 Gmail ID를 알고 있으면 실패 기록을 남길 수 있다. plain/HTML 본문과 RAW는 이 테이블에 저장하지 않는다.

## 3. `analysis_results` — AI가 어떻게 판정했는가

| 컬럼 | 뜻 | 넣는 값 |
|---|---|---|
| `id` | 분석 실행의 고유 번호 | DB가 자동 생성하는 UUID |
| `user_id` | 분석 대상 메일의 소유자 | 메일 행의 `user_id`와 같은 값 |
| `mail_id` | 분석한 메일 | `mail_messages.id` |
| `status` | 분석 진행 상태 | `pending`(진행 중), `succeeded`(완료), `failed`(실패) |
| `risk_level` | 위험도 | 완료 시 `normal`(정상), `suspicious`(의심), `danger`(위험). 진행 중·실패 시 비워 둠 |
| `reason` | 사용자가 볼 판정 이유 | 원문 인용 없이 300자 이내의 짧은 설명. 근거가 없으면 비워 둘 수 있음 |
| `model` | 사용한 모델 | 이름과 버전을 한 값으로 기록. 예: `local-detector:v1` |
| `error_message` | 분석 실패 이유 | 실패 시 200자 이내의 안전한 오류 설명. 메일 원문이나 비밀값을 넣지 않음 |
| `created_at` | 분석 기록을 만든 시각 | DB가 자동 기록 |

분석을 시작할 때 `pending` 행을 만들고, 끝나면 같은 행을 `succeeded` 또는 `failed`로 갱신한다. 다시 분석하면 **새 행**을 만든다. 최신 기록은 `created_at`이 가장 늦은 행이다. `succeeded`에는 `risk_level`이 필요하고, `failed`에는 `error_message`가 필요하도록 DB에서 검사한다.

## 실제 적용된 접근 정책

| 정책 | 설명 |
|---|---|
| RLS | 세 테이블 모두 켬. 로그인 사용자는 `user_id = auth.uid()`인 자기 행만 조회 |
| 익명 접근 | `anon`의 테이블 조회·쓰기 권한 없음 |
| 브라우저 쓰기 | 로그인 사용자도 INSERT·UPDATE·DELETE 권한 없음. Gmail 수집·분석 서버가 기록 |
| 소유권 검사 | 메일은 `(connection_id, user_id)`, 분석은 `(mail_id, user_id)`가 함께 일치해야 저장됨 |
| 삭제 | 연결 삭제 → 해당 메일 삭제 → 해당 분석 결과 삭제. 앱 사용자 삭제 시도 같은 순서로 연쇄 삭제 |

서비스 키를 쓰는 서버는 RLS를 우회할 수 있으므로, 요청마다 로그인 사용자와 연결 소유권을 서버 코드에서 확인해야 한다. 서비스 키는 브라우저에 전달하지 않는다.

## 데이터 보관 기준과 현재 범위

- DB에는 제목·발신자 등 메타데이터, 수집 품질, 분석 결과만 저장한다. 메일 본문·RAW·첨부 내용·전체 분석 텍스트는 저장하지 않는다.
- Gmail 갱신 토큰이 필요하면 서버 전용 암호화 비밀 저장소에 보관한다. 이 저장소와 Gmail 수집 코드의 DB 연결은 아직 구현되지 않았다.
- 연결 해제 API에서 수집 중단, Google 권한 철회 시도, 토큰 삭제, 연결 행 삭제를 처리해야 한다. DB 연쇄 삭제는 적용돼 있다.
- Supabase 보안 Advisor 경고는 0건이었다. 실제 사용자 두 명으로 서로의 행이 보이지 않는지 검증하는 테스트는 아직 남아 있다.

