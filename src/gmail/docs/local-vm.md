# 계정 로그인 없는 로컬 동적 분석 VM

## 현재 구성

2026-10-29 이지훈의 M3 MacBook Air 16GB에서 Lima **2.2.1**과 Apple VZ로 실제 실행을 확인했다. Docker 계정 로그인은 사용하지 않는다.

```text
Mac: React 대시보드 / Gmail·분석 제어 서버 / Ollama 추론
                         │ 고정된 Docker CLI 명령, 메일은 stdin
                         ▼
Lima 전용 Linux VM (prodog-lab, 2 CPU / 2GiB RAM / 16GiB 가상 디스크)
  └─ 독립 rootless Docker 엔진 (context: prodog-vm)
       └─ 메일마다 새 prodog-exp-… 컨테이너 → 관측 → 제거
```

호스트와 VM 사이에 프로젝트·홈 폴더를 공유하지 않는다. SSH agent·X11·호스트 프록시 환경도 전달하지 않는다. 컨테이너에는 Gmail 토큰·API 키·모델 파일을 넣지 않는다. Ollama는 Mac에서 GPU를 사용하고, 컨테이너는 네트워크 없이 shell/Python 명령만 실행한다.

**VM은 메일 사이에 유지하며 컨테이너만 새로 만든다.** 메일마다 VM·커널까지 새로 만드는 구성은 아니다. VM 디스크 16GiB는 논리 상한이며 실제 사용량에 따라 호스트 공간을 차지한다. Ollama 메모리는 VM의 2GiB 제한 밖이다.

## Mac 최초 준비

기존 Docker CLI·uv·Gmail 실행 환경을 준비한 뒤 실행한다. Homebrew 설치나 Docker Desktop을 사용하기 위한 계정 로그인은 이 VM 실행의 필수 조건이 아니다.

```sh
brew install lima
cd ~/Downloads/캡스톤/workspace/src/gmail
uv run python scripts/setup_local_vm.py
```

스크립트는 VM이 없으면 `sandbox/lima.yaml`과 `--mount-none`으로 생성한다. 기존 VM이 있으면 공유 폴더·SSH 전달·포트 설정을 검사하고 맞지 않으면 중단한다. VM이나 기존 context를 덮어쓰거나 삭제하지 않는다. `prodog-vm`이 다른 엔진을 가리키고 있어도 중단한다.

VM의 rootless Docker 소켓만 호스트의 개인 사용자 디렉터리로 전달한다. 이는 **호스트가 VM을 제어하기 위한 소켓**이며 Mac의 Docker 소켓을 VM에 넣는 것이 아니다. 기본 Docker context를 변경하지 않으므로 기존 Desktop·Juice Shop 작업은 유지된다. 초기 VM 이미지와 Docker 설치는 Lima 공식 템플릿을 사용하며 최초 다운로드에는 인터넷 연결이 필요하다. 실행 시 해당 Lima 버전의 Ubuntu 이미지 digest를 확인한다.

실험 이미지 빌드와 실제 격리 검증이 통과한 후에만 기존 `.env`의 다음 한 항목을 갱신한다. OAuth 값과 암호화 키는 유지한다.

```dotenv
SANDBOX_DOCKER_CONTEXT=prodog-vm
```

검증 보고서는 Git에서 제외되는 `data/vm-readiness.json`에 저장된다. 내용은 환경 검사 결과와 이미지 ID이며 실제 사용자 메일은 없다. 결과의 가짜 토큰 복사는 **의도적으로 실행한 합성 파일 작업**이지 LLM 인젝션 성공률 측정이 아니다.

## 백엔드 적용

기존 서버 터미널에서 `Ctrl+C` 후 재시작한다. 준비 스크립트는 기존 서버를 자동으로 재시작하지 않는다.

```sh
cd ~/Downloads/캡스톤/workspace/src/gmail
uv run uvicorn prodog_gmail.app:app --host 127.0.0.1 --port 8765
```

대시보드의 **설정 → 자동 분석 → 명령 실행 환경**에서 `prodog-vm / 준비됨`을 확인한다. 설치된 Qwen Instruct 모델과 자동 분석을 설정하고 **로컬 수집** 화면에서 메일을 수집한다. 정적 판정이 의심·위험인 메일은 기존 대기열을 통해 VM 안에서 실행된다. 동적 분석 상세에도 실행 대상이 표시된다.

VM 변경은 중복 실행 판단에 반영된다. 새 수집 요청으로 새 환경의 분석을 만들며, 기존 종료 기록은 수정하거나 자동 재실행하지 않는다. 실행 기록에 context를 저장하므로 취소·서버 재시작·정리도 같은 실행 대상을 사용한다. 이미 실행한 과거 기록에 context 정보가 없으면 정리는 기존 기본 Docker 대상으로 처리한다. 아직 실행하지 않은 대기 기록에 대상 정보가 없으면 실행 직전에 선택한 VM으로 바인딩하고 그 대상을 기록한다.

VM이 꺼지거나 엔진에 연결하지 못하면 오류로 중단하며 Docker Desktop으로 자동 대체하지 않는다. 정리 실패는 다음 분석을 차단하는 기존 정책을 유지한다.

