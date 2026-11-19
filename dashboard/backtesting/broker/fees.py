"""Dated trading fees for the simulated broker.

The statutory part (taxes, exchange fees and levies) is data: broker/
fee_schedules.json holds one table of dated rows per market and component, each
row citing the notice it comes from. The broker commission is the caller's
setting (rate and minimum, the minimum applied to the commission only); when it
is not set, the market default in the same file is used and stated.

A trade dated before a component's first row is charged at that first row and
marked ``assumed`` (the result then carries the ``fee_assumed`` caveat).

Every component is rounded on its own (cents half-up, HK stamp duty up to the
next dollar, the US SEC fee up to the next cent) and the total is the sum of the
rounded parts. Decimal arithmetic inside, floats out.

What the schedule lists under ``ignored`` per market is not modelled; it is
disclosed in the result (fees_assumption_text for the assumptions, and the
fee_not_modelled caveat, whose Chinese wording is NOT_MODELLED). The HK ETF
stamp-duty exemption is one of them: symbols.resolve() does not tell HK ETFs
apart from stocks, so every HK symbol is charged as a stock; the caveat warns of
it only for a code in the ETF ranges (HK_ETF_CODE_RANGES, not_modelled_hk).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .market_rules import market_for, instrument_kind

SCHEDULE_FILE = Path(__file__).with_name("fee_schedules.json")
CENT = Decimal("0.01")
DOLLAR = Decimal("1")


def _to_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):          # pandas.Timestamp
        return value.to_pydatetime().date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _round(amount: Decimal, rule: str) -> Decimal:
    if rule == "dollar_up":
        return amount.quantize(DOLLAR, rounding=ROUND_CEILING).quantize(CENT)
    if rule == "cent_up":
        return amount.quantize(CENT, rounding=ROUND_CEILING)
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


class FeeSchedule:
    """The parsed fee_schedules.json (or an equivalent dict, for tests)."""

    def __init__(self, data: dict):
        self.data = data
        self.version = str(data.get("version", ""))
        self._rows: Dict[Tuple[str, str], List[Tuple[date, Decimal]]] = {}
        for market, spec in data.get("markets", {}).items():
            for name, component in spec.get("components", {}).items():
                rows = sorted((date.fromisoformat(row["from"]), _dec(row["rate"])) for row in component["rows"])
                if not rows:
                    raise ValueError(f"fee schedule {market}.{name} has no rows")
                self._rows[(market, name)] = rows

    @classmethod
    def from_file(cls, path=SCHEDULE_FILE) -> "FeeSchedule":
        with open(path, encoding="utf-8") as handle:
            return cls(json.load(handle))

    def components(self, market: str) -> Dict[str, dict]:
        return self.data.get("markets", {}).get(market, {}).get("components", {})

    def rate(self, market: str, component: str, when: Optional[date]) -> Tuple[Decimal, bool]:
        """(rate in force on `when`, assumed). An unknown date uses the latest row."""
        rows = self._rows[(market, component)]
        if when is None:
            return rows[-1][1], False
        if when < rows[0][0]:
            return rows[0][1], True
        current = rows[0][1]
        for start, row_rate in rows:
            if when >= start:
                current = row_rate
        return current, False

    def first_date(self, market: str) -> Optional[date]:
        """Latest first-row date across the market's components: trades before it
        use at least one assumed row."""
        starts = [rows[0][0] for (m, _), rows in self._rows.items() if m == market]
        return max(starts) if starts else None

    def commission_default(self, market: str) -> Tuple[Decimal, Decimal]:
        spec = self.data.get("commission_defaults", {}).get(market, {"rate": "0", "min": "0"})
        return _dec(spec["rate"]), _dec(spec["min"])

    def ignored(self, market: str) -> List[str]:
        return list(self.data.get("markets", {}).get(market, {}).get("ignored", []))


@lru_cache(maxsize=1)
def default_schedule() -> FeeSchedule:
    return FeeSchedule.from_file()


# The schedule's `ignored` entries in the user's language, per market (keep in
# step with fee_schedules.json markets.<market>.ignored). Level of the caveat:
# warning where the omission can move a result materially.
NOT_MODELLED = {
    "a_share": ("info", "经手费和证管费不单独计算（通常已含在券商佣金里，若你的佣金不含请调高佣金率）；"
                        "2015-08-01 之前沪市按股数收取的过户费没有建模，按 2015 年起的费率估算。"),
    "hk": ("warning", "港股 ETF 自 2015-02-13 起免征印花税，这里没有建模：代码无法区分港股 ETF 和股票，"
                      "所有港股都按股票收取买卖各约 0.1% 的印花税，交易 ETF 时成本被高估、收益被低估；"
                      "中央结算交收费和券商平台费也没有计入，可把它们算进佣金。"),
    "us": ("info", "卖出时的 FINRA 交易活动费（TAF，每笔最多几美元）没有计入；"
                   "费率表最后一行之后的 SEC 费率假定不变（SEC 每个财年至少调整一次）。"),
}


def not_modelled(market: str) -> str:
    """What the fee model leaves out for `market`, in Chinese ('' if nothing)."""
    return NOT_MODELLED.get(market, ("", ""))[1]


# HKEX stock codes allocated to exchange-traded funds and leveraged and inverse products
# (HKD, USD and RMB counters), the instruments the 2015-02-13 stamp-duty relief covers.
# A code outside these ranges is not an ETF (shares, warrants, CBBCs, debt securities ...).
HK_ETF_CODE_RANGES = ((2800, 2849), (3000, 3199), (7200, 7399), (7500, 7599), (9000, 9199),
                      (9200, 9399), (9500, 9599), (9800, 9849), (82800, 82849), (83000, 83199),
                      (87200, 87399), (87500, 87599))
HK_PLATFORM_FEES = "中央结算交收费和券商平台费没有计入，可把它们算进佣金。"


def hk_etf_code(symbol: str) -> Optional[bool]:
    """True when the HK symbol's code is in an ETF / leveraged and inverse product range,
    False when it is not, None when its code cannot be read."""
    try:
        from backtesting.data.symbols import resolve
        code = int(resolve(symbol).code)
    except (ValueError, TypeError, AttributeError, ImportError):
        return None
    return any(low <= code <= high for low, high in HK_ETF_CODE_RANGES)


def not_modelled_hk(symbols) -> Tuple[str, str]:
    """(level, text) of the HK fee_not_modelled caveat for the HK symbols of a run: the
    stamp-duty relief only where it can apply (an ETF code, or a code that cannot be read)."""
    found = {symbol: hk_etf_code(symbol) for symbol in symbols}
    if any(value is None for value in found.values()):
        return NOT_MODELLED["hk"]
    etfs = [symbol for symbol, value in found.items() if value]
    if etfs:
        return ("warning", f"{'、'.join(etfs)} 的代码在港股 ETF（含杠杆及反向产品）的代码段内：港股 ETF 自 "
                           f"2015-02-13 起免征印花税，这里没有建模，仍按股票收取买卖各约 0.1% 的印花税，成本被高估、"
                           f"收益被低估；" + HK_PLATFORM_FEES)
    return ("info", HK_PLATFORM_FEES)


def fees_assumption_text(schedule: Optional[FeeSchedule] = None) -> str:
    """The result's assumptions.fees text: what is charged and what is not modelled."""
    schedule = schedule or default_schedule()
    ignored = [f"{market}: {item}" for market in schedule.data.get("markets", {})
               for item in schedule.ignored(market)]
    return ("commission from the run settings (minimum applied to the commission only); stamp duty, "
            "transfer fee, HK trading fee and levies and the US SEC fee from the dated schedule "
            f"fee_schedules.json version {schedule.version}; not modelled: " + " ".join(ignored))


