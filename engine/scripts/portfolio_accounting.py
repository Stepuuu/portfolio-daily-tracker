"""Pure accounting helpers shared by snapshot and holdings writers.

Holdings.cash is the legacy CNY balance. When cash_balances is present it is
authoritative and cash, if present, must mirror its CNY component. Snapshot.cash
is the converted CNY total for compatibility with existing reports.
"""
import math
import json
import os
import tempfile
import re
from pathlib import Path
from contextlib import contextmanager


@contextmanager
def atomic_text_writer(path):
    """Replace one file after a complete write; preserve the old file on error.

    This prevents truncated files. It is not a multi-file transaction or a lock.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json_dump(data, path):
    with atomic_text_writer(path) as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)


def finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def currency_from_text(text):
    currencies = [currency for currency, pattern in {
        "CNY": r"人民币|人民币元|(?<![A-Z])CNY(?![A-Z])|(?<![A-Z])RMB(?![A-Z])",
        "USD": r"美元|美金|(?<![A-Z])USD(?![A-Z])",
        "HKD": r"港币|港元|(?<![A-Z])HKD(?![A-Z])",
    }.items() if re.search(pattern, text.upper())]
    if len(currencies) > 1:
        raise ValueError("Please update one cash currency per clause")
    return currencies[0] if currencies else "CNY"


def cash_balances(group):
    balances = group.get("cash_balances")
    if balances is None:
        return {"CNY": finite_number(group.get("cash", 0), "cash")}
    if not isinstance(balances, dict):
        raise ValueError("cash_balances must be a currency-to-amount object")
    result = {}
    for currency, amount in balances.items():
        if currency not in {"CNY", "HKD", "USD"}:
            raise ValueError(f"Unsupported cash currency: {currency}")
        result[currency] = finite_number(amount, f"cash_balances.{currency}")
    if "cash" in group and finite_number(group["cash"], "cash") != result.get("CNY", 0):
        raise ValueError("Holdings cash must equal cash_balances.CNY; it is not the converted total")
    return result


def set_cash_balance(group, amount, currency="CNY"):
    balances = cash_balances(group)
    if currency not in {"CNY", "HKD", "USD"}:
        raise ValueError(f"Unsupported cash currency: {currency}")
    amount = finite_number(amount, "cash amount")
    balances[currency] = amount
    if "cash_balances" in group or currency != "CNY":
        group["cash_balances"] = balances
    group["cash"] = balances.get("CNY", 0)


def fx_rate(currency, rates):
    if currency == "CNY":
        return 1.0
    rate = finite_number(rates.get(currency), f"{currency}/CNY exchange rate")
    if rate <= 0:
        raise ValueError(f"{currency}/CNY exchange rate must be positive")
    return rate


def value_cash(group, rates):
    balances = cash_balances(group)
    values = {currency: amount * fx_rate(currency, rates) if amount else 0.0
              for currency, amount in balances.items()}
    return sum(values.values()), balances, values


def ordered_history(history, snapshots, before_date):
    """CSV takes precedence; snapshots fill gaps. Ignore today's/future rows."""
    rows = {}
    for snapshot in snapshots or []:
        summary = snapshot.get("summary", {})
        day = snapshot.get("date", "")
        if day and day < before_date and "total_value" in summary and "total_cost" in summary:
            rows[day] = (day, summary["total_value"], summary["total_cost"])
    for day, value, cost in history or []:
        if day < before_date:
            rows[day] = (day, value, cost)
    return [rows[day] for day in sorted(rows)]


def drawdowns(returns):
    """Current and maximum drawdown of linked, flow-adjusted daily returns.

The caller estimates external flows using changes in contributed capital.
This is a daily end-of-period-flow approximation, not intraday TWR or XIRR.
"""
    nav = peak = 1.0
    worst = current = 0.0
    for daily_return in returns:
        nav *= 1.0 + daily_return
        peak = max(peak, nav)
        current = (nav / peak - 1.0) * 100
        worst = min(worst, current)
    return round(current, 2), round(worst, 2)
