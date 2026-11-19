"""Value a ledger state at live prices. Pure functions, ``Decimal`` throughout.

The rules agree with ``calculate_snapshot`` in the snapshot engine (a parity
test compares them): a position is worth quantity x price x currency rate, an
account is its positions plus cash in every currency plus its fund value, and
a zero cash balance needs no currency rate.

Two rules are stricter than the engine's, because these numbers are shown
while the market moves:

* a missing price or currency rate makes the affected figures ``None``, and
  every total that contains them ``None`` as well, each with an entry in
  ``Valuation.unavailable``. A total is never the sum of only the parts that
  happened to be known, and an unknown figure is never zero. A price in
  another currency than the ledger books the holding in counts as missing:
  B shares trade in USD or HKD but the ledger books them in CNY, and their
  price is never read as yuan;
* day profit follows the shares, not just the closing balance:

      quantity now x price - quantity before x previous close
          - sum(bought quantity x price + fee) + sum(sold quantity x price - fee)

  so shares bought today earn from their purchase price and shares sold
  today stop earning at their sale price. A position sold out during the
  session keeps its day profit in ``AccountValue.closed_today``.

Day profit covers price moves on listed positions. It leaves out currency
moves on the previous value, dividends, standalone fees and fund
revaluations (trade fees are in it).

Two profit figures, not to be confused:

* ``total_pnl`` is *unrealized*: open positions against their average cost.
  It says nothing about positions already sold.
* ``cumulative_pnl_cny`` is total assets minus the capital contributed
  (``contributed_cny`` in the ledger: opening capital plus deposits, less
  withdrawals, across transfers), the same as the snapshot engine's
  ``total_profit``. ``cumulative_pnl_pct`` divides it by that capital and is
  ``None`` unless the capital is positive.

Money is rounded to the cent where it is first computed: each row's figures
(native and CNY, the CNY one from the unrounded native value) and each cash
balance's CNY value. Account totals are sums of those rounded figures and
portfolio totals are sums of account totals, so every displayed total is
exactly the sum of the displayed parts. Percentages are not rounded here.

Estimated values. A figure is computed from a quote only when the quote has
a price; it is *verified* when the quote is ``live``, ``delayed`` or a
``closed`` from a market and year whose holidays the calendar knows that has
settled. A row valued from a ``stale``, ``time_unknown`` or ``prev_close``
quote, from a close in a year the holiday table does not cover
(``weekday_only``), from a New York close that has not settled
(``close_pending``: seen before ``calendar.CLOSE_SETTLE`` after the bell), or
converted at a ``stale`` or ``time_unknown`` currency rate is still computed
but has ``estimated`` set with a reason.
Every account and the portfolio carry ``quality`` (``'complete'`` or
``'estimated'``) and ``estimated``, the non-verified inputs behind their
totals; ``unavailable`` lists what made a figure ``None``.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .instruments import InstrumentError, parse
from .quotes import CLOSE_NOT_FINAL, CLOSED, DELAYED, LIVE, UNAVAILABLE, FxRate, LiveQuote, to_decimal

ZERO = Decimal(0)
HUNDRED = Decimal(100)
CENT = Decimal('0.01')

NO_PREVIOUS_STATE = '无法重建上一交易日的持仓，当日盈亏不可用'
SPLIT_TODAY = '当日有拆股或合股，当日盈亏不可用'
OPENING_TODAY = '账户期初余额建于当日，当日盈亏从下一交易日开始计算'
ADJUSTED_TODAY = '当日账本有买卖以外的持仓变动（期初、拆股或冲销），当日盈亏不可用'
NO_PREV_CLOSE = '行情缺少昨收价，当日盈亏不可用'
UNVERIFIED_QUOTE = '行情未经核实为最新价格'
UNVERIFIED_CLOSE = '未收录该市场该年的节假日表，收盘价未经核实'
UNVERIFIED_RATE = '汇率未经核实为最新'
COMPLETE, ESTIMATED = 'complete', 'estimated'


@dataclass(frozen=True)
class PositionValue:
    account: str
    ticker: str
    name: str
    currency: str
    quantity: Decimal
    cost_price: Optional[Decimal]
    price: Optional[Decimal]
    prev_close: Optional[Decimal]
    change_pct: Optional[Decimal]
    market_value: Optional[Decimal]        # in ``currency``
    market_value_cny: Optional[Decimal]
    total_pnl: Optional[Decimal]           # in ``currency``, against average cost
    total_pnl_pct: Optional[Decimal]
    day_pnl: Optional[Decimal]             # in ``currency``
    day_pnl_cny: Optional[Decimal]
    weight_pct: Optional[Decimal]          # of the whole portfolio in CNY, cash and funds included
    quote: Optional[LiveQuote]
    estimated: bool = False                # computed from a non-verified quote or rate
    estimated_reason: Optional[str] = None
    total_pnl_cny: Optional[Decimal] = None
    cost_cny: Optional[Decimal] = None     # quantity x average cost x rate, for a holding


@dataclass(frozen=True)
class AccountValue:
    name: str
    positions: Tuple[PositionValue, ...]
    closed_today: Tuple[PositionValue, ...]   # sold out during the session; quantity is 0
    cash: Dict[str, Decimal]
    cash_cny: Optional[Decimal]
    fund_cny: Decimal
    market_value_cny: Optional[Decimal]
    total_cny: Optional[Decimal]
    day_pnl_cny: Optional[Decimal]
    total_pnl_cny: Optional[Decimal]
    quality: str = COMPLETE                   # 'estimated' when ``estimated`` lists anything
    # {'ticker' | 'currency': ..., 'status': ..., 'reason': ...} per non-verified input
    estimated: Tuple[Dict[str, str], ...] = ()
    contributed_cny: Optional[Decimal] = None


@dataclass(frozen=True)
class Valuation:
    accounts: Tuple[AccountValue, ...]
    by_currency: Dict[str, Dict[str, Optional[Decimal]]]   # {currency: {'market_value', 'cash'}}
    total_cny: Optional[Decimal]
    day_pnl_cny: Optional[Decimal]
    total_pnl_cny: Optional[Decimal]                       # open positions against average cost
    # {'ticker' | 'currency': ..., 'reason': ...}; an entry with
    # 'field': 'day_pnl' affects day profit only.
    unavailable: Tuple[Dict[str, str], ...]
    quality: str = COMPLETE
    estimated: Tuple[Dict[str, str], ...] = ()
    contributed_cny: Optional[Decimal] = None
    cumulative_pnl_cny: Optional[Decimal] = None   # total_cny - contributed_cny
    cumulative_pnl_pct: Optional[Decimal] = None   # of contributed_cny, when positive
    # day_pnl_cny / (total_cny - day_pnl_cny): of the value at the reference prices
    day_pnl_pct: Optional[Decimal] = None
    # total_pnl_cny / the open positions' cost in CNY at current rates
    total_pnl_pct: Optional[Decimal] = None


def cents(value: Optional[Decimal]) -> Optional[Decimal]:
    return quantized(value, CENT)


def _times(value: Optional[Decimal], rate: Optional[Decimal]) -> Optional[Decimal]:
    return value * rate if value is not None and rate is not None else None


def _percent(part: Optional[Decimal], whole: Optional[Decimal]) -> Optional[Decimal]:
    """``part / whole * 100``; ``None`` unless both are known and ``whole`` is positive."""
    if part is None or whole is None or whole <= 0:
        return None
    return part / whole * HUNDRED


def _total(parts: Sequence[Optional[Decimal]]) -> Optional[Decimal]:
    """Sum, or ``None`` as soon as one part is unknown."""
    total = ZERO
    for part in parts:
        if part is None:
            return None
        total += part
    return total


def _ledger_decimal(value) -> Optional[Decimal]:
    """A ledger amount, or ``None`` when absent.

    Ledger amounts are validated decimal strings of up to 128 characters;
    ``to_decimal`` is for provider fields and stops at 64.
    """
    if isinstance(value, str) and len(value) <= 256:
        try:
            number = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
        return number if number.is_finite() else None
    return to_decimal(value)


def _number(value) -> Decimal:
    number = _ledger_decimal(value)
    if number is None:
        raise ValueError('the ledger state holds a value that is not a number')
    return number


def quantized(value: Optional[Decimal], exponent: Decimal) -> Optional[Decimal]:
    """``value`` rounded half up to ``exponent`` (e.g. ``Decimal('0.01')``).

    The context is widened to the value's size, so this never raises however
    large the figure is; the default 28 digits do not hold 1e18 shares at a
    price in the millions.
    """
    if value is None or not value.is_finite():
        return None
    digits = max(value.adjusted(), 0) - exponent.as_tuple().exponent + 2
    context = Context(prec=max(28, digits))
    return value.quantize(exponent, rounding=ROUND_HALF_UP, context=context)


class _Problems:
    """Reasons, in first-seen order and without repeats."""

    def __init__(self):
        self._seen: Dict[tuple, Dict[str, str]] = {}

    def add(self, reason: str, **subject: str) -> None:
        entry = {**subject, 'reason': reason}
        self._seen.setdefault(tuple(sorted(entry.items())), entry)

    def entries(self) -> Tuple[Dict[str, str], ...]:
        return tuple(self._seen.values())


def quote_estimate(quote: Optional[LiveQuote]) -> Optional[Tuple[str, str]]:
    """``(status, reason)`` when a priced quote is not verified, else ``None``."""
    if quote is None or quote.price is None or quote.status in (LIVE, DELAYED, UNAVAILABLE):
        return None
    if quote.status == CLOSED:
        if quote.close_pending:        # a New York close that has not settled (``quotes.CLOSE_NOT_FINAL``)
            return CLOSED, quote.reason or CLOSE_NOT_FINAL
        if quote.calendar_confidence == 'weekday_only':
            return CLOSED, quote.reason or UNVERIFIED_CLOSE
        return None
    return quote.status, quote.reason or UNVERIFIED_QUOTE


def rate_estimate(entry) -> Optional[Tuple[str, str]]:
    """``(status, reason)`` when a usable rate is not verified, else ``None``.

    A plain number handed in by a caller is taken as verified.
    """
    if isinstance(entry, FxRate) and entry.rate is not None and entry.status not in (LIVE, DELAYED, CLOSED, UNAVAILABLE):
        return entry.status, entry.reason or UNVERIFIED_RATE
    return None


class _Estimates:
    """Non-verified inputs, in first-seen order and without repeats."""

    def __init__(self):
        self._seen: Dict[tuple, Dict[str, str]] = {}

    def add(self, subject: str, key: str, status: str, reason: str) -> None:
        entry = {subject: key, 'status': status, 'reason': reason}
        self._seen.setdefault(tuple(entry.items()), entry)

    def extend(self, entries) -> None:
        for entry in entries:
            self._seen.setdefault(tuple(entry.items()), entry)

    def entries(self) -> Tuple[Dict[str, str], ...]:
        return tuple(self._seen.values())

    def quality(self) -> str:
        return ESTIMATED if self._seen else COMPLETE


def _rate(fx: Mapping[str, object], currency: str) -> Tuple[Optional[Decimal], Optional[str]]:
    """CNY per unit of ``currency``, or ``None`` and why."""
    if currency == 'CNY':
        return Decimal(1), None
    entry = fx.get(currency) if fx else None
    if isinstance(entry, FxRate):
        if entry.rate is not None and entry.rate > 0 and entry.status != UNAVAILABLE:
            return entry.rate, None
        return None, entry.reason or '汇率不可用'
    rate = to_decimal(entry)
    if rate is not None and rate > 0:
        return rate, None
    return None, '汇率不可用'


def booking_problem(ticker: str, currency: Optional[str], quote: Optional[LiveQuote]) -> Optional[str]:
    """Why ``quote`` is no price for a holding the ledger books in ``currency``, or ``None``.

    The instrument trades in another currency (a B share), or the quote says
    it is in another currency: its price must not be read in the booking
    currency, and nothing converts it, because the ledger's cost and trades
    for the holding are in the booking currency too.
    """
    if not currency:
        return None
    try:
        problem = parse(ticker).ledger_currency_problem
    except InstrumentError:
        problem = None
    if problem:
        return problem
    if quote is not None and quote.currency and quote.currency != currency:
        return f'行情以 {quote.currency} 计价，账本按 {currency} 记录该持仓，币种不一致，不能估值'
    return None


def _price(quote: Optional[LiveQuote]) -> Tuple[Optional[Decimal], Optional[str]]:
    if quote is None:
        return None, '没有行情'
    if quote.status == UNAVAILABLE or quote.price is None or quote.price <= 0:
        return None, quote.reason or '没有有效价格'
    return quote.price, None


def _trades(events: Sequence[dict]) -> Tuple[Dict[tuple, dict], Dict[tuple, str]]:
    """Per (account, ticker): today's bought and sold totals, and blockers.

    Reversed events and the reversals themselves are ignored.
    """
    reversed_ids = {event.get('target_id') for event in events if event.get('kind') == 'reverse'}
    flows: Dict[tuple, dict] = {}
    blocked: Dict[tuple, str] = {}
    for event in events:
        kind = event.get('kind')
        if kind == 'reverse' or event.get('id') in reversed_ids:
            continue
        account = event.get('account')
        if kind == 'opening':
            for position in event.get('positions') or []:
                blocked.setdefault((account, position.get('ticker')), OPENING_TODAY)
            continue
        key = (account, event.get('ticker'))
        if kind == 'split':
            blocked[key] = SPLIT_TODAY
        elif kind in ('buy', 'sell'):
            quantity, price = _number(event.get('quantity')), _number(event.get('price'))
            fee = _number(event.get('fee', 0))
            flow = flows.setdefault(key, {'bought': ZERO, 'paid': ZERO, 'sold': ZERO, 'received': ZERO})
            if kind == 'buy':
                flow['bought'] += quantity
                flow['paid'] += quantity * price + fee
            else:
                flow['sold'] += quantity
                flow['received'] += quantity * price - fee
    return flows, blocked


def _day_pnl(quantity: Decimal, before: Decimal, price: Optional[Decimal], prev_close: Optional[Decimal],
             flow: Optional[dict]) -> Tuple[Optional[Decimal], Optional[str]]:
    """Day profit in the position's currency, or ``None`` and why.

    ``None`` with no reason means the price itself is missing, which the
    caller has already reported.
    """
    if quantity > 0 and price is None:
        return None, None
    bought = flow['bought'] if flow else ZERO
    sold = flow['sold'] if flow else ZERO
    if before + bought - sold != quantity:
        return None, ADJUSTED_TODAY
    if before > 0 and prev_close is None:
        return None, NO_PREV_CLOSE
    value_now = quantity * price if quantity > 0 else ZERO
    value_before = before * prev_close if before > 0 else ZERO
    return value_now - value_before - (flow['paid'] if flow else ZERO) + (flow['received'] if flow else ZERO), None


def value_portfolio(state: Mapping, quotes: Mapping[str, LiveQuote], fx: Mapping[str, object],
                    prev_state: Optional[Mapping], todays_events: Sequence[dict], *,
                    day_reasons: Optional[Mapping[str, str]] = None) -> Valuation:
    """Value ``state`` (as returned by ``Ledger.state()``, decimal strings).

    ``quotes``         ticker -> ``LiveQuote``; a missing or unavailable one makes
                       that position's figures ``None``
    ``fx``             currency -> ``FxRate`` (or a plain rate), CNY per unit
    ``prev_state``     what was held before the session being valued began, in
                       the same shape; ``None`` when it could not be rebuilt
    ``todays_events``  ledger events of that session
    ``day_reasons``    ticker -> why day profit cannot be computed for it;
                       the key ``'*'`` explains a missing ``prev_state``
    """
    day_reasons = dict(day_reasons or {})
    problems = _Problems()
    flows, blocked = _trades(todays_events or [])
    if prev_state is None:
        problems.add(day_reasons.get('*') or NO_PREVIOUS_STATE, field='day_pnl')
    previous_accounts = (prev_state or {}).get('accounts', {})

    def before(account: str, ticker: str) -> Decimal:
        held = previous_accounts.get(account, {}).get('positions', {}).get(ticker)
        return _number(held['quantity']) if held else ZERO

    def row(account: str, ticker: str, position: Mapping, quantity: Decimal, notes: _Estimates) -> PositionValue:
        currency = position.get('currency')
        quote = quotes.get(ticker) if quotes else None
        mismatch = booking_problem(ticker, currency, quote)
        if mismatch:
            price, price_reason, prev_close = None, mismatch, None
        else:
            price, price_reason = _price(quote)
            prev_close = quote.prev_close if quote is not None else None
        rate, rate_reason = _rate(fx, currency)
        held = quantity > 0
        if held and price is None:
            problems.add(price_reason, ticker=ticker)
        if rate is None and currency:
            problems.add(rate_reason, currency=currency)

        cost_price = _ledger_decimal(position.get('cost_price'))
        exact_value = quantity * price if held and price is not None else (ZERO if not held else None)
        market_value = cents(exact_value)
        market_value_cny = cents(_times(exact_value, rate))
        total_pnl = total_pnl_pct = total_pnl_cny = None
        cost_cny = cents(_times(quantity * cost_price, rate)) if held and cost_price is not None else None
        if held and exact_value is not None and cost_price is not None:
            cost_value = quantity * cost_price
            exact_pnl = exact_value - cost_value
            total_pnl, total_pnl_cny = cents(exact_pnl), cents(_times(exact_pnl, rate))
            # A zero cost basis has no percentage; that is not the same as 0%.
            total_pnl_pct = exact_pnl / cost_value * HUNDRED if cost_value > 0 else None
        change_pct = ((price - prev_close) / prev_close * HUNDRED
                      if price is not None and prev_close is not None else None)

        exact_day = None
        if prev_state is not None:
            # A holding without a price is reported once, above; a position
            # sold out today needs the reason for its day profit.
            reason = day_reasons.get(ticker) or blocked.get((account, ticker)) or (None if held else mismatch)
            if reason is None:
                exact_day, reason = _day_pnl(quantity, before(account, ticker), price, prev_close,
                                             flows.get((account, ticker)))
            if reason is not None:
                exact_day = None
                problems.add(reason, ticker=ticker, field='day_pnl')
        day_pnl, day_pnl_cny = cents(exact_day), cents(_times(exact_day, rate))

        # Which inputs the computed figures rest on: the price for a holding,
        # the previous close for day profit on shares held before, the rate
        # for every CNY figure.
        reasons = []
        uses_quote = (held and price is not None) or (day_pnl is not None and before(account, ticker) > 0)
        flagged = quote_estimate(quote) if uses_quote else None
        if flagged:
            notes.add('ticker', ticker, *flagged)
            reasons.append(flagged[1])
        uses_rate = currency != 'CNY' and ((held and market_value_cny is not None) or day_pnl_cny is not None)
        if uses_rate:
            flagged_rate = rate_estimate(fx.get(currency) if fx else None)
            if flagged_rate:
                notes.add('currency', currency, *flagged_rate)
                reasons.append(f'汇率 {currency}：{flagged_rate[1]}')

        return PositionValue(
            account=account, ticker=ticker, name=_display_name(position, quote, ticker), currency=currency, quantity=quantity, cost_price=cost_price, price=price, prev_close=prev_close,
            change_pct=change_pct, market_value=market_value, market_value_cny=market_value_cny,
            total_pnl=total_pnl, total_pnl_pct=total_pnl_pct, day_pnl=day_pnl, day_pnl_cny=day_pnl_cny,
            weight_pct=None, quote=quote, estimated=bool(reasons), estimated_reason='；'.join(reasons) or None,
            total_pnl_cny=total_pnl_cny, cost_cny=cost_cny)

    accounts: List[AccountValue] = []
    portfolio_notes = _Estimates()
    for name, account in state.get('accounts', {}).items():
        notes = _Estimates()
        positions = [row(name, ticker, position, _number(position['quantity']), notes)
                     for ticker, position in account.get('positions', {}).items()]
        # Sold out during the session: no longer a holding, but its day profit
        # belongs to today's total.
        gone = [ticker for ticker in previous_accounts.get(name, {}).get('positions', {})
                if ticker not in account.get('positions', {})]
        gone += [ticker for (owner, ticker) in flows
                 if owner == name and ticker not in account.get('positions', {}) and ticker not in gone]
        closed = []
        for ticker in gone:
            template = previous_accounts.get(name, {}).get('positions', {}).get(ticker)
            if template is None:
                template = {'name': ticker, 'currency': _event_currency(todays_events, name, ticker),
                            'cost_price': None}
            closed.append(row(name, ticker, template, ZERO, notes))

        cash = {currency: _number(balance) for currency, balance in account.get('cash_balances', {}).items()}
        cash_parts: List[Optional[Decimal]] = []
        for currency, balance in cash.items():
            if balance == 0:
                cash_parts.append(ZERO)
                continue
            rate, reason = _rate(fx, currency)
            if rate is None:
                problems.add(reason, currency=currency)
            else:
                flagged_rate = rate_estimate(fx.get(currency) if fx else None)
                if flagged_rate:
                    notes.add('currency', currency, *flagged_rate)
            cash_parts.append(cents(_times(balance, rate)))
        cash_cny = _total(cash_parts)
        fund_cny = cents(_number(account.get('fund_cny', 0)))
        market_value_cny = _total([position.market_value_cny for position in positions])
        accounts.append(AccountValue(
            name=name, positions=tuple(positions), closed_today=tuple(closed), cash=cash, cash_cny=cash_cny,
            fund_cny=fund_cny, market_value_cny=market_value_cny,
            total_cny=_total([market_value_cny, cash_cny, fund_cny]),
            day_pnl_cny=(None if prev_state is None
                         else _total([position.day_pnl_cny for position in positions + closed])),
            total_pnl_cny=_total([position.total_pnl_cny for position in positions]),
            quality=notes.quality(), estimated=notes.entries(),
            contributed_cny=cents(_number(account.get('contributed_cny', 0)))))
        portfolio_notes.extend(notes.entries())

    total_cny = _total([account.total_cny for account in accounts])
    if total_cny is not None and total_cny > 0:
        accounts = [_with_weights(account, total_cny) for account in accounts]

    by_currency: Dict[str, Dict[str, Optional[Decimal]]] = {}
    for account in accounts:
        for currency, balance in account.cash.items():
            entry = by_currency.setdefault(currency, {'market_value': ZERO, 'cash': ZERO})
            entry['cash'] += cents(balance)
        for position in account.positions:
            entry = by_currency.setdefault(position.currency, {'market_value': ZERO, 'cash': ZERO})
            entry['market_value'] = _total([entry['market_value'], position.market_value])

    contributed_cny = _total([account.contributed_cny for account in accounts])
    cumulative_pnl_cny = _total([total_cny, -contributed_cny]) if contributed_cny is not None else None
    day_pnl_cny = _total([account.day_pnl_cny for account in accounts])
    total_pnl_cny = _total([account.total_pnl_cny for account in accounts])
    cost_cny = _total([position.cost_cny for account in accounts for position in account.positions])
    return Valuation(
        contributed_cny=contributed_cny, cumulative_pnl_cny=cumulative_pnl_cny,
        cumulative_pnl_pct=_percent(cumulative_pnl_cny, contributed_cny),
        day_pnl_pct=_percent(day_pnl_cny, _total([total_cny, -day_pnl_cny]) if day_pnl_cny is not None else None),
        total_pnl_pct=_percent(total_pnl_cny, cost_cny),
        accounts=tuple(accounts), by_currency=by_currency, total_cny=total_cny,
        day_pnl_cny=day_pnl_cny, total_pnl_cny=total_pnl_cny,
        unavailable=problems.entries(), quality=portfolio_notes.quality(), estimated=portfolio_notes.entries())


def _display_name(position: Mapping, quote: Optional[LiveQuote], ticker: str) -> str:
    # The ledger stores the ticker as the name when none was entered.
    name = position.get('name')
    if name and name != ticker:
        return name
    return (quote.name if quote is not None else None) or ticker


def _event_currency(events: Sequence[dict], account: str, ticker: str) -> Optional[str]:
    for event in events or []:
        if event.get('account') == account and event.get('ticker') == ticker and event.get('currency'):
            return event['currency']
    return None


def _with_weights(account: AccountValue, total_cny: Decimal) -> AccountValue:
    positions = tuple(
        replace(position, weight_pct=position.market_value_cny / total_cny * HUNDRED)
        if position.market_value_cny is not None else position
        for position in account.positions)
    return replace(account, positions=positions)
