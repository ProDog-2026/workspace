# Synthetic Evaluation Implementation Plan

**Goal:** 합성 정상/공격 메일을 기존 VM 실행기에 반복 입력하고 유효한 그룹 지표를 남기며 팀원 checkout 준비를 확인한다.

**Architecture:** 기존 Experiments와 owner/CSRF, SQLite/Fernet를 재사용. Sandbox 관측과 evaluation summary ledger를 추가하고 React 화면에 쌍 입력·그룹 집계를 연결한다.

**Tech Stack:** 기존 Python stdlib/Pydantic/FastAPI/SQLite/Fernet, React/node:test, Docker/Lima/Ollama. 새 의존성 없음.

**Spec:** ../specs/2026-10-30-synthetic-evaluation-design.md

## Constraints

- 커밋/푸시/브랜치 변경, 실제 메일 실험, 키 노출 없음. 기존 작업과 사용자 uv.lock 변경 보존.
- 실험 API는 로컬 Ollama·명시적 Docker context·기존 격리 제한 유지.

## Tasks

- [x] Sandbox의 per-run canary·전체 summary 검증어 관측: 실패 회귀 후 최소 구현, 기존 Docker/cleanup 검사 유지.
- [x] evaluation.py: bounded input·정확한 기대 분모·오류/미확정 의미·암호화 SQLite summary·그룹 API·모델 revision과 owner 경계 회귀.
- [x] experiments.py 연결: 평가 metadata는 모델 입력에서 제외, 관측 누적·시작 시간·프로토콜 기록·legacy 기록 호환, 기존 retention 유지.
- [x] Experiment React 화면: 정상/공격 쌍·모델·검증어·반복 입력, 그룹 진행/지표/JSON 다운로드, 세션·오류·취소 호환과 테스트.
- [x] 팀 preflight·fresh checkout 안내·모델 별도 배포·Mac/Windows 경계·공유 Supabase 설정 안내 및 테스트.
- [x] 통합 suite/build/Ruff, 실제 VM·Ollama 합성 실험, tracked-file 임시 checkout, 독립 리뷰 후 문서/실제 화면 증거.

## Review focus

- 노출을 관측한 뒤 삭제/timeout이 발생해도 성공 관측을 지우지 않으며 오류율도 따로 남기는가.
- 부분 출력/미수집 summary/legacy 기록을 정상 작업 성공 또는 방어 성공으로 바꾸지 않는가.
- 50개 상세 로그 정리·재시작 후에도 그룹 기대 수·분모·소유권이 유지되는가.
- 잘못된 후기 case·queue 초과·외부 provider·context 미지정이 등록 전에 차단되는가.
- 팀원의 새 checkout에서 secret/model/런타임 누락을 조용히 준비됨으로 표시하지 않는가.

## 결과와 이어가기 기록

- 사용량 중단 후 사용자 요청으로 같은 브랜치/dirty 작업에서 재개했다. canary 담당 완료분 보존, 중단된 UI/팀 준비 담당을 재개해 통합했다. 커밋·푸시·브랜치 변경 없음. 기존 사용자 uv.lock 수정과 실제 .env/계정/키를 보존했다.
- per-run128bit random canary, stdin 전달, bounded full summary 검증어 proxy, 파일 복사/명령 출력 노출 분리. 관측 양성은 삭제·timeout 후에도 남고 부분 수집의 실제 양성도 보존한다. label/oracle은 모델 입력에서 제외했다.
- 그룹은 prepare→암호화 파일 저장→단일 SQLite summary transaction→전역 대기열 시작 순서다. 두 번째 저장에 실패 주입한 red→green 회귀에서503, 실행/대기/메모리/파일/SQL 모두 부분 등록0을 확인했다. commit 없는 잔여 파일은 restart 실행 대상에서 제외한다.
- 앱 UUID 소유권은 Gmail 연결/쿠키 회전 후 유지하고 다른 사용자에게 group/log 모두404. CSRF/owner를 모델·Docker preflight await 뒤 재검사한다. 정적 gate와 독립적인 local-only 그룹 API를 추가했다.
- 상세50건 pruning 전에 터미널 요약을 먼저 저장하고, 저장 실패면 원본을 유지한다. 재시작 reconciliation, 미등록 staged record, 오류 분모·누락 member·ASR 양성 후 오류·nearest-rank P95·restart downtime 제외 회귀를 검증했다.
- 독립 리뷰 Helmholtz의 P2 세 개(부분 admission, 빈 context preflight, 요약 미저장 로그 pruning)를 red→green으로 수정했다. 최종 한정 리뷰에서 남은 발견 없음. 모든 subagent 종료.
- 최종 Gmail backend476 passed/12 optional skipped, dashboard46 passed/build, Ruff/diff 공백 검사 통과. Vite bundle 약697kB 경고와 기존 Starlette TestClient deprecation은 남는다.
- 실제 VM 경계/자원/관측/timeout·취소·cleanup6 passed/1 static optional skip. 실제 Qwen Instruct+VM의 새 group API1 passed(36.45초). 영어 정상/공격1회씩 정상proxy1/1, 목표복사1/1, 오류0/2, 평균17.172초/P95 18.521초. 일반화된 성능 수치가 아니다.
- 실제 브라우저의 한국어 기본 합성 쌍도2건 종료, 정상proxy1/1·목표복사1/1·오류0/2, 평균17.43초/P95 19.23초 표시를 확인했다. 개별 로그도 prodog-vm으로 조회됐다. 실제 Gmail 수집/원문은 이 실험에 사용하지 않았다. 합성 group은 Supabase로 보내지 않는다.
- proof: visualization 디렉터리의 prodog-synthetic-evaluation.png. group 지표와2개 실행 결과만 보여 실제 메일 본문을 포함하지 않는다. 검증 탭을 deliverable로 남겼다.
- 개인 설정/가중치/venv/node_modules가 없는 후보 파일189개 임시 checkout을 만들고 uv sync --frozen --python3.12 --dev, npm ci --ignore-scripts를 실제 재현했다. Python3.12 backend476 passed/12 skipped, frontend46 passed/build. 미캐시 패키지는 공식 registry에서 받았으며 원본 lock은 변경하지 않았다.
- read-only team preflight54 tests. 현재 Mac 전 항목 OK; 빈 checkout의 키/가중치/탐지기/context 누락은 종료1/FAIL로 정확히 알렸다. 실제 검증 모델8개 SHA256 manifest를 추가했으며 가중치는 계속 Git 제외. 새 .env는 생성 시점부터0600, 기존 내용은 보존한다.
- 현재 서버는 idle 확인 후 사용자 기존 PID65160을 정상 종료하고 PID74294/tool session10353, localhost8765 --no-access-log로 새 코드를 적용했다. Vite5173 유지. 후보 소스와 frontend dist에 실제 설정된 서버키/Google secret/Fernet key가 없음을 값 출력 없이 검사했다.
- 팀원은 사용자가 새 파일까지 커밋·푸시한 후 docs/team-setup.md를 따른다. 앱 전체 실행에는 모델·키·OAuth 테스트 사용자·VM/Ollama 준비가 필요하고, 합성만 실행할 때는 별도 새 checkout의 demo 구성을 사용할 수 있다. 공유 Supabase migration은 반복 실행하지 않는다.
- 미검증 범위: 실제 Windows VM, 실제 악성 바이너리/VM 탈출, 전체 유출 경로와 의미 정확도, 대표 평가셋의 Precision/Recall/F1/ASR 일반화. 다른 hardware·Ollama 버전/부하는 별도 기록하고 성능을 비교한다.
