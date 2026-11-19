"""Live quotes that say where they came from and how old they are.

A ``LiveQuote`` carries the exchange's own timestamp (``quote_time``) next to
the time we fetched it. Its ``status`` is derived from that exchange time and
the session clock every time the quote is handed out, so a cached or old
price can never be labelled live:

``live``          exchange time within a minute, market trading (or in its
                  opening auction, or in a closing auction before the
                  closing price is final)
``delayed``       exchange time 1-20 minutes old, market trading (or in
                  one of those auctions, or early in a midday break
                  while a delayed source may still be catching up)
``stale``         older than that while trading or in an auction; once the
                  market has closed, anything that is not that session's
                  close (or, in a midday break, the morning's last price)
``closed``        no trading and no auction under way, and the exchange
                  time is the most recent completed session's closing
                  print or later (see the table). Verified when the
                  calendar knows the market's holidays for that year and,
                  in New York, the close has settled (``close_pending``
                  until then); in a year the table does not cover it is
                  the latest weekday's close and ``reason`` says it is
                  unverified
``prev_close``    the security has not traded in the current session (the
                  payload shows no volume and no day range); ``price`` is
                  the previous close, an estimate of what it is worth. A
                  previous-close quote from an older session is ``stale``
``time_unknown``  the source gave a price but no usable exchange time
``unavailable``   no usable price; ``reason`` says why

Only ``live``, ``delayed`` and ``closed`` in a year with a holiday table
(and, in New York, settled) are verified (``valuation.quote_estimate``);
every other priced status makes the row and its totals estimated.

The decision (``assess``), with ``t`` the quote's exchange time, ``age`` the
time since then, and "current session" the trading day whose prices are
current now (``SessionClock.session_of``)::

    market now                       quote                                   status
    -------------------------------  --------------------------------------  ------------
    any                              no price                                unavailable
    any                              no exchange time, or > 2 min ahead      time_unknown
    any                              untraded (price is the previous close)  prev_close; stale when
                                                                             not the current session
    continuous trading               current session, age <= 1 min           live
      CN 09:30-11:30, 13:00-15:00      current session, age <= 20 min          delayed
      (closing call auction            older, or an earlier session            stale
      14:57-15:00 included);
      HK 09:30-12:00, 13:00-16:00;
      US 09:30-16:00; shorter on
      early-close days
    opening auction                  current session, age <= 1 / 20 min      live / delayed
      CN 09:15-09:30, HK 09:00-09:30   current session, older                  stale
                                     an earlier session                      as "closed" below
    closing auction (until the       current session, age <= 1 / 20 min      live / delayed
      close is final): HK 16:00-       current session, older                  stale: not the close
      16:10, half day 12:00-12:10      an earlier session                      stale
    midday break                     t in the morning, at most 20 min        closed (the morning's
      CN 11:30-13:00, HK 12:00-13:00     before its end, or later              last price)
                                     the same, more than 1 min before the    delayed: a delayed
                                       end and seen at most 20 min after t     source may still be
                                       (so before the end + 20 min)            catching up
                                     earlier, or an earlier session          stale
    closed: after the close, before  t in the most recent session, at or     closed (with reason
      the opening auction, weekend,    after its closing print: CN 15:00,      UNVERIFIED_CLOSE in a
      holiday, half-day afternoon      HK end + 8 min (16:08, 12:08 on a       year without a holiday
                                       half day)                               table)
                                     the same, but seen before the close     closed, not verified
                                       was final (CN 15:00, HK 16:10)          (SEEN_BEFORE_FINAL)
                                     t in that session, before the print     stale
                                     t in an earlier session                 stale
    New York after the close         t later than the end + 10 min and the   stale: it may be an
      (end: 16:00, 13:00 on an         source does not name the price the      extended-hours price
      early-close day)                 regular session's
                                     seen before the close settles (end +    closed, not verified
                                       30 min, 16:30 / 13:30): t at most 20    (close_pending,
                                       min before the end                      CLOSE_NOT_FINAL): an
                                                                               estimate
                                     seen once settled: t in [end, end + 10  closed (UNVERIFIED_CLOSE
                                       min], or the source names the regular   in a year without a
                                       session and t is at most 1 min before   holiday table)
                                       the end or later
                                     t before the end (earlier than 20 min    stale (a regular-session
                                       before it, before settling)             price: REGULAR_BEFORE_
                                                                               CLOSE)

The windows and the settle delay are ``calendar.CLOSE_PRINT_WINDOW`` (the
settle delay is ``calendar.CLOSE_SETTLE``, which is also the backtest's bar
finality margin), the one table of these constants. "Seen" is when
``QuoteService`` received the quote (``LiveQuote.observed_at``): a quote seen
before a close (or a morning) was final is never promoted by the clock
passing; the service reads it again once that moment has passed (and
without the source's answer it has no price, never the old one as the close).
A closing auction prints the close: a price from before the
print is the last one the source had, not the close, however close to the
bell, and nothing is a verified close while the auction can still change it
(Hong Kong's random close falls between 8 and 10 minutes after the bell; the
mainland and New York print at the bell). A midday break has no auction, so
the morning's last trade stands. In a year without a holiday table the
weekday calendar decides, and early closes are not known.

Why these windows (observed on 2026-11-19 01:53 Beijing time, ten hours
after the 2026-11-18 close):

* Hong Kong. Eastmoney's ``f124`` was 16:08:02 or 16:08:03 for every stock
  (liquid, illiquid GEM; the HSI 16:08:28) and Yahoo's ``regularMarketTime``
  16:08:02, liquid and illiquid alike: both providers stamp the close at the
  start of the random-close window, not at the stock's own uncrossing second
  (which can be up to 16:10). A rule wanting a stamp after the true uncross
  would mark every Hong Kong holding estimated every evening, so a stamp at
  or after 16:08:00 (12:08:00 on a half day) is the close once the auction
  has ended (16:10 / 12:10); observed during the auction it is live or
  delayed, never closed.
* The mainland. Eastmoney stamped the close 15:34:00-15:34:51 (Shenzhen) and
  16:11:34-16:11:59 (Shanghai, B shares too); Yahoo 15:00:02. After-hours
  fixed-price trading prints at the close, so there is no upper bound. A
  suspended share had no price and ``f124`` 08:00:00 (``prev_close``).
* New York (the 2026-11-18 session, Eastmoney ``ulist`` ``f2``/``f124`` and
  Yahoo's ``regularMarketPrice``/``regularMarketTime``, probed twice):

  - 12 minutes after the close Eastmoney stamped AAPL, NVDA, TSLA, GOOGL, SPY
    and F 16:00:00 and BRK_B, KO and SIRI 15:59:59; Yahoo AAPL 16:00:01,
    BRK-B 15:59:59, KO, SIRI and SPY 16:00:00, F 15:59:59. The prices were
    not the official closes yet (Eastmoney AAPL 352.27 where Yahoo already
    had 352.45; KO 98.08 against Yahoo's 98.04 and the final 98.09).
  - 100 minutes after it, with after-hours trading under way, Eastmoney
    stamped every stock exactly 16:00:00 and its ``f2`` was the official
    close (AAPL 352.45, BRK_B 574.62, KO 98.09, SIRI 32.22, F 11.28, SPY
    730.2); Yahoo showed the same prices stamped 16:00:00-16:00:39. Its
    indices: NDX 16:00:00, SPX and DJIA 15:59:46 at both probes.

  So the official closing prices reach both providers minutes after the bell,
  and a quote seen soon after it is not final, even stamped 16:00:00 and even
  Yahoo's regular-session price: until 30 minutes after the close
  (``CLOSE_SETTLE``) it is ``closed`` with ``close_pending`` (an estimate,
  ``CLOSE_NOT_FINAL``; a one-second-early stamp is not called stale for
  it). Once settled, a stamp in [16:00:00, 16:10:00] or Yahoo's
  regular-session flag makes it the verified close; Eastmoney's 15:59:46
  index stamps stay stale, and ``QuoteService`` asks the next source, whose
  regular-session price wins. A later stamp without the flag may be an
  extended-hours trade (until 20:00): stale.

``QuoteService`` also records ``session_date``, the exchange-local trading
day the price belongs to, and that day's ``calendar_confidence``.

A missing value is ``None``. Nothing in this module substitutes zero, a
cached price or a guess for a value a source did not return.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from core.provider_errors import log_failure

from .calendar import ZONES, SessionClock
from .instruments import InstrumentError, parse, quote_key

logger = logging.getLogger(__name__)

LIVE, DELAYED, STALE, CLOSED = 'live', 'delayed', 'stale', 'closed'
TIME_UNKNOWN, UNAVAILABLE = 'time_unknown', 'unavailable'
PREV_CLOSE_ONLY = 'prev_close'   # a status; the same word as the price basis below
STATUSES = (LIVE, DELAYED, STALE, CLOSED, PREV_CLOSE_ONLY, TIME_UNKNOWN, UNAVAILABLE)

LIVE_MAX_AGE = timedelta(seconds=60)
DELAYED_MAX_AGE = timedelta(minutes=20)
# A timestamp this far ahead of our clock means one of the two clocks is wrong.
FUTURE_TOLERANCE = timedelta(minutes=2)
# A price this close to a session's end is that session's close: it would
# still have counted as delayed when the bell rang. Earlier, it is a price
# that stopped updating, whatever the clock says now.
CLOSE_WINDOW = DELAYED_MAX_AGE

FX_CURRENCIES = ('USD', 'HKD')
FX_LIVE_MAX_AGE = timedelta(minutes=5)
FX_DELAYED_MAX_AGE = timedelta(hours=1)
# The currencies shut for the weekend at 17:00 New York time on Friday and
# reopen at 17:00 on Sunday, in summer and winter time alike. A rate stamped
# at most ``FX_DELAYED_MAX_AGE`` before the Friday close (delayed at worst
# when the market shut), or later, is the weekend's rate. Observed in November
# 2026: Eastmoney's USDCNH and HKDCNH stop at 16:59, Yahoo's USDCNY=X and
# HKDCNY=X print until 17:00-18:00 New York time; Eastmoney resumes at 17:05 on
# Sunday, Yahoo about two hours later (``QuoteService`` then asks the next source).
FX_WEEKEND_CLOSE = time(17)

# Magnitudes a source may report. Anything outside is a broken answer, not a
# price: no listing on these exchanges trades below 0.0001 or above 100
# million per unit, and CNY rates for USD and HKD are near 7 and 0.9.
MIN_PRICE, MAX_PRICE = Decimal('0.0001'), Decimal('100000000')
MAX_VOLUME = Decimal('1e15')
MIN_FX_RATE, MAX_FX_RATE = Decimal('0.0001'), Decimal('10000')
OUT_OF_RANGE_PRICE = '数据源返回的价格超出支持范围（0.0001 至 1 亿），视为无效'
OUT_OF_RANGE_RATE = '数据源返回的汇率超出支持范围（0.0001 至 10000），视为无效'
UNVERIFIED_CLOSE = '未收录该市场该年的节假日表，按最近一个工作日的收盘价处理，未经核实'
# A New York close observed before it settles (``calendar.CLOSE_SETTLE``).
CLOSE_NOT_FINAL = ('收盘价尚未最终确定：交易所的官方收盘价在收盘后数分钟才发布，当地 {settled} 之前看到的价格'
                   '按估算处理，之后再次读取时确认')
# A price read before the day's close was final (the mainland's 15:00, Hong
# Kong's closing auction to 16:10), handed out after: the next read confirms it.
# A regular-session price stamped more than ``LIVE_MAX_AGE`` before New York's
# bell: Yahoo names the price the regular session's, but not the close.
REGULAR_BEFORE_CLOSE = '收盘前的最后一笔成交，未能核实为收盘价（行情时间为当地 {when}，收盘 {close}）'
# The same for the morning's last price, read before the morning session ended.
MORNING_SEEN_EARLY = ('上午最后价格尚未确认：这是上午收市（当地 {final}）之前读到的价格，按估算处理，'
                      '之后再次读取时确认')
SEEN_BEFORE_FINAL = ('收盘价尚未确认：这是收盘价确定（当地 {final}）之前读到的价格，按估算处理，'
                     '之后再次读取时确认')

# ``price_basis`` values.
LAST_TRADE, PREV_CLOSE = 'last', 'prev_close'

# After a source fails as a whole it is left alone for a while, doubling up
# to the maximum, so a dead source costs one timeout and not one per poll.
RETRY_AFTER = timedelta(seconds=30)
RETRY_AFTER_MAX = timedelta(minutes=10)


class SourceError(Exception):
    """The source as a whole could not be reached; the message is shown to the user.

    The message must be a fixed text of the source's own (``core.provider_errors``
    classifies an exception into one): it becomes ``reason``, which the
    assistant's tools pass on as trusted text. Any other exception a source
    raises is classified by ``QuoteService`` itself.
    """


def to_decimal(value) -> Optional[Decimal]:
    """Decimal from a source field, or ``None`` when it holds no number.

    Floats go through ``str`` so 12.26 stays 12.26. Sources mark a missing
    value in several ways ('-', '', null); none of them becomes zero.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, float, str)):
        text = str(value).strip()
        if len(text) > 64:
            return None
        try:
            number = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    return number if number.is_finite() else None


