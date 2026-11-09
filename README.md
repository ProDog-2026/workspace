# ProDog

ProDog(2026-2 캡스톤)의 코드, 연구 문서, 교수 피드백, 회의 기록을 모으는 단일 저장소다.
작업 규칙은 [`AGENTS.md`](AGENTS.md), Git 규칙은 [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Windows에서 실행

워크스페이스 폴더의 **`run.cmd`**를 더블클릭하면 동적 검사 컨테이너, Gmail 백엔드, 웹 대시보드를 준비하고 브라우저에서 `http://127.0.0.1:5173/`을 연다. 이미 실행 중인 서비스는 재사용한다. 첫 실행에는 Docker Desktop(Linux 컨테이너), Node.js/npm, Python 환경 생성용 uv가 필요하며, 누락된 프로젝트 의존성과 Docker 이미지는 준비 과정에서 설치·빌드한다.

Gmail의 기존 `.env`는 유지한다. 설정이 없으면 데모 모드로 생성하며, 실제 Gmail은 별도 Google OAuth 설정이 필요하다. 동적 검사에는 Ollama와 도구 호출을 지원하는 모델이 별도로 필요하다. 오류가 나면 실행 창에 표시되는 메시지나 Gmail의 `src/gmail/artifacts/run-backend.err.log`, 대시보드의 `src/dashboard/artifacts/run-dashboard.err.log`를 확인한다.

종료할 때는 같은 폴더의 **`stop.cmd`**를 더블클릭한다. 이 워크스페이스의 웹 대시보드, Gmail 백엔드와 동적 검사 컨테이너를 종료하며, 진행 중인 동적 검사도 중지한다. Docker Desktop과 Ollama는 계속 실행된다. 종료 전 대상만 확인하려면 PowerShell에서 `./stop.cmd -Preview`를 실행한다.

## 폴더

| 폴더 | 내용 |
|---|---|
| [`src/`](src/) | 팀 코드 |
| [`docs/`](docs/) | 연구 문서, 논문, 선행연구, 데이터셋 |
| [`review/`](review/) | 교수 피드백 |
| [`discussion/`](discussion/) | 회의록, 주간 보고, 결정 기록 |
| [`arch/`](arch/) | 아키텍처 설계와 다이어그램 |
| `notes/<이름>/` | 개인 작업 메모 ([`notes/`](notes/)) |
| [`.agents/skills/`](.agents/skills/) | 팀 공용 AI 스킬 (원본) |

## 처음 세팅

1. `git clone https://github.com/ProDog-2026/<레포이름>.git && cd <레포이름> && git switch develop`
2. 이 폴더에서 Claude Code나 Codex를 실행하면 팀 스킬과 `AGENTS.md`가 자동으로 적용된다.
3. `gh auth login`으로 GitHub CLI에 로그인한다. AI가 이슈(할 일)를 읽으려면 필요하다.
4. 작업 전에는 항상 `git pull` 후 `gh issue list --assignee @me`로 내 할 일을 확인한다. 할 일은 Issues에서 관리한다.
