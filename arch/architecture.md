# ProDog 목표 아키텍처

## 주제

Gmail을 OAuth로 연동해 받은편지함의 메일에 숨은 프롬프트 인젝션을 찾아 알려 주는 웹앱
## 목표

AI 비서나 LLM 요약 기능에 메일을 읽히는 사용자가, 메일 속에 숨은 지시문이 자기 AI를 조종하기 전에 위험한 메일을 알아보도록 돕는다.

이 문서는 서비스의 목표 구조와 현재 구현 상태를 구분해 기록한다. 지금 동작하는 부분은 Gmail 수집, 대시보드의 메일 목록, ML 정적 탐지기(TF 계열과 Transformer 결합), 수집 서버의 탐지기 호출, Supabase 쓰기 어댑터, agent-container 기반 동적 검사이며, 대시보드가 Supabase를 읽는 구간과 사용자 로그인 귀속은 아직 구현 전이다. 현재 구성은 단일 사용자 로컬 시연용이다. **현재 우선 범위는 정적 탐지기가 내는 `safe`, `suspicious`, `danger` 3단계 판정을 대시보드에 보이는 것까지**이다.

## 범위

- 사용자가 Google 계정을 연결하면 받은편지함 메일을 읽기 전용으로 수집한다.
- 메일의 제목, 발신자, 본문(plain·HTML), 첨부 메타데이터에서 간접 프롬프트 인젝션 확률을 계산한다.
- 확률을 `safe`(정상), `suspicious`(의심), `danger`(위험)로 나누고 판정 근거를 붙인다.
- `suspicious`와 `danger` 메일은 동적 검사(agent-container)로 넘겨 LLM 에이전트가 메일 속 지시를 따르는지 확인한다.
- 대시보드에서 메일별 판정, 점수, 근거, 수집이 불완전했던 부분을 보여 준다.

## 설계 원칙

- Gmail 권한은 `gmail.readonly`만 쓴다. 메일을 보내거나 지우거나 옮기지 않는다.
- 메일 본문은 브라우저로 보내지 않고 Supabase에도 저장하지 않는다. 본문은 수집 서버와 탐지 모델 사이에서 메모리로만 오간다. 저장하는 것은 판정, 점수, 제한된 근거 발췌다.
- 탐지기는 최종 악성 판정을 하지 않는다. 확실한 정상만 `safe`로 통과시키고, 애매한 메일은 `suspicious`로 다음 단계에 넘긴다.
- HTML은 실행하거나 외부 리소스를 불러오지 않고 문자열로만 분석한다.
- 브라우저는 Supabase를 읽기 키(anon)로 읽기만 하고, 쓰기는 수집 서버가 서비스 키로 한다.
- 분석 실패나 수집 불완전을 `safe`로 채우지 않는다. 수집 상태와 판정을 따로 보관한다.
- 수집기, 탐지기, 저장소, 화면은 계약(필드와 값)으로만 만나게 하고, 한쪽을 바꿔도 다른 쪽을 건드리지 않게 한다.

## 논리 구성

```mermaid
flowchart LR
    User((사용자))
    Dash[대시보드<br/>src/dashboard]

    subgraph Collect[수집 서버 · src/gmail]
        OAuth[Gmail OAuth<br/>gmail.readonly]
        Fetch[메일 수집·파싱<br/>본문 해시·경고 코드]
    end

    GA[Gmail API]

    subgraph Detect[탐지 · src/detector]
        Static[ML 정적 탐지기<br/>해시 특징 + SGD]
        LLM[동적 검사 · agent-container<br/>suspicious·danger]
    end

    subgraph Store[Supabase]
        Conn[(gmail_connections)]
        Mail[(mail_messages)]
        Res[(analysis_results)]
        Scans{{scans 읽기 뷰}}
    end

    Auth[Supabase Auth]

    User -->|로그인·Gmail 연결| Dash
    Dash -->|OAuth 시작| OAuth
    OAuth -->|동의·토큰| GA
    Fetch -->|메일 목록·본문| GA
    Fetch ==>|메일 dict · 메모리에서만| Static
    Static -->|safe · danger| Fetch
    Static -->|suspicious · danger| LLM
    LLM -->|이탈 여부·근거| Fetch
    Fetch ==>|service_role 쓰기| Mail
    Fetch ==>|판정·점수·근거| Res
    Fetch --> Conn
    Mail --> Scans
    Res --> Scans
    Dash -->|anon 키 읽기 · RLS| Scans
    Dash -->|로그인| Auth
```

점선은 아직 정하지 않은 연결이고, 굵은 선은 본문이나 판정이 흐르는 선이다. TBD는 연결 지점만 정하고 구현 기술은 정하지 않았다는 뜻이다. 현재는 `Dash → Collect → Static` 구간과 동적 검사 호출이 동작하고, Supabase 쓰기는 어댑터가 있으나(`POST /api/sync`) 대시보드는 아직 읽지 않는다. 수집 서버는 토큰과 메타데이터를 로컬 SQLite에 둔다.

## 구성 요소

### 1. 대시보드 (`src/dashboard`, 담당 이지훈)

