# 작업: 에이전트 컨테이너 1차 — 제어 백엔드의 LLM 연결 부분

캡스톤 프로젝트용이다. 동작하는 최소 구현을 목표로 하고 과하게 만들지 않는다.

## 먼저 읽을 문서
- `Agent Container/파일구조+프레임워크.md`: **파일 구조와 기술 스택의 기준**이다. 폴더·파일 이름과 역할은 이 문서를 그대로 따른다.
- `Agent Container/agent-container-design.md`: 설계 기준. 4절 "공통 LLM 어댑터"와 실행 토큰·한도 부분을 위주로 본다.
- 참고 코드: `LLM comparison test/backend/app/adapters.py`. 필요한 부분은 복사해서 써도 되지만 그 프로젝트는 수정하지 않는다.

프로젝트 루트는 `Agent Container/`이다. 기존 md 문서 두 개는 옮기거나 수정하지 않는다.

## 이번 범위
파일구조 문서의 `control-dashboard/backend/` 중 아래 파일만 구현한다. 나머지 파일(frontend, evidence.py, rules.py, receiver, agent-container, compose.yaml)은 만들지 않거나 빈 자리표시만 둔다.

1. **`backend/llm.py`**: OpenAI 호환 Chat Completions로 Ollama(`{주소}/v1`)를 호출한다. 설정은 `provider`, `base_url`, `api_key`(선택), `model`이다.
   - OpenRouter는 이번에 구현하지 않는다. 나중에 같은 OpenAI 호환 경로에 `provider="openrouter"`와 API 키만 추가하면 되도록 구조만 열어 둔다.
   - 응답은 텍스트, 도구 호출 목록(ID·이름·인자), 종료 사유로 정리한다.
   - `finish_reason == "length"`면 `limit_reached`로 표시하고, 그 응답의 도구 호출은 실행하지 않는다.
2. **`backend/runs.py`**: 메모리에 실행을 관리한다. run 생성(연결 설정, 도구 정의), 실행 토큰 발급, 중지.
   - 한도: 실행당 LLM 호출 8회, 요청당 `max_tokens` 2048, 전체 600초. 넘으면 거부하고 `limit_reached`로 표시한다.
   - 작업 컨테이너 생성·정리는 이번에 구현하지 않는다. 나중에 붙일 자리만 함수로 남긴다.
3. **`backend/worker_api.py`**: 작업 컨테이너가 호출할 LLM 창구다.
   - 요청에는 `run_id`, `messages`, `tools`를 담고, 헤더에 실행 토큰을 붙인다.
   - 모델·키 같은 연결 설정은 요청으로 받지 않는다. runs.py에 저장된 설정을 쓴다.
   - 토큰이 틀리거나 실행이 중지·종료됐으면 LLM을 호출하지 않고 거부한다.
4. **`backend/main.py`와 `backend/api.py`**: 대시보드용 API 앱과 worker_api 앱을 **서로 다른 포트**로 띄운다.
   - 대시보드 API(설정 저장, run 생성·중지, 연결 확인)는 최소한으로만 만든다.
   - worker_api 앱에는 LLM 경로만 등록하고 관리 경로는 넣지 않는다.
5. **연결 확인**: api.py의 연결 확인 기능 또는 별도 스크립트로 구현한다. 지정한 모델에 더미 도구 하나를 주고 "도구 호출 → 결과 전달 → 최종 답변"이 되는지 확인한다.
6. API 키와 토큰은 로그에 출력하지 않는다.
7. **`control-dashboard/requirements.txt`**: FastAPI, uvicorn, httpx, pydantic, pytest 정도만 넣는다.

## 테스트 (`tests/`)
pytest와 `httpx.MockTransport`로 아래 정도만 확인한다.
- 정상 요청이 도구 호출을 포함한 응답을 돌려준다.
- 잘못된 토큰, 중지된 실행의 요청은 거부된다.
- 9번째 호출은 한도로 거부된다.
- `length` 종료는 `limit_reached`로 표시된다.
- worker_api 앱에 관리 경로가 없다.

실제 Ollama 확인은 연결 확인 기능으로 한다.

## 다음 단계를 위해 지켜둘 것
이번에 구현하지 않더라도 구조를 이렇게 잡아 둔다.
- OpenRouter 등 외부 API는 추가 구현으로 남긴다. 추가할 때 API 키는 제어 백엔드에만 두고 로그·파일에 남기지 않는다.
- 나중에 `compose.yaml`의 작업 네트워크는 `name:`으로 이름을 고정한다. 백엔드가 동적으로 만드는 작업 컨테이너를 이 네트워크에 붙인다.
- 대시보드 포트는 호스트 `127.0.0.1`에만 공개하고, worker_api 포트는 호스트에 공개하지 않는다.
- 나중에 `frontend/app.js`를 만들 때 메일 본문·모델 출력·도구 인자·오류는 `textContent`로만 넣고 `innerHTML`은 쓰지 않는다.

## 마무리
- 루트 `README.md`에 설치·실행·테스트 방법을 적는다.
- 끝나면 만든 파일, 테스트 결과, 확인하지 못한 것을 짧게 보고한다.
