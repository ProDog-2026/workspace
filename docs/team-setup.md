# 팀원 pull 후 로컬 실행

대상은 React 대시보드 + Gmail 수집 + 팀 정적 모델 2개 + Ollama 자동 분석이다.
소스만 pull하면 가중치, `.env`, Python 환경, Docker 이미지, Ollama 모델은 생기지 않는다.
아래 준비 명령은 팀원이 자기 PC에서 실행하는 절차다. 사전 점검 스크립트는 준비 작업을
대신 실행하지 않는다.

## 1. 받을 브랜치와 먼저 확인할 것

이 변경이 포함된 **이지훈의 `feat/jihun` push를 확인한 뒤** 해당 브랜치를 받는다.
`develop`에 머지되기 전에는 `develop`만 pull해서 같은 기능을 기대하지 않는다.
새 폴더에 받는 예시:

```sh
git clone --branch feat/jihun https://github.com/ProDog-2026/workspace.git
cd workspace
git status --short
```

기존 checkout은 먼저 `git status --short`로 확인한다. 수정·미추적 파일이나 로컬 `uv.lock`
변경이 있으면 그대로 보존하고, 덮어쓰는 reset/clean/pull이나 브랜치 전환 대신 별도 폴더에
clone한다. 깨끗하고 이미 `feat/jihun`인 checkout만 `git pull --ff-only origin feat/jihun`한다.
PR이 `develop`에 머지된 뒤에는 깨끗한 checkout에서 `git switch develop`과
`git pull --ff-only origin develop`을 사용한다. 이 절차에 commit/push는 필요 없다.

저장소 루트에서 **이미 설치된 Python**으로 먼저 점검한다. uv로 실행하면 자동 환경 준비가
개입할 수 있으므로 점검 자체에는 `uv run`을 쓰지 않는다.

```sh
# macOS / Linux (Python 3.11 이상)
python3 -I -S -B src/gmail/scripts/check_team_setup.py --help
python3 -I -S -B src/gmail/scripts/check_team_setup.py
```

```powershell
# Windows PowerShell: Python Launcher와 Python 3.12가 설치된 경우
py -3.12 -I -S -B src/gmail/scripts/check_team_setup.py --help
py -3.12 -I -S -B src/gmail/scripts/check_team_setup.py
$LASTEXITCODE
```

`--help`는 프로젝트 의존성·설정 없이 동작한다. 일반 실행은 필수 항목이 모두 준비되면
종료 코드 **0**, 하나라도 없거나 확인할 수 없으면 **1**이다. 새 clone에서는 실패가 정상이다.
항목 이름과 OK/FAIL만 출력하며 키·이메일·설정값·외부 명령의 오류 원문은 출력하지 않는다.
모델은 파일 존재·크기와 저장소 SHA256 manifest 일치를 확인하며 로드하지 않는다.

점검은 Python 패키지 메타데이터/버전, Node·npm·uv 버전, 선택한 Docker 엔진과 이미지,
로컬 Ollama의 `/api/tags` 및 `/api/show`만 읽는다. 설치·다운로드·모델 추론·컨테이너 실행·
DB/메일 접근·설정 변경은 없다. Docker 명령은 각각 최대 8초, Ollama 요청은 각각 3초다.
엔진 소켓/로컬 포트 접근이 제한된 도구 안에서는 FAIL일 수 있으므로 본인 터미널에서도 확인한다.

## 2. 런타임과 의존성 준비

| 구분 | 현재 코드의 요구 / 사용할 환경 |
| --- | --- |
| Gmail 서버 | `src/gmail/pyproject.toml`은 Python **>=3.11**. 현재 Mac 실행 환경은 **3.14.7**지만 신규 설치는 **3.12**를 사용해 고정 sklearn의 wheel 호환 부담을 줄인다. 3.14 전용 앱이 아니다. |
| 팀 정적 탐지 | 별도 `src/detector/.venv`, **Python 3.12**. 탐지 패키지 자체의 선언은 >=3.10이지만 팀 모델 재현 경로는 3.12다. scikit-learn **1.6.0**, PyTorch >=2.7,<3, Transformers >=4.48,<5. |
| 대시보드 | Node **>=22.15.0**, npm. Node 22.14는 부족하다. |
| 자동 동적 분석 | Linux Docker 엔진 + `prodog-experiment:local`, 호스트에서 실행 중인 Ollama와 도구 호출 지원 모델. |

Python/uv/Node/Docker/Ollama 설치는 각자 OS에 맞게 먼저 완료한다. Gmail과 탐지기는 같은
Python 3.12를 선택해도 **각 폴더의 `.venv`는 분리**한다. 저장소 루트에서:

