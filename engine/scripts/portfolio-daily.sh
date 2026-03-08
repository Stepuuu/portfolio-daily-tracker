#!/bin/bash
# 投资组合每日快照 + 报告 + 推送
# 推送时间：工作日收盘后（北京时间 15:30 → UTC 07:30）
# cron: 30 7 * * 1-5
# 架构：判断交易日 → 取价 → 计算快照 → 生成报告 → 推送飞书

WORKSPACE="${PORTFOLIO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
TODAY=$(date +%Y-%m-%d)
DOW=$(date +%u)  # 1=Mon..7=Sun
REPORT_FILE="$WORKSPACE/reports/portfolio-$(date +%Y%m%d).md"
FEISHU_CHAT_ID="${FEISHU_CHAT_ID:-}"

# ── 跳过周末（双保险，cron已经限制1-5） ──
if [ "$DOW" -gt 5 ]; then
    echo "⏭️ 周末不推送"
    exit 0
fi

# ── 中国法定节假日检查（手动维护，每年初更新） ──
HOLIDAYS="2026-01-01 2026-01-26 2026-01-27 2026-01-28 2026-01-29 2026-01-30 2026-02-16 2026-02-17 2026-05-01 2026-05-02 2026-05-05 2026-06-19 2026-10-01 2026-10-02 2026-10-05 2026-10-06 2026-10-07"
if echo "$HOLIDAYS" | grep -qw "$TODAY"; then
    echo "⏭️ 节假日不推送: $TODAY"
    exit 0
fi

echo "📊 生成 $TODAY 投资组合日报..."
mkdir -p "$WORKSPACE/reports"

# ── 第一步：生成快照（取价+计算） ──
echo "→ 获取价格并生成快照..."
if ! python3 "$WORKSPACE/scripts/portfolio_snapshot.py" --date "$TODAY" 2>&1; then
    echo "❌ 快照生成失败"
    exit 1
fi

# ── 第二步：生成报告 ──
echo "→ 生成报告..."
python3 "$WORKSPACE/scripts/portfolio_report.py" --date "$TODAY" --output "$REPORT_FILE" 2>&1

if [ ! -s "$REPORT_FILE" ]; then
    echo "❌ 报告文件为空"
    exit 1
fi

REPORT_CONTENT=$(cat "$REPORT_FILE")

# ── 第三步：推送到飞书 ──
echo "→ 推送到飞书..."
openclaw message send \
    --channel feishu \
    --target "$FEISHU_CHAT_ID" \
    --message "$REPORT_CONTENT" 2>/dev/null || echo "⚠️ 飞书推送失败"

echo "✅ 投资组合日报完成！报告: $REPORT_FILE"