## 시작·중지·상태

```sh
limactl start prodog-lab
limactl list prodog-lab
docker --context prodog-vm ps
docker --context prodog-vm info
```

실험이 끝나고 백엔드가 새 분석을 등록하지 않는 상태에서 다음으로 VM을 중지하면 RAM을 돌려준다. 디스크·이미지는 남는다.

```sh
limactl stop prodog-lab
```

정리 실패 시 위 `docker --context prodog-vm ps -a`로 해당 실행의 `prodog-exp-…`만 확인한다. 다른 컨테이너나 VM을 일괄 삭제하지 않는다. 기본 Docker 작업을 계속하려고 `docker context use prodog-vm`을 실행할 필요는 없다.

## 확인한 경계와 남은 한계

- 실제 VM에 virtiofs/9p 호스트 공유 마운트가 없고 Mac `/Users`가 보이지 않음을 확인했다.
- VM의 wildcard IP에 서버를 띄워도 해당 포트가 호스트에 자동 전달되지 않는 것을 실제 확인했다.
- 실제 컨테이너에서 UID 10001, 읽기 전용 루트, 호스트의 합성 파일 및 Docker 소켓 미노출, 외부 TCP 연결 실패를 확인했다.
- cgroup 파일에서 실제 1 CPU·256MiB 메모리·64 PID 제한을 확인했다. Docker 요청 옵션만 확인한 결과가 아니다.
- 합성 메일 읽기, 요약 파일 생성, 가짜 토큰 파일 복사와 스냅샷, 컨테이너 제거를 확인했다. 기존 통합 테스트로 API 실행·취소·출력 초과 뒤 정리도 VM에서 검증했다.
- VM은 초기 설치·이미지 준비를 위한 NAT 네트워크를 갖는다. 네트워크 차단은 **실험 컨테이너**에 적용된다. 컨테이너 탈출 후 VM 네트워크까지 호스트 방화벽으로 차단한 구성은 아니다.
- 컨테이너·VM 탈출, 하이퍼바이저 취약점, 모든 악성 코드 차단을 보장하지 않는다. 실제 악성 바이너리·VM 탈출 실험은 이 구성의 검증 범위가 아니다.
- shell은 기존 비루트·읽기 전용 제한을 유지한다. VM root나 호스트 shell 권한을 모델에게 새로 주지 않는다. 정상 과제 불이행·권한 거부·모델 실패를 공격 차단 성공으로 계산하지 않는다.

## 2026-10-30 실제 재검증

기준 checkout은 `feat/jihun`, `624bfe2`다. 기존 Lima 2.2.1의 `prodog-lab`과 `prodog-vm`, 기존 실험 이미지 `sha256:d09c3caa34d53bf6c863bd46d6e034cc30c45902b87a2868e58aa0827cc7175d`를 사용했다. 이미지를 다시 빌드하거나 VM·디스크·context를 교체하지 않았다.

검증 시작 시 VM은 **Stopped**였다. 이 상태에서 실제 `docker_status('prodog-vm')`는 `ready=False`, `context=prodog-vm`을 반환했고 `Sandbox.start()`도 실패했다. Desktop으로 자동 전환되지 않는 것을 확인한 후 `limactl start --tty=false --timeout=10m prodog-lab`으로 기존 VM을 시작했다. 가동 중 VM을 중지하는 장애 주입은 하지 않았다. 시작 실패의 오류 코드는 현재 `unsafe_sandbox_image`여서 엔진 불가와 이미지 문제를 구분하지 못하지만, 메일 전달과 컨테이너 실행은 차단된다.

### 발견하고 수정한 결함

1. `validate_vm()`이 Docker 소켓의 `reverse: true`를 허용했다. 제어 소켓 전달 방향을 검사하도록 수정했다.
2. 같은 검사가 `copyToHost`를 허용해 “호스트 공유 마운트 없음” 검사만으로는 VM 파일의 호스트 자동 복사를 차단하지 못했다. 자동 복사 설정이 있으면 VM 시작 전에 거부한다.
3. `check_sandbox()`가 제거 후 `docker inspect`의 모든 실패를 제거 성공으로 취급했다. 이제 해당 컨테이너의 `No such object` 응답이 있어야 제거를 확인하며, 엔진 연결 오류는 검증 실패다.

위 설정 우회는 합성 설정을 주입해 재현했다. 실제 VM에 역방향 소켓이나 호스트 파일 복사 설정을 적용하지 않았다. 새 회귀테스트는 수정 전 각각 `DID NOT RAISE`로 실패했고, 수정 후 `test_vm_setup.py` **6 passed**를 확인했다. 제거 확인 중 엔진 불가는 모의 응답으로 검증했다.

### 실제 VM에서 통과한 경계

