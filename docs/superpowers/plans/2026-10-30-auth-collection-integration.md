# Supabase 사용자 귀속·자동 분석 저장 구현 계획

**Goal:** 사용자 요청 1–5와 스크린샷 #38을 모두 충족한다. 로그인 후 Gmail 연결·재수집을 복원하고, 검증된 Supabase user_id의 결과만 저장·조회하며, 실제 VM 경계를 재검증한다.

**Architecture:** 기존 React/Vite·FastAPI·SQLite·Supabase·Ollama·Lima를 재사용한다. Supabase access token은 서버의 Auth `/user` 요청으로 검증한다. OAuth 시작 세션에 검증된 UUID를 바인딩하고 callback의 state/PKCE와 함께 유지한다. 기존 unowned Gmail 연결을 이메일 일치로 양도하지 않는다. 브라우저 DB 권한은 anon+본인 SELECT만 유지하고 쓰기는 서버 전용 키로 수행한다.

**Tech Stack:** 기존 httpx, sqlite3, Fernet, node:test, pytest. 새 프레임워크/배포/추론 서비스 없음.

**Spec:** 사용자 goal 1–5, 첨부 image-1~3, arch/SUPABASE_TABLE.md. 기존 작업 브랜치에서 진행하며 커밋·푸시는 사용자에게 남긴다.

## 작업 및 증거

- [x] Store에 account/session owner 매핑 추가. 구버전 DB와 토큰 보존; OAuth race와 사용자 A/B·만료/잘못된 토큰 회귀검사.
- [x] 서버 Auth 검증·세션 복원·OAuth 연결을 구현. 프론트 API가 access token을 보내고 /api/session 초기 동시 요청을 합친다. 앱 로그인과 Gmail 연결 상태를 명확하게 표시한다.
- [x] 고정 SUPABASE_USER_ID 데모 쓰기를 검증된 owner 쓰기로 교체. 메타데이터와 정적/동적 분석 결과를 durable outbox로 저장·실패 재시도하며 원문/토큰을 DB에 보내지 않는다.
- [x] scans view의 최초 pending 상태와 최신 완료 결과를 함께 지원. RLS와 SELECT 전용 권한 유지. 합성 데이터로 실제 저장·조회·A/B 차단 확인.
- [x] #38의 3단계·검사 전·불완전 안내·텍스트 evidence·로그인→Gmail·anon 전용을 브라우저/테스트로 검증한다.
- [x] 별도 작업자가 VM/컨테이너 경계를 실제 확인하고 결함만 최소 수정한다. 실제 모델 입력·명령·출력·cleanup을 합성 실험으로 확인한다.
- [x] 전체 테스트/빌드/보안 점검 및 실제 흐름 검증. 병목·분석 실패 원인을 근거로 개선하고 독립 리뷰 후 결과를 설명한다.

## Review focus

- 사용자 전환/로그아웃/늦은 OAuth callback이 다른 사용자의 Gmail·실험을 복원하지 않아야 한다.
- Auth 실패와 클라우드 저장 실패를 정상·빈 결과로 숨기지 않는다. 출력/키/원문을 오류에 노출하지 않는다.
- 재수집·동적 완료·재시작·재시도가 결과를 중복 생성하거나 다른 연결을 덮어쓰지 않아야 한다.
- GET 조회는 동적 분석 또는 원격 쓰기를 등록하지 않는다. 서버 쓰기만 허용한다.
- VM이 준비되지 않으면 호스트 Docker로 fallback하지 않으며 실험 cleanup 실패가 새 실행을 막아야 한다.

## 현재 확인

2026-10-30: clean feat/jihun @624bfe2. Supabase URL/anon 키는 프론트에만 존재하며 서버 전용 쓰기 키는 없어 사용자에게 .env 직접 입력을 요청했다. 스크린샷은 Supabase 로그인만 있고 Gmail OAuth가 없는 상태다. 실제 DB schema·security advisors를 읽기 전용으로 확인했다. 이전 goal turn은 병합 코드 변경과 회귀테스트를 수행한 progress다.

## 진행 기록

