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

import json, os, sys, argparse, glob, copy, subprocess, re, shutil
from fractions import Fraction
from portfolio_write_lock import portfolio_write_locked, run_child

from datetime import datetime, timedelta
from pathlib import Path

from portfolio_accounting import currency_from_text, atomic_text_writer, atomic_json_dump, cash_balances, set_cash_balance

BASE_DIR = Path(__file__).parent
PORTFOLIO_DIR = Path(os.environ.get("PORTFOLIO_DIR", str(BASE_DIR.parent / "portfolio")))
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
    Only a file dated before ``date_str`` is copied: holdings of a later day
    are never the day's (``(None, False)`` and the ``NO_HOLDINGS`` message).
    """
    from ledger_bridge import guard_legacy_write
    guard_legacy_write(PORTFOLIO_DIR)
    today_file = HOLDINGS_DIR / f"{date_str}.json"
    
    if today_file.exists():
        print(f"  ℹ️  今日持仓文件已存在: {today_file.name}")
        return today_file, False
    
    prev_file = get_latest_holdings_file(before_date=date_str)
    if not prev_file:
        # Only later holdings, or none: copying a later day's cash and positions back would invent the day.
        print(f"  ❌ {NO_HOLDINGS}：没有 {date_str} 当日或之前的持仓文件（不会用之后日期的持仓代替）", file=sys.stderr)
        return None, False
    
    with open(prev_file) as f:
        data = json.load(f)
    
    data["date"] = date_str
    data["updated_at"] = datetime.now().isoformat()
    data["cloned_from"] = prev_file.stem
    
    HOLDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(today_file) as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
    
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
    from ledger_bridge import guard_legacy_write
    guard_legacy_write(PORTFOLIO_DIR)
    path = HOLDINGS_DIR / f"{date_str}.json"
    holdings["updated_at"] = datetime.now().isoformat()
    with atomic_text_writer(path) as f:
        json.dump(holdings, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"  ✅ 持仓已更新: {path.name}")
    return path


def parse_and_apply_changes(holdings, text):
    """Parse natural language text and apply changes to holdings.

    Supported clauses (separate several with , ; 、 or new lines):
    - "持仓未变化" / "不变" / "no change" → no changes
    - "现金变为5000" / "进攻现金-50万" → set cash; "现金增加2万" / "稳健现金减少5000" → change cash
    - "基金变为16万" / "进攻基金155900" → set fund; "基金减少1万" → change fund
    - "卖了500股药明康德" / "药明康德减500" / "卖出药明康德500股" → reduce shares
    - "买了1000股xxx" / "xxx加1000" → add shares; "成本:25" or "均价25" in the clause
      updates the average cost
    - "买了500股xxx 代码是SHA:688275" → new position when xxx is not held
    - "新增 xxx ticker:SHE:002050 数量:1000 成本:50 组:进攻" → add new position
    - "xxx 800股" / "xxx数量800" → set quantity
    - "删除 xxx" / "清仓 xxx" / "xxx清了" → remove position
    - "成本调整为58.5万" / "进攻成本585000" / "成本增加4万" → account cost_basis

    A stock is matched by its exact name or ticker, or by the only held name that
    contains the text. An account is matched the same way; when none is named the
    first account is used. Anything unmatched or ambiguous becomes an "unknown"
    change, and then nothing at all is applied to ``holdings``.

    Returns list of changes (for logging).
    """
    working = copy.deepcopy(holdings)
    changes = []
    for part in _split_clauses(text):
        if _is_no_change(working, part):
            continue
        change = _parse_single_change(working, part)
        changes.append(change)
        if change["action"] != "unknown":
            _apply_single_change(working, change)

    if not any(change["action"] == "unknown" for change in changes):
        holdings.clear()
        holdings.update(working)
    return changes


_FILLER = r'(?:今天|今日|昨天|刚才|刚刚|刚|我|又|再|已经|已|还|用现金)*'
# A verb never stops short of its own 了: "买了123股" is not a stock called "了".
_SELL = r'(?:卖出了?|卖掉了?|卖了|卖|减持了?|减仓了?|减少了?|减了|减)(?!了)'
_BUY = r'(?:买入了?|买进了?|买了|买|加仓了?|增持了?|加了|加)(?!了)'
# A whole number: never part of a longer run of digits, at most six decimals.
_QTY = r'(?<![\d.])\d+(?:\.\d{1,6})?(?![\d.])'
# SHA:688275, NASDAQ:AAPL, NYSE:BRK-B. The word "ticker" before a colon is a label, not an exchange.
_TICKER = r'(?<![A-Za-z0-9])(?!(?i:ticker)[：:])[A-Za-z]{2,10}:[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*'
_SET_OP = r'(?:变为|变成|改为|改成|调整为|调整到|设为|为|是|到|→|=|:|：)'
_CUR = r'(?:人民币元?|美元|美金|港币|港元|(?<![A-Za-z])(?:CNY|RMB|USD|HKD)(?![A-Za-z]))'
_CURRENCY_WORDS = re.compile(_CUR, re.IGNORECASE)
_PRICE = re.compile(r'(?:成本价?|均价|买入价|单价|cost)\s*(?:是|为)?\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*元?', re.IGNORECASE)
_PRICE_ONLY = re.compile(r'(?:成本价|均价|买入价|单价)\s*(?:是|为)?\s*[:：=]?\s*\d+(?:\.\d+)?\s*元?')
_NO_CHANGE = re.compile(
    r'^(?P<subject>.*?)\s*(?:都|也|均|还是|仍然|仍|保持|暂时|目前|暂)*\s*'
    r'(?:没有变化|未变化|没变化|无变化|不变|没变|未变|一样|no\s*changes?|unchanged)(?:了|的)?$', re.IGNORECASE)
_GENERIC_SUBJECT = re.compile(r'今天|今日|目前|持仓|仓位|其他|其它|其余|别的|剩下|全部|一切|所有|现金|基金|成本|账户|股票|数量|昨天|昨日|上次|之前|的|同|\s')
_NOT_A_NAME = re.compile(r'\s|清仓|删除|新增|卖|买|加|减|现金|基金|成本')
_NOT_A_NEW_NAME = re.compile(r'清仓|删除|新增|卖出|卖了|买入|买了|现金|同时|并且|然后|以及')
_MONEY = re.compile(
    rf'^(?P<prefix>.*?)\s*(?P<cur1>{_CUR})?\s*(?P<kw>现金|基金|成本)(?!价)(?:余额|金额|基础|总额)?\s*'
    r'(?P<op>变为|变成|改为|改成|调整为|调整到|设为|增加了?|减少了?|为|是|到|→|=|:|：)?\s*'
    rf'(?P<cur2>{_CUR})?\s*(?P<amount>[-+]?\d+(?:\.\d+)?(?:万\d?|千|亿)?)\s*(?P<cur3>{_CUR})?\s*(?:元|块钱|块)?$',
    re.IGNORECASE)


def _split_clauses(text):
    """Split an update into clauses, keeping thousands separators and a trailing price together."""
    text = re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', text.strip())
    clauses = []
    for part in re.split(r'[;；\n,，、]', text):
        part = part.strip()
        if not part:
            continue
        if clauses and _PRICE_ONLY.fullmatch(part) and re.search(_BUY, clauses[-1]):
            clauses[-1] = f"{clauses[-1]} {part}"
        else:
            clauses.append(part)
    return clauses


def _is_no_change(holdings, part):
    """True for clauses such as "其他不变" that only state that nothing changed.

    The clause must end with the statement, and whatever it is about must be
    generic ("持仓", "其他"), an account, or a held position. Anything else is
    left for the parser so that it is reported instead of being dropped.
    """
    match = _NO_CHANGE.match(part.strip())
    if not match:
        return False
    for subject in re.split(r'[和与跟及]', match.group("subject")):
        subject = _GENERIC_SUBJECT.sub('', subject)
        if subject and not _resolve_group(holdings, subject) and not _match_positions(holdings, subject)[1]:
            return False
    return True


def _unknown(text, reason="未识别"):
    return {"action": "unknown", "raw_text": text, "description": f"{reason}: {text}"}


def _number(value):
    """Keep whole numbers as int so holdings files do not turn 500 into 500.0."""
    if isinstance(value, int):
        return value
    value = round(value, 6)
    return int(value) if value.is_integer() else value


def _quantity(text):
    """A share quantity exactly as written, or None when a float cannot hold it."""
    exact = Fraction(text)
    if exact.denominator == 1:
        return int(exact)
    value = float(exact)
    return value if Fraction(str(value)) == exact else None


def _exact_sum(left, right):
    """left + right without float drift; None when the result cannot be stored exactly."""
    total = Fraction(str(left)) + Fraction(str(right))
    if total.denominator == 1:
        return int(total)
    value = float(total)
    return value if Fraction(str(value)) == total else None


def _parse_amount(text, shorthand=False):
    """Parse a money amount.

    "5万" → 50000, "3万5" → 35000, "5千" → 5000, "1.2亿" → 120000000.
    With ``shorthand`` (CNY account amounts), a bare decimal or negative number
    below 1000 is read as 万: "-44.273" → -442730, "15.635" → 156350.
    Returns None when the text is not a single well-formed amount.
    """
    match = re.fullmatch(r'([-+]?)(\d+(?:\.\d+)?)(?:(万)(\d)?|(千)|(亿))?', text.strip())
    if not match:
        return None
    sign = -1 if match.group(1) == "-" else 1
    number = float(match.group(2))
    if match.group(3):
        if match.group(4) and "." in match.group(2):
            return None
        value = round(number * 10000 + int(match.group(4) or 0) * 1000)
    elif match.group(5):
        value = round(number * 1000)
    elif match.group(6):
        value = round(number * 100000000)
    elif shorthand and ("." in match.group(2) or sign < 0) and number < 1000:
        value = round(number * 10000)
    else:
        value = number
    return sign * value


def _extract_ticker(text):
    """Extract ticker like SHA:688275 or NASDAQ:AAPL from free-form text."""
    patterns = [
        rf'(?:ticker|代码(?:是|为)?)[：:\s]*({_TICKER})',
        rf'({_TICKER})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).upper()
    return ""


def _strip_ticker_phrase(text):
    """Remove ticker phrases from a natural-language stock name hint."""
    text = re.sub(rf'(?:代码(?:是|为)?|(?i:ticker))[：:\s]*{_TICKER}', '', text)
    text = re.sub(_TICKER, '', text)
    text = re.sub(r'\s+', ' ', text).strip(" ：:，,。.;；")
    return text


def _default_group(holdings):
    groups = list(holdings.get("groups", {}))
    return groups[0] if groups else None


def _resolve_group(holdings, hint):
    """Return the account a hint names: its exact name, or the only account whose name contains it."""
    groups = list(holdings.get("groups", {}))
    hint = hint.strip()
    without_particle = re.sub(r'的$', '', hint).strip()
    candidates = [hint, without_particle, re.sub(r'(?:账户|组)$', '', without_particle).strip()]
    for candidate in candidates:
        if candidate in groups:
            return candidate
    for candidate in candidates:
        if not candidate:
            continue
        matches = [group for group in groups if candidate in group]
        if matches:
            return matches[0] if len(matches) == 1 else None
    return None


def _leading_group(holdings, prefix):
    """Text before a keyword or verb must be empty, filler words, or an account.

    Returns (ok, account); account is None when no account was named.
    """
    prefix = prefix.strip()
    if not prefix:
        return True, None
    group = _resolve_group(holdings, prefix)
    if group:
        return True, group
    prefix = re.sub(rf'^{_FILLER}', '', prefix).strip()
    if not prefix:
        return True, None
    group = _resolve_group(holdings, prefix)
    return bool(group), group


def _match_positions(holdings, hint, group=None):
    """Return (tier, matches): tier 0 is an exact name or ticker, tier 1 a partial name.

    A partial match is a hint contained in a held name ("茅台" for "贵州茅台"), or a
    held name with only filler words around it ("今天药明康德"). A bare number is
    never a partial name.
    """
    hint = hint.strip(" 的")
    if not hint:
        return 1, []
    upper = hint.upper()
    numeric = re.fullmatch(r'[\d.\s]+', hint) is not None
    exact, partial = [], []
    for gname, gdata in holdings.get("groups", {}).items():
        if group and gname != group:
            continue
        for index, pos in enumerate(gdata.get("positions", [])):
            name = str(pos.get("name", ""))
            ticker = str(pos.get("ticker", "")).upper()
            code = ticker.rsplit(":", 1)[-1]
            if hint == name or upper == ticker or (code and upper == code):
                exact.append((gname, index, pos))
            elif name and not numeric and (hint in name or (
                    name in hint and re.fullmatch(_FILLER, hint.replace(name, "", 1).strip(" 的")))):
                partial.append((gname, index, pos))
    return (0, exact) if exact else (1, partial)


def _parse_money_clause(holdings, text):
    """Account-level cash, fund and cost_basis clauses."""
    match = _MONEY.match(text.strip())
    if not match:
        return None
    keyword, op, raw = match.group("kw"), match.group("op") or "", match.group("amount")

    # The account is resolved before any currency word is removed, so an account
    # called "美元" is not mistaken for a currency.
    prefix = match.group("prefix")
    currency_words = [match.group(name) for name in ("cur1", "cur2", "cur3") if match.group(name)]
    named, group = _leading_group(holdings, prefix)
    if not named:
        in_prefix = _CURRENCY_WORDS.findall(prefix)
        if not in_prefix:
            return None
        currency_words += in_prefix
        named, group = _leading_group(holdings, _CURRENCY_WORDS.sub('', prefix))
        if not named:
            return None
    if group is None and any(_resolve_group(holdings, word) for word in currency_words):
        return _unknown(text, "有歧义（币种和账户名相同，请写「xx账户」）")
    if match.group("cur1") and (
            (prefix + match.group("cur1")).strip() in holdings.get("groups", {})
            or _resolve_group(holdings, (prefix + match.group("cur1")).strip()) not in (None, group)):
        return _unknown(text, "有歧义（账户名里带币种，请写「xx账户」）")
    group = group or _default_group(holdings)
    if not group:
        return None
    currency = currency_from_text(" ".join(currency_words))
    if keyword != "现金" and currency != "CNY":
        return None

    if keyword != "成本" and raw[0] == "+":
        return None  # "现金+5万" could mean "set to" or "add"; "-" is a negative balance
    delta_sign = 0
    if op.startswith("增加") or op.startswith("减少"):
        if raw[0] in "+-":
            return None
        delta_sign = 1 if op.startswith("增加") else -1
    elif keyword == "成本" and not op and raw[0] in "+-":
        delta_sign = 1 if raw[0] == "+" else -1
        raw = raw[1:]

    if keyword == "成本":
        if delta_sign:
            delta = _parse_amount(raw)
            if delta is None:
                return None
            return {"action": "delta_cost_basis", "group": group, "value": delta_sign * delta,
                    "description": f"{group}成本{'增加' if delta_sign > 0 else '减少'}{delta}"}
        amount = _parse_amount(raw, shorthand=True)
        if amount is None:
            return None
        return {"action": "set_cost_basis", "group": group, "value": amount, "description": f"{group}成本→{amount}"}

    amount = _parse_amount(raw, shorthand=currency == "CNY")
    if amount is None:
        return None
    label = "现金" if keyword == "现金" else "基金"
    data = holdings["groups"][group]
    if delta_sign:
        current = cash_balances(data).get(currency, 0) if keyword == "现金" else data.get("fund", 0)
        new_value = _number(current + delta_sign * amount)
        description = f"{group}{label}{'增加' if delta_sign > 0 else '减少'}{amount}→{new_value}"
    else:
        new_value, description = amount, f"{group}{label}→{amount}"
    if keyword == "基金":
        return {"action": "set_fund", "group": group, "value": new_value, "description": description}
    return {"action": "set_cash", "group": group, "value": new_value,
            **({"currency": currency} if currency != "CNY" else {}), "description": description}


def _parse_new_position(holdings, text):
    """ "新增 xxx ticker:SHE:002050 数量:1000 成本:50 组:进攻" """
    new_match = re.search(r'^(?P<prefix>.*?)新增\s+(?P<name>\S+)', text)
    if not new_match:
        return None
    named, prefix_group = _leading_group(holdings, new_match.group("prefix"))
    if not named:
        return None
    name = new_match.group("name")
    ticker_m = re.search(r'(?:ticker|代码(?:是|为)?)[：:]\s*(\S+)', text, flags=re.IGNORECASE)
    qty_m = re.search(rf'(?:数量|qty)[：:]\s*({_QTY})', text, flags=re.IGNORECASE)
    cost_m = re.search(r'(?:成本价?|cost)[：:]\s*(\d+(?:\.\d+)?)', text, flags=re.IGNORECASE)
    group_m = re.search(r'(?:组|group)[：:]\s*(\S+)', text, flags=re.IGNORECASE)

    field_group = _resolve_group(holdings, group_m.group(1)) if group_m else None
    if (group_m and not field_group) or (field_group and prefix_group and field_group != prefix_group):
        return None
    ticker = ticker_m.group(1).upper() if ticker_m else ""
    qty = _quantity(qty_m.group(1)) if qty_m else 0
    cost = float(cost_m.group(1)) if cost_m else 0.0
    group = field_group or prefix_group or _default_group(holdings)
    if not (ticker and qty and group):
        return None
    # Every part of the clause must have been understood.
    spans = sorted(m.span() for m in (new_match, ticker_m, qty_m, cost_m, group_m) if m)
    rest, end = "", 0
    for start, stop in spans:
        rest, end = rest + text[end:max(end, start)], max(end, stop)
    if (rest + text[end:]).strip(" ：:，,。.;；"):
        return None
    return {
        "action": "add_position", "group": group,
        "position": {"name": name, "ticker": ticker, "quantity": qty, "cost_price": cost},
        "description": f"新增{name} {ticker} {qty}股@{cost}"
    }


def _position_readings(holdings, body, ticker):
    """List every way a clause can be read.

    Each reading is (kind, quantity, name hint, account, account_is_explicit, starts_with_account).
    The caller keeps the readings whose name matches a holding best and rejects
    the clause when they disagree. A clause that gives only a ticker uses it as
    the name.
    """
    readings = []

    def add(kind, quantity, hint, group=None):
        hint = hint.strip() or ticker
        if not hint:
            return
        if group is not None:
            readings.append((kind, quantity, hint, group, True, False))
            return
        extra = []
        if ticker and hint != ticker:
            # "稳健账户 SHA:510300 减100": the words are the account, the ticker the stock.
            named, account = _leading_group(holdings, hint)
            if named and account:
                extra.append((kind, quantity, ticker, account, True, False))
        # "稳健账户药明康德减500": the name may start with an account.
        for gname in holdings.get("groups", {}):
            for prefix, explicit in ((f"{gname}账户", True), (f"{gname}组", True), (gname, False)):
                if hint.startswith(prefix) and len(hint) > len(prefix):
                    extra.append((kind, quantity, hint[len(prefix):], gname, explicit, False))
        led = re.match(r'^(.+?)(?:账户|组)的?\s*(.+)$', hint)
        if led and _resolve_group(holdings, led.group(1)):
            extra.append((kind, quantity, led.group(2), _resolve_group(holdings, led.group(1)), True, False))
        readings.append((kind, quantity, hint, None, False, any(reading[4] for reading in extra)))
        readings.extend(extra)

    def add_before_number(kind, match, group=None):
        """ "沪深3005股" may be 沪深 + 3005 or 沪深300 + 5: offer every split."""
        name, digits = match.group("name"), match.group("qty")
        variants = [(name, digits)]
        if name.strip() and match.end("name") == match.start("qty"):
            whole = digits.split(".")[0]
            variants += [(name + whole[:cut], digits[cut:]) for cut in range(1, len(whole))]
        for index, (hint, number) in enumerate(variants):
            quantity = _quantity(number)
            if quantity is not None and (quantity > 0 or (kind == "set" and index == 0)):
                add(kind, quantity, hint, group)

    for kind, verb in (("sell", _SELL), ("buy", _BUY)):
        verb_first = [
            rf'^(?P<prefix>.*?){verb}\s*(?P<qty>{_QTY})\s*股\s*的?\s*(?P<name>.*)$',
            rf'^(?P<prefix>.*?){verb}\s*(?P<qty>{_QTY})\s*(?P<name>[^\d.\s股].*)$',
        ]
        for pattern in verb_first:
            match = re.match(pattern, body)
            if not match:
                continue
            ok, group = _leading_group(holdings, match.group("prefix"))
            quantity = _quantity(match.group("qty"))
            if ok and quantity:
                add(kind, quantity, match.group("name"), group)
        match = re.match(rf'^(?P<prefix>.*?){verb}\s*(?P<name>.+?)\s*(?P<qty>{_QTY})\s*股$', body)
        if match:
            ok, group = _leading_group(holdings, match.group("prefix"))
            if ok:
                add_before_number(kind, match, group)
        match = re.match(rf'^(?P<name>.*?)\s*{verb}\s*(?P<qty>{_QTY})\s*股?$', body)
        if match and _quantity(match.group("qty")):
            add(kind, _quantity(match.group("qty")), match.group("name"))

    match = re.match(r'^(?P<prefix>.*?)(?:清仓|删除|清掉|卖光|卖完)了?\s*(?P<name>.*)$', body)
    if match:
        ok, group = _leading_group(holdings, match.group("prefix"))
        if ok:
            add("remove", 0, match.group("name"), group)
    match = re.match(r'^(?P<name>.+?)\s*(?:清了|清仓了?|清空了?|卖完了?|卖光了?|全卖了|全部卖出|删除)$', body)
    if match:
        add("remove", 0, match.group("name"))

    for pattern in (
        rf'^(?P<name>.*?)\s*的?\s*(?:数量|股数|持仓)?\s*{_SET_OP}?\s*(?P<qty>{_QTY})\s*股$',
        rf'^(?P<name>.*?)\s*的?\s*(?:数量|股数)\s*{_SET_OP}?\s*(?P<qty>{_QTY})\s*股?$',
    ):
        match = re.match(pattern, body)
        if match:
            add_before_number("set", match)
    return readings


def _parse_position_clause(holdings, text):
    """Buy, sell, remove and set-quantity clauses for one position."""
    if len({found.upper() for found in re.findall(_TICKER, text)}) > 1:
        return _unknown(text, "有歧义（一条里只能有一个代码）")
    ticker = _extract_ticker(text)
    body = _strip_ticker_phrase(text)
    named_group = None
    group_match = re.search(r'(?:组|group)[：:]\s*(\S+)', body, flags=re.IGNORECASE)
    if group_match:
        named_group = _resolve_group(holdings, group_match.group(1))
        if not named_group:
            return None
        body = body[:group_match.start()] + body[group_match.end():]
    body = re.sub(r'\s+', ' ', body).strip(" ：:，,。.;；")
    # A price is the last thing in a clause. The same words can also end a stock
    # name, so the clause is read both ways and the readings must agree.
    bodies = [(body, None)]
    price_match = _PRICE.search(body)
    if price_match and price_match.end() == len(body):
        bodies.append((body[:price_match.start()].strip(" ：:，,。.;；"), float(price_match.group(1))))

    resolved, conflict, purchases = [], False, []
    for body, price in bodies:
        readings = [
            (kind, quantity, hint, group or named_group, explicit or bool(named_group), account_led)
            for kind, quantity, hint, group, explicit, account_led in _position_readings(holdings, body, ticker)
            if not (group and named_group and group != named_group)
        ]
        if price is not None or len(bodies) == 1:
            purchases = (readings, price)
        for kind, quantity, hint, group, _explicit, account_led in readings:
            ticker_tier, by_ticker = _match_positions(holdings, ticker, group) if ticker else (1, [])
            if ticker_tier != 0:
                by_ticker = []
            if ticker and hint.upper() == ticker:
                tier, matches = 0, by_ticker
            else:
                tier, matches = _match_positions(holdings, hint, group)
                if by_ticker:
                    # The ticker identifies the holding; the name must not point elsewhere.
                    common = [m for m in matches if any(m[:2] == t[:2] for t in by_ticker)]
                    if matches and not common:
                        conflict = True
                        continue
                    if common:
                        matches = common
                    elif account_led:
                        continue  # the account-qualified readings decide
                    elif _NOT_A_NAME.search(hint):
                        conflict = True  # more than a slightly different name for that security
                        continue
                    else:
                        tier, matches = 0, by_ticker
                elif ticker and matches:
                    conflict = True  # a held name, but the ticker is a different security
                    continue
            if matches:
                resolved.append((tier, kind, quantity, price, matches))

    if not resolved:
        if conflict:
            return _unknown(text, "名称和代码对不上")
        readings, price = purchases
        return _new_position_from_purchase(holdings, text, readings, ticker, price)

    # Exact names beat partial ones; readings that still disagree are ambiguous.
    best = min(tier for tier, *_ in resolved)
    outcomes = {}
    for tier, kind, quantity, price, matches in resolved:
        if tier != best:
            continue
        if len(matches) != 1:
            return _unknown(text, "有歧义（匹配到多个持仓，请写明账户或完整名称）")
        gname, index, pos = matches[0]
        outcomes[(kind, gname, index, quantity, price)] = pos
    if len(outcomes) != 1:
        return _unknown(text, "有歧义（可以有多种理解）")

    (kind, gname, index, quantity, price), pos = next(iter(outcomes.items()))
    held = pos["quantity"]
    target = {"group": gname, "position_index": index, "name": pos["name"]}

    if price is not None and kind in ("set", "remove"):
        return _unknown(text, "价格只能写在买入里")
    if kind == "remove" or (kind == "set" and quantity == 0):
        return {"action": "remove_position", **target, "value": 0, "description": f"清仓{pos['name']}"}
    if kind == "set":
        return {"action": "set_quantity", **target, "value": quantity,
                "description": f"{pos['name']}数量→{quantity}股"}
    if kind == "sell":
        if quantity > held:
            raise ValueError(f"Cannot sell {quantity} shares; only {held} held")
        new_qty = _exact_sum(held, -quantity)
        if new_qty is None:
            return _unknown(text, "数量精度超出可记录范围")
        note = "（卖出价未记录）" if price is not None else ""
        return {"action": "set_quantity" if new_qty > 0 else "remove_position", **target,
                "value": new_qty, "description": f"{pos['name']}减{quantity}股→{new_qty}股{note}"}

    new_qty = _exact_sum(held, quantity)
    if new_qty is None:
        return _unknown(text, "数量精度超出可记录范围")
    change = {"action": "set_quantity", **target, "value": new_qty,
              "description": f"{pos['name']}加{quantity}股→{new_qty}股"}
    old_cost = pos.get("cost_price")
    if price is not None:
        if isinstance(old_cost, (int, float)) and not isinstance(old_cost, bool):
            change["cost_price"] = round((held * old_cost + quantity * price) / new_qty, 4)
            change["description"] += f"，成本价→{change['cost_price']}"
        else:
            change["description"] += "（原持仓没有成本价，成本价未更新）"
    return change


def _new_position_from_purchase(holdings, text, readings, ticker, price):
    """Buying something that is not held creates a position when a ticker is given."""
    purchases = {
        (hint.strip(" 的"), group, quantity, explicit)
        for kind, quantity, hint, group, explicit, _account_led in readings
        if kind == "buy" and hint.strip(" 的").upper() != ticker and re.search(r'[^\d\s.]', hint)
        and not _NOT_A_NEW_NAME.search(hint)
    }
    if not ticker or not purchases:
        return None
    explicit = {(name, group, quantity) for name, group, quantity, is_explicit in purchases if is_explicit}
    if explicit:
        candidates = explicit
    elif any(group for _name, group, _quantity_, _explicit in purchases):
        # "稳健特斯拉加100": the name may or may not start with an account.
        return _unknown(text, "有歧义（请用「账户」或「组:」写明账户）")
    else:
        candidates = {(name, group, quantity) for name, group, quantity, _explicit in purchases}
    if len(candidates) != 1:
        return _unknown(text, "有歧义（可以有多种理解）")
    name, group, quantity = next(iter(candidates))
    group = group or _default_group(holdings)
    if not group:
        return None
    cost = float(price or 0)
    return {
        "action": "add_position", "group": group,
        "position": {"name": name, "ticker": ticker, "quantity": quantity, "cost_price": cost},
        "description": f"新增{name} {ticker} {quantity}股@{cost}"
    }


def _parse_single_change(holdings, text):
    """Parse a single change directive."""
    text = text.strip()
    for parser in (_parse_money_clause, _parse_new_position, _parse_position_clause):
        change = parser(holdings, text)
        if change:
            return change
    return _unknown(text)


def _apply_single_change(holdings, change):
    """Apply a single parsed change to holdings."""
    action = change["action"]
    
    if action == "no_change":
        return
    
    if action == "set_cash":
        set_cash_balance(holdings["groups"][change["group"]], change["value"], change.get("currency", "CNY"))
    
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
        if "cost_price" in change:
            pos["cost_price"] = change["cost_price"]
    
    elif action == "remove_position":
        holdings["groups"][change["group"]]["positions"].pop(change["position_index"])
    
    elif action == "add_position":
        holdings["groups"][change["group"]]["positions"].append(change["position"])


def _infer_missing_cost_prices(holdings_before, changes):
    """Infer per-share cost for a newly added position when omitted by the user.

    Only when the cash (or fund) spent can be attributed to that one purchase:
    - the position is the only quantity change in its account in this update
    - it is priced in CNY (SHA/SHE ticker), like the account cash it is compared with
    - the same update sets the account cash, or else fund, to a lower value
    """
    quantity_actions = {"add_position", "set_quantity", "remove_position"}
    for change in changes:
        if change.get("action") != "add_position":
            continue
        pos = change.get("position", {})
        qty = pos.get("quantity", 0)
        cost_price = pos.get("cost_price", 0) or 0
        group = change.get("group")
        if qty <= 0 or cost_price > 0 or not group:
            continue
        if str(pos.get("ticker", "")).upper().split(":")[0] not in {"SHA", "SHE"}:
            continue
        if sum(1 for c in changes if c.get("group") == group and c.get("action") in quantity_actions) != 1:
            continue

        before_group = holdings_before.get("groups", {}).get(group, {})
        if "cash_balances" in before_group:
            continue
        old_cash = before_group.get("cash")
        old_fund = before_group.get("fund")

        # Use the balances the update ends with, not the first of several changes.
        cash_values = [c["value"] for c in changes if c.get("action") == "set_cash"
                       and c.get("group") == group and "currency" not in c]
        fund_values = [c["value"] for c in changes if c.get("action") == "set_fund" and c.get("group") == group]
        new_cash = cash_values[-1] if cash_values else None
        new_fund = fund_values[-1] if fund_values else None

        inferred = None
        if old_cash is not None and new_cash is not None and new_cash < old_cash:
            inferred = (old_cash - new_cash) / qty
        elif old_fund is not None and new_fund is not None and new_fund < old_fund and (new_cash is None or new_cash == old_cash):
            inferred = (old_fund - new_fund) / qty

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
    
    from feishu_workbench_notify import try_daily_card
    if try_daily_card(date_str, chat_id):
        print("  ✅ 每日更新卡片已进入发送队列")
        return True

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


def python_command():
    """Command prefix that starts a Python engine script.

    The default is the interpreter running this process, so the engine works
    without conda. Set ``CONDA_ENV`` to run the scripts with
    ``conda run -n $CONDA_ENV python3`` instead; ``CONDA_EXE`` selects the conda
    executable. The scheduler and the cron shell scripts follow the same rule.
    """
    conda_env = os.environ.get("CONDA_ENV", "").strip()
    if conda_env:
        conda = os.environ.get("CONDA_EXE") or shutil.which("conda") or "conda"
        return [conda, "run", "-n", conda_env, "python3"]
    return [sys.executable]


# Seconds a pipeline child may run. On a timeout the child and everything it
# started (such as the Python under ``conda run``) are stopped before the stage
# fails, so nothing writes after the caller releases the portfolio lock.
SNAPSHOT_TIMEOUT = 120  # includes the quote and exchange-rate requests
REPORT_TIMEOUT = 30


def run_snapshot(date_str):
    """Run the snapshot engine.

    Callers normally hold the portfolio write lock (the CLI entrypoint, the
    agent tools and the Feishu adapter's pipeline child do). Only then is the
    child told that it may work under that lock; otherwise it takes the lock
    itself.
    """
    script = BASE_DIR / "portfolio_snapshot.py"
    command = [*python_command(), str(script), "--date", date_str]

    try:
        result = run_child(command, timeout=SNAPSHOT_TIMEOUT, portfolio_dir=PORTFOLIO_DIR, cwd=str(BASE_DIR))
    except OSError as e:
        print(f"  ❌ 快照生成失败: 无法启动 {command[0]} ({e})")
        return False
    except subprocess.TimeoutExpired:
        print(f"  ❌ 快照生成失败: 超过 {SNAPSHOT_TIMEOUT} 秒未完成，已终止")
        return False

    if result.returncode == 0:
        print(f"  ✅ 快照生成成功")
        if result.stdout:
            # Print last few lines (summary)
            lines = result.stdout.strip().split('\n')
            for line in lines[-8:]:
                print(f"    {line}")
        return True
    else:
        # The reason is the last thing the snapshot prints (after any per-ticker warnings).
        print(f"  ❌ 快照生成失败: {result.stderr.strip()[-300:]}")
        return False


def run_report(date_str):
    """Generate markdown report."""
    script = BASE_DIR / "portfolio_report.py"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORTS_DIR / f"portfolio-{date_str.replace('-', '')}.md"
    command = [*python_command(), str(script), "--date", date_str, "-o", str(report_file)]

    try:
        result = run_child(command, timeout=REPORT_TIMEOUT, cwd=str(BASE_DIR))
    except OSError as e:
        print(f"  ⚠️ 报告生成失败: 无法启动 {command[0]} ({e})")
        return None
    except subprocess.TimeoutExpired:
        print(f"  ⚠️ 报告生成失败: 超过 {REPORT_TIMEOUT} 秒未完成，已终止")
        return None

    if result.returncode == 0 and report_file.exists():
        print(f"  ✅ 报告已生成: {report_file.name}")
        return report_file
    else:
        print(f"  ⚠️ 报告生成失败: {result.stderr[:200]}")
        return None


def holdings_digest(date_str):
    """Content hash of the holdings the snapshot for ``date_str`` values, or None.

    That is the confirmed ledger when it has a state for the date (the hash
    covers its revision), as in ``portfolio_snapshot.load_holdings``; otherwise
    the bytes of the legacy holdings file, which the Feishu adapter also hashes.
    """
    import hashlib
    from ledger_bridge import ledger_holdings
    confirmed = ledger_holdings(PORTFOLIO_DIR, date_str)
    if confirmed is not None:
        text = json.dumps(confirmed, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(text.encode()).hexdigest()
    holdings_file = HOLDINGS_DIR / f"{date_str}.json"
    if not holdings_file.is_file():
        return None
    return hashlib.sha256(holdings_file.read_bytes()).hexdigest()


def file_sha(path):
    """SHA-256 of a file's bytes, or None when it cannot be read."""
    import hashlib
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


# The checkpoint ``pipeline-state/<date>.json`` records, besides the stage flags,
# which snapshot each stage was made from, so that no stage counts as done just
# because its file exists:
#   snapshot_before   SHA-256 of the day's snapshot JSON before the snapshot stage
#                     started (None: there was none); tells, after an interruption,
#                     whether the JSON on disk was saved by that stage
#   snapshot_sha      SHA-256 of the snapshot JSON the later stages are made from
#   history_snapshot  the snapshot the day's history.csv row was written from
#   report_snapshot   the snapshot the report was made from
#   report_sha        SHA-256 of the report file as it was made; a report changed or
#                     replaced since is not the day's report and is made again
#
# A snapshot JSON counts only for the day it names inside (``date``): one of
# another day saved under this day's file name is refused, never written into
# this day's history row or report.


def snapshot_day_problem(path, date_str):
    """Why the snapshot JSON at ``path`` cannot be ``date_str``'s, or None."""
    try:
        snapshot = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        print(f"  ⚠️ 当日快照文件无法读取（{type(error).__name__}）", file=sys.stderr)
        return "当日快照文件无法读取（不是有效的 JSON 或无法打开）"
    day = snapshot.get("date") if isinstance(snapshot, dict) else None
    if day != date_str:
        return (f"{WRONG_DAY}：文件名是 {date_str}，内容的日期是 {str(day)[:10]}；未写入历史记录和日报。"
                "请检查快照文件，或删除它后重新生成")
    return None


def report_is_current(state, report_file, snapshot_sha):
    """Whether the day's report is the one this checkpoint made from the snapshot now on disk, unchanged since."""
    return bool(state.get('report') and report_file.is_file() and state.get('report_snapshot') == snapshot_sha
                and state.get('report_sha') is not None and state.get('report_sha') == file_sha(report_file))


def run_pipeline(date_str, send_report=True, *, resume=False):
    """Checkpoint snapshot/report stages and never resend an uncertain push.

    The report counts as done only when it was made from the snapshot now on
    disk (``report_snapshot``); otherwise it is made again, never reused.
    """
    config = load_config()
    digest = holdings_digest(date_str)
    if digest is None:
        print(f"  ❌ 没有 {date_str} 的持仓（{NO_HOLDINGS}）：账本没有该日或之前的确认记录，也没有该日的持仓文件",
              file=sys.stderr)
        return False
    checkpoint_path = PORTFOLIO_DIR / "pipeline-state" / f"{date_str}.json"
    state = {}
    if checkpoint_path.exists():
        try:
            state = json.loads(checkpoint_path.read_text())
        except (OSError, ValueError):
            return False
    if state.get('holdings_hash') != digest:
        if resume:
            return False
        state = {'holdings_hash': digest, 'snapshot': False, 'report': False, 'push': 'not_started'}
    def save():
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_dump(state, checkpoint_path)
    snap_file = SNAPSHOTS_DIR / f"{date_str}.json"
    if not state.get('snapshot'):
        state['snapshot_before'] = file_sha(snap_file)
    save()
    if not state.get('snapshot'):
        if not run_snapshot(date_str):
            return False
        # The snapshot writes its JSON and then the day's history row; it exits 0 only after both.
        made = file_sha(snap_file)
        state.update(snapshot=True, history=True, snapshot_sha=made, history_snapshot=made)
        save()
    # The snapshot stage saves the day's own JSON; one found there from an earlier run may be another day's.
    problem = snapshot_day_problem(snap_file, date_str) if snap_file.exists() else None
    if problem:
        print(f"  ❌ {problem}", file=sys.stderr)
        return False
    current = file_sha(snap_file)
    report_file = REPORTS_DIR / f"portfolio-{date_str.replace('-', '')}.md"
    if not report_is_current(state, report_file, current):
        report_file = make_report(date_str)
        if report_file is None:
            return False
        state.update(report=True, report_snapshot=current, report_sha=file_sha(report_file))
        save()
    if send_report and state.get('push') != 'complete':
        if state.get('push') == 'uncertain':
            # A process can die after Feishu accepts the message. Require
            # reconciliation instead of silently publishing a second copy.
            return False
        state['push'] = 'uncertain'
        save()
        if not send_feishu_report(date_str, report_file.read_text(), config):
            return False
        state['push'] = 'complete'
        save()
    state['complete'] = not send_report or state.get('push') == 'complete'
    save()
    return True


def action_notify(date_str):
    """Cron action: clone holdings + send Feishu notification.

    With an active ledger nothing is cloned (the holdings are the ledger's; the Feishu card records into it)
    and the reminder describes the ledger's holdings on the day.
    """
    config = load_config()
    
    print(f"📋 每日持仓更新通知 — {date_str}")
    
    from ledger_bridge import active_ledger, ledger_holdings
    if active_ledger(PORTFOLIO_DIR):
        print("\n[1/2] 交易账本已启用：不克隆旧持仓文件")
        holdings = ledger_holdings(PORTFOLIO_DIR, date_str) or {"groups": {}}
        print("\n[2/2] 发送飞书通知...")
        send_feishu_notification(date_str, holdings, config)
        return True

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
    if any(change.get("action") == "unknown" for change in changes):
        print("  ❌ 部分内容未识别，未保存任何变更。请拆分或使用结构化 CLI。", file=sys.stderr)
        return False
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


# Printed when a stage of an interrupted day is completed; the backend's close
# snapshot routine reads these to say what was repaired.
HISTORY_REPAIRED = "已补写当日历史记录"
REPORT_REPAIRED = "已补生成日报"


# Printed when the auto pipeline finds a day whose snapshot was made for other holdings.
HOLDINGS_CHANGED = "当日快照之后持仓有变化"
# Printed when the day has no holdings in force (no ledger state on or before it, no holdings file
# dated on or before it); the backend's close snapshot routine reports the day as unavailable.
NO_HOLDINGS = "没有该日有效的持仓"
# Printed when the snapshot JSON under the day's name is another day's.
WRONG_DAY = "当日快照文件的日期与请求的日期不符"


def make_report(date_str):
    """Generate the day's report from the snapshot on disk; returns the file or None.

    The previous report is removed first, so a failure can never leave an
    older report looking like this one.
    """
    report_file = REPORTS_DIR / f"portfolio-{date_str.replace('-', '')}.md"
    try:
        report_file.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f"  ⚠️ 无法替换旧日报（{type(error).__name__}）", file=sys.stderr)
        return None
    made = run_report(date_str)
    return made if made and made.is_file() else None


