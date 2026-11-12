# 비가상화 감사 수정 (2026-10-31)

사용자가 감사 항목 1–13 전부 수정을 승인했다. 기존 feat/jihun의 미커밋 작업을 보존한다.
커밋·푸시·브랜치 전환, 운영 데이터 행 변경, 실제 메일/OAuth/모델 실행은 하지 않는다.
리뷰에서 과거 legacy 행이 판정을 가리는 문제가 확인되어 scans 조회뷰 정렬만 수정한다.
뷰 컬럼·소유권 조건·security_invoker·기존 RLS/권한은 유지하며 결과 행은 삭제하지 않는다.
각 항목은 회귀 테스트 RED→최소 수정→GREEN, 최종 전체 suite/build로 확인한다.
추가 제안인 CI·수동 앙상블 UI·대시보드 자동 갱신은 범위 밖이다.

- [x] 1 연동 해제의 삭제 안내·확인 (UI)
- [x] 2 인증 없는 세션 GET의 기존 상태 보존 (app)
- [x] 3 늦은 Google refresh/401의 새 grant 보호 (Google/Store)
- [x] 4 HTML 본문 중복 전달·fallback 제거 (detector bridge)
- [x] 5 legacy sync의 파이프라인 판정 재사용·중복 방지 (app/Supabase)
- [x] 6 취소된 작업의 최종 동적 상태 저장 (pipeline)
- [x] 7 로그아웃 시 사용자·세션 평가 작업 중지 (app)
- [x] 8 초기 세션 실패 후 자동 polling 복구 (UI)
- [x] 9 누적 canary 관측과 최종 snapshot 상태 분리 (pipeline/summary)
- [x] 10 MIME part 구분과 총 길이 예산 보존 (mail)
- [x] 11 잘못된 ML 확률을 오류로 처리 (detector/legacy adapter)
- [x] 12 paired 데이터 split·출처 보존 (data)
- [x] 13 관리자 데모 입력 검증·draft (UI)

## 작업 분담

UI, 탐지 입력/학습, Google refresh는 서로 다른 파일에서 병렬 수정한다.
주 작업자는 app의 세션·logout·sync 및 pipeline 결과 요약을 수정한다.
공유 인터페이스: detector serializer 변경은 모델 식별자에 자동 반영된다.
취소 작업은 top-level cancelled를 유지하면서 최종 동적 요약을 갱신한다.
기존 /api/sync는 동적 작업을 만들지 않으며 이미 있는 pipeline 결과를 우선한다.
파이프라인 없는 legacy 결과는 결정적 id로 중복 upsert를 방지한다.

## 검증 기록

- 1–13 구현 및 회귀 확인 완료. 기존 미커밋 소스와 사용자 uv.lock을 보존했고 커밋·푸시·브랜치 전환 없음.
- UI: 삭제 범위 안내/확인, 세션 in-flight 공유와 실패/만료/재시작 후 재시도, 관리자 draft 검증. 새 회귀 RED→GREEN 및 59 tests/build 통과.
- Google: transactional expected-bundle 비교 저장/무효화, OAuth 완료와 refresh/두 번째401 race 15회귀 RED→GREEN.
- 탐지 입력: plain/HTML 중복 fallback 제거, MIME 구분자 포함300000자 예산, NaN/무한대/범위 밖 확률 거절, paired split·출처 보존. 신규37 회귀와 detector56 tests 통과.
- 부모 작업: 세션 GET 상태 보존, user/session 평가 logout, 기존 pipeline 판정 sync 재사용, legacy 결정적 UUID, 취소 완료/조회 상태 유지, 누적 관측/최종 snapshot 분리. 초기8실패→8통과, 조회 projection 추가1실패→통과.
- MIME 변경으로 이전2검사의 계약 예상값을 갱신했다. 길이 제한/구분자 검사를 함께 유지하고 합성 collect-api-sample.json을 공식 생성 스크립트로 갱신했다.
- 독립 리뷰2건(기존 legacy history 우선순위, 취소 projection)을 수정했다. Supabase 실제 Postgres에서 이전정렬은 legacy-safe를 선택함을 확인했고, 새 정렬은 history/pending/terminal/legacy-only4개 합성 CTE를 통과했다. 실제 데이터 행을 만들거나 수정하지 않았다.
- 공유 프로젝트 plungwdrjfbppblvhkze에 prefer_pipeline_scan_results_over_legacy 조회뷰 migration을 적용했다. 데이터 행 삭제/갱신 없음. security_invoker=true, 기존 RLS 정책/GRANT 해시 동일, anon SELECT=false/authenticated SELECT=true/browser INSERT=false 확인.
- 최종 전체 backend516 passed/12 opt-in skipped, detector56 passed, frontend59 passed/build. Ruff와 git diff --check 통과. 기존 TestClient deprecation과 약699kB Vite chunk 경고는 남는다.
- 실제 Gmail/OAuth/학습 모델·VM 실행 및 성능 평가, live REST 데이터 쓰기 테스트는 하지 않았다. 이미 실행 중인8765 백엔드는 재시작하지 않았다. 새 소스 적용은 정상 종료 후 재시작해야 한다.
- 추가 제안3개(CI, 수동 앙상블 UI, Supabase 대시보드 자동 갱신)는 사용자 승인 범위1–13 밖이라 구현하지 않았다.
