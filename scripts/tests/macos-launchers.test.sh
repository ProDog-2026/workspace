#!/bin/bash
# Isolated fake services; never contacts real Docker or kills real processes.
set -euo pipefail
source_root=$(CDPATH= cd -P -- "$(dirname "$0")/../.." && pwd -P)
temp_root=${TMPDIR:-/tmp}
tmp=$(mktemp -d "$temp_root/prodog-launchers.XXXXXX")
cleanup() {
  case "$tmp" in "$temp_root"/prodog-launchers.*) rm -rf -- "$tmp" ;; *) echo 'Unexpected test path' >&2; exit 1 ;; esac
}
trap cleanup EXIT
mkdir -p "$tmp/프로젝트 with spaces/scripts" "$tmp/bin" "$tmp/state"
MOCK_FIXTURE=$(CDPATH= cd -P -- "$tmp/프로젝트 with spaces" && pwd -P)
MOCK_STATE=$(CDPATH= cd -P -- "$tmp/state" && pwd -P)
export MOCK_FIXTURE MOCK_STATE
cp "$source_root/run.sh" "$source_root/stop.sh" "$source_root/run.command" "$source_root/stop.command" "$MOCK_FIXTURE/"
cp "$source_root/scripts/macos-common.sh" "$MOCK_FIXTURE/scripts/"
mkdir -p "$MOCK_FIXTURE/src/gmail/.venv/bin" "$MOCK_FIXTURE/src/dashboard/node_modules/vite/bin" "$MOCK_FIXTURE/src/agent-container"
printf 'dummy\n' > "$MOCK_FIXTURE/src/gmail/.env"
printf 'dummy\n' > "$MOCK_FIXTURE/src/dashboard/node_modules/vite/bin/vite.js"

# Exercise real ps/lsof against a disposable process in a Unicode/spaced path.
root="$MOCK_FIXTURE"
source "$source_root/scripts/macos-common.sh"
(cd "$gmail_dir"; exec python3 -c 'import time; time.sleep(90)' prodog_gmail.app:app) &
test_pid=$!
sleep 1
if ! is_project_process "$test_pid" gmail; then
  kill "$test_pid" 2>/dev/null || true
  wait "$test_pid" 2>/dev/null || true
  echo 'Real process ownership check failed.' >&2
  exit 1
fi
kill "$test_pid"
wait "$test_pid" 2>/dev/null || true
echo 'Real process ownership check passed.'

cat > "$MOCK_FIXTURE/src/gmail/.venv/bin/python" <<'EOF'
#!/bin/bash
exec python3 "$@"
EOF
cat > "$tmp/bin/docker" <<'EOF'
#!/bin/bash
set -eu
echo "$*" >> "$MOCK_STATE/docker-calls"
case "$1" in
  info) exit 0 ;;
  image) [ -f "$MOCK_STATE/images" ] ;;
  inspect)
    case "$*" in
      *working_dir*) if [ -f "$MOCK_STATE/foreign-project" ]; then echo '/other/project'; else echo "$MOCK_FIXTURE/src/agent-container"; fi ;;
      *com.docker.compose.project*) if [ -f "$MOCK_STATE/foreign-name" ]; then echo other-project; else echo agent-container; fi ;;
      *State.Status*) [ -f "$MOCK_STATE/stack" ] && echo running || exit 1 ;;
      *Health.Status*) [ -f "$MOCK_STATE/stack" ] && echo healthy || exit 1 ;;
      *agent.run_id*) if [ -f "$MOCK_STATE/foreign-worker" ]; then echo other-run; else echo test-run; fi ;;
      *agent-container-control-1*|*agent-test-receiver*) [ -f "$MOCK_STATE/stack" ] ;;
      *) exit 1 ;;
    esac ;;
  ps)
    case "$*" in
      *project=agent-container*) [ ! -f "$MOCK_STATE/stack" ] || echo control-id ;;
      *agent.run_id*) [ ! -f "$MOCK_STATE/busy" ] || echo worker-id ;;
    esac ;;
  build) touch "$MOCK_STATE/images" ;;
  compose)
    case "$*" in
      *' up '*--no-build*) touch "$MOCK_STATE/stack" ;;
      *' up '*) echo 'Compose must not invoke Bake.' >&2; exit 1 ;;
      *' stop '*) rm -f "$MOCK_STATE/stack" ;;
      *) exit 1 ;;
    esac ;;
  stop) touch "$MOCK_STATE/worker-stopped"; rm -f "$MOCK_STATE/busy" ;;
  *) exit 1 ;;
