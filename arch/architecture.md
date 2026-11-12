# ProDog 현재 아키텍처

2026-10-30 구현 기준. Gmail의 간접 프롬프트 인젝션을 정적 분류하고, 설정한 의심·위험 메일을 로컬 LLM과 격리 컨테이너에서 동적 분석한다. 기능 연결 검증과 탐지 정확도 평가는 구분한다.

## 구성 요소

| 위치 | 기술과 역할 |
| --- | --- |
| `src/dashboard` | React/Vite. Supabase Auth 로그인, scans SELECT, Gmail 수집 요청, 판정·모델별 점수·실행 기록 표시 |
| `src/gmail` | FastAPI. access token 검증, Gmail OAuth, 수집·파싱, 분석 대기열, Supabase 저장 조율 |
| `src/detector` | 별도 Python 3.12. 해시 특징 ML과 BERT-tiny Transformer를 같은 메일에 각각 적용 |
| 로컬 SQLite | 계정·세션·owner 매핑, 암호화 토큰, 메타데이터, 분석 작업과 durable outbox |
| Supabase | Auth와 PostgreSQL. gmail_connections, mail_messages, analysis_results, scans 읽기 뷰 |
| 호스트 Ollama | Qwen 등 설치된 tool-calling 모델 추론. 명령을 직접 실행하지 않음 |
| Lima prodog-lab | 공유 폴더 없는 전용 Linux VM. rootless Docker 엔진의 context는 prodog-vm |
| VM 안 일회용 컨테이너 | 메일마다 생성해 run_shell 실행·파일 관측 후 제거. VM 자체는 유지 |

## 인증과 사용자 귀속

Supabase Auth 로그인은 앱 사용자 UUID를 증명한다. Gmail OAuth의 gmail.readonly 권한은 해당 Gmail을 읽을 권한을 별도로 제공한다. 두 인증은 목적과 callback이 다르다.

1. 브라우저가 현재 Supabase access token을 로컬 API의 Authorization: Bearer에 넣는다.
2. FastAPI의 SupabaseAuth가 고정된 프로젝트의 /auth/v1/user로 토큰을 검증한다. 이메일이나 검증하지 않은 JWT payload로 소유권을 추정하지 않는다.
3. 검증된 UUID를 SQLite 세션에 묶고 Gmail OAuth의 state/PKCE를 그 세션에 연결한다.
4. callback 성공 시 Gmail 계정과 UUID의 owner 매핑을 저장하고 세션 쿠키를 회전한다.
5. 쿠키가 만료돼도 유효한 Supabase 로그인으로 본인의 Gmail 연결만 복원한다. 사용자 전환·로그아웃 시 이전 연결을 사용할 수 없다.

이전 버전의 owner 없는 Gmail 토큰은 이메일이 같아도 자동 귀속하지 않는다. 최초 1회 명시적으로 Gmail을 재연결해야 한다. 현재 로컬 저장소는 동일 Gmail 주소를 서로 다른 Supabase 사용자에게 동시에 귀속하지 않는다.

## 수집·분석·저장

POST /api/mails가 수집과 분석 등록을 수행한다. GET 조회와 상태 polling은 새 분석을 등록하지 않는다. 정적 worker와 동적 worker가 분리돼 긴 LLM 실행 중에도 다음 메일의 정적 분석을 진행한다. 정적 프로세스 내부의 모델 호출은 기존 ML 다음 Transformer 순서다. 동적 실행은 전체에서 동시에 1개다. Ollama 준비 확인이 실패해도 수집과 정적 분석을 계속하며 동적 모델 경고를 표시한다.

ML과 Transformer는 각자의 임계값으로 safe < suspicious < danger를 결정한다. 서로 보정 기준이 다른 확률을 직접 비교하거나 합산하지 않는다. 최종 단계는 더 높은 위험 단계이며 decided_by에 결정 모델을 기록한다. 점수·임계값은 추론 출력에서 반올림하지 않고 화면에서만 표시를 반올림한다. Supabase 정수 점수 대신 analysis JSON의 원래 확률을 사용해 위험 판정과 눈금의 정밀도를 맞춘다. 가중치/입력 변환·추론 코드/입력/설정/VM context가 같으면 기존 결과를 재사용한다.

