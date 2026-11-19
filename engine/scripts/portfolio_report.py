#!/usr/bin/env python3
"""Portfolio report generator: read snapshot → create markdown report.

Usage:
    python3 portfolio_report.py [--date YYYY-MM-DD] [--output FILE]
"""

import json, os, sys, argparse, glob
from datetime import datetime

from portfolio_accounting import beijing_time_text, market_today, percent_text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_DIR = os.environ.get("PORTFOLIO_DIR", os.path.join(os.path.dirname(BASE_DIR), "portfolio"))
# The day change of a snapshot without an earlier day (portfolio_snapshot.NO_EARLIER_DAY).
NO_EARLIER_DAY = "不可用（没有上一日的记录可比）"


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


def csv_number(row, key):
    """A history.csv field as a float; None when it is missing, empty or not a number."""
    try:
        return float(row.get(key, ""))
    except (TypeError, ValueError):
        return None


def rate_text(rate):
    """An exchange rate with four decimals; a dash when the snapshot has none."""
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        return "—"
    return f"{rate:.4f}"


# What the footer's 数据来源 calls each price source of the snapshot (a position's ``price_source``), in this order.
PRICE_SOURCE_NAMES = {"yahoo": "Yahoo Finance", "tencent": "腾讯"}


def price_sources_text(snapshot):
    """``Yahoo Finance``, ``Yahoo Finance、腾讯`` or ``腾讯``: the sources that priced the day's holdings.

    A position without ``price_source`` (a snapshot made before sources were recorded) was priced from Yahoo;
    a day without positions says Yahoo Finance, as every report did before Tencent.
    """
    used = {pos.get("price_source") or "yahoo" for group in (snapshot.get("groups") or {}).values()
            for pos in group.get("positions") or []}
    names = [name for source, name in PRICE_SOURCE_NAMES.items() if source in used]
    names += sorted(str(source) for source in used if source not in PRICE_SOURCE_NAMES)
    return "、".join(names) or PRICE_SOURCE_NAMES["yahoo"]


def generated_text(snapshot):
    """The footer's 生成时间: ``2026-11-20 15:40:12（北京时间）``, whatever this machine's time zone.

    ``generated_at`` carries its offset; an older snapshot's has none and is the local time of the machine
    that made it, read as this machine's. A value that is not a time is shown as before (its first 19 characters).
    """
    text = snapshot.get("generated_at", "?")
    try:
        moment = datetime.fromisoformat(text) if isinstance(text, str) and "T" in text else None
    except ValueError:
        moment = None
    return beijing_time_text(moment) if moment is not None else str(text)[:19]


def generate_report(snapshot, history=None):
    """Generate markdown report from snapshot."""
    s = snapshot["summary"]
    date_str = snapshot["date"]
    fx = snapshot.get("fx_rates") or {}

    lines = []
    lines.append(f"# 📊 投资组合日报")
    lines.append(f"")
    lines.append(f"**{date_str}**  |  汇率 USD/CNY={rate_text(fx.get('USD'))}  HKD/CNY={rate_text(fx.get('HKD'))}")
    lines.append(f"")
    # Figures that rest on an estimated input (portfolio_snapshot.calculate_snapshot), as Today marks them 估算.
    if s.get("estimated"):
        lines.append(f"> ⚠️ 本日快照含估算：{'；'.join(str(reason) for reason in s.get('estimated_reasons') or [])}")
        lines.append(f"")

    # ── 总览 ──
    # 日变动 is the flow-adjusted market change. A deposit or withdrawal (inferred
    # from the change in contributed capital) gets its own row and is never a gain.
    # Snapshots written before these fields existed only have the raw change.
    market_change = s.get("market_daily_change", s["daily_change"])
    market_change_pct = s.get("market_daily_change_pct", s["daily_change_pct"])
    capital_change = s.get("capital_change") or 0
    # No earlier day to compare with (the first snapshot): the change is unavailable, not 0.
    arrow = "📊" if market_change is None else "📈" if market_change >= 0 else "📉"
    lines.append(f"## {arrow} 总览")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|:---|---:|")
    lines.append(f"| 总资产 | {format_money(s['total_value'])} |")
    lines.append(f"| 总成本 | {format_money(s['total_cost'])} |")
    lines.append(f"| 累计盈亏 | {format_money(s['total_profit'])} ({percent_text(s.get('total_return_pct'))}) |")
    if market_change is None:
        lines.append(f"| 日变动 | {NO_EARLIER_DAY} |")
    else:
        lines.append(f"| 日变动 | {format_money(market_change)} ({percent_text(market_change_pct)}) |")
    if capital_change:
        lines.append(f"| {'当日入金' if capital_change > 0 else '当日出金'} | {format_money(abs(capital_change))} |")
    lines.append(f"| 本月收益 | {percent_text(s.get('month_return_pct'))} |")
    if 'max_value' in s:
        lines.append(f"| 历史最高 | {format_money(s['max_value'])} |")
    lines.append(f"| 最大回撤 | {s['max_drawdown_pct']:.2f}% |")
    lines.append(f"")

    # ── 各组详情 ──
    for gname, gdata in snapshot["groups"].items():
        # Return is None when contributed capital is not positive; the sign then comes from the profit.
        group_return = gdata.get("return_pct")
        g_arrow = "🟢" if (group_return if group_return is not None else gdata["profit"]) >= 0 else "🔴"
        lines.append(f"## {g_arrow} {gname}  ({format_money(gdata['total_value'])}  {percent_text(group_return)})")
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
                f"{percent_text(pos.get('profit_pct'), 1)} |"
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
        def market_pct(row):
            # Flow-adjusted when the row has it (11-column history), raw change otherwise.
            value = csv_number(row, "market_daily_change_pct")
            return value if value is not None else csv_number(row, "daily_change_pct")

        values = [float(r.get("total_value", 0)) for r in history[-30:]]
        changes = [market_pct(r) or 0 for r in history[-30:]]
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
                f"{percent_text(market_pct(r))} | "
                f"{percent_text(csv_number(r, 'return_pct'))} | "
                f"{float(r.get('max_drawdown_pct', 0)):.2f}% |"
            )
        lines.append(f"")

    lines.append(f"---")
    lines.append(f"*数据来源: {price_sources_text(snapshot)} | 生成时间: {generated_text(snapshot)}*")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate portfolio report")
    parser.add_argument("--date", default=market_today(), help="Date (YYYY-MM-DD; default: today in Shanghai)")
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
