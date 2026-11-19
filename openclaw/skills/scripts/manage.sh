#!/usr/bin/env bash
# portfolio-daily-tracker skill — Position management helper
# Thin wrapper around portfolio_manager.py with friendlier interface
#
# Usage:
#   bash manage.sh show                              # show all holdings
#   bash manage.sh groups                             # show groups
#   bash manage.sh add <ticker> <name> <group> [--qty N] [--cost P]
#   bash manage.sh update <ticker> --qty N [--group G] [--cost P]
#   bash manage.sh remove <ticker> [--group G]
#   bash manage.sh set-fund --group G --value V
#   bash manage.sh set-cash --group G --value V
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

MANAGER="$PROJECT_ROOT/engine/scripts/portfolio_manager.py"

if [ ! -f "$MANAGER" ]; then
  echo "Error: portfolio_manager.py not found at $MANAGER"
  echo "Make sure the portfolio-daily-tracker repo is set up correctly."
  exit 1
fi

if [ $# -eq 0 ]; then
  echo "Portfolio Manager — Quick Commands"
  echo ""
  echo "  bash manage.sh show                        Show all holdings"
  echo "  bash manage.sh groups                      Show portfolio groups"
  echo "  bash manage.sh add NASDAQ:AAPL Apple Growth --qty 100 --cost 150"
  echo "  bash manage.sh update SHA:603259 --qty 4000 --group Growth"
  echo "  bash manage.sh remove NASDAQ:META --group Growth"
  echo "  bash manage.sh set-fund --group Growth --value 160000"
  echo "  bash manage.sh set-cash --group Growth --value -500000"
  echo ""
  echo "Ticker formats:"
  echo "  Shanghai A-shares: SHA:XXXXXX (e.g. SHA:603259)"
  echo "  Shenzhen A-shares: SHE:XXXXXX (e.g. SHE:002050)"
  echo "  Hong Kong:         HKG:XXXX   (e.g. HKG:0700)"
  echo "  NASDAQ:            NASDAQ:XXXX (e.g. NASDAQ:GOOGL)"
  echo "  NYSE:              NYSE:XXXX   (e.g. NYSE:BRK.B)"
  exit 0
fi

python3 "$MANAGER" "$@"
