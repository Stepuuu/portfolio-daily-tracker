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

# Find project root: the nearest directory above this script's real location
# that holds engine/scripts. A symlink to the script is followed (relative links
# included), and the current directory is never consulted, so the script always
# acts on its own checkout.
SOURCE="${BASH_SOURCE[0]}"
hops=0
while [ -L "$SOURCE" ]; do
  hops=$((hops + 1))
  if [ "$hops" -gt 40 ]; then
    echo "Error: Cannot find project root: too many symbolic links."
    exit 1
  fi
  LINK_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in /*) ;; *) SOURCE="$LINK_DIR/$SOURCE" ;; esac
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
PROJECT_ROOT=""
candidate="$SCRIPT_DIR"
while [ "$candidate" != "/" ]; do
  if [ -d "$candidate/engine/scripts" ]; then
    PROJECT_ROOT="$candidate"
    break
  fi
  candidate="$(dirname "$candidate")"
done
if [ -z "$PROJECT_ROOT" ]; then
  echo "Error: Cannot find project root above $SCRIPT_DIR. The script must stay inside its portfolio-daily-tracker checkout; link to it rather than copying it."
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
DATE="${DATE:-$(TZ=CST-8 date +%Y-%m-%d)}"   # today in Beijing (UTC+8), the trading day, whatever the server's zone

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
