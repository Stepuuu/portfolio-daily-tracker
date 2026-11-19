"""Trading sessions in exchange-local time.

``SessionClock`` answers "is this market trading now, and how sure are we?".
Each market's years in the shipped table (``holidays.json``: the mainland,
Hong Kong and New York, see the file for which years and their sources) are
decided by that table, including the days a market closes early (Hong Kong's
half days, New York's 13:00 closes). A year that is not in the table is
decided by weekday alone and reported as ``weekday_only`` so callers can say
so instead of pretending; an early close in such a year is not known either.
Unscheduled closures (an exchange outage, say) are never in the table:
callers must not take "the calendar says open" as "trading". Hong Kong no
longer closes for typhoons or black rainstorms (HKEX Severe Weather Trading,
since 23 September 2024).

All datetimes handed out are timezone-aware in Asia/Shanghai.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from .instruments import CN, HK, US, MARKETS



class _USEastern(tzinfo):
    """New York time by the rule in force since 2007.

    Used only when the host has no usable time zone database. It is exact
    away from the two changeover nights, which is all a 09:30-16:00 session
    needs.
    """

    def dst(self, moment):
        if moment is None:
            return timedelta(0)
        naive = moment.replace(tzinfo=None)
        march, november = datetime(naive.year, 3, 8), datetime(naive.year, 11, 1)
        start = march + timedelta(days=(6 - march.weekday()) % 7, hours=2)          # second Sunday of March
        end = november + timedelta(days=(6 - november.weekday()) % 7, hours=2)      # first Sunday of November
        return timedelta(hours=1) if start <= naive < end else timedelta(0)

    def utcoffset(self, moment):
        return timedelta(hours=-5) + self.dst(moment)

    def tzname(self, moment):
        return 'EDT' if self.dst(moment) else 'EST'


def _zone(key: str, fallback: tzinfo) -> tzinfo:
    """The named zone, or ``fallback`` where the tz database is missing or broken.

    Importing this module must not fail on such a host: that would take the
    whole API down with it.
    """
    try:
        return ZoneInfo(key)
    except Exception:
        return fallback


_UTC8 = timezone(timedelta(hours=8), 'UTC+08:00')   # neither Shanghai nor Hong Kong observes daylight saving
SHANGHAI = _zone('Asia/Shanghai', _UTC8)
ZONES = {CN: SHANGHAI, HK: _zone('Asia/Hong_Kong', _UTC8), US: _zone('America/New_York', _USEastern())}

# Continuous trading only, as (start, end, phase), end exclusive. Hong Kong's
# lunch break and closing auction and US extended hours count as closed.
SESSIONS = {
    CN: ((time(9, 30), time(11, 30), 'morning'), (time(13, 0), time(15, 0), 'afternoon')),
    HK: ((time(9, 30), time(12, 0), 'open'), (time(13, 0), time(16, 0), 'open')),
    US: ((time(9, 30), time(16, 0), 'open'),),
}
CN_AUCTION_START = time(9, 15)
# When a session's first price can appear: the mainland call auction, Hong
# Kong's pre-opening session, the New York open. Earlier on a trading day the
# latest prices still belong to the previous session.
FIRST_PRICE = {CN: CN_AUCTION_START, HK: time(9, 0), US: time(9, 30)}
TRADING_PHASES = frozenset({'morning', 'afternoon', 'open'})
# How long after continuous trading ends the day's closing price can still
# change, from the exchanges' trading-hours pages: the mainland closing call
# auction (14:57-15:00) is inside the afternoon session, so nothing; Hong
# Kong's closing auction session runs from 16:00 (12:00 on a half day) to a
# random close between 8 and 10 minutes later; New York's closing auctions
# run at 16:00 (13:00 on an early-close day).
CLOSING_AUCTION = {CN: timedelta(0), HK: timedelta(minutes=10), US: timedelta(0)}
# How long after a close is final (``SessionClock.close_final``) its published
# price is taken as the official close, where the providers cannot be relied on
# to have it sooner. New York's official closing prices reach the providers
# minutes after the bell: on 2026-11-18, 12 minutes after it, Eastmoney and
# Yahoo both still showed prices that were not the official closes (stamped
# 15:59:59-16:00:01), and 100 minutes after it both showed them. Daily bars
# are published after the close too: ``backtesting.data.store``'s
# ``PUBLICATION_MARGIN`` (when a daily bar is final) is this constant.
CLOSE_SETTLE = timedelta(minutes=30)
# Which exchange times can be the day's closing print, and when it is final,
# as offsets from the end of continuous trading: ``(earliest, latest,
# settle)``. This is the one table of these constants (``quotes.assess``
# explains the rule; measured stamps are in its docstring).
#
# earliest  the mainland closing call auction and New York's closing auctions
#           match at the session end itself; Hong Kong's closing auction
#           session matches at a random moment from 8 minutes after it.
# latest    ``None`` where no price but the close can carry a later stamp: the
#           mainland (its after-hours fixed-price trading prints at the close;
#           Eastmoney stamps the close 15:34 for Shenzhen, 16:11 for Shanghai)
#           and Hong Kong (no extended hours; both providers stamp 16:08:02-03).
#           New York trades in extended hours until 20:00, so a later stamp may
#           be an extended-hours price: a source must say the price is the
#           regular session's (``LiveQuote.regular_session``) to be taken as
#           the close beyond this bound. Ten minutes: once final, Eastmoney
#           stamps the close exactly 16:00:00 and Yahoo 16:00:00-16:00:39
#           (observed 2026-11-18 during after-hours trading).
# settle    New York only: before ``close_final`` plus ``CLOSE_SETTLE`` (16:30,
#           13:30 on an early-close day) no price is a verified close, whatever
#           its stamp or source. The mainland and Hong Kong rules are their own
#           (the close counts once the auction has ended).
CLOSE_PRINT_WINDOW = {
    CN: (timedelta(0), None, None),
    HK: (timedelta(minutes=8), None, None),
    US: (timedelta(0), timedelta(minutes=10), CLOSE_SETTLE),
}
# The earliest offsets alone, for callers that only need them.
CLOSE_PRINT = {market: window[0] for market, window in CLOSE_PRINT_WINDOW.items()}
HOLIDAY_FILE = Path(__file__).with_name('holidays.json')
_SEARCH_DAYS = 40   # longer than any exchange closure


Holidays = Dict[str, Dict[int, FrozenSet[date]]]
EarlyCloses = Dict[str, Dict[date, time]]
_YEAR_KEYS = frozenset({'closed', 'early_close', 'source'})


def load_calendar(path: Optional[Path] = None) -> Tuple[Holidays, EarlyCloses]:
    """``(closures, early closes)`` from the table at ``path``; reject a malformed table.

    The file maps ``{market: {year: entry}}``; keys starting with ``_`` are
    comments. An entry is either a list of the weekdays the exchange is closed
    (``YYYY-MM-DD``) or an object::

        {"closed": [...], "early_close": {"YYYY-MM-DD": "HH:MM"}, "source": {...}}

    where ``early_close`` gives the local time continuous trading ends on a
    shortened trading day and ``source`` records where the year was copied
    from (not read here). Closures are ``{market: {year: frozenset(dates)}}``,
    early closes ``{market: {date: time}}``.
    """
    raw = json.loads(Path(path or HOLIDAY_FILE).read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('holiday table: not an object')
    closures: Holidays = {}
    early: EarlyCloses = {}
    for market, years in raw.items():
        if market.startswith('_'):
            continue
        if market not in MARKETS or not isinstance(years, dict):
            raise ValueError(f'holiday table: unknown market {market!r}')
        closures[market] = {}
        for year, entry in years.items():
            where = f'holiday table: {market} {year}'
            number = int(year)
            if isinstance(entry, dict):
                if set(entry) - _YEAR_KEYS or not isinstance(entry.get('closed'), list):
                    raise ValueError(f'{where}: expected "closed" and optionally "early_close" and "source"')
                if not isinstance(entry.get('early_close', {}), dict) or not isinstance(entry.get('source', {}), dict):
                    raise ValueError(f'{where}: "early_close" and "source" must be objects')
                days, shortened = entry['closed'], entry.get('early_close', {})
            elif isinstance(entry, list):
                days, shortened = entry, {}
            else:
                raise ValueError(f'{where}: expected a list of dates or an object')
            parsed = frozenset(date.fromisoformat(day) for day in days)
            if any(day.year != number for day in parsed):
                raise ValueError(f'{where} lists a date from another year')
            closures[market][number] = parsed
            for text, close_text in shortened.items():
                if not isinstance(close_text, str):
                    raise ValueError(f'{where}: early close {text} must be "HH:MM"')
                day, close = date.fromisoformat(text), time.fromisoformat(close_text)
                if day.year != number or day.weekday() >= 5 or day in parsed:
                    raise ValueError(f'{where}: early close {text} is not a trading weekday of that year')
                if not SESSIONS[market][0][0] < close < SESSIONS[market][-1][1]:
                    raise ValueError(f'{where}: early close {text} {close_text} is outside the trading day')
                early.setdefault(market, {})[day] = close
    return closures, early


def load_holidays(path: Optional[Path] = None) -> Holidays:
    """The closures of ``load_calendar``: ``{market: {year: frozenset(dates)}}``."""
    return load_calendar(path)[0]


def load_early_closes(path: Optional[Path] = None) -> EarlyCloses:
    """The early closes of ``load_calendar``: ``{market: {date: local closing time}}``."""
    return load_calendar(path)[1]


class SessionClock:
    """Sessions and trading days per market.

    ``holidays`` is a closures table as ``load_holidays`` returns it and
    ``early_closes`` one as ``load_early_closes`` returns it. Without
    ``holidays`` both come from the shipped table; a caller that hands in its
    own closures gets no early closes unless it hands those in as well.
    """

    def __init__(self, now_fn: Optional[Callable[[], datetime]] = None, holidays: Optional[Holidays] = None,
                 early_closes: Optional[Mapping[str, Mapping[date, time]]] = None):
        self._now_fn = now_fn or (lambda: datetime.now(SHANGHAI))
        if holidays is None:
            holidays, shipped = load_calendar()
            early_closes = shipped if early_closes is None else early_closes
        self._holidays = holidays
        self._early_closes = early_closes or {}

    # -- time ----------------------------------------------------------------

    def now(self) -> datetime:
        return self._aware(self._now_fn())

    @staticmethod
    def _aware(moment: datetime) -> datetime:
        # A naive value can only have come from a careless caller; read it as
        # Shanghai wall time rather than as the host's zone.
        if moment.tzinfo is None:
            return moment.replace(tzinfo=SHANGHAI)
        return moment.astimezone(SHANGHAI)

    def _local(self, market: str, at: Optional[datetime]) -> datetime:
        if market not in ZONES:
            raise ValueError(f'unknown market {market!r}')
        return (self.now() if at is None else self._aware(at)).astimezone(ZONES[market])

    def local_date(self, market: str = CN, at: Optional[datetime] = None) -> date:
        """Today's date on the exchange's own wall clock."""
        return self._local(market, at).date()

    # -- days ----------------------------------------------------------------

    def table_years(self, market: str = CN) -> list:
        """The years the holiday table decides for ``market``, ascending (others are ``weekday_only``)."""
        return sorted(self._holidays.get(market, {}))

    def confidence(self, market: str = CN, day: Optional[date] = None) -> str:
        """``'calendar'`` when the year's holidays are known, else ``'weekday_only'``."""
        day = day or self.local_date(market)
        return 'calendar' if day.year in self._holidays.get(market, {}) else 'weekday_only'

    def is_trading_day(self, market: str = CN, day: Optional[date] = None) -> bool:
        day = day or self.local_date(market)
        if day.weekday() >= 5:
            return False
        return day not in self._holidays.get(market, {}).get(day.year, frozenset())

    def previous_trading_day(self, market: str = CN, day: Optional[date] = None) -> date:
        """The last trading day strictly before ``day``."""
        day = day or self.local_date(market)
        for _ in range(_SEARCH_DAYS):
            day -= timedelta(days=1)
            if self.is_trading_day(market, day):
                return day
        raise RuntimeError('no trading day found; the holiday table is implausible')

    def current_trading_day(self, market: str = CN, at: Optional[datetime] = None) -> date:
        """Today when it is a trading day, otherwise the one before."""
        day = self.local_date(market, at)
        return day if self.is_trading_day(market, day) else self.previous_trading_day(market, day)

    def session_of(self, market: str = CN, at: Optional[datetime] = None) -> date:
        """The trading day whose prices are current at ``at`` (default: now).

        That is the exchange-local day itself once its first price can have
        appeared (``FIRST_PRICE``), and otherwise the trading day before: at
        08:00 on a Monday the latest mainland prices are Friday's. Applied to
        a quote's exchange time it names the session the quote belongs to.
        """
        local = self._local(market, at)
        day = local.date()
        if self.is_trading_day(market, day) and local.time() >= FIRST_PRICE[market]:
            return day
        return self.previous_trading_day(market, day)

    def in_opening_auction(self, market: str = CN, at: Optional[datetime] = None) -> bool:
        """True between a session's first price and its continuous trading."""
        local = self._local(market, at)
        if not self.is_trading_day(market, local.date()):
            return False
        return FIRST_PRICE[market] <= local.time() < SESSIONS[market][0][0]

    def in_closing_auction(self, market: str = CN, at: Optional[datetime] = None) -> bool:
        """True after continuous trading has ended and before the closing price is final (``close_final``)."""
        local = self._local(market, at)
        end, final = self.session_end(market, local.date()), self.close_final(market, local.date())
        return end is not None and end <= local < final

    # -- sessions ------------------------------------------------------------

    def early_close(self, market: str = CN, day: Optional[date] = None) -> Optional[time]:
        """The local time continuous trading stops on ``day`` when the table shortens it, else ``None``."""
        day = day or self.local_date(market)
        return self._early_closes.get(market, {}).get(day)

    def sessions(self, market: str = CN, day: Optional[date] = None) -> Tuple[Tuple[time, time, str], ...]:
        """``day``'s continuous sessions as ``(start, end, phase)``, end exclusive.

        Empty on a day the exchange is closed; on an early-close day the
        sessions stop at the early close (Hong Kong's half days keep only the
        morning session).
        """
        day = day or self.local_date(market)
        if not self.is_trading_day(market, day):
            return ()
        close = self.early_close(market, day)
        if close is None:
            return SESSIONS[market]
        return tuple((start, min(end, close), name) for start, end, name in SESSIONS[market] if start < close)

    def session_end(self, market: str = CN, day: Optional[date] = None) -> Optional[datetime]:
        """When continuous trading ends on ``day`` (default: today), or ``None`` on a closed day."""
        bounds = self._session_bounds(market, day or self.local_date(market))
        return bounds[-1][1].astimezone(SHANGHAI) if bounds else None

    def close_final(self, market: str = CN, day: Optional[date] = None) -> Optional[datetime]:
        """When ``day``'s closing price can no longer change: the session end plus the closing auction.

        ``None`` on a day the exchange is closed.
        """
        end = self.session_end(market, day)
        return end + CLOSING_AUCTION[market] if end is not None else None

    def close_print(self, market: str = CN, day: Optional[date] = None) -> Optional[datetime]:
        """The earliest moment ``day``'s closing price can have been printed (``CLOSE_PRINT``).

        A price stamped before it is not the close. ``None`` on a day the exchange is closed.
        """
        end = self.session_end(market, day)
        return end + CLOSE_PRINT[market] if end is not None else None

    def close_print_latest(self, market: str = CN, day: Optional[date] = None) -> Optional[datetime]:
        """The latest exchange time that can still be ``day``'s closing print (``CLOSE_PRINT_WINDOW``).

        ``None`` when the market has no such bound, or the exchange is closed that day.
        """
        end, latest = self.session_end(market, day), CLOSE_PRINT_WINDOW[market][1]
        return end + latest if end is not None and latest is not None else None

    def close_settled(self, market: str = CN, day: Optional[date] = None) -> Optional[datetime]:
        """When ``day``'s closing price, as the providers publish it, is final: the one answer to that question.

        ``close_final`` plus the market's settle delay (``CLOSE_PRINT_WINDOW``):
        New York 16:30 (13:30 on an early-close day), ``CLOSE_SETTLE`` after
        its bell; the mainland and Hong Kong at ``close_final`` itself. The
        quote status (``quotes.assess``), the close snapshot and the agent's
        price history take a close as final from this moment. ``None`` on a
        day the exchange is closed.
        """
        final, settle = self.close_final(market, day), CLOSE_PRINT_WINDOW[market][2]
        return final + settle if final is not None and settle is not None else final

    def phase(self, market: str = CN, at: Optional[datetime] = None) -> str:
        """Where the market is in its day.

        CN: ``pre_open`` (before 09:15), ``call_auction`` (09:15-09:30),
        ``morning``, ``lunch_break``, ``afternoon``, ``closed``.
        HK and US: ``open`` or ``closed``. After an early close: ``closed``.
        """
        local = self._local(market, at)
        sessions = self.sessions(market, local.date())
        if not sessions:
            return 'closed'
        moment = local.time()
        for start, end, name in sessions:
            if start <= moment < end:
                return name
        if market != CN or moment >= sessions[-1][1]:
            return 'closed'
        if moment < CN_AUCTION_START:
            return 'pre_open'
        if moment < SESSIONS[CN][0][0]:
            return 'call_auction'
        return 'lunch_break'

    def is_trading(self, market: str = CN, at: Optional[datetime] = None) -> bool:
        """True during continuous trading."""
        return self.phase(market, at) in TRADING_PHASES

    def _session_bounds(self, market: str, day: date) -> Tuple[Tuple[datetime, datetime], ...]:
        zone = ZONES[market]
        return tuple((datetime.combine(day, start, tzinfo=zone), datetime.combine(day, end, tzinfo=zone))
                     for start, end, _ in self.sessions(market, day))

    def next_open(self, market: str = CN, at: Optional[datetime] = None) -> datetime:
        """Start of the next continuous session strictly after ``at``."""
        local = self._local(market, at)
        day = local.date()
        for _ in range(_SEARCH_DAYS):
            for start, _end in self._session_bounds(market, day):
                if start > local:
                    return start.astimezone(SHANGHAI)
            day += timedelta(days=1)
        raise RuntimeError('no session found; the holiday table is implausible')

    def last_session_end(self, market: str = CN, at: Optional[datetime] = None) -> datetime:
        """End of the most recent continuous session that has finished by ``at`` (early closes included)."""
        local = self._local(market, at)
        day = local.date()
        for _ in range(_SEARCH_DAYS):
            for _start, end in reversed(self._session_bounds(market, day)):
                if end <= local:
                    return end.astimezone(SHANGHAI)
            day -= timedelta(days=1)
        raise RuntimeError('no session found; the holiday table is implausible')