esac
EOF
cat > "$tmp/bin/curl" <<'EOF'
#!/bin/bash
case "$*" in
  *'/api/session'*) [ -f "$MOCK_STATE/gmail" ] || exit 7; printf '{"mode":"demo","configured":true}' ;;
  *'/prompts'*) [ -f "$MOCK_STATE/stack" ] || exit 7; printf '{"prompts":[]}' ;;
  *'/runs/'*'/stop'*) touch "$MOCK_STATE/run-stopped"; printf '{}' ;;
  *'/runs'*) [ -f "$MOCK_STATE/stack" ] || exit 7; if [ -f "$MOCK_STATE/active-job" ]; then printf '[{"run_id":"test-run","status":"running","phase":"executing"}]'; else printf '[]'; fi ;;
  *'127.0.0.1:5173/'*) [ -f "$MOCK_STATE/dashboard" ] || exit 7; printf '<title>ProDog</title>' ;;
  *) exit 7 ;;
esac
EOF
cat > "$tmp/bin/lsof" <<'EOF'
#!/bin/bash
case "$*" in
  *'-tiTCP:8765'*) [ ! -f "$MOCK_STATE/gmail" ] || echo 101 ;;
  *'-tiTCP:5173'*) if [ -f "$MOCK_STATE/foreign-port" ]; then echo 303; elif [ -f "$MOCK_STATE/dashboard" ]; then echo 202; fi ;;
  *'-tiTCP:8000'*) [ ! -f "$MOCK_STATE/stack" ] || echo 404 ;;
  *'-p 101'*) printf 'p101\nfcwd\nn%s/src/gmail\n' "$MOCK_FIXTURE" ;;
  *'-p 202'*) printf 'p202\nfcwd\nn%s/src/dashboard\n' "$MOCK_FIXTURE" ;;
  *'-p 303'*) printf 'p303\nfcwd\nn/other/project\n' ;;
esac
EOF
cat > "$tmp/bin/ps" <<'EOF'
#!/bin/bash
case "$*" in
  *'uid='*) echo "$(id -u)" ;;
  *'-p 101 '*) [ ! -f "$MOCK_STATE/gmail" ] || echo 'python -m uvicorn prodog_gmail.app:app' ;;
  *'-p 202 '*) [ ! -f "$MOCK_STATE/dashboard" ] || echo 'node vite' ;;
  *'-p 303 '*) echo 'node vite' ;;
esac
EOF
cat > "$tmp/bin/nohup" <<'EOF'
#!/bin/bash
case "$*" in *uvicorn*) touch "$MOCK_STATE/gmail" ;; *vite*) touch "$MOCK_STATE/dashboard" ;; *) exit 1 ;; esac
EOF
cat > "$tmp/bin/open" <<'EOF'
#!/bin/bash
echo "$*" >> "$MOCK_STATE/open-calls"
EOF
cat > "$tmp/bin/node" <<'EOF'
#!/bin/bash
exit 0
EOF
cat > "$tmp/bin/npm" <<'EOF'
#!/bin/bash
touch "$MOCK_STATE/npm-installed"
mkdir -p "$MOCK_FIXTURE/src/dashboard/node_modules/vite/bin"
touch "$MOCK_FIXTURE/src/dashboard/node_modules/vite/bin/vite.js"
EOF
cat > "$tmp/bin/uv" <<'EOF'
#!/bin/bash
touch "$MOCK_STATE/uv-installed"
mkdir -p "$MOCK_FIXTURE/src/gmail/.venv/bin"
printf '#!/bin/bash\nexec python3 "$@"\n' > "$MOCK_FIXTURE/src/gmail/.venv/bin/python"
chmod +x "$MOCK_FIXTURE/src/gmail/.venv/bin/python"
exit 0
EOF
chmod +x "$tmp/bin/"* "$MOCK_FIXTURE/src/gmail/.venv/bin/python"
export PATH="$tmp/bin:$PATH"

# Override the Bash builtin only inside this test's child shells.
kill() {
  case "$*" in
    '-TERM 101') rm -f "$MOCK_STATE/gmail" ;;
    '-TERM 202') rm -f "$MOCK_STATE/dashboard" ;;
    '-TERM 303') touch "$MOCK_STATE/foreign-killed" ;;
    '-0 101') [ -f "$MOCK_STATE/gmail" ] ;;
    '-0 202') [ -f "$MOCK_STATE/dashboard" ] ;;
    '-0 303') [ -f "$MOCK_STATE/foreign-port" ] ;;
    *) return 1 ;;
  esac
}
export -f kill

