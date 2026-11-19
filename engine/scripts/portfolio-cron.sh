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
#
# 解释器（与 portfolio_daily_update.py / portfolio_scheduler.py 相同的规则）：
#   默认使用 PATH 中的 python3（可用 PYTHON 指定其他解释器）。
#   设置 CONDA_ENV=<环境名> 时改用 `conda run -n <环境名> python3`，
#   conda 可执行文件可用 CONDA 或 CONDA_EXE 指定。

set -euo pipefail

WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"
export PORTFOLIO_DIR="${PORTFOLIO_DIR:-$WORKSPACE/portfolio}"
SCRIPT="$WORKSPACE/scripts/portfolio_daily_update.py"
if [ -n "${CONDA_ENV:-}" ]; then
    CONDA="${CONDA:-${CONDA_EXE:-$(command -v conda 2>/dev/null || echo conda)}}"
    PYTHON_CMD=("$CONDA" run -n "$CONDA_ENV" python3)
else
    PYTHON_CMD=("${PYTHON:-python3}")
fi
# 交易日按北京时间计（与引擎的 --date 默认值相同），与服务器所在时区无关；CST-8 即 UTC+8，不需要时区数据库。
TODAY=$(TZ=CST-8 date +%Y-%m-%d)
DOW=$(TZ=CST-8 date +%u)  # 1=Mon..7=Sun
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
# 上交所 2026 年工作日休市日，依据其 2025-12-22 通知（上证公告〔2025〕45号）：
# 元旦 1/1-1/3、春节 2/15-2/23、清明 4/4-4/6、劳动节 5/1-5/5、端午 6/19-6/21、中秋 9/25-9/27、国庆 10/1-10/7。
# 每年按交易所通知更新，并与 portfolio_scheduler.py 中的 HOLIDAYS 保持一致。
HOLIDAYS="2026-01-01 2026-01-02 2026-02-16 2026-02-17 2026-02-18 2026-02-19 2026-02-20 2026-02-23 2026-04-06 2026-05-01 2026-05-04 2026-05-05 2026-06-19 2026-09-25 2026-10-01 2026-10-02 2026-10-05 2026-10-06 2026-10-07"
if echo "$HOLIDAYS" | grep -qw "$TODAY"; then
    log "⏭️ 节假日不运行: $TODAY"
    exit 0
fi

ACTION="${1:-notify}"
log "🚀 开始执行: $ACTION (日期: $TODAY)"

case "$ACTION" in
    notify|auto-pipeline|pipeline)
        ARGS=(--action "$ACTION" --date "$TODAY")
        ;;
    update)
        TEXT="${2:-}"
        if [ -z "$TEXT" ]; then
            log "❌ update 需要提供变更文本"
            exit 1
        fi
        ARGS=(--action update --date "$TODAY" --text "$TEXT")
        ;;
    *)
        echo "Usage: $0 {notify|auto-pipeline|pipeline|update \"text\"}"
        exit 1
        ;;
esac

# Under set -e a failing pipeline would end the script before its result is
# logged. Record the Python exit code from PIPESTATUS (a failing tee only counts
# when Python itself succeeded), log it and exit with it.
set +e
"${PYTHON_CMD[@]}" "$SCRIPT" "${ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
STATUS=("${PIPESTATUS[@]}")
set -e
EXIT_CODE=${STATUS[0]}
if [ "$EXIT_CODE" -eq 0 ]; then
    EXIT_CODE=${STATUS[1]}
fi
if [ "$EXIT_CODE" -eq 0 ]; then
    log "✅ 执行完成 (exit: 0)"
else
    log "❌ 执行失败 (exit: $EXIT_CODE)"
fi
exit "$EXIT_CODE"
