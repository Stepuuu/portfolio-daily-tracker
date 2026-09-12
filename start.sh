#!/usr/bin/env bash
# Development launcher. Run make setup once before starting.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
export BACKEND_PORT="${BACKEND_PORT:-8000}"
export FRONTEND_PORT="${FRONTEND_PORT:-3000}"
export BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
export FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
TRACKER_PYTHON="${PYTHON:-python3}"
MODE="${1:-all}"
case "$MODE" in all|backend|frontend|engine) ;; *) echo 'Usage: ./start.sh [all|backend|frontend|engine]' >&2; exit 2;; esac
command -v "$TRACKER_PYTHON" >/dev/null || { echo 'Python 3.10+ is required.' >&2; exit 1; }
pids=()
TRACKER_PID_FILE=""
if [ "$MODE" != "engine" ]; then
    mkdir -p .runtime
    TRACKER_PID_FILE="$SCRIPT_DIR/.runtime/$$.json"
    "$TRACKER_PYTHON" - "$$" "$TRACKER_PID_FILE" <<'PYCODE'
import json, subprocess, sys
from pathlib import Path
pid = int(sys.argv[1])
started = subprocess.check_output(['ps', '-p', str(pid), '-o', 'lstart='], text=True).strip()
Path(sys.argv[2]).write_text(json.dumps({'pid': pid, 'started': started}))
PYCODE
fi
cleanup() {
    trap - EXIT INT TERM
    if [ -n "$TRACKER_PID_FILE" ]; then rm -f -- "$TRACKER_PID_FILE"; fi
    for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
    for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
start_backend() {
    "$TRACKER_PYTHON" -c 'import fastapi, uvicorn, pydantic, pandas' || {
        echo 'Backend dependencies are missing. Run make setup first.' >&2; exit 1;
    }
    "$TRACKER_PYTHON" - "$BACKEND_HOST" "$BACKEND_PORT" <<'PY'
import socket, sys
with socket.socket() as sock:
    sock.bind((sys.argv[1], int(sys.argv[2])))
PY
    (
        cd "$SCRIPT_DIR/dashboard"
        exec "$TRACKER_PYTHON" -m uvicorn backend.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
    ) &
    local pid=$!
    pids+=("$pid")
    local ready=0
    for ((attempt=0; attempt<30; attempt++)); do
        kill -0 "$pid" 2>/dev/null || { echo 'Backend exited during startup.' >&2; exit 1; }
        if "$TRACKER_PYTHON" - "$BACKEND_PORT" <<'PY' >/dev/null 2>&1
import urllib.request, sys
urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:' + sys.argv[1] + '/health', timeout=1)
PY
        then ready=1; break; fi
        sleep 1
    done
    [ "$ready" -eq 1 ] || { echo 'Backend did not become ready within 30 seconds.' >&2; exit 1; }
    echo "Backend ready: http://localhost:$BACKEND_PORT"
}
start_frontend() {
    [ -d dashboard/frontend/node_modules ] || { echo 'Run make setup to install frontend dependencies.' >&2; exit 1; }
    (
        cd "$SCRIPT_DIR/dashboard/frontend"
        exec node node_modules/vite/bin/vite.js --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
    ) &
    pids+=("$!")
    echo "Frontend starting: http://localhost:$FRONTEND_PORT"
}
case "$MODE" in
    backend) start_backend ;;
    frontend) start_frontend ;;
    all) start_backend; start_frontend ;;
    engine) exec "$TRACKER_PYTHON" engine/scripts/portfolio_snapshot.py ;;
esac
# Stop the companion service if either exits; EXIT also handles Ctrl+C.
wait -n "${pids[@]}"
