"""The Today view: confirmed ledger holdings valued at live prices.

``build_today`` is the single place that turns a ledger and the market
runtime into the ``GET /api/today`` payload, so the page and anything else
that needs "the portfolio right now" show the same numbers. ``build_quotes``
serves ``GET /api/quotes`` with the same quote objects.

Response contract
=================

Money and quantities are decimal strings (money and percentages with two
places; a row's ``quantity`` and ``cost_price`` with every digit the ledger
holds, such as an average cost of 10.00333333333333333333333333; prices with
at most six); ``null`` means unknown, never zero; times are ISO-8601 in
Asia/Shanghai with an offset. Nothing here raises for bad provider data.

Estimated values (shown by the page as an amber qualifier next to a number).
A quote is *verified* when its status is ``live``, ``delayed``, or
``closed`` in a market and year whose holidays the calendar knows
(``quote.reason`` is then null; ``holidays.json`` lists the mainland,
Hong Kong and New York years it covers). A row valued from any other priced
quote (``stale``, ``time_unknown``, ``prev_close``, or ``closed`` in a year
the table does not cover, ``calendar_confidence: 'weekday_only'``), or
converted at a ``stale`` or ``time_unknown`` currency rate, keeps its numbers
but has ``estimated: true`` and ``estimated_reason``. A totals block (each account, and ``totals``) has
``quality``: ``"estimated"`` when ``estimated[]`` lists any non-verified
input behind it, ``"complete"`` otherwise, ``null`` when there is nothing to
value. An *unavailable* input (no usable price or rate, or a price in another
currency than the ledger books the holding in: B shares trade in USD or HKD
but are booked in CNY) is never estimated: the figures that need it are
``null`` and ``unavailable[]`` says why.

Quote statuses (``quotes.py`` decides them per market phase in its table):

    status        values a row   totals
    live          yes            complete
    delayed       yes            complete
    closed        yes            complete; estimated when weekday-only
                                 (a year without a holiday table) or a New
                                 York close seen before it settles (16:30,
                                 ``calendar.CLOSE_SETTLE``)
    stale         yes            estimated
    prev_close    yes            estimated (price is the previous close)
    time_unknown  yes            estimated; day profit null
    unavailable   no             null, listed in unavailable[]

FX statuses: ``live``, ``delayed`` and ``closed`` (weekend) are verified;
``stale`` and ``time_unknown`` mark totals estimated; ``unavailable`` makes
every CNY figure in that currency null (a zero balance needs no rate).

``GET /api/today``::

    as_of            now
    source           quote sources, '+'-joined
    holdings_source  'confirmed_ledger'
    ledger_revision  int; 0 without a ledger; null when no consistent read
    session          {market: 'CN', phase, trading_day, is_trading_day,
                      calendar_confidence, next_open, markets: {CN, HK, US}}
    fx               {USD|HKD: {rate, source, quote_time, status, reason}}
    accounts[]       see below; [] together with ``notice``
    totals           see below
    notices[]        [{kind, message, ...}], possibly empty:
                       future_events   count: records dated after today (not applied)
                       corrected_view  accounts: a later-dated reversal applied early
                       exchange_local_dates  US trades in the last two sessions
    notice           present only when accounts is [] (no ledger, nothing
                     confirmed yet, everything dated in the future, or the
                     ledger could not be read consistently)

account: ``name``, ``positions[]``, ``closed_today[]`` (rows sold out in
their session, quantity 0), ``cash`` ({currency: amount}), ``cash_cny``,
``fund_cny``, ``market_value_cny``, ``total_cny``, ``day_pnl_cny``,
``total_pnl_cny``, ``quality``, ``estimated[]``.

row: ``ticker`` (as the ledger stores it), ``name``, ``currency``,
``quantity``, ``cost_price``, ``price``, ``prev_close``, ``change_pct``,
``market_value`` (native), ``market_value_cny``, ``day_pnl`` (native),
``day_pnl_cny``, ``total_pnl`` (native, unrealized), ``total_pnl_cny``,
``total_pnl_pct`` (of quantity x cost price), ``weight_pct`` (of the whole
portfolio's ``total_cny``, cash and funds included), ``weight_pct_exact``
(the same weight cut, never rounded up, at 20 places: what the alert monitor
compares with a position cap, which ``weight_pct`` rounded to 0.01 cannot
tell; the new-alert dialog reads it), ``quote{source,
quote_time, fetched_at, status, reason, session_date}``, ``estimated``,
``estimated_reason``, ``identity`` (the listing, ``core.market.search.identity_key``:
``HKG:700`` and ``HKG:00700``, ``NYSE:BRK-B`` and ``NYSE:BRK.B`` are one; the
pages match alert rules, plans and the watchlist by it).

totals: ``by_currency`` ({currency: {market_value, cash}}, native),
``total_cny``, ``day_pnl_cny``, ``day_pnl_pct``, ``total_pnl_cny``,
``total_pnl_pct``, ``total_pnl_kind`` ('unrealized'), ``contributed_cny``,
``cumulative_pnl_cny``, ``cumulative_pnl_pct``, ``day_pnl_excludes``
(['dividend', 'fee']), ``unavailable[]`` ({ticker|currency, reason,
field?}; ``field: 'day_pnl'`` affects day profit only), ``quality``,
``estimated[]`` ({ticker|currency, status, reason}).

Definitions:

* ``total_pnl*`` is unrealized profit of open positions against average
  cost; ``cumulative_pnl_cny`` = ``total_cny`` - ``contributed_cny`` (the
  ledger's contributed capital), as the daily snapshot's total profit.
* ``day_pnl_pct`` = ``day_pnl_cny`` / (``total_cny`` - ``day_pnl_cny``)
  x 100: of the portfolio's value at the reference prices (each row's
  previous close or trade price, at current rates), i.e. the value before
  the session's price moves when no money came in or went out.
* ``total_pnl_pct`` = ``total_pnl_cny`` / sum of open positions' quantity x
  average cost x current rate x 100.
* ``cumulative_pnl_pct`` = ``cumulative_pnl_cny`` / ``contributed_cny`` x 100.
* Each percentage is ``null`` when a part is unknown or its denominator is
  not positive.
* Day profit is per instrument, for the session its quote belongs to
  (``quote.session_date``): price moves on positions and trade fees. It
  excludes dividends and standalone fees (``day_pnl_excludes``), currency
  moves on the previous value, and fund revaluations. It is ``null`` with a
  reason when that session's reference price is missing, when trades are
  dated after that session ("行情尚未更新到最新交易日"), and in the other
  cases ``valuation.py`` lists.
* CNY amounts are rounded to the cent per row (and per cash balance) and
  totals are sums of the rounded parts, so displayed totals reconcile.

``GET /api/quotes?tickers=A,B`` (1-50 tickers, else 422)::

    {as_of, source, quotes: [{ticker, name, price, prev_close, change_pct,
      currency, quote{...as in a row}, estimated, estimated_reason}]}

in request order, one per distinct ticker; ``estimated`` there reflects the
quote only (no currency conversion).

Sessions
========

Where "today" starts is decided per instrument, from the session its own
quote belongs to: at 10:00 in Shanghai a US quote is still yesterday's New
York close, so its day profit is yesterday's session and must be measured
against what was held before that session, not before today. Events dated
after today (Shanghai) are not applied; positions sold out in a quote's
session are found on the trading calendar, however long a closure was.

Trade dates are exchange-local dates. The ledger stores a date and no time,
and a trade is assigned to a session by that date read on its exchange's
calendar: New York's for NASDAQ and NYSE, Hong Kong's and the mainland's
(both Shanghai's date) for the others. A US trade executed at 01:00 on a
Tuesday in Shanghai happened on Monday in New York and must be dated Monday;
dated Tuesday it would count in Tuesday's New York session instead. Nothing
here can detect such a date, so while the ledger has US trades in New York's
last two sessions the response carries a reminder (``notices[]`` entry of
kind ``exchange_local_dates``).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, Context, Decimal, DefaultContext, localcontext
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from core.ledger import LedgerError, replay, replay_as_of

from .calendar import SHANGHAI, SessionClock
from .instruments import CN, MARKETS, US, InstrumentError, parse
from .quotes import FX_CURRENCIES, PREV_CLOSE, TIME_UNKNOWN, UNAVAILABLE, FxRate, LiveQuote, normalize_ticker
from .valuation import AccountValue, PositionValue, Valuation, quantized, quote_estimate, value_portfolio

CENT = Decimal('0.01')
WORKING_DIGITS = 60
# Without a holiday table a real session may lie further back than the
# weekday calendar says; look this much further for sold-out positions.
UNKNOWN_HOLIDAYS_DAYS = 7
POSITION_KINDS = ('buy', 'sell', 'split', 'opening')
# Ledger event kinds whose money day profit leaves out (see the module docstring).
DAY_PNL_EXCLUDES = ('dividend', 'fee')
EMPTY_NOTICE = '账本还没有已确认的数据。请先在“交易账本”中确认期初余额，今日页才会显示持仓。'
LEDGER_BUSY = '账本正在写入，未能读到同一版本的持仓和流水。请稍后刷新。'
LEDGER_UNREADABLE = '账本无法回放，今日页暂不显示持仓'
FUTURE_ONLY_NOTICE = '账本中的记录都晚于今天，尚未生效，今日没有可显示的持仓。'
READ_ATTEMPTS = 3
MAX_QUOTE_TICKERS = 50
NO_EXCHANGE_TIME = '行情缺少交易所时间，无法确定当日区间，当日盈亏不可用'
PREVIOUS_UNKNOWN = '无法重建该交易日之前的持仓，当日盈亏不可用'
UNTRADED_QUOTE = '行情仍为昨收、尚未反映本交易日的成交，当日盈亏暂不可用'
NO_REFERENCE = '行情不可用，缺少该交易日的参考价（昨收），当日盈亏不可用'
BEHIND_SESSION = '行情尚未更新到最新交易日'
US_DATES_HINT = ('美股交易按纽约当地日期记账：北京时间凌晨成交的美股，日期应记为纽约的前一天，'
                 '否则会计入错误的交易日。')


# -- formatting ---------------------------------------------------------------

def money(value: Optional[Decimal]) -> Optional[str]:
    """Two decimal places as a string; ``None`` stays ``None``. Never raises."""
    rounded = quantized(value, CENT)
    if rounded is None:
        return None
    return format(rounded if rounded != 0 else Decimal('0.00'), 'f')


# Places of ``weight_pct_exact``: more than a threshold has (core/alerts/engine.py MAX_THRESHOLD_PLACES).
COMPARE_PLACES = 20


def cut(value: Optional[Decimal], places: int = COMPARE_PLACES) -> Optional[str]:
    """``value`` cut (rounded down, never up) to ``places`` decimals, no padding; ``None`` stays ``None``.

    Compared with a threshold of at most ``places`` decimals it gives what the full value gives."""
    if value is None or not value.is_finite():
        return None
    context = Context(prec=max(28, max(value.adjusted(), 0) + places + 2))
    return exact(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_FLOOR, context=context))


def exact(value: Optional[Decimal]) -> Optional[str]:
    """A ledger figure (a quantity, an average cost) with every digit it has, no padding; ``None`` stays ``None``."""
    if value is None or not value.is_finite():
        return None
    if value == 0:
        return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def plain(value: Optional[Decimal], places: int = 6) -> Optional[str]:
    """A quantity, price or rate: at most ``places`` decimals, no padding."""
    rounded = quantized(value, Decimal(1).scaleb(-places))
    if rounded is None:
        return None
    if rounded == 0:
        return '0'
    text = format(rounded, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def moment(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(SHANGHAI).isoformat(timespec='seconds') if value is not None else None


# -- where each instrument's day begins --------------------------------------

def _session_date(ticker: str, quote: Optional[LiveQuote], clock: SessionClock) -> Optional[date]:
    """The trading session the quote's prices belong to, or ``None`` when unknown."""
    if quote is None or not quote.usable or quote.quote_time is None or quote.status == TIME_UNKNOWN:
        return None
    if quote.session_date is not None:
        return quote.session_date
    try:
        return clock.session_of(parse(ticker).market, quote.quote_time)
    except InstrumentError:
        return None