| 대상 | 확인한 결과 |
|---|---|
| VM → 호스트 | virtiofs/9p 마운트 없음, `/Users` 없음, SSH agent 환경 없음, wildcard TCP listener의 호스트 자동 전달 없음 |
| 컨테이너 권한 | UID/GID 10001, `CapEff=0`, `NoNewPrivs=1`, `Seccomp=2`, 읽기 전용 루트, Docker·SSH agent 소켓 및 API 키·토큰 환경 미노출 |
| 네트워크 | loopback 인터페이스만 존재하고 외부 `1.1.1.1:443` 연결 실패 |
| 자원 설정 | cgroup의 1 CPU·256MiB·64 PID, swap 0, 열린 파일 수 256 확인 |
| 실제 자원 강제 | 제한된 자식 프로세스 생성 시 `EAGAIN`, `/workspace`·`/tmp` 채우기 시 `ENOSPC`, 320MiB 할당 시 종료 코드 137 및 cgroup `oom_kill` 증가 |
| 파일 관측 | 합성 메일 읽기·요약 파일·가짜 canary 복사·스냅샷, symlink 불완전 판정과 읽기 불가 디렉터리 오류 |
| 정리 | 정상 실행·API 취소·출력 초과·명령 시간 초과 뒤 이번 테스트가 만든 컨테이너 제거 확인 |

PID 생성은 최대 80회 시도하며 생성된 자식은 모두 종료·회수한다. 메모리와 임시 파일 부하는 해당 합성 컨테이너의 제한 안에서만 발생한다. API 테스트는 프로세스 내부 TestClient와 임시 DB, 고정 모델 응답을 사용한다. 실제 5173/8765 서버·DB·메일 세션과 공유 Ollama 추론은 사용하지 않았다.

### 재현 명령과 결과

저장소 루트에서 기존 `src/gmail/.venv`로 실행했다. 실제 VM 검증에는 Lima/Docker 소켓 접근 권한이 필요하다. 다음 명령은 기본 Docker context 설정을 변경하지 않는다.

```sh
src/gmail/.venv/bin/python -m pytest \
  src/gmail/tests/test_vm_context.py src/gmail/tests/test_vm_setup.py \
  src/gmail/tests/test_experiments.py -q
# 41 passed

PRODOG_VM_TESTS=1 PRODOG_DOCKER_TESTS=1 \
DOCKER_CONTEXT=prodog-vm SANDBOX_DOCKER_CONTEXT=prodog-vm \
src/gmail/.venv/bin/python -m pytest \
  src/gmail/tests/test_vm_live.py src/gmail/tests/test_sandbox_live.py -q
# 6 passed, 1 skipped

src/gmail/.venv/bin/python -m ruff check \
  src/gmail/scripts/setup_local_vm.py \
  src/gmail/tests/test_vm_setup.py src/gmail/tests/test_vm_live.py
# All checks passed!
```

건너뛴 1개는 별도 신뢰할 수 있는 정적 모델 자료를 요구하는 통합검증이다. Starlette TestClient의 httpx 사용에 관한 기존 deprecation 경고가 1개 발생했다. Ollama 추론, 실제 악성 코드·VM 탈출, Windows와 전체 저장소 테스트는 이번 검증 범위에 포함하지 않았다.

검증 후 VM은 **Running**, 기본 context는 **desktop-linux**로 유지했다. 이번 테스트의 컨테이너는 모두 제거했다. `2026-10-29T07:42:05Z`에 생성된 기존 종료 컨테이너 `prodog-exp-69a05cad5dc84b6884e48b2901b96e86`는 삭제하지 않았다. VM을 멈추려면 공유 작업자와 먼저 조율한다. 준비 스크립트의 `main()`은 실행하지 않아 `.env`와 `data/vm-readiness.json`을 갱신하지 않았다.

## Windows 팀원

이번 준비 스크립트는 Apple Silicon/VZ용이며 Windows에서 같은 파일을 그대로 실행하지 않는다. 기존 Docker Desktop Linux 컨테이너 방식은 유지된다. 그 방식은 전용 Lima VM을 검증한 것과 동일한 상태가 아니다.

별도 Windows VM을 추가할 때도 백엔드의 `SANDBOX_DOCKER_CONTEXT`에 **해당 VM의 Docker context 이름**을 지정하는 동일 인터페이스를 사용한다. Docker CLI·Ollama는 Windows에서 그대로 실행할 수 있다. 전용 VM의 Windows 드라이버·드라이브 자동 공유·SSH/제어 연결은 해당 팀원 PC에서 별도로 구성하고 동일 경계 검사를 통과해야 한다.

Lima의 WSL2 드라이버는 공식 문서상 실험 상태이며 표준 qcow2 이미지 대신 별도 rootfs archive를 요구한다. Mac 템플릿의 `vmType`만 바꾸는 절차는 제공하지 않는다. Windows/HCS 역시 여기서 검증하지 않았다.

## 출처 (공식 문서 직접 확인)

- [Lima Docker 엔진·소켓 연결](https://lima-vm.io/docs/examples/containers/docker/)
- [Lima 공유 마운트](https://lima-vm.io/docs/config/mount/)
- [Lima 포트 전달](https://lima-vm.io/docs/config/port/)
- [Lima WSL2와 제한](https://lima-vm.io/docs/config/vmtype/wsl2/)
