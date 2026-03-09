#!/bin/bash
# 投资组合日报入口脚本
#
# 正确流程（两步，由 cron + agent 协作完成）：
#
#   步骤 1 — cron 触发（北京时间 21:30，收盘后有充分时间更新持仓）：
#     bash portfolio-daily.sh notify
#     → 克隆昨日持仓为今日基底，推送飞书消息询问持仓变化
#
#   步骤 2 — 用户回复飞书后，agent/手动触发：
#     bash portfolio-daily.sh update "现金变为5000, 卖了500股药明康德"
#     → 解析变化 → 更新 holdings 文件 → 生成快照 → 推送报告 → 同步 QR Dashboard
#
#     如果无变化，直接：
#     bash portfolio-daily.sh pipeline
#     → 使用当日默认持仓生成快照 → 推送报告 → 同步 QR Dashboard
#
# cron 示例（北京 21:30 = UTC 13:30）：
#   30 13 * * 1-5  bash /path/to/engine/scripts/portfolio-daily.sh notify
#
# 环境变量：
#   PORTFOLIO_DIR  — 数据目录（默认: engine/portfolio/）
#   FEISHU_CHAT_ID — 飞书推送目标（user openId 或 chat chatId）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="${PORTFOLIO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TODAY=$(date +%Y-%m-%d)
DOW=$(date +%u)   # 1=Mon..7=Sun
ACTION="${1:-notify}"

# ── 跳过周末 ──
if [ "$DOW" -gt 5 ]; then
    echo "⏭️ 周末不执行"
    exit 0
fi

# ── 中国法定节假日检查（每年初更新） ──
HOLIDAYS="2026-01-01 2026-01-26 2026-01-27 2026-01-28 2026-01-29 2026-01-30 2026-02-16 2026-02-17 2026-05-01 2026-05-02 2026-05-05 2026-06-19 2026-10-01 2026-10-02 2026-10-05 2026-10-06 2026-10-07"
if echo "$HOLIDAYS" | grep -qw "$TODAY"; then
    echo "⏭️ 节假日不执行: $TODAY"
    exit 0
fi

UPDATER="$SCRIPT_DIR/portfolio_daily_update.py"
export PORTFOLIO_DIR="$WORKSPACE"

case "$ACTION" in
    notify)
        # 步骤1：克隆昨日持仓 + 推送飞书询问变化
        echo "📢 [$TODAY] 发送持仓变化询问..."
        python3 "$UPDATER" --action notify --date "$TODAY"
        echo "✅ 飞书通知已发送，等待用户回复后执行 'update' 或 'pipeline'"
        ;;

    update)
        # 步骤2A：有变化 — 传入用户描述（来自飞书回复或命令行）
        CHANGES="${2:-}"
        if [ -z "$CHANGES" ]; then
            echo "Usage: $0 update '现金变为5000, 卖了500股药明康德'"
            exit 1
        fi
        echo "📝 [$TODAY] 应用持仓变化并生成报告..."
        python3 "$UPDATER" --action update --date "$TODAY" --text "$CHANGES"
        echo "✅ 持仓已更新，日报已生成并推送"
        ;;

    pipeline)
        # 步骤2B：无变化 — 直接用当日持仓生成报告
        echo "📊 [$TODAY] 使用当日持仓生成日报..."
        python3 "$UPDATER" --action pipeline --date "$TODAY"
        echo "✅ 日报已生成并推送"
        ;;

    auto-pipeline)
        # 兼容旧的自动模式（如果快照已存在则跳过）
        python3 "$UPDATER" --action auto-pipeline --date "$TODAY"
        ;;

    *)
        echo "Usage: $0 [notify|update '<changes>'|pipeline|auto-pipeline]"
        echo ""
        echo "  notify          推送飞书询问 → 等待用户回复（cron 入口）"
        echo "  update '<text>' 更新持仓并生成报告（用户有变化时）"
        echo "  pipeline        直接生成报告（用户无变化时）"
        exit 1
        ;;
esac