def effective_events(events: Sequence[dict]) -> List[dict]:
    """Events that still count: reversals and what they reversed are dropped."""
    reversed_ids = {event.get('target_id') for event in events if event.get('kind') == 'reverse'}
    return [event for event in events if event.get('kind') != 'reverse' and event.get('id') not in reversed_ids]


def events_in_force(events: Sequence[dict], as_of: str, corrected_accounts: Sequence[str] = ()) -> List[dict]:
    """The effective events behind ``replay_as_of(events, as_of)``.

    Events dated after ``as_of`` have not happened yet. For the accounts the
    ledger had to correct (``as_of_corrected_accounts``), the targets of
    later-dated reversals are dropped as well, as its corrected view does.
    """
    dated = [event for event in events if event['date'] <= as_of]
    corrected = set(corrected_accounts)
    if corrected:
        later = {event.get('target_id') for event in events if event.get('kind') == 'reverse' and event['date'] > as_of}
        dated = [event for event in dated
                 if not (event.get('id') in later
                         and (event.get('account') in corrected or event.get('to_account') in corrected))]
    return effective_events(dated)


def day_basis(events: Sequence[dict], held: Sequence[str], quotes: Mapping[str, LiveQuote],
              clock: SessionClock) -> Tuple[dict, List[dict], Dict[str, str]]:
    """``(prev_state, todays_events, day_reasons)`` for ``value_portfolio``.

    Every ticker's day is the trading session its quote belongs to
    (``LiveQuote.session_date``). ``prev_state`` holds, per ticker, the
    position before that session; ``todays_events`` holds the events on or
    after it. When the quote cannot say (no exchange time, or no quote at
    all) the session is the one the calendar says is current, so a position
    sold out in it still has its row, and wherever a reference price would be
    needed (shares held before the session, or a holding) day profit gets a
    reason instead of a guess or a zero.
    """
    events = effective_events(events)
    held = set(held)
    starts: Dict[str, date] = {}
    reasons: Dict[str, str] = {}
    unknown: Dict[str, str] = {}
    for ticker, quote in quotes.items():
        try:
            market = parse(ticker).market
        except InstrumentError:
            continue
        start = _session_date(ticker, quote, clock)
        if start is None:
            start = clock.session_of(market)
            unknown[ticker] = NO_EXCHANGE_TIME if quote is not None and quote.usable else NO_REFERENCE
        starts[ticker] = start

    states: Dict[date, Optional[dict]] = {}
    for start in set(starts.values()):
        try:
            states[start] = replay([event for event in events if event['date'] < start.isoformat()])
        except (LedgerError, KeyError, TypeError, ValueError):
            states[start] = None

    previous: Dict[str, dict] = {}
    for ticker, start in starts.items():
        state = states[start]
        if state is None:
            reasons[ticker] = PREVIOUS_UNKNOWN
            continue
        for name, account in state['accounts'].items():
            position = account['positions'].get(ticker)
            if position is not None:
                previous.setdefault(name, {'positions': {}})['positions'][ticker] = position

    for ticker, reason in unknown.items():
        # A holding without a usable quote is already unavailable as a whole;
        # one with a price but no exchange time cannot be placed in a session.
        # A position sold out since needs the session's previous close.
        if ticker in held:
            if reason == NO_EXCHANGE_TIME:
                reasons.setdefault(ticker, reason)
        elif any(ticker in account['positions'] for account in previous.values()):
            reasons.setdefault(ticker, reason)

    todays: List[dict] = []
    for event in events:
        if event.get('kind') == 'opening':
            positions = [position for position in event.get('positions') or []
                         if position.get('ticker') in starts and event['date'] >= starts[position['ticker']].isoformat()]
            if positions:
                todays.append({**event, 'positions': positions})
        elif event.get('ticker') in starts and event['date'] >= starts[event['ticker']].isoformat():
            todays.append(event)
    # Trades dated after the session a quote belongs to are not in its
    # prices: netting them against those prices mixes two sessions.
    for event in todays:
        for ticker in _position_tickers(event):
            if ticker in starts and event['date'] > starts[ticker].isoformat():
                reasons.setdefault(ticker, BEHIND_SESSION)
    # A quote still showing the previous close has not caught up with trades
    # recorded for its session; measuring them against it would be wrong.
    traded = {event.get('ticker') for event in todays if event.get('kind') in ('buy', 'sell')}
    for ticker in traded:
        if quotes[ticker].price_basis == PREV_CLOSE:
            reasons.setdefault(ticker, UNTRADED_QUOTE)
    return {'accounts': previous}, todays, reasons