React + Vite. 로그인 화면에서 Gmail OAuth를 시작하고, 받은편지함 목록과 메일별 판정을 보여 준다. 개발 중에는 Vite proxy로 수집 서버(`127.0.0.1:8765`)를 호출한다. 판정이 있으면 판정을 먼저 보이고, 수집이 불완전했으면 그 안내를 함께 보인다. 지금 판정 자리는 목업 데이터이거나 `검사 전`이다. 최종 목표는 Supabase `scans` 뷰를 읽는 것이다.

### 2. Gmail 수집 서버 (`src/gmail`, 담당 심재훈)

Google OAuth로 계정을 연결하고 받은편지함 메일을 읽어 정규화한다. 출력은 본문 해시, 경고 코드 목록, 잘림 여부, 첨부 메타데이터를 포함한다. 세션 쿠키와 CSRF 토큰으로 보호하고, Host와 Origin이 설정한 주소와 같을 때만 요청을 받는다. 목표 구조에서는 수집 서버가 탐지 호출과 Supabase 쓰기를 조율하는 서버 역할을 맡는다.

### 3. ML 정적 탐지기 (`src/detector`, 담당 윤주혁)

단어 1~2-gram과 문자 3~5-gram 해시 특징에 `SGDClassifier`(log loss)를 얹은 ML 전용 분류기다. 수작업 룰은 점수에 쓰지 않는다. 인젝션 확률(0~1)을 내고 두 임계값으로 3단계를 정한다.

| 판정 | 기준 | 현재 값 |
| --- | --- | ---: |
| `safe` | 확률이 의심 임계값 미만 | 0.05 미만 |
| `suspicious` | 의심 임계값 이상, 위험 임계값 미만 | 0.05 ~ 0.414419 |
| `danger` | 위험 임계값 이상 | 0.414419 이상 |

`static_score`는 최종 정적 판정을 만든 모델의 출력값을 `round(score * 100)`으로 바꾼 표시용
백분율이다. 실제 판정은 이 값으로 다시 계산하지 않고 각 모델의 임계값으로 정한다. 병렬 탐지에서
두 모델이 같은 최종 판정을 내리면 더 높은 출력값을 쓴다. 대시보드 R01~R07은 데모 근거 탐지에만
사용하며 `static_score`나 실제 판정에 합산하지 않는다.

임계값은 개발셋에서 조정했으므로 최종 일반화 성능은 새 외부 데이터로 따로 확인해야 한다. 근거는 [src/detector/README.md](../src/detector/README.md)에 있다.

### 4. 동적 검사 (`src/agent-container`, 담당 박한빈)

