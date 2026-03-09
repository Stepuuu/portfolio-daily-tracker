#!/bin/bash
# 投资组合每日定时任务
# ┌─────────────────────────────────────────────────────┐
# │  21:30 (UTC 13:30) — 克隆持仓 + 飞书询问变化       │
# │  22:30 (UTC 14:30) — 自动生成快照（如用户未回复）   │
# └─────────────────────────────────────────────────────┘
#
# crontab 设置:
#   30 13 * * 1-5 /path/to/portfolio-cron.sh notify
#   30 14 * * 1-5 /path/to/portfolio-cron.sh auto-pipeline
#
# 手动运行:
#   bash portfolio-cron.sh notify         # 发送通知
#   bash portfolio-cron.sh auto-pipeline  # 自动生成快照（无回复时的兜底）
#   bash portfolio-cron.sh pipeline       # 强制运行管道

set -euo pipefail

WORKSPACE="${PORTFOLIO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
SCRIPT="$WORKSPACE/scripts/portfolio_daily_update.py"
CONDA="${CONDA:-$(which conda 2>/dev/null || echo conda)}"
TODAY=$(date +%Y-%m-%d)
DOW=$(date +%u)  # 1=Mon..7=Sun
LOG_DIR="$WORKSPACE/logs"
LOG_FILE="$LOG_DIR/portfolio-$(date +%Y%m%d).log"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ── 跳过周末 ──
if [ "$DOW" -gt 5 ]; then
    log "⏭️ 周末不运行"
    exit 0
fi

# ── 中国法定节假日检查 ──
HOLIDAYS="2026-01-01 2026-01-26 2026-01-27 2026-01-28 2026-01-29 2026-01-30 2026-02-16 2026-02-17 2026-05-01 2026-05-02 2026-05-05 2026-06-19 2026-10-01 2026-10-02 2026-10-05 2026-10-06 2026-10-07"
if echo "$HOLIDAYS" | grep -qw "$TODAY"; then
    log "⏭️ 节假日不运行: $TODAY"
    exit 0
fi

ACTION="${1:-notify}"
log "🚀 开始执行: $ACTION (日期: $TODAY)"

case "$ACTION" in
    notify)
        $CONDA run -n ${CONDA_ENV:-base} python3 "$SCRIPT" --action notify --date "$TODAY" 2>&1 | tee -a "$LOG_FILE"
        ;;
    auto-pipeline)
        $CONDA run -n ${CONDA_ENV:-base} python3 "$SCRIPT" --action auto-pipeline --date "$TODAY" 2>&1 | tee -a "$LOG_FILE"
        ;;
    pipeline)
        $CONDA run -n ${CONDA_ENV:-base} python3 "$SCRIPT" --action pipeline --date "$TODAY" 2>&1 | tee -a "$LOG_FILE"
        ;;
    update)
        TEXT="${2:-}"
        if [ -z "$TEXT" ]; then
            log "❌ update 需要提供变更文本"
            exit 1
        fi
        $CONDA run -n ${CONDA_ENV:-base} python3 "$SCRIPT" --action update --date "$TODAY" --text "$TEXT" 2>&1 | tee -a "$LOG_FILE"
        ;;
    *)
        echo "Usage: $0 {notify|auto-pipeline|pipeline|update \"text\"}"
        exit 1
        ;;
esac

EXIT_CODE=$?
log "✅ 执行完成 (exit: $EXIT_CODE)"
exit $EXIT_CODE
