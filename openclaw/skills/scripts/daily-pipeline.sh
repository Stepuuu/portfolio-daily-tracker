#!/usr/bin/env bash
# portfolio-daily-tracker skill — Daily pipeline runner
# Runs: snapshot → report → (optional) push notification
#
# Usage:
#   bash daily-pipeline.sh [date] [--push]
#
# Examples:
#   bash daily-pipeline.sh                  # today, no push
#   bash daily-pipeline.sh 2026-03-10       # specific date
#   bash daily-pipeline.sh --push           # today + push notifications
set -euo pipefail

# Find project root (look for engine/scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$SCRIPT_DIR/../../engine/scripts" ]; then
  PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
elif [ -d "./engine/scripts" ]; then
  PROJECT_ROOT="$(pwd)"
else
  echo "Error: Cannot find project root. Run from the portfolio-daily-tracker directory."
  exit 1
fi

# Parse arguments
DATE=""
PUSH=false
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=true ;;
    *) DATE="$arg" ;;
  esac
done
DATE="${DATE:-$(date +%Y-%m-%d)}"

echo "=== Portfolio Daily Pipeline — $DATE ==="
cd "$PROJECT_ROOT/engine"

# Step 1: Snapshot
echo "[1/3] Generating snapshot..."
python3 scripts/portfolio_snapshot.py --date "$DATE"
echo "  ✓ Snapshot saved to portfolio/snapshots/$DATE.json"

# Step 2: Report
echo "[2/3] Generating report..."
python3 scripts/portfolio_report.py --date "$DATE"
echo "  ✓ Report generated"

# Step 3: Push (optional)
if [ "$PUSH" = true ]; then
  echo "[3/3] Pushing notifications..."
  if [ -f scripts/portfolio-daily.sh ]; then
    bash scripts/portfolio-daily.sh "$DATE"
    echo "  ✓ Notifications sent"
  else
    echo "  ⚠ portfolio-daily.sh not found, skipping push"
  fi
else
  echo "[3/3] Push skipped (use --push to enable)"
fi

echo ""
echo "=== Pipeline complete ==="