- 사용자 secret key 입력 완료. URL/anon은 동일 프로젝트 값으로 서버 .env에 추가했으며 값은 출력하지 않았다. 새 sb_secret 키는 apikey 헤더에만 전송한다. 서버 키 실제 읽기 HTTP 200.
- 원격 migration owner_scoped_pipeline_results 적용. analysis_results.analysis JSONB, REST upsert용 user_id/email unique index, pending도 표시하는 security_invoker scans. RLS/브라우저 쓰기 권한은 확장하지 않았다.
- 실제 REST 합성 pending → static/dynamic 결과 → 동일 id upsert → 조회 → 이번 합성 연결 제거 테스트 1 passed. 실제 SQL authenticated role에서 owner pending 조회 1, 다른 UUID 조회 0, INSERT 권한 없음 검증. SQL 합성 행은 rollback했다.
- Auth 5개 red→green. 초기 session 동시 요청 3개→1개 및 access-token 전송 red→green. cached 분석 재수집 시 outbox 누락 red→green; 분석 상태/outbox를 같은 SQLite transaction으로 기록한다.
- 기본 백엔드 259 passed / 11 opt-in skipped. 프론트 21 passed와 build 통과(681KiB chunk 경고는 후속 점검 대상). Ruff 사소한 줄 길이 2개는 수정했다. 후속 변경 후 최종 suite 재실행 필요.
- VM 담당 Feynman 종료. setup 검증의 reverse socket·copyToHost 허용, daemon 연결 실패를 cleanup 성공으로 오인하는 3개 결함 수정(red→green). 단위41 passed, 실제VM6 passed /1 optional skipped. 프로덕션 DB·세션은 건드리지 않음.
- 실제 qwen3:4b-instruct + 팀 ML/Transformer + prodog-vm에서 합성 정상/인젝션 자동 경로 1 passed (37.45s). 보고서: /private/var/folders/gt/9080n9cj7dz41w6syjv8s5dr0000gn/T/pytest-of-jihun/pytest-108/test_actual_team_ensemble_auto0/report.json. 성능/공격 성공률 평가는 아님.
- 이전 서버 PID35186은 cmd/cwd를 확인하고 SIGINT로 정상 종료했다. 실제 기존 jobs는 모두 terminal(160개), accounts2개였다. 새 백엔드는 tool session77517 / PID47986, 8765 --no-access-log로 실행 중. Vite5173는 유지.
- Chrome 브라우저3의 기존 사용자 tab1707840606, cua binding dashboardTab / prodogBrowser. 실제 Supabase 로그인 jihun6844@gmail.com 유지, 업데이트 후 설정에서 'Supabase 저장 연결됨 · 저장 대기0건' 확인. Gmail은 owner 미귀속 legacy 연결을 임의 복원하지 않아 OAuth가 1회 필요하다.
- 자동 승인 검토가 accounts.google.com 접근을 거절: 이전 OAuth 설정을 팀원에게 맡긴 맥락 때문에 권한 부족. 우회하지 않았으며 사용자에게 직접 연결 또는 기존 계정/gmail.readonly 재연결 명시 허용을 비동기로 요청했다. 답변 대기 중. Tab을 markHandoff했다. 다음 goal turn에서도 이 거절을 우회하지 말 것.
- 아직 남은 것: 실제 Gmail OAuth 이후 재수집/새로고침·cloud 결과 브라우저 검증, 사용자/프론트/아키텍처 문서 갱신, 전체 효율/보안 점검·독립 리뷰·최종 검증, 기술 용어를 유지한 구조 설명. 목표 완료 아님.

### 후속 진행

