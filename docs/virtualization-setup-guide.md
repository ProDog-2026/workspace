# 가상화 환경 구성 지시서

작성 기준: 2026-11-01, `feat/jihun`의 현재 코드와 아래 공식 문서.
이 문서는 **Windows PC에서 작업할 AI에게 그대로 전달하는 실행 지시서**다.
프로젝트 폴더에서 명령을 실행할 수 있는 Codex·Claude 등의 에이전트에게 아래 요청과 함께 전달한다.
파일을 첨부하는 것만으로 설치가 실행되지는 않는다. 관리자 승인·재부팅·계정 로그인은 필요한 경우
사용자가 직접 진행하고, AI는 이어서 설정과 검증을 수행한다.
문서 작성 환경은 Mac이므로 **Windows 실기 검증 완료를 뜻하지 않는다.**

## AI에게 전달할 요청

> 이 저장소의 `docs/virtualization-setup-guide.md`를 읽고 내 Windows PC에 동적 분석 개발 환경을 구성해 줘.
> 현재 받은 코드를 기준으로 진행하고 develop 등 다른 브랜치로 임의 전환하지 마.
> 현재 상태를 먼저 확인하고, 이미 준비된 항목은 재설치하지 마.
> 기본 목표는 Windows의 Docker Desktop·WSL 2와 로컬 Ollama로 합성 메일 테스트를 실행하는 것이야.
> 설치·설정·검증을 진행하고, 재부팅이나 사람이 직접 조작해야 하는 부분만 설명해 줘.
> 기존 파일·컨테이너·다른 개발 환경을 보존하고 커밋·푸시는 하지 마.
> 실제로 실행해 확인한 항목과 아직 확인하지 못한 항목을 나눠 보고해 줘.

## 1. 목표와 구성

```text
Windows 호스트
  React 대시보드 :5173
  Gmail/FastAPI·분석 제어기 :8765
  Ollama 추론 :11434
        │ Docker CLI, 메일은 stdin으로 전달
        ▼
Docker Desktop의 WSL 2 Linux 엔진
  └─ 메일마다 새 제한 컨테이너 생성 → 실행·관측 → 제거
```