def _position_tickers(event: dict) -> List[str]:
    """The tickers whose holding ``event`` changes."""
    if event.get('kind') == 'opening':
        return [position['ticker'] for position in event.get('positions') or [] if position.get('ticker')]
    if event.get('kind') in POSITION_KINDS and event.get('ticker'):
        return [event['ticker']]
    return []


def discovery_start(clock: SessionClock, market: str) -> date:
    """The earliest session a current quote of ``market`` can belong to.

    That is the session before the one whose prices are current now (before
    the open, and in the opening auction, quotes still show its close). It is
    found on the calendar, so a long closure is crossed however many days it
    lasts; where holidays are unknown the search reaches a week further.
    """
    start = clock.previous_trading_day(market, clock.session_of(market))
    if clock.confidence(market, start) != 'calendar':
        start -= timedelta(days=UNKNOWN_HOLIDAYS_DAYS)
    return start


def us_trades_recently(events: Sequence[dict], clock: SessionClock) -> bool:
    """True when a US ticker was bought or sold in New York's last two sessions."""
    since = clock.previous_trading_day(US, clock.session_of(US)).isoformat()
    for event in events:
        if event.get('kind') in ('buy', 'sell') and event['date'] >= since:
            try:
                if parse(event.get('ticker')).market == US:
                    return True
            except InstrumentError:
                continue
    return False


