#!/usr/bin/env python3
"""Portfolio Daily Update — interactive holdings update via Feishu / CLI.

Flow:
  1. clone_holdings()   — Copy previous day's holdings → today
  2. send_notification() — Send Feishu message asking for changes
  3. apply_changes()    — Parse natural language → update holdings JSON
  4. run_pipeline()     — Snapshot → report → push → sync to QR

Usage:
    # Full interactive flow (cron entry-point)
    python3 portfolio_daily_update.py --action notify

    # Apply changes from text (called by agent or CLI)
    python3 portfolio_daily_update.py --action update --text "现金变为5000, 卖了500股药明康德"

    # Run pipeline only (snapshot + report + push)
    python3 portfolio_daily_update.py --action pipeline

    # Auto pipeline — run pipeline if today's snapshot doesn't exist yet
    python3 portfolio_daily_update.py --action auto-pipeline

    # Clone only
    python3 portfolio_daily_update.py --action clone
"""

import json, os, sys, argparse, glob, copy, subprocess, re
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
PORTFOLIO_DIR = BASE_DIR.parent / "portfolio"
CONFIG_PATH = PORTFOLIO_DIR / "config.json"
HOLDINGS_DIR = PORTFOLIO_DIR / "holdings"
SNAPSHOTS_DIR = PORTFOLIO_DIR / "snapshots"
REPORTS_DIR = BASE_DIR.parent / "reports"

# OpenClaw / nvm setup
NVM_SH = os.environ.get("NVM_SH", os.path.expanduser("~/.nvm/nvm.sh"))


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_latest_holdings_file(before_date=None):
    """Find the most recent holdings file, optionally before a given date."""
    files = sorted(HOLDINGS_DIR.glob("*.json"))
    if before_date:
        files = [f for f in files if f.stem < before_date]
    return files[-1] if files else None


def clone_holdings(date_str):
    """Copy previous day's holdings as today's baseline.
    
    Returns (path, is_new) — path to today's holdings, whether it was newly created.
    """
    today_file = HOLDINGS_DIR / f"{date_str}.json"
    
    if today_file.exists():
        print(f"  ℹ️  今日持仓文件已存在: {today_file.name}")
        return today_file, False
    
    prev_file = get_latest_holdings_file(before_date=date_str)
    if not prev_file:
        # No previous file, check if any file exists at all
        any_file = get_latest_holdings_file()
        if any_file:
            prev_file = any_file
        else:
            print("  ❌ 没有找到任何历史持仓文件", file=sys.stderr)
            return None, False
    
    with open(prev_file) as f:
        data = json.load(f)
    
    data["date"] = date_str
    data["updated_at"] = datetime.now().isoformat()
    data["cloned_from"] = prev_file.stem
    
    HOLDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(today_file, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"  ✅ 从 {prev_file.name} 克隆持仓 → {today_file.name}")
    return today_file, True