```sh
cd src/gmail
uv sync --frozen --python 3.12 --dev
cd ../detector
uv sync --python 3.12 --extra transformer --extra dev
cd ../dashboard
npm ci
cd ../..
```

PowerShell에서도 같은 `cd`, `uv`, `npm` 명령을 사용할 수 있다. 탐지기의 `dev`는 optional
extra이므로 `--extra dev`다. Gmail의 `dev`는 dependency group이라 `--dev`다.
탐지기는 현재 추적된 `uv.lock`이 없어 로컬 lock을 생성한다. Gmail의 기존 lock은 보존한다.
`--frozen` 설치가 실패하면 공유된 소스/lock 조합과 오류를 담당자에게 확인하고 lock을 임의로
재생성하지 않는다. [uv의 frozen·sync 동작](https://docs.astral.sh/uv/concepts/projects/sync/).

## 3. 팀 가중치는 별도 공유 + SHA256 확인

현재 출처는 저장소의 [자동 분석 안내](../src/gmail/docs/automatic-analysis.md)에 기록된
[팀 모델 공유 페이지](https://app.notion.com/p/40714dd1c3c48153b351f8fd6c01855d)다.
담당자가 공유한 `feature/31-transformer-ensemble` / `3e8968e` 유래의 동일 릴리스 파일과
**담당자가 확인한 파일별 SHA256 목록**을 별도 경로로 받는다. 여기서 공유 페이지 접근 권한이나
실제 배포 파일의 진위를 확인한 것은 아니다. 공식 upstream의 모델로 임의 대체하면 팀 학습
가중치·임계값이 달라진다. 이번 로컬 검증에 사용한8개 파일의 실제 SHA256은
[`src/detector/model-artifacts.sha256`](../src/detector/model-artifacts.sha256)에 기록했다.
이는 검증 파일 버전의 식별값이며 원격 공유 페이지의 진위나 배포 서명을 보증하지 않는다.

압축을 풀었다면 디렉터리 중첩 없이 다음 위치에 놓는다:

```text
src/detector/models/
  prompt_injection.joblib
  quadrat-transformer/
    config.json
    model.safetensors
    prodog_transformer.json
    special_tokens_map.json
    tokenizer.json
    tokenizer_config.json
    vocab.txt
```

파일별 SHA256은 저장소 루트에서 다음처럼 구한다. macOS/Linux:

```sh
shasum -a 256 src/detector/models/prompt_injection.joblib src/detector/models/quadrat-transformer/*
```

Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath src/detector/models/prompt_injection.joblib
Get-ChildItem -File src/detector/models/quadrat-transformer | Get-FileHash -Algorithm SHA256
```

위 8개 파일이 모두 있고 **각 해시가 담당자의 신뢰할 수 있는 목록과 일치하는지** 비교한다.
Linux에서 `shasum`이 없으면 동일 인수로 `sha256sum`을 쓴다. 받은 파일에서 계산한 해시끼리만
비교하는 것은 출처 검증이 아니다. 기대 목록이 없거나 값이 다르면 모델을 로드하지 말고
담당자에게 확인한다. joblib은 로드 중 Python 코드 실행이 가능하다.
[scikit-learn 모델 영속화 보안·버전 제약](https://scikit-learn.org/1.6/model_persistence.html).

`**/models/`는 `.gitignore` 대상이다. 가중치·credentials·메일·로컬 설정을 Git에 올리거나
`git add -f`로 우회하지 않는다. preflight의 `Team model files` OK는 해시 일치나 추론 성공을
뜻하지 않는다. `Team model SHA256`도 OK여야 이번 검증 파일 버전과 일치한다.

## 4. 각자 로컬 설정과 로그인 경계

새 checkout에서만 `src/gmail`로 이동해 다음을 실행한다:

```sh
# macOS / Linux
.venv/bin/python scripts/setup_local.py
```

```powershell
# Windows
.venv/Scripts/python.exe scripts/setup_local.py
```

기존 스크립트는 `.env`가 없을 때만 복사·Fernet 키 생성하며 기존 파일을 보존한다.
기존 `TOKEN_ENCRYPTION_KEY`를 교체하면 저장된 토큰/대기 payload를 해독할 수 없으므로
재생성하지 않는다. 개인 DB·토큰·키를 다른 팀원에게 복사하지 않는다.

설정 파일은 각자 로컬 편집기로 채운다. 키는 승인된 팀 공유 경로로 받고 채팅/스크린샷에 붙이지 않는다.

| 파일 | 필요한 항목 |
| --- | --- |
| `src/gmail/.env` | `APP_MODE=google`, `APP_BASE_URL=http://127.0.0.1:8765`, `DASHBOARD_URL=http://127.0.0.1:5173`, 생성된 `TOKEN_ENCRYPTION_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` |
| 같은 서버 파일 | 같은 프로젝트의 `SUPABASE_URL`, `SUPABASE_ANON_KEY`, 서버 전용 `SUPABASE_SERVICE_KEY` (`sb_secret_…` 또는 legacy service_role). `SUPABASE_USER_ID` 고정값은 사용하지 않는다. |
| 같은 서버 파일 | `EXPERIMENTS_ENABLED=true`, 준비한 `SANDBOX_DOCKER_CONTEXT`, 설치된 `OLLAMA_MODEL` 이름. 외부 AI API 키는 자동 Ollama 경로의 필수값이 아니다. |
| `src/dashboard/.env.local` | `.env.example`을 참고해 같은 프로젝트의 `VITE_SUPABASE_URL`, **legacy anon JWT**인 `VITE_SUPABASE_ANON_KEY`. 현재 `browserKey()`는 publishable 형식도 거절한다. 서버/service_role 키와 Google secret은 `VITE_`에 넣지 않는다. |

기본 탐지 경로는 위 모델 배치와 탐지기 `.venv`를 OS에 맞춰 선택한다. 별도 위치를 쓸 때만
서버 파일의 `STATIC_PYTHON`, `STATIC_MODEL_PATH`를 지정한다. 상대 경로는 **`src/gmail` 기준**이며
Transformer는 joblib과 같은 부모 아래 `quadrat-transformer/`에 둔다.
Windows 예: `STATIC_PYTHON=../detector/.venv/Scripts/python.exe`.
`DETECTOR_MODEL_PATH`는 옛 동기 단일 모델 경로다. 자동 ensemble에는 `STATIC_MODEL_PATH`를
쓰며, 새 설정에서 `DETECTOR_MODEL_PATH`는 비워 둔다.

preflight는 한 줄 `KEY=value`, 단순 따옴표, `export`, 주석만 지원한다. 변수 보간(`$`),
여러 줄 값·복잡한 escape는 지원하지 않아 FAIL로 알린다. 셸에 이미 있는 환경변수는 파일보다
우선하며, 프론트 파일은 Vite dev 순서 `.env` → `.env.local` → `.env.development` →
`.env.development.local`로 읽는다. 값 자체를 출력해 디버깅하지 않는다.

**앱 Google 로그인과 Gmail 읽기 권한 연결은 별도다.** 앱은 Supabase PKCE 로그인 후
`http://127.0.0.1:5173/?auth_callback=1`로 돌아온다. Supabase Google provider와 허용 redirect는
프로젝트 관리자가 설정한다. Google Cloud에 등록하는 앱 로그인 callback은
`https://<project-ref>.supabase.co/auth/v1/callback`이다.
[Supabase 공식 Google 로그인 안내](https://supabase.com/docs/guides/auth/social-sign-in/auth-google).

Gmail 수집 OAuth callback은 **`http://127.0.0.1:8765/oauth/google/callback`**이다.
Google OAuth Testing 상태라면 각자 계정을 테스트 사용자로 등록해야 한다.
상세 절차는 [Google 설정](../src/gmail/docs/google-setup.md)을 따른다.
서버는 앱 access token을 검증해 Gmail 연결을 사용자 UUID에 귀속한다. 설정된 Supabase Auth는
`APP_MODE=demo`에서도 적용되며, `/api/session` 외 API는 앱 로그인이 필요하다.
`npm run dev`는 개발 서버 실행일 뿐 인증 우회 모드가 아니다.

**공유 Supabase의 기존 스키마 변경은 이미 적용됐다. 팀원은 migration을 재실행하지 않는다.**
[`arch/supabase-pipeline-integration.sql`](../arch/supabase-pipeline-integration.sql)은 기존
3개 테이블에 `analysis` JSONB와 exact-email unique index를 추가하고 `scans` 뷰를 갱신하는
additive 변경이다. 빈 DB bootstrap이 아니다. 새 개인 프로젝트라면 담당자와
[`arch/SUPABASE_TABLE.md`](../arch/SUPABASE_TABLE.md)의 기초 스키마·RLS·뷰 계약부터 확인한다.
옛 `src/dashboard/schema.sql`의 `CREATE TABLE scans`도 실행하지 않는다.
브라우저는 자기 결과 SELECT, 서버만 결과 쓰기를 수행하며 preflight는 cloud/schema를 조회하지 않는다.

## 5. Docker 대상과 Ollama

**실제 격리를 검증한 경로는 Apple Silicon Mac의 Lima/VZ뿐**이다.
[Mac VM 안내와 검증 기록](../src/gmail/docs/local-vm.md)을 따른다. 준비 스크립트는
VM/이미지 생성과 `.env` 변경을 하므로 preflight가 자동 실행하지 않는다.
`SANDBOX_DOCKER_CONTEXT=prodog-vm`일 때 읽기 전용 확인:

```sh
docker --context prodog-vm info --format '{{.OSType}}'
docker --context prodog-vm image inspect prodog-experiment:local --format '{{json .Config.Volumes}}'
```

Linux 엔진과 선언 볼륨 없는 로컬 이미지가 필요하다. 이미지를 아직 만들지 않았다면 해당
Docker context의 엔진을 확인한 뒤 `src/gmail`에서 명시적으로 준비한다:

```sh
docker --context prodog-vm build -t prodog-experiment:local sandbox
```

Windows Docker Desktop은 Linux 컨테이너 모드, Linux 전용 VM은 해당 VM의 context를 사용한다.
위 명령의 `prodog-vm`을 **실제로 준비한 context 이름**으로 바꾸고 서버 설정과 맞춘다.
빈 `SANDBOX_DOCKER_CONTEXT`는 기존 Docker CLI 환경(`DOCKER_CONTEXT`/`DOCKER_HOST`/기본 설정)을
따른다. `docker context use`로 전역 대상을 바꿀 필요가 없다.
[Docker context 공식 명령](https://docs.docker.com/reference/cli/docker/context/).

Windows Docker Desktop/Linux VM은 인터페이스상 호환 경로이며 **이번 작업에서 실기 검증하지
않았다**. Mac용 `setup_local_vm.py`를 Windows에서 실행하지 않는다. 각 PC에서 공유 드라이브,
소켓/자격 증명 전달, 포트 자동 전달, 비루트·네트워크·자원 제한·정리를 별도 검증해야 한다.
preflight OK는 그 격리 검증을 대신하지 않는다.

Ollama는 호스트 `127.0.0.1:11434`에서 실행하고 [기존 준비 안내](../src/gmail/docs/dynamic-analysis.md)의
도구 지원 Instruct 모델을 별도로 준비한다. 설치된 모델에 `tools` capability가 있어야 하고
`thinking.values=[true]`인 추론 전용 모델은 자동 설정에서 거절한다. preflight는 설정된
`OLLAMA_MODEL`을 검사하며, 비어 있으면 첫 설치 모델의 호환성을 확인한다. 이후 화면에서
모델 선택·자동 분석 사용을 저장해야 한다. 자동 다운로드·외부 API fallback은 없다.
[Ollama 로컬 모델 목록 API](https://docs.ollama.com/api/tags).

## 6. 실행과 합성 데모 구분

전체 준비 후 루트에서 preflight를 다시 실행해 종료 코드 0인지 확인한다.
다음 두 터미널을 각각 연다. 기존 서버를 쓰고 있다면 해당 담당자와 재시작을 조율한다.

```sh
# 터미널 1, src/gmail에서 (macOS / Linux)
.venv/bin/python -m uvicorn prodog_gmail.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

```powershell
# 터미널 1, src/gmail에서 (Windows)
.venv/Scripts/python.exe -m uvicorn prodog_gmail.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

```sh
# 터미널 2, src/dashboard에서 (공통)
npm run dev
```

`http://127.0.0.1:5173/#/` → 앱 로그인 → 설정에서 Gmail 읽기 권한 연결 → 자동 분석 모델 선택·
사용 켜기·저장 순서다. 실제 메일 수집은 각자 별도 동의한 뒤 수행한다. 주소는 `localhost`와
혼용하지 않는다. 앱 로그아웃은 Gmail grant 철회와 다르며 연결 해제는 별도 기능이다.

계정 없이 **합성 수집 데모만** 보려면 별도 새 checkout의 Gmail `.env`에서 `APP_MODE=demo`,
Supabase 세 항목과 `DETECTOR_MODEL_PATH`를 빈 값, `EXPERIMENTS_ENABLED=false`로 두고
위 Gmail 서버만 실행한다. `http://127.0.0.1:8765`의 데모 연결을 사용한다. 이 경우 React 앱
로그인·공유 DB·자동 ensemble/VM 실행은 검증하지 않는다. 전체 팀 구성을 검사하는 preflight는
이 제한 데모 구성에서 FAIL하는 것이 맞다. 기존 Google 연결 환경을 데모로 덮어쓰지 않는다.

소스만으로 가능한 테스트 (`src/gmail`, 실제 메일/VM/클라우드 opt-in 없이):

```sh
# macOS / Linux
.venv/bin/python -m pytest tests/test_team_setup.py -q
# Windows
.venv/Scripts/python.exe -m pytest tests/test_team_setup.py -q
```

실제 OAuth 동의·프로젝트 Google provider·RLS/스키마·추론 정확도·VM 격리와 Windows 실기는
사전 점검의 검증 범위 밖이다. 모델/설정이 없으면 검사 전·오류로 남으며 정상 메일 판정으로
해석하지 않는다.
