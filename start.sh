#!/bin/bash
# ══════════════════════════════════════════
#   Portfolio Daily Tracker — Start Script
# ══════════════════════════════════════════
#
# Usage:
#   ./start.sh              # Start both backend + frontend
#   ./start.sh backend      # Backend only
#   ./start.sh frontend     # Frontend only
#   ./start.sh engine       # Run snapshot engine once

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1"; }

echo ""
echo "═══════════════════════════════════════"
echo "  Portfolio Daily Tracker"
echo "═══════════════════════════════════════"
echo ""

# ── Check Python ──
if ! command -v python3 &>/dev/null; then
    err "python3 not found. Please install Python 3.9+."
    exit 1
fi

# ── Auto-copy config templates if missing ──
if [ ! -f "dashboard/config.json" ]; then
    warn "dashboard/config.json not found — copying from example."
    cp dashboard/config.example.json dashboard/config.json
    echo "  ✓ Copied dashboard/config.example.json → dashboard/config.json"
    echo "  ⚠  Edit dashboard/config.json and add your API keys before using AI features."
fi

if [ ! -f "engine/portfolio/config.json" ]; then
    warn "engine/portfolio/config.json not found — copying from example."
    cp engine/portfolio/config.example.json engine/portfolio/config.json
    echo "  ✓ Copied engine/portfolio/config.example.json → engine/portfolio/config.json"
fi

mkdir -p engine/portfolio/holdings engine/portfolio/snapshots

MODE=${1:-all}

start_backend() {
    echo -e "${CYAN}Starting backend (FastAPI on :8000)...${NC}"
    cd "$SCRIPT_DIR/dashboard"
    pip install -q -r requirements.txt 2>/dev/null || true
    python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload &
    BACKEND_PID=$!
    echo "  PID: $BACKEND_PID"
    sleep 3

    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        log "Backend ready at http://localhost:8000"
    else
        warn "Backend may still be starting..."
    fi
    cd "$SCRIPT_DIR"
}

start_frontend() {
    echo -e "${CYAN}Starting frontend (Vite on :3000)...${NC}"
    cd "$SCRIPT_DIR/dashboard/frontend"

    if [ ! -d "node_modules" ]; then
        echo "  Installing frontend dependencies..."
        npm install
    fi

    npm run dev &
    FRONTEND_PID=$!
    echo "  PID: $FRONTEND_PID"
    cd "$SCRIPT_DIR"

    sleep 3
    log "Frontend ready at http://localhost:3000"
}

run_engine() {
    echo -e "${CYAN}Running portfolio snapshot engine...${NC}"
    cd "$SCRIPT_DIR/engine"
    pip install -q requests 2>/dev/null || true
    python3 scripts/portfolio_snapshot.py
    log "Snapshot complete. Check engine/portfolio/snapshots/"
    cd "$SCRIPT_DIR"
}

case "$MODE" in
    backend)
        start_backend
        wait
        ;;
    frontend)
        start_frontend
        wait
        ;;
    engine)
        run_engine
        ;;
    all)
        start_backend
        start_frontend
        echo ""
        echo "═══════════════════════════════════════"
        echo "  Services started:"
        echo "    Frontend: http://localhost:3000"
        echo "    Backend:  http://localhost:8000"
        echo "    API docs: http://localhost:8000/docs"
        echo ""
        echo "  Press Ctrl+C to stop all services"
        echo "═══════════════════════════════════════"
        trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM
        wait
        ;;
    *)
        echo "Usage: $0 [all|backend|frontend|engine]"
        exit 1
        ;;
esac