def history_line(date_str):
    """The day's ``history.csv`` row without its line end, or None."""
    try:
        lines = (PORTFOLIO_DIR / "history.csv").read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    rows = [line for line in lines[1:] if line.startswith(f"{date_str},")]
    return rows[-1] if rows else None


def history_has_row(date_str):
    """Whether ``history.csv`` has a row for ``date_str``."""
    return history_line(date_str) is not None


def history_matches(snapshot, date_str):
    """Whether the day's row is the one ``snapshot`` gives; None when the snapshot gives none to compare."""
    import portfolio_snapshot
    try:
        expected = portfolio_snapshot.history_row(snapshot, date_str).rstrip("\n")
    except (KeyError, TypeError, AttributeError):
        return None
    return history_line(date_str) == expected


def snapshot_matches_ledger(snapshot, date_str):
    """For a ledger portfolio: whether ``snapshot`` was made from the ledger's current revision.

    None when that cannot be told (legacy holdings, or a snapshot without the revision).
    """
    from ledger_bridge import ledger_holdings
    confirmed = ledger_holdings(PORTFOLIO_DIR, date_str)
    if confirmed is None or not isinstance(snapshot, dict) or snapshot.get("ledger_revision") is None:
        return None
    return snapshot.get("ledger_revision") == confirmed.get("ledger_revision")