정적 판정이 `suspicious` 또는 `danger`인 메일을 도구를 쓰는 LLM 에이전트(호스트 Ollama, 기본 `qwen3.5:9b`)에게 읽히고, 에이전트가 요약 외 도구를 요청했는지(이탈)와 실제 효과를 증거로 판정한다. 작업마다 Docker 컨테이너를 새로 만들고, 수집 서버의 `DynamicService`가 `DYNAMIC_SERVICE_URL`로 이 서비스를 호출한다(자동 제출은 설정에서 켠다). 동적 검사는 정적 판정이나 점수를 낮추지 않으며, 동적 검사를 못 해도 정적 결과는 그대로 동작한다. Docker 격리와 네트워크 차단의 실제 동작은 이 환경에서 검증하지 못했다. `llm_score` 산식과 모델 선정 근거는 아직 정하지 않았다(#36).

### 5. 저장소 (Supabase, 담당 김서진)

| 테이블 | 내용 |
| --- | --- |
| `gmail_connections` | 앱 사용자와 연결된 Gmail 계정(이메일 키), 연결 상태 |
| `mail_messages` | 메일 메타데이터, 본문 해시, 경고, 수집 상태(`complete`·`incomplete`·`failed`) |
| `analysis_results` | 분석 실행별 판정, 정적·LLM·최종 점수, 근거, 모델 버전, 실패 사유 |
| `scans` (읽기 뷰) | 메일별 최신 완료·실패 결과를 합친 대시보드용 뷰 |

세 테이블은 RLS로 본인 행의 SELECT만 허용하고 `scans` 뷰는 `security_invoker`로 같은 권한을 적용한다. 필드 규칙과 상태 계산은 [SUPABASE_TABLE.md](SUPABASE_TABLE.md)를 따른다.

## 데이터 흐름

1. 사용자가 대시보드에서 Google 계정 연결을 시작하고, 수집 서버가 OAuth 동의를 받아 토큰을 보관한다.
2. 수집 서버가 Gmail API에서 메일을 읽고 본문 해시, 경고, 잘림 여부, 첨부 메타데이터를 만든다.
3. 수집 서버가 메일을 메모리에서만 정적 탐지기에 넘기고, 탐지기가 확률과 3단계 판정을 돌려준다.
4. `suspicious`와 `danger`는 동적 검사에 넘겨 에이전트 이탈 여부와 근거를 받는다. 결과는 정적 판정과 따로 보인다.
5. 수집 서버가 서비스 키로 `mail_messages`와 `analysis_results`에 쓴다. 본문은 쓰지 않는다.
6. 대시보드가 `scans` 뷰를 읽어 판정, 점수, 근거와 수집 불완전 안내를 보여 준다.

## 현재 구현 상태

| 구간 | 상태 |
| --- | --- |
| 대시보드 ↔ 수집 서버 | 연결됨 (#11) |
| Gmail OAuth 로그인 버튼 | 연결됨 (#18). OAuth 앱이 테스트 모드라 등록한 테스트 사용자만 가능 |
| 정적 탐지기 3단계 판정 | 완료 (#19, #23) |
| 수집 서버 → 탐지기 호출 | 연결됨 (#29). 메일마다 `detection`(판정·점수)을 붙여 반환. 모델 파일(`DETECTOR_MODEL_PATH`)이 없으면 `검사 전` |
| 수집·탐지 결과 → Supabase | 어댑터 구현 (#13). `POST /api/sync`로 명시 호출해 쓴다. 테스트 사용자 1명 고정이고 `scans` 형태·적용 SQL은 레포에 없음 (#37) |
| Supabase → 대시보드 | 미연결 (#14, #38) |
| Supabase Auth 로그인 | 미연결 (#37, #34) |
| Transformer 병렬 탐지 | 병합됨 (#31). 두 탐지기 중 더 높은 판정을 쓰는 결합 로직, 외부 데이터 평가와 점수 계약은 #48 (#35) |
| 동적 검사 | 병합됨 (#51). `suspicious`·`danger` 메일을 agent-container로 검사. 로컬 단일 사용자용. Docker 격리 동작은 미검증. 모델 선정 근거는 #36 |

단계별 순서와 담당은 [INTEGRATION.md](INTEGRATION.md)에 있다.

## 배포와 보안 경계

아래는 목표 경계다. 현재 구현은 `SUPABASE_USER_ID` 1명 고정의 단일 사용자 구성이라 요청별 사용자 확인, state와 세션 대조, `user_id` 소유 확인은 구현하지 않았다(#34 not planned, 후속 #70).

- Gmail 토큰은 수집 서버가 보관하고 브라우저로 보내지 않는다. 연결 해제 시 토큰, 연결 행, 메일 행, 분석 결과를 함께 지운다.
- 서비스 키는 서버에만 두고, 매 요청에서 Supabase 사용자와 연결 소유권을 확인한다. 서비스 키는 RLS를 우회하기 때문이다.
- `evidence`의 발췌는 메일 원문이므로 개인정보로 취급한다. 서버에서 건수와 길이를 제한하고 화면에서는 HTML이 아닌 텍스트로만 표시한다.
- OAuth callback은 state(PKCE)와 현재 사용자 세션을 검증해 타인 계정이 연결되지 않게 한다.
- `.env`, OAuth 클라이언트 시크릿, 토큰은 커밋하지 않는다. 실제 메일 원문도 레포에 올리지 않고, 데이터셋은 익명화하거나 합성 샘플만 쓴다.

## 구현 순서 제안

1. **저장 계약 확정**: `scans` 형태 결정과 적용 SQL 커밋 (#12)
2. **수집→탐지 연결**: 수집 서버에서 탐지기를 호출하고 결과를 대시보드 행 모양으로 변환
3. **저장 어댑터**: 수집·탐지 결과를 Supabase에 쓰기 (#13)
4. **화면 교체**: 대시보드가 `scans`를 읽도록 변경 (#14)
5. **사용자 귀속**: Supabase Auth 로그인과 Gmail 연결을 같은 `user_id`에 묶기
6. **동적 검사**: agent-container로 `suspicious`·`danger` 메일 검사 (완료, #51)

각 단계의 구현 완료와 탐지 성능은 별도로 측정해 기록한다.

## 아직 정할 사항

| 단계 | 남은 결정 |
| --- | --- |
| 저장 | `scans`를 테이블로 둘지 뷰로 둘지(`schema.sql`은 테이블, `SUPABASE_TABLE.md`는 뷰), 링크된 `supabase-gmail-schema.sql`의 위치 |
| 동적 검사 | 모델 선정 근거 기록(#36), `llm_score` 산식, 첨부파일(PDF) 판정을 따로 낼지(#61) |
| 서버 | 결정됨: 코드는 `src/gmail`에 둔다 |
| 계정 | 이메일 키 대신 Google OIDC `sub`로 옮길 시점, OAuth 앱 테스트 모드 해제 시기 |
| 평가 | 임계값 조정에 쓰지 않은 새 외부 데이터로 일반화 성능 확인 |

## 참고

- [SUPABASE_TABLE.md](SUPABASE_TABLE.md): Supabase 테이블과 `scans` 읽기 계약
- [INTEGRATION.md](INTEGRATION.md): 모듈 연동 단계와 로컬 실행
- [src/gmail/docs/collect-api-contract.md](../src/gmail/docs/collect-api-contract.md): 수집 출력 필드와 저장 정책
- [src/detector/README.md](../src/detector/README.md): 탐지 모델 입력·출력과 임계값 근거