def traded_recently(events: Sequence[dict], clock: SessionClock) -> List[str]:
    """Tickers whose holding changed on or after their market's ``discovery_start``.

    A ticker no longer held can still have been sold out in the session its
    quote belongs to; these are the ones to ask a quote for.
    """
    starts = {market: discovery_start(clock, market).isoformat() for market in MARKETS}
    tickers: List[str] = []
    for event in events:
        for ticker in _position_tickers(event):
            try:
                market = parse(ticker).market
            except InstrumentError:
                continue
            if event['date'] >= starts[market]:
                tickers.append(ticker)
    return tickers


# -- payload ------------------------------------------------------------------

def _session(clock: SessionClock) -> dict:
    def one(market: str) -> dict:
        return {'phase': clock.phase(market), 'trading_day': clock.current_trading_day(market).isoformat(),
                'is_trading_day': clock.is_trading_day(market), 'calendar_confidence': clock.confidence(market),
                'next_open': moment(clock.next_open(market))}
    return {'market': CN, **one(CN), 'markets': {market: one(market) for market in MARKETS}}


def _fx_payload(rates: Mapping[str, Optional[FxRate]]) -> dict:
    payload = {}
    for currency in FX_CURRENCIES:
        rate = rates.get(currency)
        if rate is None:
            payload[currency] = {'rate': None, 'source': None, 'quote_time': None, 'status': UNAVAILABLE,
                                 'reason': '未请求汇率'}
        else:
            payload[currency] = {'rate': plain(rate.rate, 6), 'source': rate.source,
                                 'quote_time': moment(rate.quote_time), 'status': rate.status, 'reason': rate.reason}
    return payload


