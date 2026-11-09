#!/bin/bash
set -uo pipefail
root=$(CDPATH= cd -P -- "$(dirname "$0")" && pwd -P)
source "$root/scripts/macos-common.sh"
errors=0

if ! command -v lsof >/dev/null; then echo 'lsof가 없어 프로세스를 확인할 수 없습니다.' >&2; exit 1; fi
if ! command -v curl >/dev/null; then echo 'curl이 없어 검사 실행 상태를 확인할 수 없습니다.' >&2; exit 1; fi

stop_local() {
  local port="$1" kind="$2" label="$3" file="$state_dir/$2.pid"
  local pids pid seen=' ' matched=0 unknown=0 remaining=0 attempt
  pids=$(port_pids "$port")
  if [ -f "$file" ]; then pids="$pids $(cat "$file" 2>/dev/null || true)"; fi
  for pid in $pids; do
    case "$pid" in ''|*[!0-9]*) continue ;; esac
    case "$seen" in *" $pid "*) continue ;; esac
    seen="$seen$pid "
    if is_project_process "$pid" "$kind"; then
      matched=1
      if ! kill -TERM "$pid" 2>/dev/null; then
        echo "$label: PID $pid 종료에 실패했습니다." >&2
        return 1
      fi
    elif kill -0 "$pid" 2>/dev/null; then
      unknown=1
      echo "$label: 소유자를 확인할 수 없는 PID $pid는 그대로 둡니다."
    fi
  done
  for attempt in 1 2 3 4 5; do
    remaining=0
    for pid in $pids; do
      if is_project_process "$pid" "$kind"; then remaining=1; fi
    done
    for pid in $(port_pids "$port"); do
      if is_project_process "$pid" "$kind"; then remaining=1; fi
    done
    [ "$remaining" -eq 0 ] && break
    sleep 1
  done
  if [ "$remaining" -ne 0 ]; then echo "$label: 서버가 아직 실행 중입니다." >&2; return 1; fi
  [ ! -f "$file" ] || rm -f "$file"
  if [ "$unknown" -eq 1 ]; then echo "$label: 다른 프로세스가 포트를 사용 중입니다." >&2; return 1; fi
  if [ "$matched" -eq 1 ]; then echo "$label: 종료 완료"; else echo "$label: 이미 종료됨"; fi
}

stop_agent() {
  local containers id directory runs parser known='' running='' workers worker run_id remaining
  command -v docker >/dev/null || { echo 'Docker를 찾지 못해 컨테이너 상태를 확인할 수 없습니다.' >&2; return 1; }
  docker info >/dev/null 2>&1 || { echo 'Docker 엔진에 연결할 수 없어 컨테이너 상태를 확인할 수 없습니다.' >&2; return 1; }
  containers=$(docker ps -aq --filter label=com.docker.compose.project=agent-container) || return 1
  if [ -z "$containers" ]; then
    workers=$(docker ps -q --filter label=agent.run_id --filter network=work-net) || return 1
    [ -z "$workers" ] || { echo '남은 검사 컨테이너의 소유자를 확인할 수 없습니다.' >&2; return 1; }
    echo '동적 검사 컨테이너: 이미 종료됨'
    return 0
  fi
  validate_compose_project || return 1
  parser="$python"
  if [ ! -x "$parser" ]; then parser=$(command -v python3 || true); fi
  if [ -n "$parser" ] && runs=$(curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8000/runs 2>/dev/null); then
    known=$(printf '%s' "$runs" | "$parser" -c 'import json,re,sys; rows=json.load(sys.stdin); assert isinstance(rows,list); print("\n".join(r["run_id"] for r in rows if isinstance(r.get("run_id"),str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}",r["run_id"])))') || return 1
    running=$(printf '%s' "$runs" | "$parser" -c 'import json,re,sys; rows=json.load(sys.stdin); print("\n".join(r["run_id"] for r in rows if r.get("status")=="running" and isinstance(r.get("run_id"),str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}",r["run_id"])))') || return 1
    for run_id in $running; do
      curl --noproxy '*' -fsS -X POST --max-time 5 "http://127.0.0.1:8000/runs/$run_id/stop" >/dev/null 2>&1 || echo '검사 중지 요청에 실패하여 컨테이너 종료를 계속합니다.' >&2
    done
  else
    echo '실행기 API를 사용할 수 없어 기존 검사 기록은 확인하지 못했습니다.' >&2
  fi
  (cd "$agent_dir" && docker compose --project-name agent-container stop --timeout 30) || return 1
  workers=$(docker ps -q --filter label=agent.run_id --filter network=work-net) || return 1
  for worker in $workers; do
    run_id=$(docker inspect --format '{{index .Config.Labels "agent.run_id"}}' "$worker") || return 1
    if printf '%s\n' "$known" | grep -Fxq -- "$run_id"; then
      docker stop --time 10 "$worker" >/dev/null || return 1
    else
      echo '소유자를 확인할 수 없는 작업 컨테이너는 그대로 두었습니다. Docker에서 직접 확인하세요.' >&2
      return 1
    fi
  done
  remaining=$(docker ps -q --filter label=com.docker.compose.project=agent-container) || return 1
  [ -z "$remaining" ] || { echo '일부 Compose 서비스가 아직 실행 중입니다.' >&2; return 1; }
  echo '동적 검사 컨테이너: 종료 완료'
}

echo 'ProDog 종료 대상을 확인합니다...'
stop_local 5173 dashboard '웹 대시보드' || errors=1
stop_local 8765 gmail 'Gmail 백엔드' || errors=1
stop_agent || errors=1
if [ "$errors" -ne 0 ]; then echo '일부 종료 상태를 확인하지 못했습니다.' >&2; exit 1; fi
echo 'ProDog 종료 완료. Docker Desktop과 Ollama는 계속 실행됩니다.'