- Auth 담당 Einstein이 header/provider id/partial config 검증과 JWT exp 이후 cache 재사용 차단을 추가했다. 34 initial failures 및 clock/expiry 경계 red→green. 담당자 검증146 passed, Ruff 통과. 현재 cache는 검증된 결과 최대10초이며 exp는 cache를 줄이는 용도로만 사용한다. 토큰 철회는 최대10초 반영 지연 가능.
- Supabase의 200/201 응답에서 연결·메일·분석 결과 representation 누락을 성공으로 보지 않도록 수정했다. 3개 단계별 missing acknowledgment red→green. 실제 REST 합성 테스트 재실행1 passed (2.00s), 합성 행 cleanup 완료.
- Ollama outage가 수집 전체503을 만들던 회귀를 재현한 뒤 API 경고와 함께 수집/정적 분석을 계속하도록 수정했다. 동적 실행 전 모델 revision 검사/실패 처리는 유지된다.
- 로컬 수집의 분석 polling에 본인 cloud outbox 상태를 포함해 분석 종료 뒤 저장 retry가 남아도 오류/대기를 표시한다. GET은 새 작업을 등록하지 않는다.
- owner-authenticated API → 합성 수집 → 정적/동적(mock provider) → outbox → owner별 cloud upsert 전체 회귀 1 passed. 실제 모델/VM 경로는 앞선 별도 실제 테스트로 검증했다.
- 수동 /api/sync도 disconnect와 같은 계정별 lock을 사용하고 lock 대기 뒤 세션을 다시 검사한다. 지연된 sync가 로그아웃 후 cloud에 쓰는 회귀 red→green.
- README/자동 분석 안내/arch 데이터 계약과 architecture.md를 현재 구현으로 갱신했다. Gmail 본문은 로컬 암호화 대기/실행 로그에 남을 수 있으며 Supabase에는 보내지 않는 경계를 명시했다.
- 전체 backend323 passed /11 skipped, detector15 passed, frontend21 passed/build 통과(682KiB chunk 경고). 수동 sync lock 수정 후 backend 최종 재실행 중.
- DB advisors: 새 RLS/security 경고 없음. 기존 leaked-password protection off WARN; 기존 composite FK covering index INFO2개와 unused index INFO2개가 있다. 관련 없는 Auth 설정/인덱스 삭제는 하지 않았다.
- 독립 reviewer Herschel (01a18a75-0aeb-7d30-8968-972b14a0700f) 작업 중. 수동 sync lock gap은 main에서 수정 중임을 전달했다.
- 현재 실제 account_owners0, active analysis jobs0. 실제 Gmail OAuth는 여전히 진행되지 않음. Google origin 재시도·우회 금지. 새 확인 탭1707840613 / verificationTab은 localhost settings이고 markHandoff했다. 기존 dashboardTab도 markHandoff 유지. 스크린샷 /Users/jihun/.codex/visualizations/2026/10/05/01a10beb-cd49-7c93-b386-be65333d7ce4/prodog-settings.png 저장. Google 페이지는 접근하지 않았다.

### 독립 리뷰 수정 중

- Herschel의 P2 다섯 개를 수용: (1) outage 중 같은 메일의 이전 job 미전송, (2) 수집 실패 placeholder 미저장, (3) cached terminal 재수집 후 cloud polling 미시작, (4) connector 장애가 Supabase 로그아웃을 막음, (5) docker rm timeout이 cleanup_failed barrier를 우회.
- (1) CloudSync에 cloud_deliveries(job_id,data_json) ack 테이블을 추가했다. 메일별 최신 job 하나 대신 변경/미전송 job 모두를 전달하고 정확한 snapshot을 각각 ack한다. 재시작 후 과거 pending→terminal 및 새 job 모두 도착하는 회귀 red→green. 이미 ack한 분석은 cached 재수집에서 중복 전송하지 않는다. 기존 revision은 저장 중 새 변경을 계속 보호한다.
- (2) failed 수집 메타데이터를 SQLite에 저장하고 POST 수집 때 cloud.mark한다. GET은 새 cloud 등록을 하지 않는다. 회귀 red→green. 관련 backend40 passed, Ruff 통과.
- (3)(4) Newton (01a18a7e-4c04-7622-a213-8665ba3d5ab0)이 프론트만 수정 중.
- (5) Franklin (01a18a7e-4d3e-7553-932b-983bbcc326c3)이 experiments.py/cleanup tests만 수정 중. 실제 VM 작업은 하지 않게 지시.
- Herschel에는 main 수정 (1)(2), 수동 sync lock만 한정 재리뷰 요청. 다른 두 작업이 끝나면 해당 수정도 전달 예정.
- 기본 schema SQL 파일이 이 checkout에 실제로 없으므로 README의 잘못된 새 DB 설치 절차를 제거했다. 후속 SQL은 기존 3개 테이블 대상으로 적용된 것이며 빈 DB bootstrap이 아니라는 한계를 기록했다.
- 전체 backend324 passed /11 skipped/Ruff 통과 후 리뷰수정이 시작됐다. 리뷰수정 통합 후 최종 suite/실제 VM cleanup 재검증 필요.
- 서버 재시작 완료: 기존 session77517/PID47986 종료. 현재 session59654/PID51144가 8765 --no-access-log로 실행 중. 이 프로세스는 리뷰 후 cloud_deliveries/failed-placeholder/프론트/cleanup 수정 전 코드이므로 최종 수정 후 다시 정상 재시작해야 한다.