def repair_history(date_str):
    """Write the day's ``history.csv`` row from the saved snapshot; no price is fetched."""
    import portfolio_snapshot
    try:
        snapshot = json.loads((SNAPSHOTS_DIR / f"{date_str}.json").read_text())
        portfolio_snapshot.upsert_history(snapshot, date_str, PORTFOLIO_DIR)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"  ❌ 无法根据已保存的快照补写 history.csv（{type(error).__name__}）；"
              "请检查快照文件，或删除它后重新生成", file=sys.stderr)
        return False
    print(f"  🔧 {HISTORY_REPAIRED}（history.csv，按已保存的快照，未重新获取行情）")
    return True


def resume_pipeline(date_str, send_report):
    """Complete a day whose snapshot JSON exists, from its checkpoint, without fetching prices.

    No stage counts as done because its file exists:

    * the snapshot: when the checkpoint says its stage started and did not
      finish, the JSON on disk is that stage's only if it differs from the
      one there before (``snapshot_before``); otherwise it is an earlier one
      and the whole pipeline runs (``run_pipeline``), as without a snapshot.
      A checkpoint of the previous release has no ``snapshot_before``; a
      ledger snapshot is then judged by its ledger revision, a legacy one is
      taken as saved. A snapshot made for holdings that have changed since is
      kept (the auto pipeline makes a day once), with a notice;
    * the day's ``history.csv`` row must be the one the snapshot gives, or it
      is written again from the snapshot;
    * the report must have been made from this snapshot (``report_snapshot``)
      and after the history row was last written, and be unchanged since
      (``report_sha``), or it is made again;
    * the push: ``complete`` stays done, ``uncertain`` is never resent, one
      never started is sent.

    Without a checkpoint (the snapshot was made by another route, which never
    pushes) the snapshot is taken as saved and the rest is checked the same way.
    Before anything is written, the snapshot must be the day's own (its
    ``date``): another day's JSON under this name is refused.
    """
    checkpoint_path = PORTFOLIO_DIR / "pipeline-state" / f"{date_str}.json"
    snap_file = SNAPSHOTS_DIR / f"{date_str}.json"
    had_checkpoint = checkpoint_path.exists()
    state = {}
    if had_checkpoint:
        try:
            state = json.loads(checkpoint_path.read_text())
        except (OSError, ValueError):
            print("  ❌ 流水线检查点无法读取，未继续", file=sys.stderr)
            return False
        if not isinstance(state, dict):
            print("  ❌ 流水线检查点格式不对，未继续", file=sys.stderr)
            return False
    try:
        snapshot = json.loads(snap_file.read_text())
    except (OSError, ValueError) as error:
        print(f"  ❌ 当日快照文件无法读取（{type(error).__name__}）；请检查快照文件，或删除它后重新生成",
              file=sys.stderr)
        return False
    current = file_sha(snap_file)
    digest = holdings_digest(date_str)

    if state and not state.get('snapshot'):
        # The snapshot stage started and did not finish: was the JSON on disk saved by it?
        if 'snapshot_before' in state:
            unfinished = state['snapshot_before'] == current
        else:
            unfinished = snapshot_matches_ledger(snapshot, date_str) is False
        if unfinished:
            print("  ⚠️ 当日快照的生成曾中断，磁盘上的快照是之前生成的；重新生成当日快照")
            return run_pipeline(date_str, send_report)
    problem = snapshot_day_problem(snap_file, date_str)
    if problem:
        print(f"  ❌ {problem}", file=sys.stderr)
        return False
    if not state:
        state = {'holdings_hash': digest, 'report': False, 'push': 'not_started'}
    elif state.get('holdings_hash') != digest:
        print(f"  ⚠️ {HOLDINGS_CHANGED}：自动流水线保留按当时持仓生成的快照；"
              "如需按当前持仓重做，请运行 --action pipeline")

    def save():
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_dump(state, checkpoint_path)

    state.update(snapshot=True, snapshot_sha=current)
    matches = history_matches(snapshot, date_str)
    vouched = state.get('history') and state.get('history_snapshot') == current and history_has_row(date_str)
    rewritten = False
    if matches is False or (matches is None and not vouched):
        if not repair_history(date_str):
            state['history'] = False
            save()
            return False
        rewritten = True
    state.update(history=True, history_snapshot=current)
    report_file = REPORTS_DIR / f"portfolio-{date_str.replace('-', '')}.md"
    if rewritten or not report_is_current(state, report_file, current):
        report_file = make_report(date_str)
        if report_file is None:
            state['report'] = False
            save()
            return False
        print(f"  🔧 {REPORT_REPAIRED}：{report_file.name}")
        state['report_sha'] = file_sha(report_file)
    state.update(report=True, report_snapshot=current)
    save()
    if send_report and state.get('push') != 'complete':
        if state.get('push') == 'uncertain':
            # A process can die after Feishu accepts the message: never send a second copy.
            print("  ⚠️ 飞书推送结果未确认，不会自动重发")
        else:
            state['push'] = 'uncertain'
            save()
            if not send_feishu_report(date_str, report_file.read_text(), load_config()):
                return False
            state['push'] = 'complete'
    state['complete'] = not send_report or state.get('push') == 'complete'
    save()
    return True


