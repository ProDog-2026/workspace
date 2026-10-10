# ProDog 팀 워크스페이스 작업 규칙

Claude, Codex 등 이 레포에서 일하는 모든 AI와 사람이 따르는 규칙이다.
`CLAUDE.md`는 이 파일을 불러오기만 한다.

## 프로젝트

- 팀: ProDog (2026-2 캡스톤), 지도교수 이병천
- 팀원: 심재훈(PM), 김서진, 박한빈, 이지훈, 윤주혁
- 주제: Gmail을 OAuth로 연동해 메일 속 프롬프트 인젝션을 탐지하는 웹앱 (주제명 PromptShield)
- 조직: https://github.com/ProDog-2026

## 시작 전

1. `git switch develop && git pull`
2. 내 할 일을 확인한다: `gh issue list --assignee @me --state open`
   - 없으면 `gh issue list --state open --search "no:assignee"`에서 고르거나 PM(심재훈)에게 묻는다.
   - 할 일의 원본은 GitHub Issues다. 할 일 목록 파일을 따로 만들지 않는다.
3. 작업 브랜치를 `develop`에서 만든다. 이름은 `타입/이슈번호-기능이름` (소문자, `-`). 예: `feature/12-login-oauth`
4. PR 본문에 `Closes #이슈번호`를 넣는다. 머지되면 이슈가 자동으로 닫힌다.
5. 이슈에 없는 새 작업이 생기면 먼저 이슈를 만든다: `gh issue create --template 작업`

## 산출물 위치

| 내용 | 폴더 |
|---|---|
| 코드 (백엔드, 프론트엔드, 탐지 모델) | `src/` |
| 연구 문서, 논문, 선행연구, 데이터셋 설명 | `docs/` |
| 교수 피드백 | `review/` |
| 회의록, 주간 보고, 결정 기록 | `discussion/` |
| 아키텍처 설계, 다이어그램 | `arch/` |
| 개인 작업 메모 | `notes/<이름>/` |

## 보안 규칙 (필수)

- `.env`, OAuth 클라이언트 시크릿, Gmail 토큰, 자격 증명 파일은 절대 커밋하지 않는다. `.gitignore`에 있어도 `git add -f`로 올리지 않는다.
- 실제 사용자 메일 원문은 레포에 올리지 않는다. 데이터셋은 익명화하거나 합성 샘플만 쓴다.
- 탐지 샘플 중 실제 공격 페이로드를 쓸 때는 출처와 사용 범위를 `docs/dataset/`에 적는다.

## Git

- 세부 규칙은 `CONTRIBUTING.md`를 따른다.
- 커밋: `태그: 내용` (feat, fix, docs, style, refactor, chore)
- PR은 `develop`으로 올리고, 1명 이상 승인 후 머지한다. `main`, `develop`에 직접 push하지 않는다.

## 스킬

- 원본은 `.agents/skills/`다. 여기서만 고친다.
- `.claude/skills/`는 GitHub Action(`sync-skills`)이 PR마다 자동으로 복사한다. 직접 고치면 덮어써진다.
- Codex는 `.agents/skills/`, Claude Code는 `.claude/skills/`를 자동으로 읽는다.

## 문서 작성

- PDF, PPT, DOCX는 `to-markdown` 스킬로 md로 바꿔 원본 옆에 둔다.
- 논문을 근거로 쓸 때는 `paper-reading` 스킬을 따르고 읽기 수준(1차, 2차, 원문 미확인)을 적는다.
- 외부 사실에는 출처 URL을 붙인다. 모르는 값은 `TBD`로 두고 지어내지 않는다.