def _quote_payload(quote: Optional[LiveQuote]) -> dict:
    if quote is None:
        return {'source': None, 'quote_time': None, 'fetched_at': None, 'status': UNAVAILABLE, 'reason': '没有行情',
                'session_date': None}
    return {'source': quote.source, 'quote_time': moment(quote.quote_time), 'fetched_at': moment(quote.fetched_at),
            'status': quote.status, 'reason': quote.reason,
            'session_date': quote.session_date.isoformat() if quote.session_date else None}


def _position_payload(position: PositionValue) -> dict:
    return {
        'ticker': position.ticker, 'name': position.name, 'currency': position.currency,
        'quantity': exact(position.quantity), 'cost_price': exact(position.cost_price),
        'price': plain(position.price, 6), 'prev_close': plain(position.prev_close, 6),
        'change_pct': money(position.change_pct),
        'market_value': money(position.market_value), 'market_value_cny': money(position.market_value_cny),
        'day_pnl': money(position.day_pnl), 'day_pnl_cny': money(position.day_pnl_cny),
        'total_pnl': money(position.total_pnl), 'total_pnl_cny': money(position.total_pnl_cny),
        'total_pnl_pct': money(position.total_pnl_pct),
        'weight_pct': money(position.weight_pct), 'weight_pct_exact': cut(position.weight_pct),
        'quote': _quote_payload(position.quote),
        'estimated': position.estimated, 'estimated_reason': position.estimated_reason,
        'identity': _identity(position.ticker)}