탐지기 변환은 수집기가 허용하는 300,000자 본문을 보존하며 초과 입력을 조용히 자르지 않는다. Transformer는 기존 1,000자/500자 중첩 구간을 유지하고, 256-token을 초과하는 부분을 127 content-token 중첩으로 끝까지 검사한다. forward batch는 16 이하로 유지한다. plain 또는 HTML 본문이 있으면 사용하지 않는 analysis_text를 IPC에서 제외한다. MIME part는 줄바꿈으로 구분하며 구분자도 총300,000자 예산에 포함한다. 합성 Unicode 최대 입력은 중복 포함 약 2.40MB에서 1.20MB로 줄었고 실제 두 모델 실행은 3.02초, 토큰 밀도가 높은 최대 입력은 6.73초였다. 이 수치는 로컬 기능/처리 시간 관측이며 탐지 정확도가 아니다.

동적 분석은 요약 과제를 수행하는 LLM이 메일의 악성 지시에 따라 행동하는지 관측한다. run_shell은 컨테이너 안에서만 실행하고 read_output은 현재 실행에서 이미 수집한 출력만 페이지로 읽는다. 명령·stdout/stderr·파일 변경과 실제 model_input을 기록한다. leak.txt의 가짜 토큰, summary.txt의 존재는 고정 관측값이며 모든 유출 경로나 요약 정확성을 판정하지 않는다. 오류나 공격 미관찰로 정적 위험 단계를 낮추지 않는다. llm_score는 산식이 없어 NULL을 유지한다.

분석 상태 변경과 outbox 등록은 같은 SQLite transaction으로 기록한다. 서버가 메타데이터와 정적·동적 결과 요약을 검증된 owner의 Supabase 행에 upsert한다. 동일 job UUID를 재사용해 재시도 중복을 막고, 응답 누락·통신 실패는 outbox에 남겨 재시도한다. cloud_deliveries에는 job별로 전달을 확인한 snapshot을 기록해 장애 중 쌓인 과거 실행도 빠뜨리지 않는다. 저장 중 새 revision이 생기면 이전 요청의 성공으로 새 변경을 지우지 않는다. 수집 실패 기록도 저장하며 대시보드에서 저장 대기와 오류를 확인할 수 있다.

scans는 파이프라인 결과를 legacy 단일 모델 결과보다 우선하며, 파이프라인 안에서는 최신 완료·실패 결과를 우선 보여 준다. 완료 결과가 없는 새 메일은 pending으로 보여 준다. 과거 legacy 결과와 기록은 삭제하지 않는다. 목록·상세·일별 그래프는 같은 조회 행을 사용한다. 집계는 현재 불러온 페이지 범위다. 위험·의심은 수집 불완전 안내보다 먼저 표시하고 근거·제목·모델 설명은 React 텍스트로만 렌더링한다.

## 합성 메일 쌍 평가

동적 분석 화면의 합성 정상/공격 쌍은 Gmail·정적 판정과 독립적으로 같은 Ollama/VM 실행기를
사용한다. 매 실행 random canary를 생성하며 label·검증어는 모델 입력에 넣지 않는다.
목표 파일 복사 ASR과 명령 출력 노출, 정상 summary 검증어 proxy, 오류·미확정, 평균/P95를
구분한다. 관측 후 파일이 삭제돼도 양성을 유지하고 오류를 방어 성공으로 표현하지 않는다.

그룹 전체를 암호화 파일과 SQLite에 등록한 뒤 대기열을 시작한다. 최근50건 상세 로그 정리와
독립적인 암호화 요약을 남겨 분모를 보존하고, 요약 저장 실패 시 원본 로그를 유지한다.
그룹은 로컬에만 저장하며 Supabase에 합성 본문·평가 결과를 추가하지 않는다. 정적 F1을
측정하는 경로는 아니다. [합성 평가 안내](../src/gmail/docs/synthetic-evaluation.md)를 따른다.