def action_auto_pipeline(date_str):
    """Auto pipeline: make the day's snapshot, history row, report and push, each once.

    Without the day's snapshot JSON the whole pipeline runs. With it, the day
    is completed from its checkpoint (``resume_pipeline``): a run killed after
    the snapshot JSON was saved, or whose report failed, is finished without
    fetching prices again, and exits non-zero while a stage still fails.

    With an active ledger the snapshot values the confirmed ledger, so no
    legacy holdings file is cloned (the ledger refuses that write). The report
    is pushed only when ``feishu_chat_id`` is configured, the setting that
    ``send_feishu_report`` needs; without it a saved snapshot and report are
    a success rather than a failed push.
    """
    send_report = bool(load_config().get("feishu_chat_id"))
    snap_file = SNAPSHOTS_DIR / f"{date_str}.json"
    if snap_file.exists():
        print(f"  ℹ️  今日快照已存在 ({snap_file.name})，检查历史记录、日报和推送是否完成")
        return resume_pipeline(date_str, send_report)

    print(f"  ⏰ 今日快照不存在，自动生成...")
    from ledger_bridge import active_ledger
    if active_ledger(PORTFOLIO_DIR) is None and clone_holdings(date_str)[0] is None:
        return False
    if not send_report:
        print("  ℹ️  未配置 feishu_chat_id，日报只保存不推送")
    return run_pipeline(date_str, send_report=send_report)


@portfolio_write_locked(lambda: PORTFOLIO_DIR)
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