def _identity(ticker: str) -> Optional[str]:
    from .search import identity_key   # imported here: search reads the desk store, today need not load it first
    return identity_key(ticker)


def _account_payload(account: AccountValue) -> dict:
    return {
        'name': account.name,
        'positions': [_position_payload(position) for position in account.positions],
        'closed_today': [_position_payload(position) for position in account.closed_today],
        'cash': {currency: money(balance) for currency, balance in account.cash.items()},
        'cash_cny': money(account.cash_cny), 'fund_cny': money(account.fund_cny),
        'market_value_cny': money(account.market_value_cny), 'total_cny': money(account.total_cny),
        'day_pnl_cny': money(account.day_pnl_cny), 'total_pnl_cny': money(account.total_pnl_cny),
        'quality': account.quality, 'estimated': list(account.estimated)}


def _totals_payload(valuation: Optional[Valuation]) -> dict:
    labels = {'total_pnl_kind': 'unrealized', 'day_pnl_excludes': list(DAY_PNL_EXCLUDES)}
    if valuation is None:
        return {'by_currency': {}, 'total_cny': None, 'day_pnl_cny': None, 'total_pnl_cny': None, 'unavailable': [],
                'quality': None, 'estimated': [], 'contributed_cny': None, 'cumulative_pnl_cny': None,
                'cumulative_pnl_pct': None, 'day_pnl_pct': None, 'total_pnl_pct': None, **labels}
    return {
        'by_currency': {currency: {'market_value': money(entry['market_value']), 'cash': money(entry['cash'])}
                        for currency, entry in valuation.by_currency.items()},
        'total_cny': money(valuation.total_cny), 'day_pnl_cny': money(valuation.day_pnl_cny),
        'total_pnl_cny': money(valuation.total_pnl_cny), 'unavailable': list(valuation.unavailable),
        'quality': valuation.quality, 'estimated': list(valuation.estimated),
        'contributed_cny': money(valuation.contributed_cny), 'cumulative_pnl_cny': money(valuation.cumulative_pnl_cny),
        'cumulative_pnl_pct': money(valuation.cumulative_pnl_pct), 'day_pnl_pct': money(valuation.day_pnl_pct),
        'total_pnl_pct': money(valuation.total_pnl_pct), **labels}


