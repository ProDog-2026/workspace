# 수집 API 계약과 저장 정책

이슈 #2의 결과 문서다. 필드별 사용 조건과 엔드포인트는 [통합 범위](integration.md)를 따른다.

## 합성 샘플

[`collect-api-sample.json`](collect-api-sample.json)은 실제 메일이 아닌 합성 메일 1건(plain + HTML + 첨부)이다.

| 키 | 내용 |
| --- | --- |
| `input` | Gmail `users.messages.get?format=raw` 응답 형태 (`id`, `threadId`, `internalDate`, `labelIds`, `sizeEstimate`, `raw`) |
| `output` | `parse_gmail_message()` 결과. `GET /api/mails/{id}`가 돌려주고 후속 분석기에 넘기는 입력 |
| `stored` | 같은 메일에서 DB에 남는 메타데이터 (`mail_metadata()`) |

샘플은 `scripts/export_contract_sample.py`로 다시 만든다. 파서 출력이 바뀌었는데 샘플을 다시 만들지 않으면 `tests/test_contract_sample.py`가 실패한다.

## 출력 필드 구분

| 구분 | 필드 |
| --- | --- |
| 메일 ID | `id`, `thread_id` |
| 제목·발신자 | `subject`, `sender` (공격자가 조작할 수 있는 입력) |
| 본문 | `text_plain`, `text_html` |
| 분석 텍스트 | `analysis_text` (plain과 HTML 원문을 MIME 순서로 합침, 태그·주석 포함) |
| 해시 | `body_hash` (RFC 822 본문 바이트의 SHA-256) |
| 첨부 | `attachments` (메타데이터만, 내용은 분석하지 않음) |
| 잘림 | `truncated` |
| 파싱 경고 | `warnings` |
| 기타 | `received_at`, `labels`, `trust` (항상 `UNTRUSTED`) |

## 저장 정책 (결정)

- **메일 메타데이터 테이블은 본문·RAW를 저장하지 않는다.** `store.save_metadata()`가 허용 목록을 강제한다. 자동 분석에 필요한 본문은 암호화된 정적 대기 payload와 동적 실행 로그에 로컬 저장될 수 있다. 정적 payload는 처리 후 비우며, 실행 상세/JSON 다운로드에는 본문이 포함될 수 있다.
- **저장하는 것**: `stored`에 있는 메타데이터(제목, 발신자, 해시, 첨부 정보, 경고, 잘림 등)와 암호화한 Google 토큰.
- **보관 기간**: Gmail 연결을 해제할 때까지. 연결 해제(`POST /api/disconnect`)는 계정, 토큰, 메타데이터, 세션을 함께 지운다. 기간 기반 자동 삭제는 없다.
- 로그아웃은 로컬 세션을 지우고 계정의 진행 분석을 중지한다. 메타데이터와 Google grant는 남기며 Gmail 연결 해제와 구분한다.
- `body_hash`와 제목·발신자도 개인정보와 연결될 수 있으므로 같은 삭제 범위에 포함한다.
- Supabase에는 메타데이터와 정적/동적 결과 요약만 보낸다. 연결 해제는 본인의 Supabase 연결·메일·결과도 연쇄 삭제한다. 과거 동적 실행 로그 파일은 추가 삭제하지 않는다. [현재 아키텍처](../../../arch/architecture.md)의 저장/소유권 경계를 따른다.
