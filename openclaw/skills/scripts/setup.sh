#!/usr/bin/env bash
# portfolio-daily-tracker skill — Quick setup helper
# Clones the repo and sets up the basic directory structure.
#
# Usage:
#   bash setup.sh [target_dir]
#
# After running, follow instructions to configure config.json and .env
set -euo pipefail

TARGET="${1:-portfolio-daily-tracker}"

echo "=== Portfolio Daily Tracker — Setup ==="

# 1. Clone repo (if not already inside one)
if [ ! -d "$TARGET" ]; then
  echo "[1/4] Cloning repository..."
  git clone https://github.com/Stepuuu/portfolio-daily-tracker.git "$TARGET"
else
  echo "[1/4] Directory '$TARGET' already exists, skipping clone."
fi

cd "$TARGET"

# 2. Create data directories
echo "[2/4] Creating data directories..."
mkdir -p engine/portfolio/holdings engine/portfolio/snapshots

# 3. Copy example configs if real ones don't exist
echo "[3/4] Setting up config files..."
if [ ! -f engine/portfolio/config.json ] && [ -f engine/portfolio/config.example.json ]; then
  cp engine/portfolio/config.example.json engine/portfolio/config.json
  echo "  → Copied config.example.json → config.json (edit this!)"
fi

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "  → Copied .env.example → .env (add your API keys here!)"
fi

# 4. Install Python dependencies
echo "[4/4] Installing Python dependencies..."
if command -v pip3 &>/dev/null; then
  pip3 install -r dashboard/requirements.txt 2>/dev/null || echo "  ⚠ pip install failed — install manually: pip3 install -r dashboard/requirements.txt"
else
  echo "  ⚠ pip3 not found — install manually: pip3 install -r dashboard/requirements.txt"
fi

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit engine/portfolio/config.json — define your portfolio groups"
echo "  2. Edit .env — add API keys (OPENAI_API_KEY, FEISHU_WEBHOOK, etc.)"
echo "  3. Create your first holdings file:"
echo "     python3 engine/scripts/portfolio_manager.py add NASDAQ:AAPL Apple Growth --qty 100 --cost 150.0"
echo "  4. Generate a snapshot:"
echo "     python3 engine/scripts/portfolio_snapshot.py --date \$(date +%Y-%m-%d)"
echo "  5. Generate a report:"
echo "     python3 engine/scripts/portfolio_report.py --date \$(date +%Y-%m-%d)"