### 리뷰 수정 통합 결과

- Newton/Franklin 작업 완료·종료. 프론트29 passed/build, cleanup 관련101 passed. UI cached 재수집 cloud status 갱신과 상태 조회 실패 재시도, connector 장애여도 Supabase local signout 및 로그인 화면 경고 유지 구현. 제거 timeout/transport/예상 밖 예외를 cleanup_failed로 정규화하고 restart/cancel/shutdown 회귀를 확인했다.
- 통합 backend377 passed /11 skipped/Ruff 통과. 실제 VM 경계·자원·명령 timeout·API 취소/제거6 passed /1 static opt-in skipped (15.19s). 실제 Supabase 합성 failed 메타데이터도 scans에서 failed/NULL로 보임을 확인, REST test1 passed (1.95s), 해당 합성 연결 삭제.
- Herschel 한정 재리뷰에서 1/3/4/5와 수동 sync lock 통과. 남은2의 failed metadata/outbox atomicity를 추가 지적했다. Store.save_metadata에 기존 conn 전달을 지원하고 실패 수집 분기에서 BEGIN IMMEDIATE 안에 metadata+cloud.mark를 같이 기록하도록 수정. mark 직전 interruption 주입 시 metadata만 살아남던 회귀 red→green. 관련54 passed. 마지막 한정 재리뷰와 전체 backend 재실행 중.
- collect-api-contract의 '본문은 메모리로만' 설명을 메타데이터 테이블/암호화 분석 payload·로그 구분으로 수정했다. 과거 동적 로그의 추가 삭제를 하지 않는 경계도 명시했다.

### 현재 인계 상태 — 목표는 아직 active

- Herschel 최종 한정 재리뷰: P1/P2 남은 항목 없음. 다섯 발견과 수동 sync 이슈 모두 닫힘. atomic success, mark 전/후 rollback, 기존 메타데이터와 기본 transaction 보존 독립 검사4개 통과. Reviewer 종료. 활성 subagent 없음.
- 최종 backend378 passed /11 optional skipped (5.70s), detector15 passed, frontend29 passed/build, Ruff/diff 공백 검사 통과. 실제 VM6 passed /1 static opt-in skipped, 실제 Qwen+팀 모델+VM1 passed, 실제 Supabase REST1 passed와 RLS owner/다른UUID/INSERT 불허 확인. 정상 합성 메일 static danger 오탐은 미해결 평가 한계다.
- 최종 서버 적용: session59654/PID51144 정상 종료 후 session21924/PID53961이 8765 --no-access-log 실행 중. Cloud deliveries 테이블 포함 startup 완료. Vite5173 유지. DB키·원문 출력·커밋·푸시 없음.
- 최종 실제 Chrome settings 확인: 앱 로그인 유지, Supabase 저장 연결됨/대기0건, Gmail 읽기 권한 필요 및 Gmail 연결하기 버튼. proof PNG 갱신. verificationTab(1707840613)과 dashboardTab은 markHandoff.
- 유일한 다음 사용자 의존 단계: 직접 Gmail 연결 후 완료 통지, 또는 현재 계정/기존 gmail.readonly로 agent 재연결 명시 허용. 자동 승인 검토의 accounts.google.com 거절을 우회하지 않았다. pending async 질문에 아직 답변 없음. 이전 flow는 10분 만료일 수 있어 기존 Google 화면 제출 대신 settings에서 새 Gmail 연결하기로 시작한다.
- 다음: 권한/연결 답변 후 실제 refresh→메일 수집→본인 Supabase scans→상세/그래프→재refresh 중복 방지 검증. 사용자 요구4의 1–3 완료 후 전체 개선 최종 audit와 요구5 구조 설명을 마무리. architecture.md는 현재 기술 구조와 검증 한계를 이미 기록했다. 실제 Gmail 경로 전에는 목표 complete로 표시하지 말 것. 이번은 이 OAuth 대기에 대한 첫 실제 goal-turn impasse이며, 같은 차단3연속 goal turn이고 다른 유의미한 작업도 없을 때만 blocked로 바꾼다.

