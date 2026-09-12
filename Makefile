# Portfolio Daily Tracker — Makefile
# ────────────────────────────────────────
# Quick-start commands for common operations
#
# Usage:
#   make setup     — One-time setup (install deps, copy configs)
#   make start     — Start backend + frontend
#   make stop      — Stop all services
#   make snapshot  — Generate today's portfolio snapshot
#   make docker    — Start with Docker Compose
#   make clean     — Remove __pycache__, node_modules, etc.

.PHONY: setup start stop snapshot docker clean help backend frontend cli test demo lab

help:
	@echo ""
	@echo "  Portfolio Daily Tracker"
	@echo "  ─────────────────────────"
	@echo "  make setup      One-time setup (install deps, copy configs)"
	@echo "  make start      Start backend + frontend (dev mode)"
	@echo "  make stop       Stop all running services"
	@echo "  make backend    Start backend only"
	@echo "  make frontend   Start frontend only"
	@echo "  make cli        Start CLI trading assistant"
	@echo "  make snapshot   Generate today's portfolio snapshot"
	@echo "  make lab        Open the stock research workbench without account services"
	@echo "  make demo       Explore fictional data, without API keys"
	@echo "  make test       Run offline regression tests"
	@echo "  make docker     Start with Docker Compose"
	@echo "  make clean      Remove caches and temp files"
	@echo ""

# ── One-time setup ──
setup:
	@echo "Setting up Portfolio Daily Tracker..."
	@# Python deps
	python3 -m pip install -r dashboard/requirements.txt
	python3 -m pip install requests
	@# Frontend deps
	cd dashboard/frontend && npm ci
	@# Copy config templates (don't overwrite existing)
	@[ -f dashboard/config.json ] || cp dashboard/config.example.json dashboard/config.json
	@[ -f engine/portfolio/config.json ] || cp engine/portfolio/config.example.json engine/portfolio/config.json
	@# Create data directories
	@mkdir -p engine/portfolio/holdings engine/portfolio/snapshots
	@mkdir -p dashboard/data/conversations dashboard/data/screenshots
	@echo ""
	@echo "✓ Setup complete!"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Edit dashboard/config.json — add your LLM API key"
	@echo "  2. Edit engine/portfolio/config.json — set group names and cost basis"
	@echo "  3. Create engine/portfolio/holdings/$$(date +%Y-%m-%d).json"
	@echo "  4. Run: make start"

# ── Development ──
test:
	python3 -m pytest -q

demo:
	@[ -d .demo/portfolio ] || python3 engine/scripts/create_demo.py --output .demo/portfolio
	@echo "Open http://localhost:$${FRONTEND_PORT:-3000}/tracker — fictional data only"
	PORTFOLIO_DIR="$(CURDIR)/.demo/portfolio" TRACKER_LAB_DIR="$(CURDIR)/.demo/research" TRACKER_DEMO_MODE=1 bash start.sh

lab:
	@echo "Open http://localhost:$${FRONTEND_PORT:-3000}/lab"
	TRACKER_LAB_ONLY=1 bash start.sh

# ── Development ──
start:
	@bash start.sh

stop:
	@bash stop.sh

backend:
	@bash start.sh backend

frontend:
	@bash start.sh frontend

cli:
	@cd dashboard && python3 main.py

# ── Engine ──
snapshot:
	@cd engine && python3 scripts/portfolio_snapshot.py

# ── Docker ──
docker:
	docker compose up --build

docker-down:
	docker compose down

# ── Cleanup ──
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .vite -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✓ Cleaned"