def load_holdings(date_str):
    """Load holdings for a date."""
    path = HOLDINGS_DIR / f"{date_str}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def save_holdings(holdings, date_str):
    """Save holdings to file."""
    path = HOLDINGS_DIR / f"{date_str}.json"
    holdings["updated_at"] = datetime.now().isoformat()
    with open(path, "w") as f:
        json.dump(holdings, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 持仓已更新: {path.name}")
    return path


def parse_and_apply_changes(holdings, text):
    """Parse natural language text and apply changes to holdings.
    
    Supported patterns:
    - "持仓未变化" / "不变" / "no change" → no changes
    - "现金变为5000" / "进攻现金-50万" → update cash
    - "基金变为16万" / "进攻基金155900" → update fund
    - "卖了500股药明康德" / "药明康德减500" → reduce shares
    - "买了1000股xxx" / "xxx加1000" → add shares
    - "新增 xxx ticker:SHE:002050 数量:1000 成本:50" → add new position
    - "删除 xxx" / "清仓 xxx" → remove position
    - "成本调整为58.5万" / "进攻成本585000" → update cost_basis
    
    Returns list of changes applied (for logging).
    """
    changes = []
    text = text.strip()
    
    # Split text by common delimiters
    parts = re.split(r'[;；\n,，、]', text)
    
    # Per-part no-change keywords (e.g. "其他不变" should be silently skipped)
    no_change_patterns = ["未变化", "不变", "没变", "no change", "unchanged", "一样"]
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # Skip parts that are purely no-change declarations
        if any(p in part.lower() for p in no_change_patterns) and not any(
            kw in part for kw in ["现金", "基金", "成本", "持仓", "股"]
        ):
            continue
        
        change = _parse_single_change(holdings, part)
        if change:
            changes.append(change)
            _apply_single_change(holdings, change)
    
    return changes


def _find_group_and_position(holdings, name_hint):
    """Find a position by name across all groups."""
    name_hint = name_hint.strip()
    for gname, gdata in holdings.get("groups", {}).items():
        for i, pos in enumerate(gdata.get("positions", [])):
            if name_hint in pos["name"] or pos["name"] in name_hint:
                return gname, i, pos
            # Also match by ticker
            if name_hint.upper() in pos["ticker"]:
                return gname, i, pos
    return None, None, None


def _find_group_by_hint(holdings, text):
    """Determine which group the text refers to."""
    for gname in holdings.get("groups", {}):
        if gname in text:
            return gname
    # Default to first group if not specified
    groups = list(holdings.get("groups", {}).keys())
    return groups[0] if groups else None


def _parse_number(text):
    """Parse a number from text, handling 万 suffix."""
    text = text.strip().replace(",", "").replace("，", "")
    if "万" in text:
        num_str = text.replace("万", "").strip()
        try:
            val = float(num_str) * 10000
            # Round to nearest integer to avoid float precision issues (e.g. -44.41万 → -444100)
            return round(val)
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_money_number(text):
    """Parse money amounts with a portfolio-oriented shorthand heuristic.

    Examples users commonly send:
    -44.273  -> -44.273万
     15.635  -> 15.635万

    Rules:
    - explicit "万" always wins
    - plain decimals with abs(value) < 1000 are treated as 万
    - otherwise treat as raw yuan
    """
    cleaned = text.strip().replace(",", "").replace("，", "")
    if "万" in cleaned:
        return _parse_number(cleaned)
    try:
        val = float(cleaned)
    except ValueError:
        return None
    if ("." in cleaned or "-" in cleaned) and abs(val) < 1000:
        return round(val * 10000)
    return val


def _parse_share_cost(text):
    """Parse per-share cost price as a raw numeric value."""
    cleaned = text.strip().replace(",", "").replace("，", "").replace("万", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_ticker(text):
    """Extract ticker like SHA:688275 or NASDAQ:AAPL from free-form text."""
    patterns = [
        r'(?:ticker|代码(?:是|为)?)[：:\s]*([A-Z]{2,10}:[A-Za-z0-9]+)',
        r'\b([A-Z]{2,10}:[A-Za-z0-9]+)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return ""


def _strip_ticker_phrase(text):
    """Remove ticker phrases from a natural-language stock name hint."""
    text = re.sub(r'(?:代码(?:是|为)?|ticker)[：:\s]*[A-Z]{2,10}:[A-Za-z0-9]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b[A-Z]{2,10}:[A-Za-z0-9]+\b', '', text)
    text = re.sub(r'\s+', ' ', text).strip(" ：:，,。.;；")
    return text


def _parse_single_change(holdings, text):
    """Parse a single change directive."""
    text_lower = text.lower().strip()
    
    # Pattern: cash changes — "现金变为5000" / "进攻现金-484110" / "稳健账户现金3300"
    cash_match = re.search(r'(?:(.+?)(?:账户|组))?.*?现金.*?(?:变为|改为|=|:)?[：:]?\s*([-\d.万]+)', text)
    if cash_match:
        group_hint = cash_match.group(1) or ""
        group = _find_group_by_hint(holdings, group_hint + text) if group_hint else _find_group_by_hint(holdings, text)
        amount = _parse_money_number(cash_match.group(2))
        if amount is not None and group:
            return {"action": "set_cash", "group": group, "value": amount, "description": f"{group}现金→{amount}"}
    
    # Pattern: fund changes — "基金变为16万" / "进攻基金155900"
    fund_match = re.search(r'(?:(.+?)(?:账户|组))?.*?基金.*?(?:变为|改为|=|:)?[：:]?\s*([-\d.万]+)', text)
    if fund_match:
        group_hint = fund_match.group(1) or ""
        group = _find_group_by_hint(holdings, group_hint + text) if group_hint else _find_group_by_hint(holdings, text)
        amount = _parse_money_number(fund_match.group(2))
        if amount is not None and group:
            return {"action": "set_fund", "group": group, "value": amount, "description": f"{group}基金→{amount}"}
    
    # Pattern: cost_basis delta — "成本增加4万" / "进攻账户成本减少2万"
    cost_delta_match = re.search(r'(?:(.+?)(?:账户|组))?.*?成本(增加|减少|\+|\-)\s*([\d.万]+)', text)
    if cost_delta_match:
        group_hint = cost_delta_match.group(1) or ""
        group = _find_group_by_hint(holdings, group_hint + text) if group_hint else _find_group_by_hint(holdings, text)
        sign = 1 if cost_delta_match.group(2) in ("增加", "+") else -1
        delta = _parse_number(cost_delta_match.group(3))
        if delta is not None and group:
            return {"action": "delta_cost_basis", "group": group, "value": sign * delta,
                    "description": f"{group}成本{'增加' if sign > 0 else '减少'}{delta}"}
    
    # Pattern: cost_basis set — "进攻成本585000" / "成本调整为58.5万"
    # Skip this generic rule when the text is clearly describing a new position,
    # e.g. "买了500股万润新能代码是SHA:688275 成本110.2".
    cost_match = re.search(r'(?:(.+?)(?:账户|组))?.*?成本.*?(?:变为|调整为|改为|=|:)?[：:]?\s*([-\d.万]+)', text)
    if cost_match and not (("新增" in text or "买" in text) and _extract_ticker(text)):
        group_hint = cost_match.group(1) or ""
        group = _find_group_by_hint(holdings, group_hint + text) if group_hint else _find_group_by_hint(holdings, text)
        amount = _parse_money_number(cost_match.group(2))
        if amount is not None and group:
            return {"action": "set_cost_basis", "group": group, "value": amount, "description": f"{group}成本→{amount}"}
    
    # Pattern: sell/reduce — "卖了500股药明康德" / "药明康德减500" / "卖出药明康德500股"
    # Try both patterns: "verb qty name" and "name verb qty"
    _sell_result = None
    for sell_match, swapped in [
        (re.search(r'(?:卖[了出]?|减[少持]?|清仓)\s*(\d+)\s*股?\s*(.+?)$', text), False),
        (re.search(r'(.+?)\s*(?:卖[了出]?|减[少持]?)\s*(\d+)\s*股?', text), True),
    ]:
        if not sell_match:
            continue
        if swapped:
            _qty, _name = int(sell_match.group(2)), sell_match.group(1).strip()
        else:
            _qty, _name = int(sell_match.group(1)), sell_match.group(2).strip()
        gname, idx, pos = _find_group_and_position(holdings, _name)
        if pos:
            new_qty = max(0, pos["quantity"] - _qty)
            _sell_result = {
                "action": "set_quantity" if new_qty > 0 else "remove_position",
                "group": gname, "position_index": idx, "name": pos["name"],
                "value": new_qty, "description": f"{pos['name']}减{_qty}股→{new_qty}股"
            }
            break
    if _sell_result:
        return _sell_result
    
    # Pattern: buy/add — "买了1000股药明康德" / "药明加1000"
    _buy_result = None
    for buy_match, swapped in [
        (re.search(r'(?:买[了入]?|加[仓]?)\s*(\d+)\s*股?\s*(.+?)$', text), False),
        (re.search(r'(.+?)\s*(?:买[了入]?|加[仓]?)\s*(\d+)\s*股?', text), True),
    ]:
        if not buy_match:
            continue
        if swapped:
            _qty, _name = int(buy_match.group(2)), buy_match.group(1).strip()
        else:
            _qty, _name = int(buy_match.group(1)), buy_match.group(2).strip()
        ticker = _extract_ticker(text)
        clean_name = _strip_ticker_phrase(_name)
        gname, idx, pos = _find_group_and_position(holdings, clean_name)
        if pos:
            new_qty = pos["quantity"] + _qty
            _buy_result = {
                "action": "set_quantity", "group": gname, "position_index": idx,
                "name": pos["name"], "value": new_qty,
                "description": f"{pos['name']}加{_qty}股→{new_qty}股"
            }
            break
        group = _find_group_by_hint(holdings, text)
        if ticker and clean_name and group:
            cost_m = re.search(r'(?:成本|cost)[：:]\s*([-\d.万]+)', text, flags=re.IGNORECASE)
            cost = _parse_share_cost(cost_m.group(1)) if cost_m else 0
            _buy_result = {
                "action": "add_position",
                "group": group,
                "position": {
                    "name": clean_name,
                    "ticker": ticker,
                    "quantity": _qty,
                    "cost_price": float(cost or 0),
                },
                "description": f"新增{clean_name} {ticker} {_qty}股@{float(cost or 0)}"
            }
            break
    if _buy_result:
        return _buy_result
    
    # Pattern: clear/remove — "清仓药明康德"
    clear_match = re.search(r'清仓\s*(.+)', text)
    if clear_match:
        name = clear_match.group(1).strip()
        gname, idx, pos = _find_group_and_position(holdings, name)
        if pos:
            return {
                "action": "remove_position", "group": gname, "position_index": idx,
                "name": pos["name"], "value": 0, "description": f"清仓{pos['name']}"
            }
    clear_match_2 = re.search(r'(.+?)\s*(?:清了|清仓了|卖完了|全卖了|清空了)$', text)
    if clear_match_2:
        name = clear_match_2.group(1).strip()
        gname, idx, pos = _find_group_and_position(holdings, name)
        if pos:
            return {
                "action": "remove_position", "group": gname, "position_index": idx,
                "name": pos["name"], "value": 0, "description": f"清仓{pos['name']}"
            }
    
    # Pattern: set quantity — "药明康德4000股" / "药明康德数量4000"
    qty_match = re.search(r'(.+?)\s*(?:数量|股数)?[\s:：]*(\d+)\s*股', text)
    if qty_match:
        name = qty_match.group(1).strip()
        qty = int(qty_match.group(2))
        gname, idx, pos = _find_group_and_position(holdings, name)
        if pos:
            return {
                "action": "set_quantity", "group": gname, "position_index": idx,
                "name": pos["name"], "value": qty,
                "description": f"{pos['name']}数量→{qty}股"
            }
    
    # Pattern: new position — "新增 xxx ticker:SHE:002050 数量:1000 成本:50 组:进攻"
    new_match = re.search(r'新增\s+(\S+)', text)
    if new_match:
        name = new_match.group(1)
        ticker_m = re.search(r'(?:ticker|代码(?:是|为)?)[：:]\s*(\S+)', text, flags=re.IGNORECASE)
        qty_m = re.search(r'(?:数量|qty)[：:]\s*(\d+)', text)
        cost_m = re.search(r'(?:成本|cost)[：:]\s*([-\d.万]+)', text, flags=re.IGNORECASE)
        group_m = re.search(r'(?:组|group)[：:]\s*(\S+)', text)
        
        ticker = ticker_m.group(1).upper() if ticker_m else ""
        qty = int(qty_m.group(1)) if qty_m else 0
        cost = _parse_share_cost(cost_m.group(1)) if cost_m else 0
        group = group_m.group(1) if group_m else _find_group_by_hint(holdings, text)
        
        if ticker and qty > 0 and group:
            return {
                "action": "add_position", "group": group,
                "position": {"name": name, "ticker": ticker, "quantity": qty, "cost_price": float(cost)},
                "description": f"新增{name} {ticker} {qty}股@{cost}"
            }
    
    # If no pattern matched, return as raw text for logging
    return {"action": "unknown", "raw_text": text, "description": f"未识别: {text}"}


def _apply_single_change(holdings, change):
    """Apply a single parsed change to holdings."""
    action = change["action"]
    
    if action == "no_change":
        return
    
    if action == "set_cash":
        holdings["groups"][change["group"]]["cash"] = change["value"]
    
    elif action == "set_fund":
        holdings["groups"][change["group"]]["fund"] = change["value"]
    
    elif action == "set_cost_basis":
        holdings["groups"][change["group"]]["cost_basis"] = change["value"]
    
    elif action == "delta_cost_basis":
        holdings["groups"][change["group"]]["cost_basis"] = (
            holdings["groups"][change["group"]].get("cost_basis", 0) + change["value"]
        )
    
    elif action == "set_quantity":
        pos = holdings["groups"][change["group"]]["positions"][change["position_index"]]
        pos["quantity"] = change["value"]
    
    elif action == "remove_position":
        holdings["groups"][change["group"]]["positions"].pop(change["position_index"])
    
    elif action == "add_position":
        holdings["groups"][change["group"]]["positions"].append(change["position"])


def _infer_missing_cost_prices(holdings_before, changes):
    """Infer per-share cost for newly added positions when omitted by the user.

    Heuristic:
    - only apply to add_position changes with cost_price <= 0
    - if the same update sets group cash, use |cash_delta| / quantity
    - fallback to |fund_delta| / quantity when cash is unchanged but fund changes
    """
    for change in changes:
        if change.get("action") != "add_position":
            continue
        pos = change.get("position", {})
        qty = pos.get("quantity", 0)
        cost_price = pos.get("cost_price", 0) or 0
        group = change.get("group")
        if qty <= 0 or cost_price > 0 or not group:
            continue

        before_group = holdings_before.get("groups", {}).get(group, {})
        old_cash = before_group.get("cash")
        old_fund = before_group.get("fund")

        new_cash = next((c["value"] for c in changes if c.get("action") == "set_cash" and c.get("group") == group), None)
        new_fund = next((c["value"] for c in changes if c.get("action") == "set_fund" and c.get("group") == group), None)

        inferred = None
        if old_cash is not None and new_cash is not None and old_cash != new_cash:
            inferred = abs(new_cash - old_cash) / qty
        elif old_fund is not None and new_fund is not None and old_fund != new_fund:
            inferred = abs(new_fund - old_fund) / qty

        if inferred and inferred > 0:
            pos["cost_price"] = round(inferred, 4)
            change["description"] = (
                f"新增{pos['name']} {pos['ticker']} {qty}股@{pos['cost_price']} "
                f"(由账户资金变动推断)"
            )


def send_feishu_notification(date_str, holdings, config):
    """Send Feishu message asking user about today's holdings changes."""
    chat_id = config.get("feishu_chat_id", "")
    if not chat_id:
        print("  ⚠️ 未配置 feishu_chat_id，跳过飞书通知")
        return False
    
    # Build a summary of current holdings
    groups_summary = []
    for gname, gdata in holdings.get("groups", {}).items():
        pos_names = [p["name"] for p in gdata.get("positions", [])]
        cash = gdata.get("cash", 0)
        fund = gdata.get("fund", 0)
        groups_summary.append(
            f"【{gname}】持仓: {', '.join(pos_names)}"
            f"\n  现金: ¥{cash:,.0f} | 基金: ¥{fund:,.0f}"
        )
    
    message = (
        f"📊 投资组合每日更新 — {date_str}\n"
        f"\n"
        f"今日持仓已基于昨日自动生成，当前状态：\n"
        f"\n"
        f"{''.join(chr(10) + s for s in groups_summary)}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"请告诉我今日变化：\n"
        f"• 回复「未变化」→ 直接生成日报\n"
        f"• 回复变化内容，例如：\n"
        f"  - 卖了500股药明康德\n"
        f"  - 进攻现金变为-48万\n"
        f"  - 基金变为16万\n"
        f"  - 买了1000股xxx\n"
        f"\n"
        f"回复后我会自动更新持仓、生成快照和日报 📈"
    )
    
    return _send_openclaw_message(chat_id, message)


def send_feishu_report(date_str, report_content, config):
    """Send the daily report to Feishu."""
    chat_id = config.get("feishu_chat_id", "")
    if not chat_id:
        return False
    
    return _send_openclaw_message(chat_id, report_content)


def _send_openclaw_message(chat_id, message):
    """Send a message via OpenClaw CLI."""
    try:
        cmd = (
            f'source {NVM_SH} && nvm use v22.22.0 > /dev/null 2>&1 && '
            f'openclaw message send --channel feishu --target "{chat_id}" --message "{_escape_shell(message)}"'
        )
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": os.environ.get("OPENCLAW_HOME", os.path.expanduser("~"))}
        )
        if result.returncode == 0:
            print(f"  ✅ 飞书消息已发送")
            return True
        else:
            print(f"  ⚠️ 飞书发送失败: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"  ⚠️ 飞书发送异常: {e}")
        return False


def _escape_shell(text):
    """Escape special characters for shell command."""
    return text.replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')


def run_snapshot(date_str):
    """Run the snapshot engine."""
    script = BASE_DIR / "portfolio_snapshot.py"
    conda = os.environ.get("CONDA_EXE", "conda")
    
    result = subprocess.run(
        [conda, "run", "-n", "quant", "python3", str(script), "--date", date_str],
        capture_output=True, text=True, timeout=120,
        cwd=str(BASE_DIR)
    )
    
    if result.returncode == 0:
        print(f"  ✅ 快照生成成功")
        if result.stdout:
            # Print last few lines (summary)
            lines = result.stdout.strip().split('\n')
            for line in lines[-8:]:
                print(f"    {line}")
        return True
    else:
        print(f"  ❌ 快照生成失败: {result.stderr[:300]}")
        return False


def run_report(date_str):
    """Generate markdown report."""
    script = BASE_DIR / "portfolio_report.py"
    conda = os.environ.get("CONDA_EXE", "conda")
    
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORTS_DIR / f"portfolio-{date_str.replace('-', '')}.md"
    
    result = subprocess.run(
        [conda, "run", "-n", "quant", "python3", str(script), "--date", date_str, "-o", str(report_file)],
        capture_output=True, text=True, timeout=30,
        cwd=str(BASE_DIR)
    )
    
    if result.returncode == 0 and report_file.exists():
        print(f"  ✅ 报告已生成: {report_file.name}")
        return report_file
    else:
        print(f"  ⚠️ 报告生成失败: {result.stderr[:200]}")
        return None


def run_pipeline(date_str, send_report=True):
    """Full pipeline: snapshot → report → push Feishu → sync QR."""
    config = load_config()
    
    print(f"\n🚀 运行完整管道 — {date_str}")
    
    # Step 1: Generate snapshot
    print("\n[1/3] 生成快照...")
    if not run_snapshot(date_str):
        return False
    
    # Step 2: Generate report  
    print("\n[2/3] 生成报告...")
    report_file = run_report(date_str)
    
    # Step 3: Push to Feishu
    if send_report and report_file:
        print("\n[3/3] 推送飞书...")
        report_content = report_file.read_text()
        send_feishu_report(date_str, report_content, config)
    else:
        print("\n[3/3] 跳过推送")
    
    print(f"\n✅ 管道完成！日期: {date_str}")
    
    # Check if QR sync happened (it's done inside snapshot engine)
    qr_path = config.get("qr_portfolio_path", "")
    if qr_path and os.path.exists(qr_path):
        print(f"  📱 QR Dashboard 数据已同步")
    
    return True


def action_notify(date_str):
    """Cron action: clone holdings + send Feishu notification."""
    config = load_config()
    
    print(f"📋 每日持仓更新通知 — {date_str}")
    
    # Clone holdings
    print("\n[1/2] 克隆持仓...")
    path, is_new = clone_holdings(date_str)
    if not path:
        return False
    
    # Load today's holdings
    holdings = load_holdings(date_str)
    
    # Send notification
    print("\n[2/2] 发送飞书通知...")
    send_feishu_notification(date_str, holdings, config)
    
    return True


def action_update(date_str, text):
    """Apply changes from text and run pipeline."""
    print(f"📝 更新持仓 — {date_str}")
    
    # Ensure today's holdings exist
    clone_holdings(date_str)
    holdings = load_holdings(date_str)
    if not holdings:
        print("  ❌ 无法加载今日持仓", file=sys.stderr)
        return False
    holdings_before = copy.deepcopy(holdings)
    
    # Parse and apply changes
    print(f"\n  解析变更: {text[:100]}...")
    changes = parse_and_apply_changes(holdings, text)
    _infer_missing_cost_prices(holdings_before, changes)
    
    for c in changes:
        icon = "✅" if c["action"] != "unknown" else "❓"
        print(f"    {icon} {c['description']}")
    
    # Save updated holdings
    if any(c["action"] not in ("no_change", "unknown") for c in changes):
        save_holdings(holdings, date_str)
    elif any(c["action"] == "no_change" for c in changes):
        print("  ℹ️  持仓未变化，保持原样")
    
    # Run full pipeline
    return run_pipeline(date_str)


def action_auto_pipeline(date_str):
    """Auto pipeline: run only if today's snapshot doesn't exist yet."""
    snap_file = SNAPSHOTS_DIR / f"{date_str}.json"
    if snap_file.exists():
        print(f"  ℹ️  今日快照已存在 ({snap_file.name})，跳过")
        return True
    
    print(f"  ⏰ 今日快照不存在，自动生成...")
    clone_holdings(date_str)
    return run_pipeline(date_str)


def main():
    parser = argparse.ArgumentParser(description="Portfolio daily update system")
    parser.add_argument("--action", required=True,
                        choices=["notify", "update", "pipeline", "auto-pipeline", "clone"],
                        help="Action to perform")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="Date (YYYY-MM-DD)")
    parser.add_argument("--text", "-t", default="",
                        help="Change description text (for 'update' action)")
    parser.add_argument("--no-push", action="store_true",
                        help="Skip Feishu push")
    args = parser.parse_args()
    
    date_str = args.date
    
    if args.action == "notify":
        success = action_notify(date_str)
    elif args.action == "update":
        if not args.text:
            print("❌ --text 参数不能为空", file=sys.stderr)
            sys.exit(1)
        success = action_update(date_str, args.text)
    elif args.action == "pipeline":
        clone_holdings(date_str)
        success = run_pipeline(date_str, send_report=not args.no_push)
    elif args.action == "auto-pipeline":
        success = action_auto_pipeline(date_str)
    elif args.action == "clone":
        path, is_new = clone_holdings(date_str)
        success = path is not None
    else:
        parser.print_help()
        success = False
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
