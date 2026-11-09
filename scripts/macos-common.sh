#!/bin/bash
# Shared helpers for run.sh and stop.sh; compatible with macOS Bash 3.2.

# Finder-launched Terminal windows may not have Homebrew or uv on their PATH.
PATH=${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}
for tool_dir in /opt/homebrew/bin /usr/local/bin "${HOME:-}/.local/bin"; do
  if [ -d "$tool_dir" ]; then
    case ":$PATH:" in *":$tool_dir:"*) ;; *) PATH="$PATH:$tool_dir" ;; esac
  fi
done
export PATH

gmail_dir="$root/src/gmail"
dashboard_dir="$root/src/dashboard"
agent_dir="$root/src/agent-container"
python="$gmail_dir/.venv/bin/python"
state_dir="$root/.local-run"

port_pids() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

is_project_process() {
  local pid="$1" kind="$2" command cwd uid
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  uid=$(ps -p "$pid" -o uid= 2>/dev/null | tr -d '[:space:]')
  [ "$uid" = "$(id -u)" ] || return 1
  command=$(ps -ww -p "$pid" -o command= 2>/dev/null) || return 1
  cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | awk '/^n/{print substr($0,2); exit}')
  case "$kind" in
    dashboard) [ "$cwd" = "$dashboard_dir" ] && [[ "$command" == *vite* ]] ;;
    gmail) [ "$cwd" = "$gmail_dir" ] && [[ "$command" == *prodog_gmail.app:app* ]] ;;
    *) return 1 ;;
  esac
}

check_gmail() {
  curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8765/api/session 2>/dev/null | "$python" -c 'import json,sys; data=json.load(sys.stdin); sys.exit(0 if data.get("mode") in ("google","demo") and "configured" in data else 1)' 2>/dev/null
}

check_dashboard() {
  curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:5173/ 2>/dev/null | grep -q '<title>ProDog</title>'
}

check_control() {
  curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8000/prompts 2>/dev/null | "$python" -c 'import json,sys; sys.exit(0 if isinstance(json.load(sys.stdin).get("prompts"),list) else 1)' 2>/dev/null
}

check_stack() {
  local state health
  state=$(docker inspect --format '{{.State.Status}}' agent-container-control-1 2>/dev/null) || return 1
  health=$(docker inspect --format '{{.State.Health.Status}}' agent-test-receiver 2>/dev/null) || return 1
  [ "$state" = running ] && [ "$health" = healthy ] && check_control
}

wait_ready() {
  local attempt=0
  while [ "$attempt" -lt 30 ]; do
    if "$1"; then return 0; fi
    sleep 1
    attempt=$((attempt + 1))
  done
  return 1
}

validate_compose_project() {
  local ids id directory physical name project
  ids=$(docker ps -aq --filter label=com.docker.compose.project=agent-container) || return 1
  for id in $ids; do
    directory=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$id") || return 1
    if [ ! -d "$directory" ]; then
      echo 'Docker 프로젝트 경로를 확인할 수 없어 다른 컨테이너를 건드리지 않습니다.' >&2
      return 1
    fi
    physical=$(CDPATH= cd -P -- "$directory" && pwd -P) || return 1
    if [ "$physical" != "$agent_dir" ]; then
      echo '같은 이름의 다른 Docker 프로젝트가 있어 작업을 중단합니다.' >&2
      return 1
    fi
  done
  # Compose service names are shared globally, including the fixed receiver name.
  for name in agent-container-control-1 agent-test-receiver; do
    if docker inspect "$name" >/dev/null 2>&1; then
      project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$name") || return 1
      if [ "$project" != agent-container ]; then
        echo '동적 검사 컨테이너 이름을 다른 Docker 프로젝트가 사용하고 있습니다.' >&2
        return 1
      fi
    fi
  done
}