기본 경로는 **Docker Desktop의 WSL 2 백엔드**다. Windows 안의 Linux 실행 환경을 사용하지만,
Mac의 공유 폴더 없는 전용 Lima VM과 같은 구성이라고 보고하면 안 된다.
WSL의 호스트 파일 접근·통합 기능과 컨테이너의 마운트 제한은 서로 다른 경계다.
[Docker의 WSL 2 구성·보안 설명](https://docs.docker.com/desktop/features/wsl/).

앱·Python·Node·Ollama는 **Windows에서 실행**한다. 프로젝트를 WSL 안으로 옮기거나
WSL 안에 별도 Docker Engine·Ollama를 중복 설치하지 않는다. Docker Desktop은 특정 Ubuntu
배포판 설치 없이 Windows 터미널에서 사용할 수 있다.
[Docker WSL 2 안내](https://docs.docker.com/desktop/features/wsl/).

호스트 공유 없는 **별도 전용 VM**이 필수라는 요구가 있으면 이 기본 경로의 차이를 먼저 설명한다.
별도 VM은 공유 드라이브·클립보드·SSH agent·포트 전달을 따로 구성·검증해야 하며, 이 문서는
그 VM의 설치 자동화를 제공하지 않는다. Windows에서 Mac용 `scripts/setup_local_vm.py`,
`sandbox/lima.yaml`, `limactl`을 실행하거나 `prodog-vm`을 있다고 가정하지 않는다.

완료 기준은 다음과 같다.

- 실제 선택한 Linux Docker 엔진에 프로젝트 이미지가 준비됨.
- 비루트·읽기 전용 루트·네트워크 없음·마운트·자원 제한을 확인함.
- 합성 메일 명령 실행과 파일 관측, 취소·출력 제한 후 컨테이너 제거를 확인함.
- 실제 로컬 Ollama를 사용한 합성 메일 실행과 대시보드 결과 조회를 확인함.

Gmail 수집·정적 모델·Supabase 연결은 마지막 확장 단계다. 합성 테스트에는 실제 메일과
서비스 키가 필요하지 않다. 처음부터 Gmail 권한·공유 DB를 건드리지 않는다.

## 2. AI의 작업 원칙

1. 받은 checkout과 `git status --short`, 현재 브랜치를 먼저 확인한다. 이번 작업을 위해
   임의로 `develop`으로 전환하거나 기존 수정 사항을 reset/clean/stash하지 않는다.
2. 아래 명령은 **PowerShell** 기준이다. 저장소 루트를 `$Repo`에 실제 절대 경로로 저장한다.
   `$HOME`, `$env:USERPROFILE` 같은 기존 시스템 변수를 다른 용도로 재정의하지 않는다.
3. 명령은 단계별로 실행하고 외부 명령 직후 `$LASTEXITCODE`를 확인한다.
   PowerShell의 `$ErrorActionPreference = 'Stop'`만으로 모든 외부 명령 실패가 중단되는 것은 아니다.
4. 없는 프로그램은 공식 배포 경로로 설치한다. 기존 WSL·Docker·Python 환경을 제거하지 않는다.
   기존 설치와 충돌하면 원인과 선택지를 설명한다. 자동 재부팅이나 `wsl --shutdown`으로
   다른 작업까지 종료하지 않는다. 재부팅이 필요하면 완료 단계와 재개 위치를 남긴다.
5. `.env`, `.env.local`, 암호화 키, 사용자 DB, 모델 파일은 보존한다. 키·토큰·실제 메일을
   터미널·AI 답변·보고서·Git에 출력하지 않는다. 다른 팀원의 개인 `.env`나 DB를 복사하지 않는다.
6. Docker 전역 context·전역 환경변수를 바꾸지 않는다. 설치 프로그램이 기본 대상을 바꿨다면
   전후 상태를 기록하고, 기존 작업에 영향이 있는지 확인한다.
7. 테스트에 필요한 명령은 프로젝트의 `Sandbox`를 사용한다. 임의의 특권 컨테이너,
   호스트 폴더/소켓 마운트, `--network host`, 포트 공개, 실제 자격 증명 전달을 추가하지 않는다.
8. 실패를 숨기려고 제한·인증·검증을 끄거나 의존성 lock을 재생성하지 않는다.
   코드 수정이 필요한 호환성 문제라면 원인과 최소 변경을 별도로 제시한다.
9. 커밋·푸시·PR 생성·공유 DB 변경은 이 환경 구성 작업에 포함하지 않는다.

기존 checkout에 실제 계정 설정이 있다면 **별도 새 폴더**를 사용한다. 새로 받을 경우의 예:

```powershell
git clone --branch feat/jihun https://github.com/ProDog-2026/workspace.git prodog-windows-lab
if ($LASTEXITCODE -ne 0) { throw 'clone 실패: 권한과 폴더를 확인하세요.' }
Set-Location .\prodog-windows-lab
$Repo = (Get-Location).Path
git status --short
git branch --show-current
```

이미 최신 소스를 받았다면 clone/pull은 생략하고 그 저장소에서 `$Repo`만 정한다.
원격 브랜치명이나 현재 PR의 머지 상태를 추측해 기존 작업을 전환하지 않는다.

## 3. Windows와 필수 프로그램 확인

먼저 읽기 전용으로 확인한다. 없는 명령은 설치 필요 항목으로 기록한다.

```powershell
$ErrorActionPreference = 'Stop'
[System.Environment]::OSVersion.VersionString
[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
Get-PSDrive -PSProvider FileSystem | Select-Object Name, Free
wsl --status
wsl --version
wsl --list --verbose
git --version
uv --version
node --version
npm.cmd --version
docker version
ollama --version
```

Docker Desktop의 현재 Windows·WSL·가상화 요구 조건을 해당 PC의 에디션/빌드와 대조한다.
지원되지 않는 Windows나 Windows on ARM이면 일반 x64 절차를 성공했다고 적용하지 않는다.
Ollama와 모델 파일까지 고려해 메모리·여유 디스크를 확인하되, 확인하지 않은 모델 속도를 약속하지 않는다.
[Docker Windows 설치 조건](https://docs.docker.com/desktop/setup/install/windows-install/).

필요한 프로그램:

| 항목 | 준비 방법 |
| --- | --- |
| Git | 기존 설치 우선 사용. 없으면 Git 공식 Windows 배포 경로 사용 |
| uv | [공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/). Python 3.12는 아래 `uv python install`로 준비 가능 |
| Node/npm | 저장소 요구는 Node **22.15.0 이상**. 현재 지원 LTS 중 이 조건을 만족하는 버전 사용 |
| Docker Desktop | [공식 Windows 설치 안내](https://docs.docker.com/desktop/setup/install/windows-install/), WSL 2 기반 Linux 엔진 |
| Ollama | [공식 Windows 안내](https://docs.ollama.com/windows), Windows 호스트에 설치 |

WSL이 없다면 사용자가 관리자 PowerShell에서 다음을 실행하고 필요 시 재부팅하도록 안내한다.
이미 설치돼 있다면 상태를 확인해 필요한 업데이트만 진행한다.

```powershell
# 관리자 권한이 필요한 Windows 준비 단계
wsl --install
```

`wsl --install`은 기본 Linux 배포판도 설치할 수 있다. 이 프로젝트 앱을 그 배포판 안으로
옮길 필요는 없다. 기존 배포판을 unregister하거나 초기화하지 않는다.
[Microsoft WSL 설치 안내](https://learn.microsoft.com/en-us/windows/wsl/install).

Docker Desktop을 실행하고 WSL 2 엔진·Linux 컨테이너 모드인지 확인한다. 설치 방식에 따라
`Use WSL 2 based engine` 옵션이 기본 적용돼 별도로 보이지 않을 수 있다.
WSL 내부에 이미 별도 Docker Engine이 있다면 자동 제거하지 말고 어느 엔진을 쓸지 정리한다.
[Docker WSL 2 설정](https://docs.docker.com/desktop/features/wsl/).

## 4. 실행 대상 고정과 이미지 준비

다음 작업은 저장소 루트의 **일반 PowerShell**에서 한다.

```powershell
Set-Location $Repo
docker context ls
```

목록에서 이 PC의 Docker Desktop **Linux 엔진**을 가리키는 실제 context를 선택한다.
아래 `desktop-linux`는 예시다. 없으면 다른 확인된 로컬 context를 사용한다.
`prodog-vm`이라는 이름을 만들기만 해서는 VM이 생기지 않는다.

```powershell
$DockerContext = 'desktop-linux'
$knownContexts = @(docker context ls --format '{{.Name}}')
if ($LASTEXITCODE -ne 0 -or $knownContexts -notcontains $DockerContext) {
    throw '실제로 존재하는 로컬 Linux context를 선택하세요.'
}
$contextInfo = @(docker context inspect $DockerContext | ConvertFrom-Json)[0]
if ($LASTEXITCODE -ne 0) { throw 'context 조회 실패' }
$endpoint = $contextInfo.Endpoints.docker.Host
# Windows에서 실행하는 Docker Desktop의 로컬 named-pipe 연결인지 확인.
# SSH/TCP/원격·공유 엔진이면 임의 실행하지 말고 별도 검토한다.
if (-not $endpoint.StartsWith('npipe:////./pipe/')) { throw '로컬 Docker Desktop 대상인지 확인하세요.' }
$engineOS = docker --context $DockerContext info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $engineOS.Trim() -ne 'linux') {
    throw '선택한 대상이 실행 중인 Linux 엔진이 아닙니다.'
}
docker --context $DockerContext build -t prodog-experiment:local "$Repo\src\gmail\sandbox"
if ($LASTEXITCODE -ne 0) { throw '실험 이미지 빌드 실패' }
docker --context $DockerContext image inspect prodog-experiment:local --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw '이미지 조회 실패' }
$volumes = docker --context $DockerContext image inspect prodog-experiment:local --format '{{json .Config.Volumes}}'
if ($LASTEXITCODE -ne 0 -or $volumes.Trim() -notin @('null', '{}')) {
    throw '선언 볼륨 없는 프로젝트 이미지가 필요합니다.'
}
```

`$DockerContext`는 이후 모든 단계에 같은 값을 사용한다. 새 PowerShell을 열면 변수를 다시
설정한다. 전역 `docker context use`는 필요 없다.
[Docker context와 대상 선택](https://docs.docker.com/engine/manage-resources/contexts/).

## 5. Python·대시보드·Ollama 준비

```powershell
Set-Location $Repo
uv python install 3.12
if ($LASTEXITCODE -ne 0) { throw 'Python 준비 실패' }
Set-Location "$Repo\src\gmail"
uv sync --frozen --python 3.12 --dev
if ($LASTEXITCODE -ne 0) { throw 'Gmail 의존성 설치 실패: lock을 임의 갱신하지 마세요.' }
& .\.venv\Scripts\python.exe scripts/setup_local.py
if ($LASTEXITCODE -ne 0) { throw '로컬 설정 생성 실패' }
Set-Location "$Repo\src\dashboard"
npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw '대시보드 의존성 설치 실패' }
```

Gmail 환경은 탐지기 기본 패키지도 의존성으로 설치한다. 그러나 **합성 동적 테스트만** 할 때는
팀 가중치와 별도 Transformer 환경을 준비하지 않아도 된다.

Ollama Windows 앱을 실행한다. 이미 서비스가 실행 중이면 `ollama serve`를 중복 실행하지 않는다.
기본 Windows 앱은 백그라운드에서 로컬 API를 제공한다.
[Ollama Windows 실행 안내](https://docs.ollama.com/windows).

```powershell
ollama pull qwen3:4b-instruct
if ($LASTEXITCODE -ne 0) { throw '모델 다운로드 실패: 태그와 네트워크를 확인하세요.' }
ollama list
$modelInfo = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11434/api/show' -ContentType 'application/json' -Body '{"model":"qwen3:4b-instruct"}'
if (@($modelInfo.capabilities) -notcontains 'tools') { throw '도구 호출 지원 모델이 필요합니다.' }
$thinkingModes = @($modelInfo.thinking.values)
if ($thinkingModes.Count -eq 1 -and $thinkingModes[0] -eq $true) {
    throw '추론 전용 모델입니다. 현재 자동 분석 조건과 맞지 않습니다.'
}
```

기본 모델명은 저장소에서 기능 연결을 확인했던 Instruct 태그다.
[공식 모델 페이지](https://ollama.com/library/qwen3:4b-instruct).
태그가 없거나 PC 자원이 부족하면 실패 원인을 보고한다. 아무 모델로 바꾼 뒤 검증 완료로
표시하지 않는다. 다른 모델은 도구 지원과 실제 실행을 다시 확인하고 모델명을 기록한다.
Ollama를 외부에 공개하거나 유료 API로 자동 전환하지 않는다.

## 6. 새 합성 전용 checkout의 설정

**이 단계는 새로 받은 실험용 폴더에만 적용한다.** 실제 Gmail/Supabase 설정이 있으면
그 파일의 인증 설정을 비우지 말고 별도 checkout으로 돌아간다.

다음은 `setup_local.py`가 만든 암호화 키를 유지하면서 공개 실행 설정만 채우는 예시다.
`$DockerContext`는 4단계에서 검증한 값이어야 한다.

```powershell
Set-Location "$Repo\src\gmail"
$savedSetupContext = [Environment]::GetEnvironmentVariable('PRODOG_SETUP_CONTEXT', 'Process')
$env:PRODOG_SETUP_CONTEXT = $DockerContext
try {
@'
import os, re
from pathlib import Path
from dotenv import dotenv_values, set_key
p = Path('.env')
if not p.is_file():
    raise SystemExit('Run setup_local.py first')
values = dotenv_values(p)
names = ('SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_KEY',
         'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET')
if any(values.get(k) or os.environ.get(k) for k in names):
    raise SystemExit('Existing auth configuration: use a fresh synthetic-only checkout/shell')
if not values.get('TOKEN_ENCRYPTION_KEY'):
    raise SystemExit('Missing encryption key: do not replace an existing key')
context = os.environ.get('PRODOG_SETUP_CONTEXT', '')
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', context):
    raise SystemExit('Select a verified Docker context')
for key, value in {
    'APP_MODE': 'demo',
    'APP_BASE_URL': 'http://127.0.0.1:8765',
    'DASHBOARD_URL': 'http://127.0.0.1:5173',
    'EXPERIMENTS_ENABLED': 'true',
    'SANDBOX_DOCKER_CONTEXT': context,
    'OLLAMA_MODEL': 'qwen3:4b-instruct',
}.items():
    set_key(p, key, value, quote_mode='never')
print('Synthetic settings prepared; encryption key preserved; no secrets printed')
'@ | & .\.venv\Scripts\python.exe -
    if ($LASTEXITCODE -ne 0) { throw '설정 실패: 기존 인증 설정을 보존하세요.' }
} finally {
    [Environment]::SetEnvironmentVariable('PRODOG_SETUP_CONTEXT', $savedSetupContext, 'Process')
}
```

서버 설정에서는 셸 환경변수가 `.env`보다 우선한다. `.env`를 고쳐도 실제 선택한 context·모델·
모드가 다르면 프로세스 환경의 해당 항목을 확인한다. 기존 사용자/시스템 환경변수를 삭제하지
말고 이번 실행에 한해 필요한 값을 지정한다. 값 전체나 인증 키를 출력하지 않는다.

새 합성 checkout의 대시보드에는 `.env.local`을 만들 필요가 없다. 다른 `.env*`나 셸의
`VITE_SUPABASE_*`가 남아 있으면 그것이 어떤 프로젝트인지 확인하고 인증을 우회하지 않는다.
실제 계정 설정을 재사용할 경우 [전체 팀 실행 안내](team-setup.md)의 로그인 절차를 따른다.

## 7. 실제 컨테이너 검사 — Ollama 추론 전

이 단계는 새 제한 컨테이너와 가짜 토큰만 사용한다. 모델 추론과 실제 Gmail 호출은 없다.
먼저 저장소의 Docker 통합 검사 두 개를 실행한다.

```powershell
Set-Location "$Repo\src\gmail"
$savedEnv = @{}
foreach ($name in @('DOCKER_CONTEXT', 'SANDBOX_DOCKER_CONTEXT', 'PRODOG_DOCKER_TESTS')) {
    $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:DOCKER_CONTEXT = $DockerContext
    $env:SANDBOX_DOCKER_CONTEXT = $DockerContext
    $env:PRODOG_DOCKER_TESTS = '1'
    & .\.venv\Scripts\python.exe -m pytest tests/test_sandbox_live.py::test_real_sandbox_boundaries_and_file_observation tests/test_sandbox_live.py::test_api_real_execution_and_cancel_cleanup -q
    if ($LASTEXITCODE -ne 0) { throw '실제 Docker 경계 검사 실패: 여기서 중단하세요.' }
} finally {
    foreach ($name in $savedEnv.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnv[$name], 'Process')
    }
}
```

이 두 검사는 비루트, 네트워크 없음, 읽기 전용 루트, bind mount 없음, 파일/가짜 토큰 관측과
API 실행·취소·출력 초과 후 정리를 검사한다. 모델 응답은 고정돼 있으므로 Ollama 성공 증거로
사용하지 않는다. `test_vm_live.py`는 Lima와 `prodog-vm`이 고정된 Mac 검사이므로 실행하지 않는다.

추가로 아래 짧은 검사를 실행해 실제 cgroup 값과 권한을 확인한다. 성공하더라도 이는
**컨테이너 경계 검사**이며 WSL VM 전체의 호스트 공유 차단을 증명하지 않는다.

```powershell
$savedSetupContext = [Environment]::GetEnvironmentVariable('PRODOG_SETUP_CONTEXT', 'Process')
$env:PRODOG_SETUP_CONTEXT = $DockerContext
try {
@'
import asyncio, os, uuid
from prodog_gmail.sandbox import Sandbox, docker

async def main():
    box = Sandbox(uuid.uuid4().hex, context=os.environ['PRODOG_SETUP_CONTEXT'])
    probe = r"""
import os, pathlib, socket
p = pathlib.Path
status = dict(line.split(':', 1) for line in p('/proc/self/status').read_text().splitlines())
assert os.getuid() == os.getgid() == 10001
assert int(status['CapEff'], 16) == 0
assert status['NoNewPrivs'].strip() == '1' and status['Seccomp'].strip() == '2'
assert os.statvfs('/').f_flag & os.ST_RDONLY
assert p('/sys/fs/cgroup/memory.max').read_text().strip() == '268435456'
assert p('/sys/fs/cgroup/pids.max').read_text().strip() == '64'
assert p('/sys/fs/cgroup/memory.swap.max').read_text().strip() == '0'
quota, period = map(int, p('/sys/fs/cgroup/cpu.max').read_text().split())
assert quota == period
assert set(os.listdir('/sys/class/net')) == {'lo'}
for name in ('/var/run/docker.sock', '/run/docker.sock', '/run/host-services/ssh-auth.sock'):
    assert not p(name).exists()
assert not os.environ.get('SSH_AUTH_SOCK')
assert not any('API_KEY' in key or 'TOKEN' in key for key in os.environ)
s = socket.socket(); s.settimeout(1)
assert s.connect_ex(('1.1.1.1', 443)) != 0
s.close()
print('CONTAINER_BOUNDARY_OK')
"""
    try:
        await box.start('Synthetic boundary check only')
        result = await box._docker('exec', '-i', box.name, 'python', '-I', '-', stdin=probe.encode())
        if result['exit_code'] != 0 or 'CONTAINER_BOUNDARY_OK' not in result['stdout']:
            raise RuntimeError('Container boundary check failed; inspect synthetic diagnostics locally')
    finally:
        await box.remove()
    result = await docker('inspect', box.name, context=box.context)
    if result['exit_code'] == 0 or 'No such object' not in result['stderr']:
        raise RuntimeError('Container absence not confirmed')
    print('CONTAINER_BOUNDARY_AND_CLEANUP_OK')

asyncio.run(main())
'@ | & .\.venv\Scripts\python.exe -
    if ($LASTEXITCODE -ne 0) { throw '권한/cgroup/정리 검사 실패' }
} finally {
    [Environment]::SetEnvironmentVariable('PRODOG_SETUP_CONTEXT', $savedSetupContext, 'Process')
}
```

실패하면 cgroup v2·Docker 설정·이미지·권한과 선택 대상을 조사한다. assertion이나 앱 제한을
지워 통과시키지 않는다. cgroup 값 확인은 실제 OOM/PID 소진까지 유발한 강제 시험과 구분한다.

## 8. 실제 Ollama 합성 검사와 화면 실행

앞 단계가 통과하면 임시 DB와 합성 메일 두 개로 실제 Ollama 호출을 검사한다.
설치 준비에서 다른 모델을 선택했다면 아래 모델명과 서버 설정을 그 검증된 이름으로 맞춘다.

```powershell
Set-Location "$Repo\src\gmail"
$savedEnv = @{}
foreach ($name in @('PRODOG_EVALUATION_LIVE', 'PRODOG_OLLAMA_TEST_MODEL', 'SANDBOX_DOCKER_CONTEXT')) {
    $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:PRODOG_EVALUATION_LIVE = '1'
    $env:PRODOG_OLLAMA_TEST_MODEL = 'qwen3:4b-instruct'
    $env:SANDBOX_DOCKER_CONTEXT = $DockerContext
    & .\.venv\Scripts\python.exe -m pytest tests/test_evaluation_live.py -q
    if ($LASTEXITCODE -ne 0) { throw '실제 Ollama 합성 실행 실패: 완료로 보고하지 마세요.' }
} finally {
    foreach ($name in $savedEnv.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnv[$name], 'Process')
    }
}
```

모델 로딩·추론에는 시간이 걸린다. 출력과 프로세스 상태를 확인하며 진행 상황을 보고한다.
테스트 실패 시 모델의 도구 호출, context, 시간/출력 제한, 메모리, Windows의 Python 자식
프로세스 실행 지원을 점검한다. 제한을 임의로 풀어 성공 결과를 만들지 않는다.
두 건의 통과는 기능 연결 확인이며 탐지 정확도·일반화된 ASR 측정이 아니다.

기존 8765/5173 서버가 있으면 먼저 소유 프로세스를 확인한다. 포트 번호를 PID처럼 종료하거나
다른 서버를 강제 종료하지 않는다. 실행할 두 터미널의 `$Repo`를 각각 설정한다.

```powershell
# 터미널 A: 백엔드. Windows에서는 우선 reload/다중 worker 없이 실행한다.
Set-Location "$Repo\src\gmail"
& .\.venv\Scripts\python.exe -m uvicorn prodog_gmail.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

```powershell
# 터미널 B: 대시보드
Set-Location "$Repo\src\dashboard"
npm.cmd run dev
```

브라우저에서 `http://127.0.0.1:5173/#/experiment`를 연다. localhost와 혼용하지 않는다.
AI가 백그라운드로 실행했다면 이번에 시작한 PID·로그 위치·정상 종료 방법을 기록한다.

화면에서 다음을 확인한다.

1. 모델 선택 후 준비 확인: Docker 이미지·모델·이전 정리 상태가 준비됨.
2. 기본 작업은 유지하고 정상 예시 메일을 넣어 **테스트 시작**.
3. AI 응답/요약, 파일 변화, 실행 로그가 보이고 종료 후 컨테이너가 제거됨.
4. 합성 공격 예시도 별도로 실행해 가짜 토큰 관측과 불완전 결과 표시를 확인함.
5. 한 번은 실행 중 중지해 중지 상태와 컨테이너 제거를 확인함.

기본 실행 제한은 동시 1개, 컨테이너 1 CPU·256MiB·64 PID다. Ollama의 Windows RAM/GPU는
이 컨테이너 제한 밖이다. WSL 전체 자원 설정을 바꾸면 다른 WSL 작업에도 영향을 줄 수 있으니
기존 설정을 덮어쓰지 않는다. 격리 성공을 프롬프트 인젝션 탐지 성능으로 보고하지 않는다.

## 9. Gmail·정적 모델·Supabase까지 개발할 때

여기까지는 **동적 분석 실험 환경**이다. 실제 메일 수집과 정적→동적 자동 파이프라인까지
구성하려면 [팀원 실행 안내](team-setup.md)를 이어서 따른다.

- `src/detector`에 별도 Python 3.12 환경과 Transformer 의존성을 준비한다.
- 팀이 별도로 공유한 8개 모델 파일을 배치하고 `model-artifacts.sha256`과 대조한다.
- Windows 탐지기 Python은 `../detector/.venv/Scripts/python.exe`다.
- 각자 암호화 키를 유지하며 Google OAuth와 서버 전용 Supabase 설정을 준비한다.
- 앱 로그인과 Gmail 읽기 권한 연결은 별도다. 실제 계정 선택·동의는 사용자가 수행한다.
- 프론트에는 공개 anon 키만 사용한다. 공유 Supabase migration은 임의 실행하지 않는다.
- `APP_MODE=demo`여도 Supabase 설정이 있으면 인증이 적용된다. 인증 실패를 우회하지 않는다.

전체 준비 상태 점검은 저장소 루트에서 실행한다.

```powershell
Set-Location $Repo
& .\src\gmail\.venv\Scripts\python.exe -I -S -B src/gmail/scripts/check_team_setup.py
if ($LASTEXITCODE -ne 0) { Write-Output '미완료 항목을 확인하세요. 합성 전용 구성에서는 일부 FAIL이 정상입니다.' }
```

합성 전용 구성은 정적 가중치·전체 로그인 설정이 없어 일부 항목이 FAIL이다.
이를 모두 OK로 만들려고 가짜 모델·임의 키를 넣지 않는다. 전체 점검 OK도 컨테이너 경계 검사나
실제 로그인·실제 모델 추론의 통과를 대신하지 않는다.

## 10. 자주 막히는 지점

| 증상 | AI가 확인할 것 |
| --- | --- |
| `docker` 명령 없음 | 공식 설치 완료와 새 PowerShell의 PATH. 중복 엔진 설치로 해결하지 않기 |
| 엔진이 `windows`로 나옴 | Docker Desktop의 Linux 컨테이너 모드와 선택 context |
| named pipe 연결 실패 | Docker Desktop 실행 상태와 실제 context. 원격 엔진으로 우회하지 않기 |
| `prodog-vm`을 찾지 못함 | Mac 전용 이름을 복사했는지 확인. 실제 Windows context로 서버 설정·검사 일치 |
| 이미지 없음 | 선택한 같은 context에 빌드했는지 확인 |
| Ollama 연결 실패 | Windows 앱 실행·11434 포트·호스트 위치. WSL/컨테이너 안에 중복 설치하지 않기 |
| 도구 호출 불가·추론 출력만 반복 | `/api/show`의 tools/thinking과 정확한 모델 태그 |
| `NotImplementedError`로 자식 프로세스 실행 실패 | 설치된 Uvicorn/asyncio의 Windows 이벤트 루프, reload/다중 worker 사용 여부. 호스트 shell 실행으로 우회 금지 |
| API가 401 | 합성 전용 checkout인지 실제 Supabase Auth 설정이 있는지 확인. 설정된 인증 우회 금지 |
| API가 503 `database_busy` | 해당 로컬 DB를 사용하는 다른 작업 확인 후 재시도. DB/잠금 파일 삭제 금지 |
| 연결 해제가 실패한 상태 | 설정의 해제 재시도. 삭제 대기 계정을 다시 동기화하지 않기 |
| 컨테이너 정리 실패 | 해당 context에서 이번 실행 ID만 확인·정리. `docker system prune`이나 전체 삭제 금지 |
| 메일 가져오기만 정적 실패 | 동적 실험과 구분해 팀 가중치·탐지기 환경·경로 확인 |

## 11. AI의 최종 보고 형식

각 항목을 **확인됨 / 실패 / 미실행**으로 보고하고 실패 원인·다음 단계만 간단히 적는다.

- checkout 위치·브랜치·커밋과 기존 수정 보존 여부.
- Windows/WSL/Docker/Python/Node/Ollama 버전.
- 선택 context·Linux 엔진 확인·이미지 ID·사용 모델명.
- 설치한 프로그램과 수정한 로컬 설정의 **항목 이름만**. 키·토큰·메일 내용은 제외.
- 실제 Docker 검사, cgroup/권한 검사, 실제 Ollama 검사, 화면 실행/중지/정리 결과.
- 서버 PID/로그/종료 방법과 이번에 만든 컨테이너 잔존 여부.
- 합성 전용 완료인지 Gmail·정적 모델·Supabase까지 완료인지 구분.
- Windows에서 실행하지 않은 검사를 Mac 결과로 대체하지 않았는지 확인.

검사 결과가 없으면 성공으로 표시하지 않는다. 문서 작성자의 Mac 결과를 이 PC의 검증 결과로
복사하지 않는다. 실제 악성 바이너리·VM 탈출·전체 유출 경로 차단은 이 구성의 검증 범위가 아니다.
