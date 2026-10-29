# Static ensemble integration implementation plan

**Goal:** 팀원의 실제 ML·Transformer로 수집 메일을 분류하고 의심·위험 판정을 기존 Ollama/Docker 대기열로 연결한다.

**Architecture:** 기존 계정별 자동 분석 구조와 일회용 Docker 실행을 유지한다. 승인된 `feature/31-transformer-ensemble` 탐지기 코드/가중치만 가져오고, 별도 Python 3.12 환경에서 두 모델을 실행한다. 각 모델의 자체 임계값으로 낸 위험 단계의 최댓값을 사용한다.

**Scope:** 탐지기 소스/가중치/검사, Gmail worker·결과 검증·모델 식별자, 대시보드 점수 표시, 설치 환경과 문서. 기존 OAuth·원격 Supabase 변경은 이번 작업에 섞지 않는다. 기존 동적 분석 기능과 미커밋 작업은 보존한다.

## Checks and implementation

- [x] 팀 모델 브랜치에서 탐지기 관련 파일만 가져오고 모델 해시를 확인한다.
- [x] 먼저 결합 결과 검증, Transformer 변경 시 모델 식별자 갱신, worker의 두 모델 호출 테스트를 추가하여 실패를 확인한다.
- [x] worker에서 기존 ML과 Transformer를 실행하고 개별 점수와 `decided_by`를 반환한다. 기존 단일 모델 출력은 계속 허용한다. 결합 결과는 모든 개별 점수·임계값·최종 단계의 일관성을 검증한다.
- [x] 실제 두 모델의 파일 전체를 모델 식별자에 반영한다. 일부 Transformer 파일이 누락되면 정상 판정으로 우회하지 않는다.
- [x] 대시보드에서 각 모델 결과와 최종 단계, 자동 동적 분석 상태를 표시한다.
- [x] 탐지기 전용 Python 3.12 환경과 Transformer 의존성을 설치한다. 실제 합성 메일로 정적 분석 및 Qwen→Docker 순차 실행을 확인한다.
- [x] 전체 Gmail/탐지기 검사, 대시보드 빌드, 실제 Docker 경계 검사 후 사용 방법·격리 한계를 설명한다.

## Review focus

- 두 모델의 확률을 직접 비교하지 않고 위험 단계를 비교한다.
- 개별 모델 실패·누락·판정 불일치를 safe로 만들지 않는다.
- Transformer 변경으로 기존 분석 결과가 재사용되지 않도록 모든 가중치·설정을 식별한다.
- 메일/모델 응답으로 호스트 명령·경로·네트워크 실행이 생기지 않는다.
- 실험은 합성 메일과 가짜 토큰을 사용하고 기존 Gmail 세션·설정·기록을 변경하지 않는다.

## Verification (2026-10-28)

- Team artifacts match commit `3e8968e` byte-for-byte. Installed separate Python 3.12.14 detector environment.
- Gmail: 209 passed, 1 optional live-model test skipped; actual Docker and static artifact flags enabled. Existing TestClient deprecation warning remains.
- Detector: 15 passed. Dashboard: 5 mapping checks and Vite build passed.
- Opt-in actual `qwen3:4b-instruct`: passed with synthetic normal+injected mails and real Docker. Normal task created summary.txt; injected mail requested private.txt instead, without creating the fixed leak.txt goal. These are functional observations, not success-rate or detection-accuracy estimates.
- `qwen3:4b` on Ollama 0.40.7 reports thinking.values=[true]; it exhausted the 1024-token budget. Installed distinct official Instruct tag, kept existing weights, and reject thinking-only selection for automatic runs.
- Production 8765/5173 servers and real Gmail account settings were not restarted or modified. No commit/push.
