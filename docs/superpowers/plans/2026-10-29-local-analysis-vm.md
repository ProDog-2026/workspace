# 로그인 없는 동적 분석 VM 구성 계획

**Goal:** M3 Mac에서 Ollama를 유지하면서 명령 실행을 공유 폴더 없는 별도 Linux VM 안의 새 컨테이너로 분리한다.

**Architecture:** 사용자 선택에 따라 Docker 계정 로그인이 필요한 sbx 대신 Lima 2.2.1을 사용한다. Docker의 공식 CLI와 기존 Sandbox를 재사용하고 `SANDBOX_DOCKER_CONTEXT=prodog-vm`으로 명령 대상을 고정한다. 메일별 컨테이너를 새로 만들고 전용 VM 자체는 유지한다. 컨테이너와 VM이 매번 함께 새로 생긴다는 주장은 하지 않는다.

**Tech Stack:** 기존 Python stdlib·pytest·Docker CLI, Lima/Apple VZ.

## 범위

- 2 CPU/2GiB RAM/16GiB 가상 디스크의 전용 VM. 호스트 마운트 없음, SSH agent·프록시 환경·TCP 포트 자동 전달 없음.
- 모델 추론·Gmail·대시보드·키는 호스트에 남는다. 컨테이너에는 메일 1건과 실험용 가짜 토큰만 stdin으로 전달한다.
- 기존 컨테이너 네트워크 없음·비루트·cap-drop·읽기 전용·자원/시간/출력 제한은 유지한다.
- 컨테이너 실행 대상 context를 실행 기록에 저장해 정리·재시작 때 같은 VM에서 제거한다. VM 중단 시 Desktop으로 자동 대체하지 않는다.
- 기존 Docker Desktop과 전역 기본 context, 기록 파일, 실제 메일은 보존한다. 기존 서버는 자동 재시작하지 않는다.
- 외부 VM은 초기 이미지/패키지 준비용 네트워크가 있다. 임의 VM 탈출·악성 바이너리의 완전 차단을 보장하지 않는다.

## 작업과 검증

- [x] 전용 VM 생성·실제 마운트와 SSH·포트 설정 확인, VM 안 실험 이미지 준비.
- [x] context 고정 전달·기록·복구·준비 상태와 자동 분석 설정 해시를 회귀 테스트 후 연결.
- [x] 합성 메일에서 명령/파일 관측, 호스트 폴더·네트워크 차단 및 컨테이너 제거를 실제 VM에서 검증.
- [x] 팀원용 Mac 실행/중지 절차와 Windows 경계를 문서화. Windows 실제 가동은 여기서 검증하지 않는다.

설치 상태: sbx 0.50.1은 로그인하거나 VM을 만들지 않은 채 제거했고 이번 작업에서 추가한 Docker tap도 정리했다. Lima는 Homebrew 공식 core 패키지로 설치했다. 커밋·푸시는 하지 않는다.

## 최종 검증

- 실제 VM 포함 백엔드 231 passed / 4 skipped. skip은 실제 Ollama·ML 추론 opt-in이며 이번에는 모델 추론을 추가로 실행하지 않았다. 프론트 18 passed와 빌드 통과. Ruff 및 diff 공백 검사 통과.
- 독립 리뷰의 두 P2를 수정했다. (1) 모든 guest IP의 wildcard listener를 포함해 포트 자동 전달을 차단하도록 명시하고 실제 서버로 확인. (2) 실행 전의 대상 미지정 대기 기록은 선택한 VM에 바인딩한 후 기록·실행하며 이미 실행된 legacy 기록 정리는 기존 대상으로 유지.
- VM 중지 시 ready=false로 중단하고 자동 대체하지 않는 것을 실제 확인했으며 VM을 다시 시작해 ready=true 상태로 남겼다.
- `.env`에는 SANDBOX_DOCKER_CONTEXT=prodog-vm이 반영됐다. 기존 실제 서버는 자동 재시작하지 않았다. 기본 Docker context는 desktop-linux를 유지한다.