# -- GET /api/quotes ----------------------------------------------------------

class QuoteRequestError(ValueError):
    """The ``tickers`` parameter is not a list of 1 to ``MAX_QUOTE_TICKERS`` tickers."""


def requested_tickers(text) -> List[str]:
    """``'A, b,A'`` -> ``['A', 'B']``; ``QuoteRequestError`` when empty or too many."""
    if not isinstance(text, str) or len(text) > MAX_QUOTE_TICKERS * 40:
        raise QuoteRequestError(f'tickers 过长：一次最多查询 {MAX_QUOTE_TICKERS} 个代码')
    tickers = list(dict.fromkeys(normalize_ticker(part) for part in text.split(',') if part.strip()))
    if not tickers:
        raise QuoteRequestError('请用 tickers=代码1,代码2 指定要查询的代码，例如 tickers=SHA:600519,HKG:00700')
    if len(tickers) > MAX_QUOTE_TICKERS:
        raise QuoteRequestError(f'一次最多查询 {MAX_QUOTE_TICKERS} 个代码，收到 {len(tickers)} 个')
    return tickers


def quote_object(ticker: str, quote: Optional[LiveQuote]) -> dict:
    """What a Today row says about its quote, for a ticker on its own."""
    price = quote.price if quote is not None and quote.usable else None
    prev_close = quote.prev_close if quote is not None else None
    change_pct = (price - prev_close) / prev_close * 100 if price is not None and prev_close else None
    try:
        currency = (quote.currency if quote is not None else None) or parse(ticker).currency
    except InstrumentError:
        currency = None
    flagged = quote_estimate(quote)
    return {'ticker': ticker, 'name': quote.name if quote is not None else None, 'price': plain(price, 6),
            'prev_close': plain(prev_close, 6), 'change_pct': money(change_pct), 'currency': currency,
            'quote': _quote_payload(quote), 'estimated': flagged is not None,
            'estimated_reason': flagged[1] if flagged else None}


def build_quotes(runtime, tickers: Sequence[str], refresh: bool = False) -> dict:
    """The ``GET /api/quotes`` payload for already validated ``tickers``."""
    with localcontext() as context:
        context.prec = WORKING_DIGITS
        quotes = runtime.quotes.get(tickers, refresh=refresh)
        return {'as_of': moment(runtime.clock.now()), 'source': '+'.join(runtime.quotes.source_names) or 'none',
                'quotes': [quote_object(ticker, quotes.get(ticker)) for ticker in tickers]}


def read_ledger(ledger) -> Optional[Tuple[int, List[dict]]]:
    """``(revision, events)`` read at one revision, or ``None``.

    The revision only grows, and a confirmation writes its events and the new
    revision in one transaction, so events read between two equal revision
    readings are exactly that revision's. ``None`` when the ledger kept
    changing for ``READ_ATTEMPTS`` tries; the caller must not value a mix.
    State is rebuilt from these events, never read separately.
    """
    for _ in range(READ_ATTEMPTS):
        revision = ledger.state()['revision']
        events = ledger.history()
        if ledger.state()['revision'] == revision:
            return revision, events
    return None


def today_unavailable(runtime, notice: str) -> dict:
    """The ``GET /api/today`` payload when the ledger could not be read: no accounts, ``notice`` says why.

    ``ledger_revision`` is ``None`` (no consistent read) and nothing is fetched.
    """
    with localcontext() as context:
        context.prec = WORKING_DIGITS
        clock: SessionClock = runtime.clock
        return {'as_of': moment(clock.now()), 'source': '+'.join(runtime.quotes.source_names) or 'none',
                'holdings_source': 'confirmed_ledger', 'ledger_revision': None, 'session': _session(clock),
                'notices': [], 'fx': _fx_payload({}), 'accounts': [], 'totals': _totals_payload(None),
                'notice': notice}