### 자동 이어가기 2 — 사용자 OAuth 답변 대기

- 이전 turn은 구현·검사·독립 리뷰·최종 서버 적용을 완료한 progress였다.
- 현재 worktree와 read-only SQLite를 재확인: account_owners0 / active analysis jobs0 / cloud_outbox0. 직접 재연결이나 명시 허용 답변이 아직 없으며 Google origin 거절은 계속 유효하다. 재시도/우회/테스트 반복/서비스 재시작은 하지 않았다.
- 현재 소스의 SupabaseAuth, MailPipeline, StaticDetector, Experiments, Sandbox, CloudSync와 프론트 readScans 호출 위치를 확인하고 사용자 요구5의 기술 구조·인증·분석·저장·VM 경계를 채팅에서 설명한다. 이후 요구1/2 실제 Gmail 전체 경로 검증과 요구4 최종 audit는 OAuth에 의존한다.
- 같은 OAuth 차단을 관찰한 두 번째 연속 goal turn이다. 목표 active 유지. 다음 turn에서도 같은 차단이고 다른 의미 있는 작업이 없다면 blocked threshold를 충족하므로 update_goal(blocked)로 불필요한 자동 반복을 종료한다. 사용자 재개 시 새 blocked audit로 시작한다.

### 자동 이어가기 3 — blocked audit 충족

- 이전 turn은 현재 코드 확인과 요구5의 프로젝트 구조·인증·분석·저장·VM 설명을 전달한 progress였다.
- 읽기 전용 재검증 결과 owner 연결0 / 실행 중 분석0 / cloud 대기0으로 동일하다. 재연결 완료 통지 또는 Google 재연결 명시 허용 답변이 없다.
- 동일 OAuth 의존 차단이 세 연속 goal turn에 반복됐다. 원래 범위를 유지하며 요구1/2 실제 Gmail 검증 및 그 이후 요구4 최종 audit는 미완료다. 요구3 실제 VM 검증과 요구5 설명은 수행했다. 현재 사용자 입력 없이 진행 가능한 필수 독립 작업은 없다.
- 목표를 blocked로 전환한다. 구현/검증 결과와 미커밋 worktree는 보존한다. 사용자 연결 완료/명시 허용 후 실제 refresh→수집→본인 scans 조회/상세/그래프→중복 방지부터 재개하며, Google 접근 거절을 우회하지 않는다. 재개 시 새 blocked audit로 시작한다.

### 사용자 허용 후 실제 연결 검증 — 2026-10-30 KST 오후

