# Git Flow 전략 가이드

> ProDog 팀의 협업 규칙

## 1. 팀 합의 사항

- **Git Client 도구 통일**: VS Code 내장 Git
- **Merge 규칙**: 1명 이상의 Approve 후 Merge (코드 충돌 방지, 진행 상황 공유)
- **Merge 타이밍**: 기능 하나 완성될 때마다 수시로 **develop**에 합치기

## 2. 브랜치 전략

- **main**: 완전히 작동하는 최종 결과물만 올라간다. 예: 중간 발표 데모, 최종 발표 버전
- **develop**: 각자 만든 기능이 합쳐지는 곳. 항상 에러 없이 실행되어야 한다. **Default Branch**
- **feature/...**: develop에서 복사해서 만드는 개인 작업 공간. 기능이 끝나면 develop으로 PR하고 삭제

### 브랜치 이름 규칙

> 타입/이슈번호-기능이름 (소문자와 -으로 통일). 예: `feature/12-login-oauth`

| Type | 설명 | 예시 |
| --- | --- | --- |
| **feature** | 새로운 기능 개발 | feature/12-gmail-oauth |
| **fix** | 버그 수정 | fix/15-token-refresh |
| **docs** | 문서 작업 | docs/18-dataset-readme |
| **refactor** | 코드 정리/구조 변경 | refactor/21-api-cleanup |

## 3. 커밋 메시지 규칙

> `태그: 작업 내용` 형식을 따른다.

| Tag | 의미 | 사용 예시 |
| --- | --- | --- |
| **feat** | 새로운 기능 추가 | feat: 로그인 페이지 UI 구현 |
| **fix** | 버그 수정 | fix: 토큰 만료 시 재로그인 오류 해결 |
| **docs** | 문서 수정 | docs: README 역할 분담표 업데이트 |
| **style** | 코드 포맷팅 (로직 변경 없음) | style: 들여쓰기 정리 |
| **refactor** | 코드 리팩토링 | refactor: 중복 코드 함수화 |
| **chore** | 기타 설정, 자잘한 수정 | chore: 패키지 설치 및 설정 파일 변경 |

## 4. 작업 순서

1. **브랜치 생성**: `develop`으로 이동 후 `git pull`, 그다음 `git switch -c feature/12-gmail-oauth`
2. **코딩 및 커밋**: `git add <파일>` → `git commit -m "feat: Gmail OAuth 연동 추가"` (`git add .` 전에 `git status`로 비밀 파일이 없는지 확인)
3. **업로드**: `git push -u origin feature/12-gmail-oauth`
4. **검사 요청**: GitHub에서 **Compare & pull request** → **Base: develop** <- **Compare: feature/12-gmail-oauth** 확인, PR 본문에 `Closes #12`, 팀원에게 알림
5. **병합**: 팀원 1명 이상이 확인 후 **Merge** 버튼 클릭