bash "$MOCK_FIXTURE/run.command"
[ -f "$MOCK_STATE/stack" ] && [ -f "$MOCK_STATE/gmail" ] && [ -f "$MOCK_STATE/dashboard" ]
[ "$(cat "$MOCK_FIXTURE/src/gmail/.env")" = dummy ]
[ "$(grep -c '^build ' "$MOCK_STATE/docker-calls")" -eq 3 ]
grep -Fq 'build -t agent-container-control -f control-dashboard/Dockerfile control-dashboard' "$MOCK_STATE/docker-calls"
grep -Fq 'build -t agent-container-receiver -f control-dashboard/receiver/Dockerfile control-dashboard' "$MOCK_STATE/docker-calls"
grep -Fq 'build -t agent-container-worker:phase1 -f agent-container/Dockerfile .' "$MOCK_STATE/docker-calls"
grep -Fq 'compose --project-name agent-container up -d --no-build' "$MOCK_STATE/docker-calls"
[ "$(grep -c 'compose .* up ' "$MOCK_STATE/docker-calls")" -eq 1 ]
bash "$MOCK_FIXTURE/run.sh"
[ "$(grep -c 'compose .* up ' "$MOCK_STATE/docker-calls")" -eq 1 ]
bash "$MOCK_FIXTURE/stop.command"
[ ! -f "$MOCK_STATE/stack" ] && [ ! -f "$MOCK_STATE/gmail" ] && [ ! -f "$MOCK_STATE/dashboard" ]
bash "$MOCK_FIXTURE/stop.sh"

touch "$MOCK_STATE/stack" "$MOCK_STATE/busy" "$MOCK_STATE/active-job"
bash "$MOCK_FIXTURE/stop.sh"
[ -f "$MOCK_STATE/run-stopped" ] && [ -f "$MOCK_STATE/worker-stopped" ] && [ ! -f "$MOCK_STATE/busy" ]
rm -f "$MOCK_STATE/run-stopped" "$MOCK_STATE/worker-stopped" "$MOCK_STATE/active-job"
touch "$MOCK_STATE/stack" "$MOCK_STATE/busy" "$MOCK_STATE/foreign-worker"
if bash "$MOCK_FIXTURE/stop.sh"; then echo 'Unrecognized worker should be reported.' >&2; exit 1; fi
[ -f "$MOCK_STATE/busy" ] && [ ! -f "$MOCK_STATE/worker-stopped" ]
rm -f "$MOCK_STATE/busy" "$MOCK_STATE/foreign-worker"

# First-run dependency installation and .env creation, without the real tools.
rm -f "$MOCK_FIXTURE/src/gmail/.venv/bin/python" "$MOCK_FIXTURE/src/gmail/.env" "$MOCK_FIXTURE/src/dashboard/node_modules/vite/bin/vite.js" "$MOCK_STATE/images"
mkdir -p "$MOCK_FIXTURE/src/gmail/scripts"
cat > "$MOCK_FIXTURE/src/gmail/scripts/setup_local.py" <<'EOF'
from pathlib import Path
(Path(__file__).resolve().parents[1] / '.env').write_text('demo')
EOF
bash "$MOCK_FIXTURE/run.sh"
[ -f "$MOCK_STATE/uv-installed" ] && [ -f "$MOCK_STATE/npm-installed" ]
[ "$(cat "$MOCK_FIXTURE/src/gmail/.env")" = demo ]
bash "$MOCK_FIXTURE/stop.sh"

touch "$MOCK_STATE/foreign-port"
if bash "$MOCK_FIXTURE/stop.sh"; then echo 'Foreign listener should be reported.' >&2; exit 1; fi
[ ! -f "$MOCK_STATE/foreign-killed" ]
rm -f "$MOCK_STATE/foreign-port" "$MOCK_STATE/images"
touch "$MOCK_STATE/busy"
before=$(grep -c 'compose .* up ' "$MOCK_STATE/docker-calls")
if bash "$MOCK_FIXTURE/run.sh"; then echo 'Active worker should block build.' >&2; exit 1; fi
[ "$(grep -c 'compose .* up ' "$MOCK_STATE/docker-calls")" -eq "$before" ]
rm -f "$MOCK_STATE/busy"
touch "$MOCK_STATE/stack" "$MOCK_STATE/foreign-project"
if bash "$MOCK_FIXTURE/stop.sh"; then echo 'Foreign Compose project should be rejected.' >&2; exit 1; fi
[ -f "$MOCK_STATE/stack" ]
rm -f "$MOCK_STATE/foreign-project"
touch "$MOCK_STATE/foreign-name"
if bash "$MOCK_FIXTURE/stop.sh"; then echo 'Foreign fixed container name should be rejected.' >&2; exit 1; fi
[ -f "$MOCK_STATE/stack" ]
echo 'macOS launcher mock tests passed.'
