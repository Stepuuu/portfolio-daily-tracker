#!/usr/bin/env python3
"""Portfolio report generator: read snapshot → create markdown report.

Usage:
    python3 portfolio_report.py [--date YYYY-MM-DD] [--output FILE]
"""

import json, os, sys, argparse, glob
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_DIR = os.path.join(os.path.dirname(BASE_DIR), "portfolio")


def load_snapshot(date_str):
    path = os.path.join(PORTFOLIO_DIR, "snapshots", f"{date_str}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def load_history_csv():
    """Load history.csv for trend data."""
    path = os.path.join(PORTFOLIO_DIR, "history.csv")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r") as f:
        header = f.readline().strip().split(",")
        for line in f:
            vals = line.strip().split(",")
            if len(vals) >= len(header):
                rows.append(dict(zip(header, vals)))
    return rows


def sparkline(values, width=20):
    """Generate a text sparkline from a list of numbers."""
    if not values or len(values) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    rng = mx - mn
    if rng == 0:
        return blocks[4] * min(len(values), width)

    # Sample if too many values
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values

    return "".join(blocks[min(int((v - mn) / rng * 7), 7)] for v in sampled)


def format_money(val, unit="万"):
    """Format money value as 万 or 元."""
    if unit == "万":
        return f"¥{val/10000:.2f}万"
    return f"¥{val:.2f}"


def generate_report(snapshot, history=None):
    """Generate markdown report from snapshot."""
    s = snapshot["summary"]
    date_str = snapshot["date"]
    fx = snapshot.get("fx_rates", {})

    lines = []
    lines.append(f"# 📊 投资组合日报")
    lines.append(f"")
    lines.append(f"**{date_str}**  |  汇率 USD/CNY={fx.get('USD', '?'):.4f}  HKD/CNY={fx.get('HKD', '?'):.4f}")
    lines.append(f"")

    # ── 总览 ──
    arrow = "📈" if s["daily_change"] >= 0 else "📉"
    lines.append(f"## {arrow} 总览")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|:---|---:|")
    lines.append(f"| 总资产 | {format_money(s['total_value'])} |")
    lines.append(f"| 总成本 | {format_money(s['total_cost'])} |")
    lines.append(f"| 累计盈亏 | {format_money(s['total_profit'])} ({s['total_return_pct']:+.2f}%) |")
    lines.append(f"| 日变动 | {format_money(s['daily_change'])} ({s['daily_change_pct']:+.2f}%) |")
    lines.append(f"| 本月收益 | {s['month_return_pct']:+.2f}% |")
    lines.append(f"| 历史最高 | {format_money(s['max_value'])} |")
    lines.append(f"| 最大回撤 | {s['max_drawdown_pct']:.2f}% |")
    lines.append(f"")

    # ── 各组详情 ──
    for gname, gdata in snapshot["groups"].items():
        g_arrow = "🟢" if gdata["return_pct"] >= 0 else "🔴"
        lines.append(f"## {g_arrow} {gname}  ({format_money(gdata['total_value'])}  {gdata['return_pct']:+.2f}%)")
        lines.append(f"")
        lines.append(f"| 名称 | 代码 | 数量 | 现价 | 市值(元) | 占比 | 盈亏% |")
        lines.append(f"|:---|:---|---:|---:|---:|---:|---:|")

        for pos in sorted(gdata["positions"], key=lambda x: -x["market_value_cny"]):
            curr_sym = {"CNY": "¥", "HKD": "HK$", "USD": "$"}.get(pos["currency"], "")
            lines.append(
                f"| {pos['name']} | {pos['ticker']} | {pos['quantity']:,} | "
                f"{curr_sym}{pos['current_price']:.2f} | "
                f"¥{pos['market_value_cny']:,.0f} | "
                f"{pos['weight_in_group']:.1f}% | "
                f"{pos['profit_pct']:+.1f}% |"
            )

        # Fund & cash
        if gdata.get("fund", 0) != 0:
            w = gdata["fund"] / gdata["total_value"] * 100 if gdata["total_value"] != 0 else 0
            lines.append(f"| 基金 | — | — | — | ¥{gdata['fund']:,.0f} | {w:.1f}% | — |")
        cash_w = gdata["cash"] / gdata["total_value"] * 100 if gdata["total_value"] != 0 else 0
        lines.append(f"| 现金 | — | — | — | ¥{gdata['cash']:,.0f} | {cash_w:.1f}% | — |")
        lines.append(f"")
        lines.append(f"> 成本: {format_money(gdata['cost_basis'])}  |  持仓市值: {format_money(gdata['positions_value'])}  |  盈亏: {format_money(gdata['profit'])}")
        lines.append(f"")

    # ── 趋势 ──
    if history and len(history) >= 2:
        lines.append(f"## 📈 资产趋势")
        lines.append(f"")
        values = [float(r.get("total_value", 0)) for r in history[-30:]]
        changes = [float(r.get("daily_change_pct", 0)) for r in history[-30:]]
        lines.append(f"近{len(values)}日资产: {sparkline(values)}")
        lines.append(f"近{len(changes)}日涨跌: {sparkline(changes)}")
        lines.append(f"")

        # Recent 5 days table
        recent = history[-5:]
        lines.append(f"| 日期 | 净资产 | 日涨跌 | 累计收益 | 回撤 |")
        lines.append(f"|:---|---:|---:|---:|---:|")
        for r in recent:
            lines.append(
                f"| {r['date']} | {format_money(float(r['total_value']))} | "
                f"{float(r.get('daily_change_pct', 0)):+.2f}% | "
                f"{float(r.get('return_pct', 0)):+.2f}% | "
                f"{float(r.get('max_drawdown_pct', 0)):.2f}% |"
            )
        lines.append(f"")

    lines.append(f"---")
    lines.append(f"*数据来源: Yahoo Finance | 生成时间: {snapshot.get('generated_at', '?')[:19]}*")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate portfolio report")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--output", "-o", help="Output file path")
    args = parser.parse_args()

    snapshot = load_snapshot(args.date)
    if not snapshot:
        print(f"❌ 快照 {args.date} 不存在。请先运行 portfolio_snapshot.py", file=sys.stderr)
        sys.exit(1)

    history = load_history_csv()
    report = generate_report(snapshot, history)

    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"✅ 报告已保存: {args.output}")
    else:
        print(report)

    return report


if __name__ == "__main__":
    main()
