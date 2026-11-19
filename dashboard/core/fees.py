"""Trade fee estimator. Pure functions, ``Decimal`` only, no I/O.

An estimate is a *prefill* for the entry form, never a booking: the user can
always overwrite it with the number on the broker's statement.

IMPORTANT: the statutory rates and effective dates below were written from
public rules and have NOT been checked against the original notices by a
second person or against real broker statements. Check them before relying on
them. Each table entry names the notice it comes from.

Rules implemented
-----------------
* A-share stocks (SHA, SHE): commission = max(rate x turnover, minimum) with the
  account's rate and minimum ("免五" sets the minimum to 0); transfer fee
  (过户费) on both sides; stamp duty (印花税) on sells only. Statutory rates
  are looked up by trade date.
* Exchange-traded funds (ETF) and listed open funds (LOF): commission only.
  No stamp duty and no transfer fee are added.
* HK and US: only when the account profile defines a complete model (``rate``,
  ``min`` and ``fixed`` all present) for the market and the side of the trade:
  ``max(rate x turnover, min) + fixed`` in the trade currency. A missing or
  incomplete side gives no estimate; a missing value is never read as zero.
* Anything else (convertible bonds, B shares, REITs, repos, unknown codes,
  dates before the rate tables start): no estimate, with a reason. Never a guess.

Every component is rounded half-up to 0.01 on its own; the total is the sum of
the rounded parts.

Arithmetic never uses the thread's ambient ``decimal`` context, which any
library can change. Products and sums run in ``EXACT`` (60 digits, and the
``Inexact``/``Rounded`` conditions raise, so a result that would need
rounding can never be returned silently); the only rounding is the final
half-up quantize to the cent, in ``ROUND``.

Instrument classification (6-digit codes)
-----------------------------------------
``CLASSIFICATION`` is the whole rule table: ``(exchange, code prefix, kind,
what it is)``. The longest matching prefix wins, so an exception such as
SHA 508 (REIT) overrides the general SHA 5 (fund) row. A code that matches no
row, or a row whose kind is ``None``, gets no estimate with a reason.

SHA (Shanghai)
    stock : 600 601 603 605 (main board), 688 689 (STAR)
    fund  : 5 (ETFs such as 510-518, 520, 560-563, 588/589; LOFs 501/502/506)
    none  : 500 505 (closed-end funds), 508 (REIT), 110 111 113 118
            (convertible bonds), 900 (B shares), 204 (repo)
SHE (Shenzhen)
    stock : 000 001 002 003 (main board / former SME), 300 301 (ChiNext)
    fund  : 15 (ETFs 159 and 158, former graded funds 150-153), 16 (LOFs)
    none  : 180 (REIT), 184 (closed-end funds), 123 127 128 (convertible
            bonds), 200 (B shares), 399 (index)

REITs and closed-end funds are listed funds too, but their charges are not
those of an ETF and have not been checked, so they keep no estimate.

Sources of the dated rates
--------------------------
Stamp duty (sell side only):
    * 0.1% from 2008-09-19 (Ministry of Finance / State Taxation Administration
      notice of 2008-09-18: stamp duty on securities transfers charged on the
      seller only).
    * 0.05% from 2023-08-28 (MOF / STA Announcement 2023 No. 39: halved from
      2023-08-28).
Transfer fee (both sides, as a share of turnover):
    * 0.002% from 2015-08-01 (China Securities Depository and Clearing, rates
      for Shanghai and Shenzhen unified at 0.002% of turnover).
    * 0.001% from 2022-04-29 (China Securities Depository and Clearing notice
      lowering the rate to 0.001%, effective 2022-04-29).
Before 2008-09-19 (stamp duty) or 2015-08-01 (transfer fee) the rules were
different and are not modelled: no estimate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import (Context, Decimal, DecimalException, DivisionByZero, Inexact, InvalidOperation, Overflow,
                     ROUND_HALF_UP, Rounded)
from typing import Any, Dict, List, Optional, Tuple

CENT = Decimal("0.01")
EXACT = Context(prec=60, rounding=ROUND_HALF_UP, Emin=-99, Emax=99,
                traps=[InvalidOperation, DivisionByZero, Overflow, Inexact, Rounded])
ROUND = Context(prec=60, rounding=ROUND_HALF_UP, Emin=-99, Emax=99,
                traps=[InvalidOperation, DivisionByZero, Overflow])
MAX_TEXT = 40   # characters of a number written as text

# Bounds of every number the estimator accepts. With them, turnover has at
# most 21 + 16 digits and a fee at most 21 + 26, well inside EXACT.
MAX_QUANTITY = Decimal(10) ** 12
MAX_PRICE = Decimal(10) ** 9
QUANTITY_SCALE = PRICE_SCALE = 8      # decimal places
MAX_RATE = Decimal("0.01")            # a fee rate: at most 1% of turnover
RATE_SCALE = 10
MAX_AMOUNT = Decimal("100000")        # a minimum or fixed fee per trade
AMOUNT_SCALE = 4

# (effective from, rate as a fraction of turnover), ascending.
STAMP_DUTY_SELL = [
    (date(2008, 9, 19), Decimal("0.001")),
    (date(2023, 8, 28), Decimal("0.0005")),
]
TRANSFER_FEE = [
    (date(2015, 8, 1), Decimal("0.00002")),
    (date(2022, 4, 29), Decimal("0.00001")),
]

STOCK, FUND = "stock", "fund"

# (exchange, code prefix, kind or None, what it is). Longest matching prefix wins.
CLASSIFICATION: List[Tuple[str, str, Optional[str], str]] = [
    ("SHA", "600", STOCK, "主板股票"),
    ("SHA", "601", STOCK, "主板股票"),
    ("SHA", "603", STOCK, "主板股票"),
    ("SHA", "605", STOCK, "主板股票"),
    ("SHA", "688", STOCK, "科创板股票"),
    ("SHA", "689", STOCK, "科创板存托凭证"),
    ("SHA", "5", FUND, "基金（ETF、LOF）"),
    ("SHA", "500", None, "封闭式基金"),
    ("SHA", "505", None, "封闭式基金"),
    ("SHA", "508", None, "REIT"),
    ("SHA", "110", None, "可转债"),
    ("SHA", "111", None, "可转债"),
    ("SHA", "113", None, "可转债"),
    ("SHA", "118", None, "可转债"),
    ("SHA", "900", None, "B股"),
    ("SHA", "204", None, "国债回购"),
    ("SHE", "000", STOCK, "主板股票"),
    ("SHE", "001", STOCK, "主板股票"),
    ("SHE", "002", STOCK, "主板股票"),
    ("SHE", "003", STOCK, "主板股票"),
    ("SHE", "300", STOCK, "创业板股票"),
    ("SHE", "301", STOCK, "创业板股票"),
    ("SHE", "15", FUND, "基金（ETF）"),
    ("SHE", "16", FUND, "基金（LOF）"),
    ("SHE", "180", None, "REIT"),
    ("SHE", "184", None, "封闭式基金"),
    ("SHE", "123", None, "可转债"),
    ("SHE", "127", None, "可转债"),
    ("SHE", "128", None, "可转债"),
    ("SHE", "200", None, "B股"),
    ("SHE", "399", None, "指数"),
]

MARKET_CURRENCY = {"SHA": "CNY", "SHE": "CNY", "HKG": "HKD", "NASDAQ": "USD", "NYSE": "USD"}


class FeeInputError(ValueError):
    """A number or argument this module refuses; the message is Chinese and the module's own."""