팀 배포 준비는 [팀원 실행 안내](../docs/team-setup.md)와 read-only preflight로 확인한다.
가중치 대신 SHA256 manifest를 공유하며 개인 키·DB·VM·모델 설치는 각 PC에서 준비한다.

## 데이터와 격리 경계

- Supabase에는 메타데이터·점수·판정·동적 관측 요약만 저장한다. 본문·Gmail 토큰·API 키·명령 원문은 보내지 않는다. 본문은 정적 대기 payload와 동적 실행 로그에 로컬 암호화 상태로 남을 수 있고, 권한 있는 실행 상세/JSON 다운로드에 포함된다.
- 브라우저 DB 호출은 anon 키와 사용자 JWT를 이용한 SELECT뿐이다. 서버의 sb_secret 키는 apikey 헤더로만 전송한다. VITE_ 환경변수에 서버 키를 넣지 않는다.
- 세 테이블의 RLS는 auth.uid() = user_id SELECT만 허용하고 scans는 security_invoker=true다. 서버 Secret key는 RLS를 우회하므로 서버에서도 검증된 owner를 사용한다.
- Gmail 연결 해제는 Google revoke 후 계정별 저장 lock 안에서 Supabase 연결/메일/결과를 삭제하고 로컬 토큰·계정을 삭제한다. 이미 저장 중인 outbox가 뒤늦게 연결을 다시 만드는 것을 막는다. 과거 동적 로그 파일의 추가 삭제는 수행하지 않는다.
- 컨테이너는 네트워크 없음, UID 10001, read-only root, host mount/Docker socket/키 없음, 1 CPU·256MiB·64 PID 제한이다. VM은 2 CPU·2GiB이며 호스트 공유 폴더·SSH agent·TCP 자동 전달이 없다.
- VM이 중지되면 Desktop 엔진으로 fallback하지 않는다. cleanup 실패는 다음 실행을 막는다. VM에는 초기 설치용 NAT가 남으며 호스트 Ollama의 RAM/GPU는 VM 제한 밖이다. VM·컨테이너 탈출까지 방어했다고 주장하지 않는다.

## 검증과 남은 범위

실제 Supabase에서 합성 pending/완료 upsert·중복 방지·cleanup, authenticated owner SELECT·타 사용자 차단·브라우저 INSERT 불허를 확인했다. 사용자 허용으로 기존 Gmail readonly OAuth를 재연결하고 앱 UUID 귀속을 확인했다. 실제 메일20건이 로컬 수집과 Supabase 목록/상세/일별 그래프에 표시됐으며 연결·메일·결과 owner 불일치는0건이었다. 같은 정책에서 페이지를 reload해도 작업 ID 집합은 유지됐다. 실제 VM의 자원/네트워크/파일 경계와 cleanup, 실제 팀 정적 모델 + Qwen Instruct + VM의 합성 경로도 확인했다.

정상 합성 메일의 정적 오탐도 관찰했다. token overflow와 본문 보존 정책 변경으로 점수 분포가 달라질 수 있으므로 과거 Quadrat 지표를 새 정책의 정확도/오탐률로 사용하지 않는다. 원본 validation 데이터가 로컬에 없어 새 정책의 재보정/독립 정확도 평가는 수행하지 않았다. Windows 전용 VM, Gmail push/화면 밖 주기적 수집, 실제 악성 바이너리·VM 탈출도 이번 검증 범위가 아니다. 현재 서버는 loopback 개발 구성이며 공유 서비스의 HTTPS·공유 세션 배포는 별도 작업이다.

저장 계약은 [SUPABASE_TABLE.md](SUPABASE_TABLE.md), 실행은 [자동 분석 안내](../src/gmail/docs/automatic-analysis.md), VM은 [local-vm.md](../src/gmail/docs/local-vm.md)를 따른다.