def positive(value) -> Optional[Decimal]:
    number = to_decimal(value)
    return number if number is not None and number > 0 else None


def bounded(value, low: Decimal, high: Decimal) -> Optional[Decimal]:
    """A positive number within ``[low, high]``, or ``None``."""
    number = positive(value)
    return number if number is not None and low <= number <= high else None


def _volume(value) -> Optional[Decimal]:
    number = to_decimal(value)
    return number if number is not None and 0 <= number <= MAX_VOLUME else None


def from_epoch(value) -> Optional[datetime]:
    """UTC datetime from Unix seconds; ``None`` unless it is a plausible time."""
    number = to_decimal(value)
    if number is None or not Decimal(946684800) <= number <= Decimal(4102444800):   # 2000..2100
        return None
    return datetime.fromtimestamp(int(number), tz=timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class LiveQuote:
    ticker: str
    name: Optional[str]
    price: Optional[Decimal]
    prev_close: Optional[Decimal]
    open: Optional[Decimal]
    high: Optional[Decimal]
    low: Optional[Decimal]
    volume: Optional[Decimal]
    currency: Optional[str]
    source: str
    quote_time: Optional[datetime]   # the exchange's own timestamp
    fetched_at: datetime             # when we asked
    status: str
    reason: Optional[str] = None
    # 'prev_close' when the instrument has not traded in the current session
    # and ``price`` is the previous close the exchange publishes.
    price_basis: str = LAST_TRADE
    # Set by ``QuoteService``: the exchange-local trading day the price
    # belongs to, and whether that day's calendar is known ('calendar') or
    # assumed from weekdays ('weekday_only'). ``None`` when unknown.
    session_date: Optional[date] = None
    calendar_confidence: Optional[str] = None
    # True when the source identifies ``price`` as the regular session's price
    # (never an extended-hours trade), as Yahoo's ``regularMarketPrice`` is.
    # It matters for New York closes (``CLOSE_PRINT_WINDOW``).
    regular_session: bool = False
    # Set by ``QuoteService``: ``status`` is ``closed`` but the close has not
    # settled yet (New York before ``calendar.CLOSE_SETTLE`` has passed), so
    # the price may still differ from the official close: an estimate.
    close_pending: bool = False
    # Set by ``QuoteService``: when it received the quote from the source (its
    # own clock). Only an observation made once a close is final can verify
    # it (``SessionClock.close_settled``); ``None``: judged at the current time.
    observed_at: Optional[datetime] = None

    @property
    def usable(self) -> bool:
        return self.price is not None and self.status != UNAVAILABLE


def build_quote(ticker: str, source: str, fetched_at: datetime, *, price=None, prev_close=None,
                name: Optional[str] = None, open=None, high=None, low=None, volume=None,
                currency: Optional[str] = None, quote_time: Optional[datetime] = None,
                reason: Optional[str] = None, price_basis: str = LAST_TRADE,
                regular_session: bool = False) -> LiveQuote:
    """The only way sources make a quote: it enforces the invariants.

    A price that is missing, zero, negative or outside ``MIN_PRICE`` to
    ``MAX_PRICE`` becomes ``None`` with status ``unavailable``; the other
    prices and the volume are dropped when out of range. A timestamp without a
    timezone is dropped. The status is never better than ``time_unknown``
    here; ``QuoteService`` upgrades it against the session clock.
    """
    raw = positive(price)
    price = bounded(raw, MIN_PRICE, MAX_PRICE)
    if quote_time is not None and quote_time.tzinfo is None:
        quote_time = None
    if price is None:
        status = UNAVAILABLE
        reason = OUT_OF_RANGE_PRICE if raw is not None else reason or '数据源未返回有效价格'
    else:
        status = TIME_UNKNOWN
        if quote_time is None:
            reason = reason or '数据源未提供交易所时间'

    def other(value):
        return bounded(value, MIN_PRICE, MAX_PRICE)
    return LiveQuote(ticker=ticker, name=(str(name).strip() or None) if name else None, price=price,
                     prev_close=other(prev_close), open=other(open), high=other(high),
                     low=other(low), volume=_volume(volume), currency=currency, source=source,
                     quote_time=quote_time, fetched_at=fetched_at, status=status, reason=reason,
                     price_basis=price_basis if price is not None else LAST_TRADE,
                     regular_session=regular_session is True)


def unavailable(ticker: str, source: str, fetched_at: datetime, reason: str, *,
                currency: Optional[str] = None, name: Optional[str] = None, prev_close=None) -> LiveQuote:
    return build_quote(ticker, source, fetched_at, reason=reason, currency=currency, name=name,
                       prev_close=prev_close)


@dataclass(frozen=True)
class FxRate:
    currency: str                    # USD or HKD
    rate: Optional[Decimal]          # CNY per one unit
    source: str
    quote_time: Optional[datetime]
    fetched_at: datetime
    status: str
    reason: Optional[str] = None


def build_fx(currency: str, source: str, fetched_at: datetime, *, rate=None,
             quote_time: Optional[datetime] = None, reason: Optional[str] = None) -> FxRate:
    raw = positive(rate)
    rate = bounded(raw, MIN_FX_RATE, MAX_FX_RATE)
    if quote_time is not None and quote_time.tzinfo is None:
        quote_time = None
    if rate is None:
        status = UNAVAILABLE
        reason = OUT_OF_RANGE_RATE if raw is not None else reason or '数据源未返回有效汇率'
    else:
        status = TIME_UNKNOWN
        if quote_time is None:
            reason = reason or '数据源未提供汇率时间'
    return FxRate(currency, rate, source, quote_time, fetched_at, status, reason)


@runtime_checkable
class QuoteSource(Protocol):
    """Fetch quotes for exchange-qualified tickers in one batch.

    Return a quote for every ticker it can say something about, built with
    ``build_quote``; a ticker it cannot price comes back ``unavailable`` with a
    reason. Raise ``SourceError`` when the source itself is unreachable, so
    ``QuoteService`` can stop asking it for a while. Any other exception is
    treated the same way.
    """
    name: str

    def fetch(self, tickers: Sequence[str]) -> Dict[str, LiveQuote]:
        ...


@runtime_checkable
class FxSource(Protocol):
    """Optional second capability of a source: CNY rates for ``currencies``."""
    name: str

    def fetch_fx(self, currencies: Sequence[str]) -> Dict[str, FxRate]:
        ...


def _minutes(delta: timedelta) -> int:
    return max(1, int(delta.total_seconds() // 60))


@dataclass(frozen=True)
class Assessment:
    status: str
    reason: Optional[str]
    session_date: Optional[date] = None
    calendar_confidence: Optional[str] = None
    pending: bool = False     # ``closed``, but the close has not settled: an estimate (``CLOSE_NOT_FINAL``)


def assess(quote: LiveQuote, clock: SessionClock) -> Assessment:
    """Status, reason and session of ``quote`` at the clock's current time."""
    if quote.price is None:
        return Assessment(UNAVAILABLE, quote.reason or '数据源未返回有效价格')
    if quote.quote_time is None:
        return Assessment(TIME_UNKNOWN, quote.reason or '数据源未提供交易所时间')
    try:
        market = parse(quote.ticker).market
    except InstrumentError:
        return Assessment(TIME_UNKNOWN, '无法识别代码所属市场')
    now = clock.now()
    age = now - quote.quote_time
    if age < -FUTURE_TOLERANCE:
        return Assessment(TIME_UNKNOWN, '行情时间晚于本机时钟，无法判断时效')
    session = clock.session_of(market, quote.quote_time)
    confidence = clock.confidence(market, session)

    def result(status: str, reason: Optional[str] = None, pending: bool = False) -> Assessment:
        return Assessment(status, reason, session, confidence, pending)

    if quote.price_basis == PREV_CLOSE:
        # Untraded in the session of its timestamp: an estimate for the
        # current session only. Stuck in an older one, it is stale.
        if session == clock.session_of(market):
            return result(PREV_CLOSE_ONLY, quote.reason or '本交易日尚无成交（或停牌），按昨收价估值')
        return result(STALE, f'行情停留在 {session.isoformat()}，此后没有更新（可能长期停牌或数据源未更新），'
                             '价格为当时的昨收')
    zone = ZONES[market]
    trading = clock.is_trading(market)
    closing = clock.in_closing_auction(market)
    if trading or closing or clock.in_opening_auction(market):
        # Only a price from the session under way can be live or delayed. In
        # the opening auction a price from before it is the previous close
        # and is judged as one below. In a closing auction the closing price
        # is not final yet, so no price is the close.
        if session == clock.session_of(market):
            if age <= LIVE_MAX_AGE:
                return result(LIVE)
            if age <= DELAYED_MAX_AGE:
                return result(DELAYED, f'交易所时间落后约 {_minutes(age)} 分钟')
            if closing:
                return result(STALE, f'收盘竞价尚未结束，行情已超过 {_minutes(age)} 分钟未更新，'
                                     '不是收盘价（可能无成交、停牌或数据源中断）')
            if not trading:
                return result(STALE, f'开盘竞价时段内行情已超过 {_minutes(age)} 分钟未更新（可能数据源中断）')
        if trading:
            hint = '' if clock.confidence(market) == 'calendar' else '、当地休市'
            return result(STALE, f'交易时段内已超过 {_minutes(age)} 分钟未更新（可能无成交、停牌{hint}或数据源中断）')
        if closing:
            return result(STALE, '收盘竞价尚未结束，行情不属于本交易日（可能停牌或数据源未更新）')
    # No trading and no auction under way: only the latest price of the most
    # recent completed session counts.
    end = clock.last_session_end(market)
    day = end.astimezone(zone).date()
    if session != day:
        return result(STALE, '行情时间早于最近一个交易时段（可能停牌或数据源未更新）')
    stopped = quote.quote_time.astimezone(zone).strftime('%H:%M')
    if end == clock.session_end(market, day):
        printed = clock.close_print(market, day)
        latest = clock.close_print_latest(market, day)
        # Only where a later stamp may be an extended-hours trade (New York)
        # does the source's word that a price is the regular session's count,
        # and it lifts only the upper bound: the stamp must still be at the
        # bell (up to ``LIVE_MAX_AGE`` before it, as a 15:59:59 print) or later.
        regular = quote.regular_session and latest is not None
        if latest is not None and quote.quote_time > latest and not regular:
            # New York trades in extended hours after the close: a price
            # stamped later may be one of those trades, not the close.
            local = quote.quote_time.astimezone(zone)
            when = local.strftime('%H:%M:%S') if local.date() == day else local.strftime('%m-%d %H:%M')
            return result(STALE, f'行情时间为当地 {when}，晚于收盘（{printed.astimezone(zone).strftime("%H:%M")}）'
                                 f'{_minutes(latest - printed)} 分钟以上，可能是盘后交易的价格，'
                                 '数据源未说明是常规交易时段的收盘价，不能确认为收盘价')
        settled = clock.close_settled(market, day)
        observed = quote.observed_at or now
        if (latest is not None and settled is not None and observed < settled
                and quote.quote_time >= end - CLOSE_WINDOW):
            # New York: the official close reaches the sources minutes after
            # the bell, so a price seen before it has settled is not final,
            # whatever its stamp or source, however late it is handed out
            # (``QuoteService`` reads it again). A price that had stopped long
            # before the bell is judged below.
            return result(CLOSED, CLOSE_NOT_FINAL.format(settled=settled.astimezone(zone).strftime('%H:%M')),
                          pending=True)
        if latest is None and settled is not None and observed < settled and quote.quote_time >= printed:
            # Read before the close was final (the closing auction under way):
            # the auction may still have changed it.
            return result(CLOSED, SEEN_BEFORE_FINAL.format(final=settled.astimezone(zone).strftime('%H:%M')),
                          pending=True)
        # The day's close is the closing auction's print; a price from before
        # it is the last one the source had, not the close.
        if regular and quote.quote_time < printed - LIVE_MAX_AGE:
            return result(STALE, REGULAR_BEFORE_CLOSE.format(
                when=quote.quote_time.astimezone(zone).strftime('%H:%M:%S'),
                close=printed.astimezone(zone).strftime('%H:%M')))
        if quote.quote_time < printed and not regular:
            return result(STALE, f'行情停在当地时间 {stopped}，早于收盘竞价的成交（当地时间 '
                                 f'{printed.astimezone(zone).strftime("%H:%M")}），不能确认为收盘价'
                                 '（可能收盘价尚未发布、无成交、停牌或数据源中断）')
    elif quote.quote_time < end - CLOSE_WINDOW:
        # A midday break: no auction ends the morning, its last trade is the price.
        return result(STALE, f'行情停在当地时间 {stopped}，早于午间休市前 {_minutes(CLOSE_WINDOW)} 分钟，'
                             '不能确认为上午的最后价格（可能无成交、停牌或数据源中断）')
    else:
        seen = quote.observed_at or now
        if quote.quote_time < end - LIVE_MAX_AGE and seen - quote.quote_time <= DELAYED_MAX_AGE:
            # Stamped before the morning's last minute and seen within the
            # delay bound of its stamp (so before the morning's end plus that
            # bound): a delayed source, as Yahoo's 15 minutes, may still be
            # catching up with the morning. Seen later, it is its last price.
            return result(DELAYED, f'交易所时间落后约 {_minutes(age)} 分钟')
        if seen < end:
            return result(CLOSED, MORNING_SEEN_EARLY.format(final=end.astimezone(zone).strftime('%H:%M')),
                          pending=True)
    if confidence != 'calendar':
        return result(CLOSED, UNVERIFIED_CLOSE)
    return result(CLOSED)


def derive_status(quote: LiveQuote, clock: SessionClock) -> Tuple[str, Optional[str]]:
    """Status and reason for ``quote`` at the clock's current time."""
    verdict = assess(quote, clock)
    return verdict.status, verdict.reason


def derive_fx_status(rate: FxRate, now: datetime) -> Tuple[str, Optional[str]]:
    """Currencies trade around the clock on weekdays, so age alone decides."""
    if rate.rate is None:
        return UNAVAILABLE, rate.reason or '数据源未返回有效汇率'
    if rate.quote_time is None:
        return TIME_UNKNOWN, rate.reason or '数据源未提供汇率时间'
    age = now - rate.quote_time
    if age < -FUTURE_TOLERANCE:
        return TIME_UNKNOWN, '汇率时间晚于本机时钟，无法判断时效'
    if age <= FX_LIVE_MAX_AGE:
        return LIVE, None
    if age <= FX_DELAYED_MAX_AGE:
        return DELAYED, f'汇率时间落后约 {_minutes(age)} 分钟'
    # The weekend (``FX_WEEKEND_CLOSE``), in New York time.
    zone = ZONES['US']
    local = now.astimezone(zone)
    friday = local.date() - timedelta(days=(local.weekday() - 4) % 7)
    shut = datetime.combine(friday, FX_WEEKEND_CLOSE, tzinfo=zone)
    if shut > now:
        friday -= timedelta(days=7)
        shut = datetime.combine(friday, FX_WEEKEND_CLOSE, tzinfo=zone)
    reopen = datetime.combine(friday + timedelta(days=2), FX_WEEKEND_CLOSE, tzinfo=zone)
    if now < reopen and rate.quote_time >= shut - FX_DELAYED_MAX_AGE:
        return CLOSED, None
    return STALE, f'汇率已超过 {_minutes(age)} 分钟未更新'


def normalize_ticker(ticker) -> str:
    return ticker.strip().upper() if isinstance(ticker, str) else str(ticker)


class QuoteService:
    """Batched quotes with a short cache and per-ticker fallthrough.

    ``get`` never raises because of a source and never returns a price a
    source did not supply: when every source fails for a ticker the answer is
    an ``unavailable`` quote, not the previous one. Failures are cached for
    the same short time as successes, and a source that failed as a whole is
    skipped for a growing interval (``refresh=True`` asks it again at once).

    The methods block on network I/O. Call them from a worker thread (a plain
    ``def`` FastAPI route already runs in one) or use ``aget`` / ``afx``.
    """

    def __init__(self, sources: Sequence[QuoteSource], clock: SessionClock, ttl_seconds: float = 5, *,
                 fx_sources: Optional[Sequence[FxSource]] = None, fx_ttl_seconds: float = 60):
        self.sources = list(sources)
        self.fx_sources = list(fx_sources) if fx_sources is not None else [
            source for source in self.sources if isinstance(source, FxSource)]
        self.clock = clock
        self.ttl = timedelta(seconds=ttl_seconds)
        self.fx_ttl = timedelta(seconds=fx_ttl_seconds)
        self._quotes: Dict[str, Tuple[datetime, LiveQuote]] = {}
        self._fx: Dict[str, Tuple[datetime, FxRate]] = {}
        # (source name, 'quotes' | 'fx') -> (failed at, retry at, failures, reason)
        self._down: Dict[Tuple[str, str], Tuple[datetime, datetime, int, str]] = {}
        self._lock = threading.Lock()

    @property
    def source_names(self) -> List[str]:
        return [getattr(source, 'name', type(source).__name__) for source in self.sources]

    # -- quotes --------------------------------------------------------------

    def get(self, tickers: Iterable[str], refresh: bool = False) -> Dict[str, LiveQuote]:
        """A quote for every ticker asked for, keyed and labelled as asked.

        Spellings of one listing (``HKG:700`` and ``HKG:00700``) are fetched
        and cached once, under ``quote_key``; a source is asked in the first
        spelling requested.
        """
        wanted = list(dict.fromkeys(normalize_ticker(ticker) for ticker in tickers))
        keys = {ticker: quote_key(ticker) for ticker in wanted}
        asked: Dict[str, str] = {}
        for ticker in wanted:
            asked.setdefault(keys[ticker], ticker)
        with self._lock:
            now = self.clock.now()
            missing = [ticker for key, ticker in asked.items()
                       if refresh or not self._fresh(self._quotes.get(key), now, self.ttl)
                       or self._final_since(self._quotes[key][1], now)]
            fetched = {keys[ticker]: quote for ticker, quote in (self._fetch(missing, refresh) if missing else {}).items()}
            found = {ticker: fetched[keys[ticker]] if keys[ticker] in fetched else self._quotes[keys[ticker]][1]
                     for ticker in wanted}
            if fetched:
                stored = self.clock.now()
                self._quotes.update({key: (stored, quote) for key, quote in fetched.items()})
                self._prune(self._quotes, stored, self.ttl)
        return {ticker: self._stamp(quote if quote.ticker == ticker else replace(quote, ticker=ticker))
                for ticker, quote in found.items()}

    async def aget(self, tickers: Iterable[str], refresh: bool = False) -> Dict[str, LiveQuote]:
        return await asyncio.to_thread(self.get, list(tickers), refresh)

    def probe(self, ticker: str) -> LiveQuote:
        """One quote fetched now for a check (the setup page), without the service's lock.

        Every source is asked as ``get`` would ask it, but neither the cache
        nor the sources' retry intervals are read or changed, so a slow source
        makes only the probe wait: readers of ``get`` and ``fx`` never queue
        behind it, and it never queues behind them.
        """
        ticker = normalize_ticker(ticker)
        return self._stamp(self._fetch([ticker], force=True, record=False)[ticker])

    @staticmethod
    def _fresh(entry, now: datetime, ttl: timedelta) -> bool:
        return entry is not None and timedelta(0) <= now - entry[0] < ttl

    def _final_since(self, quote: LiveQuote, now: datetime) -> bool:
        """True when ``quote`` was seen before its market's latest close (or morning) became final, and now it is.

        Such a cached quote is read again: the clock passing never turns a
        price seen too early into the verified close.
        """
        observed = quote.observed_at
        if observed is None or quote.price is None:
            return False
        try:
            market = parse(quote.ticker).market
        except InstrumentError:
            return False
        end = self.clock.last_session_end(market)
        day = end.astimezone(ZONES[market]).date()
        final = self.clock.close_settled(market, day) if end == self.clock.session_end(market, day) else end
        return final is not None and observed < final <= now

    @staticmethod
    def _prune(cache: dict, now: datetime, ttl: timedelta) -> None:
        # Entries are useless once expired; drop them so the cache cannot grow
        # with every ticker ever asked for.
        for key in [key for key, (stored, _) in cache.items() if not timedelta(0) <= now - stored < ttl * 20]:
            del cache[key]

    def _stamp(self, quote: LiveQuote) -> LiveQuote:
        verdict = assess(quote, self.clock)
        return replace(quote, status=verdict.status, reason=verdict.reason, session_date=verdict.session_date,
                       calendar_confidence=verdict.calendar_confidence, close_pending=verdict.pending)

    def _ask(self, source, kind: str, call, wanted: tuple, force: bool, record: bool = True):
        """``(answer, error)`` from one source, honouring its retry interval.

        ``record=False`` (``probe``): the source is asked without reading or
        changing its retry interval, which only lock holders may touch.
        """
        key = (getattr(source, 'name', type(source).__name__), kind)
        now = self.clock.now()
        down = self._down.get(key) if record else None
        if down and not force and down[0] <= now < down[1]:
            return {}, f'{down[3]}，{down[1].astimezone(now.tzinfo).strftime("%H:%M:%S")} 后重试'
        try:
            answer = call(wanted) or {}
        except Exception as exc:   # a source must never take the caller down
            # A SourceError carries the source's own fixed text; anything else
            # is classified, never quoted (its class name and message are not ours).
            reason = (str(exc) if isinstance(exc, SourceError) and str(exc)
                      else log_failure(logger, f'quote source {key[0]} ({kind})', exc))
            if record:
                failures = (down[2] if down else 0) + 1
                wait = min(RETRY_AFTER * 2 ** min(failures - 1, 10), RETRY_AFTER_MAX)
                self._down[key] = (now, now + wait, failures, reason)
            return {}, reason
        if record:
            self._down.pop(key, None)
        return answer, None

    # Unverified candidates, best first: a New York close that has not settled,
    # a price that stopped updating, the previous close, no exchange time.
    _CANDIDATE_RANK = {CLOSED: 0, STALE: 1, PREV_CLOSE_ONLY: 2, TIME_UNKNOWN: 3}

    def _settled(self, quote: LiveQuote) -> Tuple[bool, int]:
        """``(final, rank)``: ``final`` when no other source can give a better answer for ``quote``.

        A live, delayed or closed price is final (a close the calendar cannot
        verify is just as unverifiable from another source), except a close
        that has not settled; anything else is a candidate kept while the next
        sources are asked.
        """
        verdict = assess(quote, self.clock)
        final = verdict.status in (LIVE, DELAYED) or (verdict.status == CLOSED and not verdict.pending)
        return final, self._CANDIDATE_RANK.get(verdict.status, len(self._CANDIDATE_RANK))

    def _fetch(self, tickers: List[str], force: bool = False, record: bool = True) -> Dict[str, LiveQuote]:
        """One quote per ticker: the first source's final answer, else the best candidate of every source.

        A usable price that is not final (stale, the previous close, no
        exchange time, or a New York price that may be an extended-hours
        trade) does not end the fallback: every remaining source is asked, and
        the best answer is kept with its own source and time.
        """
        results: Dict[str, LiveQuote] = {}
        failures: Dict[str, List[str]] = {ticker: [] for ticker in tickers}
        partial: Dict[str, LiveQuote] = {}
        candidates: Dict[str, List[Tuple[int, int, LiveQuote]]] = {}
        remaining = []
        for ticker in tickers:
            try:
                instrument = parse(ticker)
            except InstrumentError as exc:
                results[ticker] = unavailable(ticker, 'none', self.clock.now(), f'无法识别的代码：{exc}')
                continue
            if instrument.symbol_problem:
                # Any provider would answer with an index; do not ask one.
                results[ticker] = unavailable(ticker, 'none', self.clock.now(), instrument.symbol_problem,
                                              currency=instrument.currency)
            else:
                remaining.append(ticker)
        for order, source in enumerate(self.sources):
            if not remaining:
                break
            name = getattr(source, 'name', type(source).__name__)
            got, error = self._ask(source, 'quotes', source.fetch, tuple(remaining), force, record)
            observed = self.clock.now()
            for ticker in remaining:
                quote = got.get(ticker)
                if isinstance(quote, LiveQuote) and quote.ticker == ticker:
                    quote = replace(self._sanitize(quote), observed_at=observed)
                    if quote.usable:
                        final, rank = self._settled(quote)
                        if final:
                            results[ticker] = quote
                        else:
                            candidates.setdefault(ticker, []).append((rank, order, quote))
                        continue
                    partial.setdefault(ticker, quote)
                    failures[ticker].append(f'{name}：{quote.reason or "无有效价格"}')
                else:
                    failures[ticker].append(f'{name}：{error or "未返回该代码"}')
            remaining = [ticker for ticker in remaining if ticker not in results]
        for ticker in list(remaining):
            if candidates.get(ticker):
                # The best unverified answer: by status, then the latest exchange time, then source order.
                results[ticker] = min(candidates[ticker], key=lambda entry: (
                    entry[0], -(entry[2].quote_time.timestamp() if entry[2].quote_time else float('-inf')),
                    entry[1]))[2]
                remaining.remove(ticker)
        for ticker in remaining:
            reason = '；'.join(failures[ticker]) or '没有可用的行情源'
            seen = partial.get(ticker)
            try:
                currency = parse(ticker).currency
            except InstrumentError:
                currency = None
            results[ticker] = unavailable(
                ticker, '+'.join(self.source_names) or 'none', self.clock.now(), reason, currency=currency,
                name=seen.name if seen else None, prev_close=seen.prev_close if seen else None)
        return results

    @staticmethod
    def _sanitize(quote: LiveQuote) -> LiveQuote:
        """Re-apply the invariants to whatever a source returned."""
        return build_quote(quote.ticker, quote.source, quote.fetched_at, price=quote.price,
                           prev_close=quote.prev_close, name=quote.name, open=quote.open, high=quote.high,
                           low=quote.low, volume=quote.volume, currency=quote.currency,
                           quote_time=quote.quote_time, reason=quote.reason, price_basis=quote.price_basis,
                           regular_session=quote.regular_session)

    # -- currencies ----------------------------------------------------------

    def fx(self, refresh: bool = False) -> Dict[str, FxRate]:
        """CNY per USD and per HKD, each with its source, time and status."""
        with self._lock:
            now = self.clock.now()
            missing = [currency for currency in FX_CURRENCIES
                       if refresh or not self._fresh(self._fx.get(currency), now, self.fx_ttl)]
            fetched = self._fetch_fx(missing, refresh) if missing else {}
            found = {currency: fetched[currency] if currency in fetched else self._fx[currency][1]
                     for currency in FX_CURRENCIES}
            stored = self.clock.now()
            self._fx.update({currency: (stored, rate) for currency, rate in fetched.items()})
        now = self.clock.now()
        stamped = {}
        for currency, rate in found.items():
            status, reason = derive_fx_status(rate, now)
            stamped[currency] = replace(rate, status=status, reason=reason)
        return stamped

    async def afx(self, refresh: bool = False) -> Dict[str, FxRate]:
        return await asyncio.to_thread(self.fx, refresh)

    def _fetch_fx(self, currencies: List[str], force: bool = False) -> Dict[str, FxRate]:
        """One rate per currency: the first source's verified rate (live, delayed or the weekend's), else the
        latest unverified one any source gave, with its own source and time, as quotes fall through."""
        results: Dict[str, FxRate] = {}
        failures: Dict[str, List[str]] = {currency: [] for currency in currencies}
        candidates: Dict[str, List[Tuple[float, int, FxRate]]] = {}
        remaining = list(currencies)
        for order, source in enumerate(self.fx_sources):
            if not remaining:
                break
            name = getattr(source, 'name', type(source).__name__)
            got, error = self._ask(source, 'fx', source.fetch_fx, tuple(remaining), force)
            for currency in remaining:
                rate = got.get(currency)
                if isinstance(rate, FxRate) and rate.currency == currency:
                    rate = build_fx(currency, rate.source, rate.fetched_at, rate=rate.rate,
                                    quote_time=rate.quote_time, reason=rate.reason)
                    if rate.rate is not None:
                        if derive_fx_status(rate, self.clock.now())[0] in (LIVE, DELAYED, CLOSED):
                            results[currency] = rate
                        else:   # stale or without a time: kept while the next sources are asked
                            stamp = rate.quote_time.timestamp() if rate.quote_time else float('-inf')
                            candidates.setdefault(currency, []).append((-stamp, order, rate))
                        continue
                    failures[currency].append(f'{name}：{rate.reason or "无有效汇率"}')
                else:
                    failures[currency].append(f'{name}：{error or "未返回该币种"}')
            remaining = [currency for currency in remaining if currency not in results]
        for currency in list(remaining):
            if candidates.get(currency):
                results[currency] = min(candidates[currency], key=lambda entry: entry[:2])[2]
                remaining.remove(currency)
        names = '+'.join(getattr(source, 'name', type(source).__name__) for source in self.fx_sources)
        for currency in remaining:
            results[currency] = build_fx(currency, names or 'none', self.clock.now(),
                                         reason='；'.join(failures[currency]) or '没有可用的汇率源')
        return results