- 사용자 명시 허용: '기존 계정·Gmail 읽기 권한으로 재연결 진행해'. 목표 active 재개. 과거 거절을 우회한 것이 아니라 새 사용자 허용으로 Google OAuth를 정상 진행했다. 이전 blocked audit는 초기화한다.
- Chrome3 연결은 사라져 inventory 확인 후 Chrome4/지훈 프로필의 기존 ProDog tab1707840689를 선택했다. 바인딩 prodogBrowser, verificationTab 갱신. localhost settings에서 새 OAuth를 시작하고 기존 jihun6844@gmail.com, 기존 gmail.readonly 한 서비스만 확인해 재연결. callback connected=1 정상 복귀.
- 실제 SQLite account_owners1, 기존 자동 설정 enabled1/qwen3:4b-instruct 유지. 설정에서 정적 ensemble·prodog-vm ready 확인. 새로운 사용자/키/권한 범위를 추가하지 않았다.
- 실제 로컬 수집20행, Supabase scans20행/graph20행 범위. 원격 연결1, 메일20, 분석42(동일 메일의 과거 job도 전달), owner1. mail_owner_mismatches0, analysis_owner_mismatches0. 메일 제목/본문/토큰은 도구 출력에 추가하지 않고 집계·상태만 확인했다.
- 실제 페이지 reload 후 수집20행 복원, 전체 analysis_jobs168개와 ID digest 08ebebdcac1f3ba051d79148e954a1f33acf5ef336e90940696eba0c394c13a1 유지. 중복 신규 job 없음. functions store gmailRefreshBaseline/After에 집계 보관.
- 이번 새 job8개 최종상태: 동적 completed4, step_limit2, dynamic_enqueue_failed1, static safe/dynamic skipped1. 실행 context prodog-vm 확인, 실제 컨테이너/로컬 Qwen 가동 관측. completed 중 summary 파일 없는 결과도 있어 작업 성공으로 과장하지 않았다. cloud pending0.
- 실제 Supabase 상세에서 정적 모델별 점수/임계값/위험/동적 상태/근거 미제공 설명 확인. DB 정수94와 JSON 원래 확률94.3564가 섞여 meter와 verdict가 어긋나는 표시 결함 발견. scans mapper가 valid completed pipeline JSON의 원래 확률을 사용하도록 수정(red2→green31 frontend tests/build). 실제 상세94.36과 danger threshold94.2692/marker94.3564 일치 확인. 기존 JSON 없는 정수 행은 호환 유지.
- 현재 사용자가 src/gmail/uv.lock의 metadata revision/transformer extras를 변경한 것으로 보이는 추가 diff가 있다. 이번 재개에서는 이 변경을 덮어쓰지 않았다.
- 전체 재검사 baseline: backend378 passed/11skip, detector15passed/Ruff, frontend31passed/build. 기존 src/gmail DOM tests용 npm ci --ignore-scripts 설치 후10passed. npm audit에서 dev transitive source-map-js/undici HIGH2 확인, 공식 보안 공지 검토 후 npm audit fix --ignore-scripts로2패키지 호환 패치, audit0. 프론트 dashboard audit0. 패치 후 DOM10재실행 결과 수집 필요(session92254).

### 요구4 분석 엔진 audit 및 수정 진행

- Ponytail audit 기준으로 로컬 단일 사용자/20메일 수집 부하를 가정. 인증/저장/파싱/격리/프론트/의존성 경로 점검. 읽기 전용 detector audit를 Planck(01a18bf2-4ea3-73d0-ab12-f3fd95523a0a)에 위임했다.
- Planck가 synthetic으로 P2 세 가지 재현: collector는300k허용하지만 converter는200k 뒤를 조용히 자름; 1000char/500overlap→256token truncation 사이에 토큰 입력 누락 구간; detector가 원래 값으로 verdict 후 score/threshold를6자리 반올림해 경계에서 static_invalid_output.
- Planck에 detector email_text.py/model.py/transformer.py/tests만 직접 수정하도록 재지시. full collector 입력 지원과 초과 시 명시 오류, tokenizer overflow+token overlap+boundedforwardbatch, fullprecision출력. 가중치/임계값/보고서/실제 메일/서비스는 변경하지 않게 했다. 활성 agent1개.
- main은 StaticDetector fingerprint에 worker/serializer/model/transformer 정책 소스를 포함해 코드 정책 변경 시 결과 캐시를 분리하도록 수정. stat 기반 hash cache 유지. text_plain이 있으면 converter가 안 쓰는 analysis_text 중복을 IPC에서 제거해 최대300k Unicode본문이 중복 때문에2MiB 제한을 넘는 회귀 수정. red2→green58passed/1optional skipped/Ruff.
- Quadrat 원본 validation JSONL은 로컬에 없다. overflow 변경 후 기존2026-10-26 metrics를 새 runtime 성능 수치로 표시하지 말 것. 합성 coverage regression과 실제 trusted 모델 latency를 확인하고, 새 정확도/오탐률은 독립 재평가 필요라고 명시해야 한다.
- 아직 해야 할 것: Planck 변경 통합/리뷰, 합성 실제 모델 긴 입력 latency와 파이프, 정책변경 후 최종 source tests/서버 적용/새runtime연결검증, 실제 증거 screenshot 갱신, docs의 OAuth대기 설명 갱신, 요구1–5 completion audit. 커밋·푸시는 계속 하지 않는다.

### 최종 통합·실제 경로 확인