def build_today(ledger, runtime, refresh: bool = False) -> dict:
    """The ``GET /api/today`` payload. ``ledger`` is ``None`` when none is active.

    ``ledger`` is a ``core.ledger.Ledger`` or a read-only ``ConfirmedLedger``
    snapshot (what ``GET /api/today`` passes): anything with ``state()`` and
    ``history()``.
    """
    # Ledger amounts go up to 1e18 with 100 decimal places; work with enough
    # digits that products of them are exact rather than silently rounded.
    with localcontext() as context:
        context.prec = WORKING_DIGITS
        return _build_today(ledger, runtime, refresh)


def _build_today(ledger, runtime, refresh: bool) -> dict:
    clock: SessionClock = runtime.clock
    notices: List[dict] = []
    payload = {'as_of': moment(clock.now()), 'source': '+'.join(runtime.quotes.source_names) or 'none',
               'holdings_source': 'confirmed_ledger', 'ledger_revision': 0, 'session': _session(clock),
               'notices': notices}

    def nothing_to_value(notice: str, revision: Optional[int]) -> dict:
        # Say why, and ask the network for nothing.
        return {**payload, 'ledger_revision': revision, 'fx': _fx_payload({}), 'accounts': [],
                'totals': _totals_payload(None), 'notice': notice}

    if ledger is None:
        return nothing_to_value(EMPTY_NOTICE, 0)
    try:
        read = read_ledger(ledger)
    except LedgerError as exc:
        return nothing_to_value(f'{LEDGER_UNREADABLE}（{exc}）', None)
    except sqlite3.Error:
        # e.g. "database is locked" after the ledger's own busy timeout
        return nothing_to_value(LEDGER_BUSY, None)
    if read is None:
        return nothing_to_value(LEDGER_BUSY, None)
    revision, events = read
    # Today is the Shanghai date; an event dated later has not happened yet.
    today = clock.now().date().isoformat()
    future = [event for event in events if event['date'] > today]
    if future:
        notices.append({'kind': 'future_events', 'count': len(future),
                        'message': f'有 {len(future)} 笔未来日期的记录尚未生效'})
    try:
        # Replayed as the ledger replays them (the default 28-digit context), so a position's quantity and
        # average cost are the ledger's own figures (10.00333333333333333333333333), not longer ones; the
        # valuation below works with WORKING_DIGITS.
        with localcontext(DefaultContext):
            state = replay_as_of(events, today)
    except LedgerError as exc:
        return nothing_to_value(f'{LEDGER_UNREADABLE}（{exc}）', None)
    corrected = list(state.get('as_of_corrected_accounts') or [])
    if corrected:
        notices.append({'kind': 'corrected_view', 'accounts': corrected,
                        'message': f'账户 {"、".join(corrected)} 按日期回放不成立，当前持仓已提前计入日期晚于今天的冲销记录'})
    events = events_in_force(events, today, corrected)
    payload['ledger_revision'] = revision
    if not state['accounts']:
        return nothing_to_value(FUTURE_ONLY_NOTICE if future else EMPTY_NOTICE, revision)

    held = list(dict.fromkeys(ticker for account in state['accounts'].values() for ticker in account['positions']))
    if us_trades_recently(events, clock):
        notices.append({'kind': 'exchange_local_dates', 'message': US_DATES_HINT})
    tickers = list(dict.fromkeys(held + traded_recently(events, clock)))
    quotes = runtime.quotes.get(tickers, refresh=refresh)
    rates = runtime.quotes.fx(refresh=refresh)
    prev_state, todays_events, day_reasons = day_basis(events, held, quotes, clock)
    valuation = value_portfolio(state, quotes, rates, prev_state, todays_events, day_reasons=day_reasons)
    return {**payload, 'fx': _fx_payload(rates),
            'accounts': [_account_payload(account) for account in valuation.accounts],
            'totals': _totals_payload(valuation)}
