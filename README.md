# ProDog

ProDog(2026-2 캡스톤)의 코드, 연구 문서, 교수 피드백, 회의 기록을 모으는 단일 저장소다.
작업 규칙은 [`AGENTS.md`](AGENTS.md), Git 규칙은 [`CONTRIBUTING.md`](CONTRIBUTING.md).

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

앱 실행은 [팀원 pull 후 설정·모델·런타임 점검 안내](docs/team-setup.md)를 먼저 따른다.
현재 공유 브랜치와 `develop` 머지 이후 절차를 구분해 안내한다.

1. `git clone https://github.com/ProDog-2026/<레포이름>.git && cd <레포이름> && git switch develop`
2. 이 폴더에서 Claude Code나 Codex를 실행하면 팀 스킬과 `AGENTS.md`가 자동으로 적용된다.
3. `gh auth login`으로 GitHub CLI에 로그인한다. AI가 이슈(할 일)를 읽으려면 필요하다.
4. 작업 전에는 항상 `git pull` 후 `gh issue list --assignee @me`로 내 할 일을 확인한다. 할 일은 Issues에서 관리한다.