- Planck 수정 통합 완료. detector36 passed/Ruff 통과. Archimedes가 coverage·fingerprint·점수 정밀도·동적 오류 전달 변경을 독립 리뷰했으며 남은 actionable P1/P2 없음. 두 agent 모두 종료했다.
- 동적 입력의 32,000자 한도를 MAX_MAIL_CHARS로 공유하고 초과 시 dynamic_input_too_large를 반환한다. ConnectorError의 안전한 code/message를 보존해 cleanup_blocked 등을 일반 enqueue 실패로 숨기지 않는다. timeout/step_limit/cancelling 한글 상태도 표시한다.
- 최종 backend382 passed/11 optional skipped, detector36 passed, dashboard32 passed/build, 기존 Gmail DOM10 passed. Ruff와 git diff --check 통과. npm audit은 Gmail·dashboard 모두0. Gmail lockfile의 source-map-js/undici 두 dev transitive 취약점만 호환 패치했다. Vite 단일 bundle 약683kB 경고는 남는다.
- 실제 팀 모델 최대 입력 합성 benchmark: dense gap1,063자4.285초; dense300,000자6.727초; Unicode300,000자3.022초. 두 모델 모두 완료. 중복 Unicode IPC2,399,741→1,199,909bytes. 가중치/임계값/과거 metrics는 변경하지 않았다. 새 정책 정확도/오탐률 재평가를 대신하는 결과가 아니다.
- 실제 Qwen Instruct+팀 ML/Transformer+prodog-vm 자동 경로를 최종 코드로 재실행해1 passed(34.98초). 합성 보고서는 pytest-146/test_actual_team_ensemble_auto0/report.json. 실제 VM6 passed/1optional skip 및 Supabase REST/RLS 검증 증거는 앞선 기록과 같다.
- 서버 최종 적용: 기존 PID53961 정상 종료, PID61725/tool session34615에서8765 --no-access-log 실행 중. 재시작 중 중복 실행 한 번은 port bind 실패로 종료됐고 실제 수신 서버는 최종 프로세스 하나다. Vite5173·기존 계정·키·설정은 유지했다.
- 최종 모델 식별자73a61f10661f3b907a8faa1baf6986a7b76fe3abd9ab6fd18d121e4dc552a44d를 실제 설정 화면과 worker에서 확인. 새 정책으로 실제20건 정적 완료·cloud pending0 확인. 그중6건은 동적 입력 한도 오류이며 정적 결과를 유지한다. 동적 실행은 기존 자동 설정에 따라 VM에서 순차 진행한다.
- 새로고침 후 조회에 추가된1건을 포함해 새 정책 작업21개는 서로 다른 메일 ID21개였다. 같은 메시지의 중복 job0. 기존168 jobs에 새 정책20개를 더한188개에서189개가 된 원인은 추가 메일 ID이며 기존 메일 재등록이 아니다.
- 재시작의 기존 완료 run 복구가 소유권이 확인된 과거 메타데이터/분석도 outbox로 전달한다. 실제 cloud 집계는 연결1/메일48/결과126/scans48(확인 시점)이며 두 owner 불일치0. 최신 수집 페이지20건과 과거 기록을 포함한 전체 cloud 집계는 범위가 다르다. 본문·명령·토큰은 cloud로 전송하지 않았다.
- architecture.md·dashboard README·Supabase 계약의 OAuth 대기 설명을 실제 연결/수집 완료 상태로 갱신했다. 사용자 uv.lock 추가 변경을 보존했고 커밋·푸시·브랜치 변경은 하지 않았다.
- 요구1–5 구현/검증 완료. 현재 동적 대기열은 앱의 정상 백그라운드 작업으로 계속 처리한다. 새 정책의 독립 정확도 평가·재보정, Windows VM, 실제 악성 바이너리/VM 탈출은 검증하지 않았다. 자세한 현재 구조와 범위는 arch/architecture.md에 기록했다.
- 최종 브라우저 scans49행과 같은 범위의 집계/그래프를 확인했다. DB 최종 연결1/메일49/scans49, 메일·분석 owner 불일치 모두0. 완료 proof는 로컬 visualization 디렉터리의 prodog-settings-connected.png와 메일 제목을 제외한 prodog-dashboard-summary.png다. 대시보드 탭을 deliverable로 남겼다.
- 실제 frontend dist에 서버 Secret key가 없고 .env/.env.local/로컬 모델이 Git 추적 대상이 아닌 것도 값 출력 없이 검증했다. 최종 코드 변경 후에는 문서만 갱신했으며 마지막 git diff --check도 통과했다.
