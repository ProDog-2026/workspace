# 대시보드 데이터 흐름 패널 (LangGraph)

대시보드(http://127.0.0.1:8000)에서 실행을 고르면 **데이터 흐름 (LangGraph)** 섹션에
그 실행이 실제로 지나간 노드와 엣지, 단계별 State가 표시된다. 진행 중인 실행은 1.5초마다 다시 계산되며,
아직 이벤트가 오지 않은 노드가 **현재 노드**(주황 테두리)로 표시된다.

## 구성

| 파일 | 역할 |
|---|---|
| `control-dashboard/backend/flow.py` | LangGraph `StateGraph` 정의, 실제 이벤트를 소비하는 노드, `trace()` |
| `control-dashboard/backend/api.py` | `GET /runs/{id}/flow` |
| `control-dashboard/frontend/flow.js` | SVG로 노드·엣지를 그리고, 단계 목록과 State를 표시 (외부 라이브러리 없음) |
| `compose.yaml` | `./data/experiments`를 읽기 전용으로 마운트 (실험 스크립트 분기 판단용) |
| `docs/dataflow/dashboard_dataflow_graph.py` | 같은 `trace()`를 터미널에서 실행 (`--replay-all`, `--replay`, `--live`) |
| `tests/test_flow.py` | 완료·진행 중·분기·불일치 검출·API 테스트 |

## `/runs/{id}/flow` 응답

- `nodes`, `edges`: LangGraph 컴파일 결과(`get_graph()`)에서 그대로 꺼낸 구조. 노드에는 통과 횟수(`visits`), 엣지에는 이번 실행의 통과 횟수(`count`)가 붙는다.
- `steps`: 노드가 실행될 때마다 1개씩 생긴다. 각 단계에는 바뀐 키(`changed`), 바뀐 값(`delta`), 그 시점의 전체 State(`state`)가 있다.
- `current`, `waiting`: 진행 중일 때 멈춰 있는 노드와 기다리는 이유.
- `anomalies`: 실제 이벤트와 맞지 않는 부분. 기대한 이벤트가 없거나, 그래프가 소비하지 못한 이벤트가 남거나, `rules.evaluate` 재계산 결과가 result.json과 다를 때 기록된다.

## 노드가 소비하는 실제 데이터

| 노드 | 실제 위치 | 데이터 |
|---|---|---|
| 실행 요청 `submit_request` | app.js / run_experiments.py → `POST /runs` | run 레코드의 model·subject·limits·prompt |
| 실행 생성 `launch_run` | `RunManager.launch` | run_id, phase |
| 작업 컨테이너 `spawn_container` | `DockerRuntime.execute` | container_id |
| LLM 호출 `llm_gateway` | agent → `:8001/llm` → `infer` → Ollama | manager `llm_request`/`llm_response` |
| 도구 실행 `execute_tools` | `agent-container/tools.py` | tool_call_id별 agent `tool_requested`/`tool_result` |
| 에이전트 종료 `agent_final` | `agent.py` | agent `final` |
| 증거 수집 `collect_evidence` | `runtime.py`, `evidence.py` | `file_verified`, receiver `request_received`, `runtime_error` |
| 판정 `evaluate_rules` | `rules.evaluate` (실제 코드 재실행) | 전체 이벤트 → result.json과 대조 |
| 결과 저장 `persist_result` | `EvidenceStore.save` | phase |
| 컨테이너 정리 `cleanup_container` | `cleanup_worker_container` | cleanup_error |
| 대시보드 표시 / 실험 기록 | app.js / run_experiments.py | run_id가 `data/experiments/*/results.csv`에 있는지 |

events.jsonl에서는 manager 이벤트(동기 기록)와 agent 이벤트(컨테이너 로그 폴링으로 늦게 도착)가 섞여 있다.
그래서 순서가 아니라 출처, 종류, `tool_call_id`로 짝을 맞춘다.

## 알려진 한계

- 진행 중일 때 agent 이벤트는 로그 폴링으로 늦게 도착하므로, 실제로는 다음 LLM 요청이 시작됐는데도 "도구 실행 결과 대기"로 보일 수 있다. 화면은 도착한 이벤트 기준이다.
- 실험 스크립트는 실행이 끝난 뒤에 CSV를 쓰므로, 막 끝난 실험 실행은 잠깐 "대시보드 표시"로 보였다가 다음 조회에서 "실험 기록"으로 바뀐다.
- 사용자 중지와 시간 초과는 별도 노드가 아니다. 둘 다 에이전트 종료 → 증거 수집으로 이어진다.

## 확인 결과 (2026-11-08)

- 보관 실행 88건 모두 불일치 0건. 경로는 3가지(실험 80건, 대시보드 7건, LLM 4회 실험 1건)이다.
- 이벤트를 앞에서부터 잘라 진행 중 상태를 재현했을 때, 현재 노드가 도착한 이벤트에 맞춰 한 칸씩 이동했다.
- control 이미지 재빌드 후 :8000 대시보드에서 패널 표시와 단계 선택을 확인했다.
- 실제 새 실행을 진행 중에 따라가는 화면은 아직 확인하지 않았다.