@dataclass
class FeeEstimate:
    total: Optional[Decimal]
    parts: List[Dict[str, Any]] = field(default_factory=list)   # [{"label", "amount": Decimal}]
    basis: str = ""
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "total": None if self.total is None else format(self.total, "f"),
            "parts": [{"label": p["label"], "amount": format(p["amount"], "f")} for p in self.parts],
            "basis": self.basis,
            "reason": self.reason,
        }


def money(value: Decimal) -> Decimal:
    """Round half-up to the cent."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP, context=ROUND)


def _total(parts) -> Decimal:
    total = Decimal(0)
    for part in parts:
        total = EXACT.add(total, part["amount"])
    return total


def bounded_decimal(value, label: str, maximum: Decimal, scale: int, allow_zero: bool = True) -> Decimal:
    """Read a number given by a user exactly, or raise ``ValueError`` (Chinese message).

    Accepts a string (at most ``MAX_TEXT`` characters, exponent notation
    allowed), an int or a ``Decimal``. The number must be finite, between 0
    (excluded unless ``allow_zero``) and ``maximum``, and have at most
    ``scale`` decimal places once trailing zeros are dropped. The result is
    that number, normalised without rounding (``format(result, "f")`` is a
    plain string such as ``"150"`` for ``"1.5e2"``). Nothing here depends on
    the ambient decimal context.
    """
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, bool) or not isinstance(value, (str, int)):
        raise FeeInputError(f"{label}必须是数字。")
    elif isinstance(value, int):
        number = Decimal(value)
    else:
        text = value.strip()
        if len(text) > MAX_TEXT:
            raise FeeInputError(f"{label}过长，最多 {MAX_TEXT} 个字符。")
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise FeeInputError(f"{label}不是有效数字。") from None
    if not number.is_finite():
        raise FeeInputError(f"{label}不是有效数字。")
    if number < 0 or number > maximum or (number == 0 and not allow_zero):
        lower = "0" if allow_zero else "大于 0"
        raise FeeInputError(f"{label}必须在 {lower} 到 {format(maximum, 'f')} 之间。")
    if number.is_zero():
        return Decimal(0)
    try:
        number = number.normalize(EXACT)
    except DecimalException:
        raise FeeInputError(f"{label}的有效数字过多。") from None
    if -number.as_tuple().exponent > scale:
        raise FeeInputError(f"{label}最多保留 {scale} 位小数。")
    return number


def split_ticker(ticker: str) -> Tuple[str, str]:
    exchange, _, code = str(ticker).strip().upper().partition(":")
    return exchange, code


def classify(ticker: str) -> Tuple[Optional[str], str]:
    """Return ``("stock" | "fund" | None, note)`` for an SHA/SHE ticker (see ``CLASSIFICATION``)."""
    exchange, code = split_ticker(ticker)
    if exchange not in ("SHA", "SHE") or not (len(code) == 6 and code.isdigit()):
        return None, "无法识别该代码的品种"
    matches = [row for row in CLASSIFICATION if row[0] == exchange and code.startswith(row[1])]
    if not matches:
        return None, "无法识别该代码的品种，暂无费率模型，请按券商成交单填写"
    _exchange, _prefix, kind, what = max(matches, key=lambda row: len(row[1]))
    if kind is None:
        return None, f"该品种（{what}）暂无费率模型，请按券商成交单填写"
    return kind, ""


def _rate_on(table, when: date) -> Optional[Decimal]:
    current = None
    for start, rate in table:
        if when >= start:
            current = rate
    return current


def quantity_value(value) -> Decimal:
    """A trade quantity: above 0, at most ``MAX_QUANTITY``, ``QUANTITY_SCALE`` decimal places."""
    return bounded_decimal(value, "数量", MAX_QUANTITY, QUANTITY_SCALE, allow_zero=False)


def price_value(value) -> Decimal:
    """A trade price: above 0, at most ``MAX_PRICE``, ``PRICE_SCALE`` decimal places."""
    return bounded_decimal(value, "价格", MAX_PRICE, PRICE_SCALE, allow_zero=False)


def _rate(value, name) -> Decimal:
    return bounded_decimal(value, name, MAX_RATE, RATE_SCALE)


def _amount(value, name) -> Decimal:
    return bounded_decimal(value, name, MAX_AMOUNT, AMOUNT_SCALE)


def _none(reason: str, basis: str = "") -> FeeEstimate:
    return FeeEstimate(total=None, parts=[], basis=basis, reason=reason)


def _commission(rate: Decimal, minimum: Decimal, turnover: Decimal) -> Decimal:
    return money(max(EXACT.multiply(rate, turnover), minimum))


def _profile_basis(profile: dict) -> str:
    return "按默认费率估算" if profile.get("is_default") else "按账户费率估算"


def estimate(profile: dict, ticker: str, side: str, quantity, price, trade_date) -> FeeEstimate:
    """Estimate the fee of one trade.

    ``profile`` is what ``account_profiles.get`` returns. ``trade_date`` is a
    ``date`` or an ISO string. ``quantity`` and ``price`` are strings, ints or
    ``Decimal`` within the bounds above (``quantity_value``, ``price_value``),
    and so are the profile's rates and amounts. Raises ``ValueError`` (Chinese
    message) for malformed or out-of-bounds arguments; returns ``total=None``
    with a reason when the input is fine but no honest estimate exists.
    """
    if side not in ("buy", "sell"):
        raise FeeInputError("方向必须是 buy 或 sell")
    qty, px = quantity_value(quantity), price_value(price)
    if not isinstance(trade_date, date):
        try:
            trade_date = date.fromisoformat(str(trade_date))
        except ValueError:
            raise FeeInputError("日期格式应为 YYYY-MM-DD") from None
    exchange, _code = split_ticker(ticker)
    turnover = EXACT.multiply(qty, px)
    basis = _profile_basis(profile)

    if exchange in ("SHA", "SHE"):
        kind, note = classify(ticker)
        if kind is None:
            return _none(note)
        a = profile.get("a_share") or {}
        rate = _rate(a.get("commission_rate"), "佣金率")
        minimum = Decimal(0) if a.get("waive_minimum") else _amount(a.get("commission_min"), "最低佣金")
        parts = [{"label": "佣金", "amount": _commission(rate, minimum, turnover)}]
        if kind == STOCK:
            transfer = _rate_on(TRANSFER_FEE, trade_date)
            if transfer is None:
                return _none("交易日早于 2015-08-01，过户费规则不同，无法估算")
            parts.append({"label": "过户费", "amount": money(EXACT.multiply(turnover, transfer))})
            if side == "sell":
                duty = _rate_on(STAMP_DUTY_SELL, trade_date)
                if duty is None:
                    return _none("交易日早于 2008-09-19，印花税规则不同，无法估算")
                parts.append({"label": "印花税", "amount": money(EXACT.multiply(turnover, duty))})
        return FeeEstimate(_total(parts), parts, basis)

    if exchange in ("HKG", "NASDAQ", "NYSE"):
        market = profile.get("hk" if exchange == "HKG" else "us")
        if not isinstance(market, dict) or not market:
            return _none("未设置该市场费率")
        model = market.get(side)
        if not isinstance(model, dict) or any(model.get(k) is None for k in ("rate", "min", "fixed")):
            return _none(f"未设置该市场{'买入' if side == 'buy' else '卖出'}方向的费率")
        rate, minimum, fixed = _rate(model["rate"], "费率"), _amount(model["min"], "最低收费"), \
            _amount(model["fixed"], "固定费用")
        parts = []
        variable = _commission(rate, minimum, turnover)
        parts.append({"label": "佣金", "amount": variable})
        if fixed:
            parts.append({"label": "固定费用", "amount": money(fixed)})
        return FeeEstimate(_total(parts), parts, basis)

    return _none("无法识别交易所")
