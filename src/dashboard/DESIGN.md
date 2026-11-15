# ProDog 대시보드 DESIGN.md

AI로 화면을 만들거나 고칠 때 이 파일을 먼저 읽힌다. 값은 `src/App.css`의 `:root` 토큰에 있다. 새 색이나 모서리 값을 직접 쓰지 말고 토큰을 쓴다.

기준: Refero Styles의 Column 스타일 (https://styles.refero.design/style/a76ec6ba-20b3-495c-9d89-1e58281e79e7). 쿨톤 흰 캔버스에 인디고 네이비 글자, 은행 서비스 같은 차분한 신뢰감을 목표로 한다.

## 1. 지켜야 할 것 (Column보다 우선)

1. 판정 상태색은 바꾸지 않는다. 위험 `--danger`, 의심 `--warn`, 정상 `--safe`. 색만으로 구분하지 않고 항상 마크와 글자를 같이 쓴다.
2. 미실행, 실패, 미완료, 검사 전은 초록(`--safe`)으로 칠하지 않는다. 안전으로 읽히면 안 된다.
3. 본문 글자는 흰 바탕 대비 4.5:1 이상. Column의 Steel `#7c7f88`은 4.0:1이라 `--muted`는 `#696c76`(5.2:1)을 쓴다.
4. 메일 원문은 텍스트로만 표시한다. HTML로 렌더링하지 않는다.

## 2. 색

| 토큰 | 라이트 | 다크 | 쓰임 |
|---|---|---|---|
| `--page` | `#f6f6f8` | `#011821` | 페이지 바탕 |
| `--surface` | `#ffffff` | `#062431` | 카드, 패널, 드로어 |
| `--surface-2` | `#f6f6f8` | `#0a2c3a` | 표 머리, 보조 면 |
| `--border` | `#e3e4e8` | `#143847` | 카드·입력 테두리, 구분선 |
| `--ink` | `#111a4a` | `#e9ecf5` | 제목, 주요 글자 |
| `--ink-2` | `#3b3e47` | `#c3cad6` | 본문 |
| `--muted` | `#696c76` | `#9aa3b2` | 보조 글자, 캡션 |
| `--brand` | `#111a4a` | `#e9ecf5` | 주 버튼 바탕, 활성 메뉴 |
| `--accent` | `#111a4a` | `#88deeb` | 링크 |
| `--data` | `#167e6c` | `#44b48b` | 숫자, 막대, 데이터 (Column Seafoam) |
| `--signal` | `#c94d1a` | `#c94d1a` | 한 페이지 한 곳 강조 면 (Column Signal Orange) |

- `--signal`은 Column `#ec652b`가 흰 글자 대비 3.25:1이라 4.6:1이 되게 어둡게 했다. 페이지당 한 곳만 쓴다. 대시보드에서는 "위험·의심 메일" 강조 카드.
- `--data`는 개수·건수에 쓴다. 단 위험·의심 개수가 0보다 크면 판정색(`--danger-text`, `--warn-text`)으로 바꾼다. 초록 숫자가 정상으로 읽히면 안 된다.
- Cobalt·Ocean은 Column에서도 장식 전용이라 쓰지 않는다.

## 3. 글꼴

- 한 가족만 쓴다: `Inter` + `Pretendard Variable`(한글). 제목용 `--display`도 같은 글꼴이다.
- 제목은 굵기 500~600, 큰 글씨일수록 자간을 좁힌다. 40px 이상 `-0.02em`, 20~28px `-0.01em`, 본문은 기본.
- 본문 최소 14px. 12px 이하는 캡션과 `--mono` 기술 라벨에만.
- 숫자, 규칙 ID, 메일 주소는 `--mono`.

## 4. 모서리와 간격

- 카드, 패널, 입력, 버튼, 메뉴: 8px (`--r-card`, `--r-box`, `--r-ctl`).
- 완전히 둥근 모양(`--r-pill`)은 배지, 태그, 진행 막대에만. 버튼에는 쓰지 않는다.
- 간격은 `--space-1`(4px) ~ `--space-12`(48px) 토큰만 쓴다. `padding: 13px`처럼 px를 직접 쓰지 않는다.
- 화면 폭에 따라 변하는 여백은 `clamp()` 토큰을 쓴다: `--page-pad-inline`(16~32px), `--page-pad-block`(24~40px), `--section-gap`(16~24px), `--card-pad`(16~24px).
- 요소 사이 간격은 자식 margin 대신 부모의 `gap`.
- 여백·위치는 논리 속성(`margin-block-start`, `padding-inline`)으로 쓴다.

## 4-1. 웹 표준 규칙 (Google Modern Web Guidance)

출처: https://developer.chrome.com/docs/modern-web-guidance (`css`, `css-layout`, `typography`, `fluid-scaling`, `responsive-design` 가이드)

- 글자 크기는 rem. px 금지 (브라우저 글자 크기 설정을 따르게).
- 글자가 들어가는 버튼·배지·태그에 고정 `height` 금지. `min-block-size`를 쓴다. 표 칸(th, td)은 예외(`height`가 최소 높이로 동작).
- 제목과 표 머리는 `text-wrap: balance`, 문단은 `text-wrap: pretty`. 설명 문단 최대 폭은 `--measure`(64ch).
- 페이지 안 배치 전환(요약 줄, 차트 줄)은 화면 폭이 아니라 `.page` 컨테이너 폭 기준 `@container page`로 한다.
- 전체 화면 높이는 `100vh` 대신 `100dvh`.

## 5. 그림자

- 카드: `--shadow-card` (hairline + 아주 옅은 그림자). 테두리처럼 보여야 하고 떠 보이면 안 된다.
- 팝업: `--shadow-pop`. 드로어: `--shadow-drawer`. 한 화면에 큰 그림자는 하나만.
- 다크 모드는 그림자 대신 1px 테두리.

## 6. 화면 구성 (Column Layout)

- 페이지 최대 너비 1440px(사이드바 옆 왼쪽 정렬), 카드 안쪽 여백 24px, 카드 사이 24px.
- 페이지 머리: 40px/600 제목 하나. 위에 작은 라벨을 붙이지 않는다.
- 요약 줄(`.overview`): 왼쪽에 강조 카드(`.feature`, Account Balance Card), 오른쪽에 구분선 없는 숫자 격자(`.stat-strip`, Stats Row). 숫자 28px/500.
- 표 머리는 바탕색 없이 위아래 hairline만.

## 7. 차트

- Recharts를 쓴다. 모양은 shadcn/ui Area Chart(https://ui.shadcn.com/charts/area)를 따른다.
- 가로 격자선만, 축선·눈금선 없음, 눈금 글자 12px `--muted`.
- 면은 위에서 아래로 0.8 → 0.1 그라데이션, 선 2px, 곡선은 `monotone`(`natural`은 0 아래로 내려가 값이 틀려 보임).
- 툴팁은 흰 카드, 8px 모서리, 색 사각형 + 이름 + 고정폭 숫자.
- 위험·의심 차트 색은 판정색. 나머지 데이터 차트는 `--data`.

## 8. 버튼

- 주 버튼: `--brand` 바탕, `--on-brand` 글자, 8px, `--shadow-btn`. 한 영역에 하나.
- 보조 버튼: 투명 바탕, `--ink` 1px 테두리(Column Secondary Outlined).
- 내비게이션: 왼쪽 사이드바(폭 232px). Refero Styles Ramp의 내비게이션 규칙을 세로로 옮겼다 (https://styles.refero.design/style/b38702a0-75ab-474c-9106-00b624535825).
  - 흰 바탕(다크는 `#1a1919`), 오른쪽 1px hairline, 그림자 없음.
  - 메뉴는 아이콘(lucide, 18px) + 굵기 400 글자, 높이 40px, 6px 모서리. 올리면 `#f4f2f0` 바탕, 현재 메뉴도 같은 바탕만 쓴다(선·테두리 강조 없음).
  - 아래쪽에 연결 계정, 주 버튼 하나(로그인/연동 관리), 테마 전환. 주 버튼은 Ramp의 형광 노랑 대신 로고 파랑 `#0058f0` + 흰 글자(5.75:1), 올리면 `#0048c8`.
  - 본문은 사이드바 바로 옆에 붙인다: 왼쪽 여백 20px(`--space-5`), 가운데 정렬하지 않음, 최대 폭 1440px.
  - 760px 이하에서는 위쪽 가로 띠로 바뀌고 메뉴는 가로 스크롤.
  - 색은 `.sidebar` 안의 `--nav-*` 토큰으로만 정의해 본문 색과 섞지 않는다. 주 버튼 두 개를 나란히 두지 않는다.
- Google 로그인 버튼(`.gbtn`)은 Google 브랜드 규정을 따르므로 이 규칙에서 뺀다.

## 9. 하지 말 것

- 그라데이션 배경, 유리 효과, 네온, 이모지 아이콘.
- 순수 검정 `#000` 글자. `--ink`나 `--ink-2`를 쓴다.
- 토큰 밖 임의 색. 필요하면 이 파일과 `App.css`에 토큰을 먼저 추가한다.