@dataclass
class FeeBreakdown:
    market: str
    kind: str
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    trading_fee: float = 0.0
    sfc_levy: float = 0.0
    afrc_levy: float = 0.0
    sec_fee: float = 0.0
    total: float = 0.0
    assumed: bool = False                       # some component used a row dated after the trade
    assumed_components: List[str] = field(default_factory=list)

    def parts(self) -> Dict[str, float]:
        """Non-zero components, for trade records."""
        names = ("commission", "stamp_duty", "transfer_fee", "trading_fee", "sfc_levy", "afrc_levy", "sec_fee")
        return {name: getattr(self, name) for name in names if getattr(self, name)}


def compute_fees(
    symbol: str,
    side: str,
    quantity,
    price,
    when,
    *,
    commission_rate=None,
    min_commission=None,
    market: str = "auto",
    schedule: Optional[FeeSchedule] = None,
) -> FeeBreakdown:
    """All costs of one fill. ``commission_rate`` / ``min_commission`` of None
    mean the market default from the schedule."""
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    schedule = schedule or default_schedule()
    market = market_for(symbol, market)
    kind = instrument_kind(symbol, market)
    qty, px = _dec(quantity), _dec(price)
    if not (qty.is_finite() and px.is_finite()) or qty < 0 or px < 0:
        raise ValueError("quantity and price must be finite and nonnegative")
    turnover = qty * px
    trade_date = _to_date(when)

    default_rate, default_min = schedule.commission_default(market)
    rate = default_rate if commission_rate is None else _dec(commission_rate)
    minimum = default_min if min_commission is None else _dec(min_commission)
    if not (math.isfinite(float(rate)) and math.isfinite(float(minimum))):
        raise ValueError("commission settings must be finite")
    commission = _round(rate * turnover, "cent_half_up") if turnover > 0 else Decimal(0)
    if turnover > 0:
        commission = max(commission, minimum.quantize(CENT, rounding=ROUND_HALF_UP))

    result = FeeBreakdown(market=market, kind=kind, commission=float(commission))
    total = commission
    for name, component in schedule.components(market).items():
        if side not in component["sides"] or kind not in component["applies_to"]:
            continue
        component_rate, assumed = schedule.rate(market, name, trade_date)
        amount = _round(turnover * component_rate, component.get("rounding", "cent_half_up"))
        if hasattr(result, name):
            setattr(result, name, float(amount))
        total += amount
        if assumed:
            result.assumed = True
            result.assumed_components.append(name)
    result.total = float(total)
    return result
