#!/bin/bash
set -euo pipefail
root=$(CDPATH= cd -P -- "$(dirname "$0")" && pwd -P)
source "$root/scripts/macos-common.sh"

fail() { echo "실행 실패: $*" >&2; exit 1; }
command -v curl >/dev/null || fail 'curl을 설치해 주세요.'
command -v lsof >/dev/null || fail 'lsof를 설치해 주세요.'
command -v node >/dev/null || fail 'Node.js와 npm을 설치해 주세요.'
command -v npm >/dev/null || fail 'Node.js와 npm을 설치해 주세요.'
command -v docker >/dev/null || fail 'Docker Desktop을 설치해 주세요.'

if [ ! -x "$python" ]; then
  command -v uv >/dev/null || fail 'Python 환경을 준비하려면 uv가 필요합니다.'
  echo 'Gmail 백엔드 의존성을 설치합니다...'
  (cd "$gmail_dir" && uv sync --frozen) || fail 'Gmail 의존성 설치에 실패했습니다.'
fi
if [ ! -f "$gmail_dir/.env" ]; then
  "$python" "$gmail_dir/scripts/setup_local.py" || fail 'Gmail 초기 설정에 실패했습니다.'
fi
if [ ! -f "$dashboard_dir/node_modules/vite/bin/vite.js" ]; then
  echo '웹 대시보드 의존성을 설치합니다...'
  (cd "$dashboard_dir" && npm install) || fail '웹 대시보드 의존성 설치에 실패했습니다.'
fi

if ! docker info >/dev/null 2>&1; then
  echo 'Docker Desktop을 시작하고 있습니다...'
  command -v open >/dev/null && open -a Docker || fail 'Docker Desktop을 시작할 수 없습니다.'
  attempt=0
  until docker info >/dev/null 2>&1; do
    [ "$attempt" -lt 90 ] || fail 'Docker 엔진에 연결할 수 없습니다. Docker Desktop을 확인하세요.'
    sleep 1
    attempt=$((attempt + 1))
  done
fi
validate_compose_project || fail '동적 검사 Docker 프로젝트를 확인하지 못했습니다.'

images_ready=true
for image in agent-container-control agent-container-receiver agent-container-worker:phase1; do
  docker image inspect "$image" >/dev/null 2>&1 || images_ready=false
done
control_state=$(docker inspect --format '{{.State.Status}}' agent-container-control-1 2>/dev/null || true)
receiver_health=$(docker inspect --format '{{.State.Health.Status}}' agent-test-receiver 2>/dev/null || true)
if $images_ready && [ "$control_state" = running ] && [ "$receiver_health" = healthy ] && check_control; then
  echo '동적 검사 컨테이너가 이미 실행 중입니다.'
else
  workers=$(docker ps -q --filter label=agent.run_id --filter status=running) || fail '검사 컨테이너 상태를 확인하지 못했습니다.'
  [ -z "$workers" ] || fail '동적 검사가 실행 중입니다. 검사가 끝난 뒤 다시 실행하세요.'
  if check_control; then
    runs=$(curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8000/runs) || fail '검사 실행 상태를 확인하지 못했습니다.'
    printf '%s' "$runs" | "$python" -c 'import json,sys; rows=json.load(sys.stdin); sys.exit(0 if isinstance(rows,list) and all(r.get("phase")=="finished" or (r.get("phase")=="manual" and r.get("status")!="running") for r in rows) else 1)' || fail '진행 중이거나 상태를 확인할 수 없는 검사가 있습니다.'
  elif [ -n "$(port_pids 8000)" ] && [ "$control_state" != running ]; then
    fail '8000번 포트를 다른 프로그램이 사용하고 있습니다.'
  fi
  echo '동적 검사 이미지를 준비하고 컨테이너를 실행합니다. 처음에는 몇 분 걸릴 수 있습니다...'
  (cd "$agent_dir" && docker compose --project-name agent-container up -d --build) || fail '동적 검사 컨테이너 실행에 실패했습니다.'
  wait_ready check_stack || fail '동적 검사 서버나 수신 서버에 연결하지 못했습니다.'
fi

mkdir -p "$state_dir" "$gmail_dir/artifacts" "$dashboard_dir/artifacts"
if check_gmail; then
  echo 'Gmail 백엔드가 이미 실행 중입니다.'
else
  [ -z "$(port_pids 8765)" ] || fail '8765번 포트를 다른 프로그램이 사용하고 있습니다.'
  (cd "$gmail_dir"; nohup "$python" -m uvicorn prodog_gmail.app:app --host 127.0.0.1 --port 8765 --no-access-log > "$gmail_dir/artifacts/run-backend.log" 2>&1 < /dev/null & echo "$!" > "$state_dir/gmail.pid")
  wait_ready check_gmail || fail "Gmail 백엔드 시작 실패: $gmail_dir/artifacts/run-backend.log"
  echo 'Gmail 백엔드가 준비됐습니다.'
fi

if check_dashboard; then
  echo '웹 대시보드가 이미 실행 중입니다.'
else
  [ -z "$(port_pids 5173)" ] || fail '5173번 포트를 다른 프로그램이 사용하고 있습니다.'
  (cd "$dashboard_dir"; nohup node "$dashboard_dir/node_modules/vite/bin/vite.js" --host 127.0.0.1 > "$dashboard_dir/artifacts/run-dashboard.log" 2>&1 < /dev/null & echo "$!" > "$state_dir/dashboard.pid")
  wait_ready check_dashboard || fail "웹 대시보드 시작 실패: $dashboard_dir/artifacts/run-dashboard.log"
  echo '웹 대시보드가 준비됐습니다.'
fi
echo '실행 완료: http://127.0.0.1:5173/'
if command -v open >/dev/null; then open 'http://127.0.0.1:5173/' || echo '브라우저에서 주소를 직접 열어 주세요.'; fi
