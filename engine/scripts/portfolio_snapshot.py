#!/usr/bin/env python3
"""Portfolio snapshot engine: fetch prices → calculate metrics → save daily snapshot.

Usage:
    python3 portfolio_snapshot.py [--date YYYY-MM-DD] [--dry-run]
"""

import json, os, sys, argparse, glob, copy, hashlib, math, time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import requests
from portfolio_write_lock import LockUnavailable, portfolio_write_lock
from portfolio_accounting import atomic_text_writer, atomic_json_dump, cash_balances, finite_number, fx_rate, value_cash, ordered_history, drawdowns, return_pct, percent_text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_DIR = os.environ.get("PORTFOLIO_DIR", os.path.join(os.path.dirname(BASE_DIR), "portfolio"))
CONFIG_PATH = os.path.join(PORTFOLIO_DIR, "config.json")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def load_holdings(date_str, config, *, persist=True):
    """Load holdings for a date. If missing, copy from previous day."""
    from ledger_bridge import ledger_holdings
    confirmed = ledger_holdings(PORTFOLIO_DIR, date_str, exact=True)
    if confirmed is not None:
        return confirmed
    path = os.path.join(PORTFOLIO_DIR, "holdings", f"{date_str}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)

    # Find most recent previous holdings
    holdings_dir = os.path.join(PORTFOLIO_DIR, "holdings")
    files = sorted(glob.glob(os.path.join(holdings_dir, "*.json")))
    prev_file = None
    for f in reversed(files):
        fname = os.path.basename(f).replace(".json", "")
        if fname < date_str:
            prev_file = f
            break

    if prev_file:
        with open(prev_file, "r") as f:
            data = json.load(f)
        data["date"] = date_str
        data["updated_at"] = datetime.now().isoformat()
        # Save as today's file
        if persist:
            with atomic_text_writer(path) as f:
                json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        print(f"  从 {os.path.basename(prev_file)} 复制持仓到 {date_str}")
        return data
    else:
        print(f"  ❌ 没有找到任何持仓文件", file=sys.stderr)
        return None


def load_previous_snapshot(date_str):
    """Load the most recent snapshot before given date."""
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
    files = sorted(glob.glob(os.path.join(snap_dir, "*.json")))
    for f in reversed(files):
        fname = os.path.basename(f).replace(".json", "")
        if fname < date_str:
            with open(f, "r") as fh:
                return json.load(fh)
    return None


def usable_price(value):
    """A fetched price, or None unless it is a finite positive number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return value


# The currency a listing is quoted in, by exchange; Shanghai and Shenzhen B shares are quoted in USD
# and HKD (the dashboard's ``core.market.instruments`` uses the same prefixes).
EXCHANGE_CURRENCIES = {"SHA": "CNY", "SHE": "CNY", "HKG": "HKD", "NASDAQ": "USD", "NYSE": "USD"}
B_SHARE_PREFIXES = {"SHA": ("900",), "SHE": ("200", "201")}
B_SHARE_CURRENCIES = {"SHA": "USD", "SHE": "HKD"}
# Where each exchange's dates are counted, for the session of a quote.
EXCHANGE_ZONES = {"SHA": "Asia/Shanghai", "SHE": "Asia/Shanghai", "HKG": "Asia/Hong_Kong",
                  "NASDAQ": "America/New_York", "NYSE": "America/New_York"}


def expected_currency(ticker):
    """The currency ``ticker`` is quoted in, or None for an exchange this engine does not know."""
    exchange, _, code = str(ticker).partition(":")
    if len(code) == 6 and code.isdigit() and code.startswith(B_SHARE_PREFIXES.get(exchange, ())):
        return B_SHARE_CURRENCIES[exchange]
    return EXCHANGE_CURRENCIES.get(exchange)


def session_date(ticker, unix_seconds):
    """The exchange-local date (``YYYY-MM-DD``) of a quote's exchange time in Unix seconds, or None."""
    if (isinstance(unix_seconds, bool) or not isinstance(unix_seconds, (int, float))
            or not math.isfinite(unix_seconds) or unix_seconds <= 0):
        return None
    from datetime import timezone
    exchange = str(ticker).partition(":")[0]
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(EXCHANGE_ZONES.get(exchange, "Asia/Shanghai"))
    except Exception:   # no time zone database: the exchange's standard offset
        zone = timezone(timedelta(hours=-5 if exchange in ("NASDAQ", "NYSE") else 8))
    return datetime.fromtimestamp(unix_seconds, timezone.utc).astimezone(zone).date().isoformat()


# When each market's closing price is final, in exchange-local time, for an engine without the dashboard's
# calendar (the engine-only image): what core.market.calendar.SessionClock.close_settled answers on a normal
# day, the end of continuous trading plus the closing auction, and for New York 30 minutes more
# (calendar.CLOSE_SETTLE: its official closing prices reach the providers minutes after the bell). An early
# close is not known here, so such a day waits until the normal time: later, never too early.
STANDARD_CLOSE_FINAL = {"SHA": (15, 0), "SHE": (15, 0), "HKG": (16, 10), "NASDAQ": (16, 30), "NYSE": (16, 30)}
CALENDAR_MARKETS = {"SHA": "CN", "SHE": "CN", "HKG": "HK", "NASDAQ": "US", "NYSE": "US"}


def _now():
    """The moment quotes are fetched (aware, UTC)."""
    from datetime import timezone
    return datetime.now(timezone.utc)


def close_final_at(ticker, day):
    """When the close of ``ticker``'s session dated ``day`` (exchange-local ``YYYY-MM-DD``) is final as published.

    The dashboard's trading calendar decides, as it does for the live quotes and the post-market brief
    (``SessionClock.close_settled``: the session end, early closes included, plus the closing auction, plus
    New York's settle delay); without it, ``STANDARD_CLOSE_FINAL``. An aware datetime, or None for an
    exchange this engine does not know.
    """
    from datetime import date as Date, time as Time, timezone
    exchange = str(ticker).partition(":")[0]
    if exchange not in STANDARD_CLOSE_FINAL:
        return None
    session = Date.fromisoformat(day)
    try:
        from core.market.calendar import SessionClock
        final = SessionClock().close_settled(CALENDAR_MARKETS[exchange], session)
        if final is not None:
            return final
    except Exception:   # the engine-only image has no calendar; a closed day in the table: the standard time
        pass
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(EXCHANGE_ZONES[exchange])
    except Exception:
        zone = timezone(timedelta(hours=-5 if exchange in ("NASDAQ", "NYSE") else 8))
    return datetime.combine(session, Time(*STANDARD_CLOSE_FINAL[exchange]), tzinfo=zone)


# What the snapshot prints last when the only prices missing are closes not final yet: when the last of them
# becomes final and whose markets they are. The close snapshot routine (dashboard/core/routines/close_snapshot.py)
# then waits until that moment instead of failing. When a closing print may still reach the source before
# then, it adds ``CHECK_AT=<moment>``: the routine looks again at that moment (``CLOSE_RECHECK``).
CLOSE_WAIT_MARKER = "CLOSE_NOT_FINAL_UNTIL="
CLOSE_CHECK_MARKER = "CHECK_AT="
MARKET_NAMES = {"CN": "A股", "HK": "港股", "US": "美股"}
SHANGHAI_ZONE = timezone(timedelta(hours=8))   # China keeps no daylight saving time
# While a price waits for its closing print, the snapshot looks again on the clock's five-minute marks: the
# print reaches the source minutes after it is made (Yahoo's usual delay is 15 minutes: Hong Kong's 16:08 print
# at 16:23), and the close is booked at the next mark (16:25), not only once the wait has run out (16:28).
CLOSE_RECHECK = timedelta(minutes=5)


def _next_look(moment):
    """The clock's first ``CLOSE_RECHECK`` mark after ``moment`` (16:10:20 -> 16:15:00), aware UTC."""
    step = int(CLOSE_RECHECK.total_seconds())
    return datetime.fromtimestamp((int(moment.timestamp()) // step + 1) * step, timezone.utc)


def close_wait_reason(markets, until, date_str, latest=False):
    """``等待港股收盘价确定，16:10 后记入``: Shanghai time, ``次日`` when it falls after ``date_str``.

    With ``latest`` (the wait looks again before ``until``): ``等待港股收盘价确定，最晚 16:28 记入``. The same
    words as the close snapshot routine's wait (``close_snapshot.wait_reason``).
    """
    local = until.astimezone(SHANGHAI_ZONE)
    names = "、".join(MARKET_NAMES[market] for market in ("CN", "HK", "US") if market in markets)
    day = "次日 " if local.date().isoformat() > str(date_str) else ""
    if latest:
        return f"等待{names}收盘价确定，最晚 {day}{local:%H:%M} 记入"
    return f"等待{names}收盘价确定，{day}{local:%H:%M} 后记入"


def _quote_check(ticker, meta, date_str, now, quote=None):
    """``(problem or None, moment to wait for when waiting is the only problem, else None,
    why the booked price is an estimate, else None, when to look again before that moment, else None)``.

    Only a wait for a closing print has a moment to look again (``CLOSE_RECHECK``); a wait for a close that is
    not final yet has nothing to look at before it is. ``quote``: another source's quote (Tencent's,
    ``_tencent_meta``), judged in place of the one Yahoo's ``meta`` makes; ``meta`` then holds its price,
    currency and time."""
    if usable_price(meta.get("regularMarketPrice")) is None:
        return "no usable regularMarketPrice", None, None, None
    currency = meta.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        return "no currency", None, None, None
    expected = expected_currency(ticker)
    if expected is not None and currency.strip().upper() != expected:
        return f"quoted in {currency.strip()[:8]}, not {expected}", None, None, None
    day = session_date(ticker, meta.get("regularMarketTime"))
    if day is None:
        return "no exchange time", None, None, None
    if date_str is not None and day > date_str:
        return f"a price of the {day} session, after {date_str}", None, None, None
    final = close_final_at(ticker, day)
    moment = now or _now()
    if final is not None and moment < final:
        return (f"a price of the {day} session, which is still trading or whose closing price is not final yet "
                f"(final at {final.isoformat(timespec='minutes')}): not a close"), final, None, None
    return _close_verdict(ticker, meta, date_str, moment, quote)


# Why a holding's price is its last close although its market has had a session since (Today: 估算).
NOT_TRADED = "{latest} 没有新的成交价（可能停牌或数据源未更新），按 {session} 的收盘价记入"
# Why a holding's price is the day's last trade, stamped before the closing print, once the wait for the print
# has run out: no trade in the closing auction (a thinly traded stock) or a source that does not restamp its
# close. Today shows such a price as stale, an estimate.
NO_CLOSING_PRINT = "收盘竞价后没有新的成交时间，按当日最后成交价记入"


def _judging_clock(market, date_str, moment):
    """The session clock a quote of ``market`` is judged by: at ``moment``, but no later than the end of
    ``date_str`` on the exchange's clock (``_close_verdict``)."""
    import ledger_bridge  # noqa: F401  (puts the dashboard package, or the image's core/, on the path)
    from datetime import date as Date, time as Time
    from core.market.calendar import ZONES, SessionClock
    when = moment
    if date_str is not None:
        day_end = datetime.combine(Date.fromisoformat(date_str) + timedelta(days=1), Time(0), tzinfo=ZONES[market])
        when = min(moment, day_end - timedelta(seconds=1))
    return SessionClock(lambda: when)


def _close_verdict(ticker, meta, date_str, moment, quote=None):
    """Today's decision table (``core.market.quotes.assess``) for a quote whose own session's close is final.

    The quote is Yahoo's ``meta`` read as ``core.market.sources_yahoo`` reads it, or ``quote`` when given (another
    source's, read by Today's reading of that source).

    The same ``(problem, wait until, estimate, look again)`` as ``_quote_check``. The quote is judged as Today
    would judge it at ``moment``, but no later than the end of ``date_str`` on the exchange's clock, so a run
    after that day still books the day's closes:

    * the market's latest session (on or before ``date_str``) still under way or its close not final: wait
      until it is (``close_final_at``), whatever the quote's own session;
    * a close Today verifies: booked; one of a year the holiday table does not cover: booked, an estimate;
    * the latest session passed without a price for the holding (suspended, or the source has none, which
      Today calls stale and shows as an estimate): its last close is booked, an estimate (``NOT_TRADED``);
    * stale otherwise (a price of the latest session stamped before its closing print, such as Hong Kong at
      15:55 seen at 16:10, or New York's regular-session price more than a minute before the bell, such as a
      stalled 14:42): not booked yet; it waits until the print plus the sources' usual delay
      (``quotes.CLOSE_WINDOW``), looking again on the way (``CLOSE_RECHECK``: the print can reach the
      source any minute, and is then the close). After that no later time will come (no trade in the closing
      auction, or a source that does not restamp its close): the day's last price is booked, an estimate
      (``NO_CLOSING_PRINT``), as Today shows it;
    * anything else Today does not take for a price: not booked.
    """
    import ledger_bridge  # noqa: F401  (puts the dashboard package, or the image's core/, on the path)
    from core.market import quotes
    from core.market.instruments import InstrumentError, parse
    try:
        instrument = parse(ticker)
    except InstrumentError:
        return "a code the trading calendar does not know", None, None, None
    market = instrument.market
    clock = _judging_clock(market, date_str, moment)
    latest = clock.session_of(market)
    final = close_final_at(ticker, latest.isoformat())
    if final is not None and moment < final:
        return (f"the {latest} session is under way or its closing price is not final yet "
                f"(final at {final.isoformat(timespec='minutes')}): no close of it yet"), final, None, None
    if quote is None:
        quote = quotes.build_quote(
            ticker, "yahoo", moment, price=meta.get("regularMarketPrice"), prev_close=meta.get("chartPreviousClose"),
            high=meta.get("regularMarketDayHigh"), low=meta.get("regularMarketDayLow"),
            volume=meta.get("regularMarketVolume"), currency=instrument.currency,
            quote_time=quotes.from_epoch(meta.get("regularMarketTime")),
            # regularMarketPrice / regularMarketTime are the regular session's (as in core.market.sources_yahoo).
            regular_session=True)
    # Seen now: only an observation made once the close is final verifies it (``LiveQuote.observed_at``).
    verdict = quotes.assess(replace(quote, observed_at=moment), clock)
    session = verdict.session_date
    if verdict.pending:
        # Seen before the close was final: not the close yet (the engine has waited, so this is a safeguard).
        settled = close_final_at(ticker, (session or latest).isoformat())
        return verdict.reason, (settled if settled is not None and moment < settled else None), None, None
    if verdict.status == quotes.CLOSED:
        return None, None, verdict.reason, None      # a reason only for a close the calendar cannot verify
    if verdict.status == quotes.PREV_CLOSE_ONLY or (
            verdict.status == quotes.STALE and session is not None and session < latest):
        closed = session or latest
        if quote.price_basis == quotes.PREV_CLOSE:
            # Untraded in its session (Tencent's reading): the price is the close of the session before.
            closed = clock.previous_trading_day(market, closed)
        reason = NOT_TRADED.format(latest=latest.isoformat(), session=closed.isoformat())
        return None, None, reason, None
    if verdict.status == quotes.STALE and session == latest:
        printed = clock.close_print(market, latest)
        due = printed + quotes.CLOSE_WINDOW if printed is not None else None
        if due is not None and moment < due:
            # The print may reach the source any minute (Yahoo's usual delay is 15 minutes): look again at the
            # next mark, at the latest at ``due``.
            return (f"{verdict.reason}; waiting for the close until {due.isoformat(timespec='minutes')}", due, None,
                    min(_next_look(moment), due))
        if printed is not None and quote.quote_time is not None and quote.quote_time < printed:
            # Traded today, but no later time came: the day's last price, as Today shows it (stale, 估算).
            return None, None, NO_CLOSING_PRINT, None
    return verdict.reason or verdict.status, None, None, None


def quote_problem(ticker, meta, date_str=None, now=None):
    """Why a chart response's quote may not be booked, or None.

    It is booked only with a usable price, the listing's own currency
    (``currency``; a missing one is never taken for CNY) and an exchange time
    (``regularMarketTime``) that identifies its session, a session no later
    than the day the snapshot is for (``date_str``) whose close is final at
    ``now`` (default: the time of the call). A price of a session still trading
    is not a close: like the post-market brief, which calls it 盘中价, the
    snapshot does not book it, so the ticker has no price until the session
    has closed: the snapshot then says when that close is final (``_generate``),
    and the run of the day made after that moment books it. After that the
    quote is judged by Today's decision table (``_close_verdict``).
    """
    return _quote_check(ticker, meta, date_str, now)[0]


def yahoo_symbol(ticker, ticker_map=None):
    """The Yahoo chart symbol for ``ticker``, or None when no source may be asked for it.

    ``ticker_map`` (``config.json``) wins; otherwise the symbol Today's quote sources ask for, from the one
    table in ``core.market.instruments`` (HKG:00700 -> 0700.HK, NYSE:BRK.B -> BRK-B). The engine image copies
    ``core/market`` beside ``scripts/`` for this (engine/Dockerfile).
    """
    mapped = (ticker_map or {}).get(ticker)
    if mapped:
        return mapped
    import ledger_bridge  # noqa: F401  (puts the dashboard package, or the image's core/, on the path)
    from core.market.instruments import InstrumentError, parse
    try:
        return parse(ticker).yahoo_symbol
    except InstrumentError:
        return None


def price_sources():
    """The sources ``fetch_prices`` asks, in order, as ``TRACKER_QUOTE_SOURCE`` selects them.

    Yahoo, then Tencent for the holdings Yahoo did not price; ``tencent`` or ``yahoo`` alone when selected. The
    snapshot has no reading of Eastmoney's quotes: with ``eastmoney`` it prices from Yahoo alone, as before.
    """
    choice = os.environ.get("TRACKER_QUOTE_SOURCE", "").strip().lower()
    return {"tencent": ["tencent"], "yahoo": ["yahoo"], "eastmoney": ["yahoo"]}.get(choice, ["yahoo", "tencent"])


def fetch_prices(holdings, config, date_str=None, not_final=None, estimated=None, look_again=None, priced_by=None):
    """Fetch current prices for all tickers via Yahoo Finance, then Tencent for those Yahoo did not price.

    A ticker whose price cannot be fetched, or comes back as anything but a
    finite positive number, is left out of the result: it is never priced at 0.
    So is one without its own currency or an exchange time, or of a session
    after ``date_str`` (``quote_problem``): a price is never booked in a
    currency it was not quoted in, nor into an earlier day; nor is a price
    Today's decision table does not take for the close (``_close_verdict``).
    ``not_final`` (a dict), when given, gets ``ticker -> moment`` for each
    ticker left out only because its close is not final yet; ``estimated``
    (a dict) gets ``ticker -> reason`` for each price booked as an estimate;
    ``look_again`` (a dict) gets ``ticker -> moment`` for each of those waits
    that may end earlier: a closing print that may still reach the source,
    to be looked for again at that moment (``CLOSE_RECHECK``). ``priced_by``
    (a dict) gets ``ticker -> "yahoo" | "tencent"``: the source of each price
    booked (the snapshot's ``price_source``, the report's 数据来源).

    Tencent (``price_sources``, ``fetch_tencent_quotes``) is asked, in one batch, for each ticker Yahoo did not
    price, except one waiting for its market's close to become final, which no source can shorten, and only
    while the run has time for it (``_tencent_timeout``). Its quote must pass the same rules and be a closing
    row (``_tencent_check``); only a verified close of the session being booked ends Yahoo's wait for a closing
    print. The script says which holdings it priced from Tencent.
    """
    proxy = config.get("proxy", "")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"User-Agent": "Mozilla/5.0"}
    ticker_map = config.get("ticker_map", {})
    yahoo_base = config.get("yahoo_base", "https://query1.finance.yahoo.com/v8/finance/chart")
    sources = price_sources()

    # Collect all unique tickers
    all_tickers = set()
    for group in holdings.get("groups", {}).values():
        for pos in group.get("positions", []):
            all_tickers.add(pos["ticker"])

    prices = {}  # ticker -> price in local currency
    currencies = {}  # ticker -> currency
    waits = {}  # ticker -> (moment, look again or None): Yahoo's answer was a wait

    for ticker in (all_tickers if "yahoo" in sources else ()):
        yahoo_sym = yahoo_symbol(ticker, ticker_map)
        if not yahoo_sym:
            print(f"  ⚠️ 获取 {ticker} 价格失败: no Yahoo symbol for this code", file=sys.stderr)
            continue

        try:
            r = requests.get(
                f"{yahoo_base}/{yahoo_sym}",
                headers=headers, proxies=proxies, timeout=15,
                params={"interval": "1d", "range": "1d"}
            )
            r.raise_for_status()
            data = r.json()
            meta = data["chart"]["result"][0]["meta"]
            problem, final, estimate, look = _quote_check(ticker, meta, date_str, _now())
            if problem is not None:
                if final is not None:
                    waits[ticker] = (final, look)
                if final is not None and not_final is not None:
                    not_final[ticker] = final
                    if look is not None and look_again is not None:
                        look_again[ticker] = look
                raise ValueError(problem)
            prices[ticker] = meta["regularMarketPrice"]
            currencies[ticker] = meta["currency"].strip().upper()
            if priced_by is not None:
                priced_by[ticker] = "yahoo"
            if estimate and estimated is not None:
                estimated[ticker] = estimate
        except Exception as e:
            print(f"  ⚠️ 获取 {ticker} ({yahoo_sym}) 价格失败: {e}", file=sys.stderr)

    # A wait for a close to become final is the calendar's: only a wait for the closing print to reach the
    # source (one with a moment to look again) may end sooner at another source.
    later = sorted(ticker for ticker in all_tickers
                   if ticker not in prices and (ticker not in waits or waits[ticker][1] is not None))
    if "tencent" in sources and later:
        _price_from_tencent(later, config, date_str, prices, currencies, not_final, estimated, look_again, waits,
                            priced_by)
    return prices, currencies


def _price_from_tencent(tickers, config, date_str, prices, currencies, not_final, estimated, look_again, waits,
                        priced_by=None):
    """``fetch_prices``' second source: Tencent's quote of each of ``tickers``, judged by ``_tencent_check``.

    A holding Yahoo left waiting for its closing print keeps that wait unless Tencent has the verified close of
    the session being booked: an estimate from Tencent (the last close carried forward, the previous close of an
    untraded row) never ends it, so the snapshot looks again as it would without Tencent.
    """
    now = _now()
    for ticker, (symbol, quote, status) in fetch_tencent_quotes(tickers, config, now).items():
        if not quote.usable:
            print(f"  ⚠️ 腾讯行情 {ticker} ({symbol}) 没有价格: {quote.reason}", file=sys.stderr)
            continue
        problem, final, estimate, look = _tencent_check(ticker, quote, status, date_str, now)
        if problem is None and estimate and ticker in waits:
            problem = f"an estimate ({estimate}) does not end the wait for the closing print"
        if problem is not None:
            print(f"  ⚠️ 腾讯行情 {ticker} ({symbol}) 未记入: {problem}", file=sys.stderr)
            if final is not None and ticker not in waits and not_final is not None:
                not_final[ticker] = final
                if look is not None and look_again is not None:
                    look_again[ticker] = look
            continue
        prices[ticker] = float(quote.price)
        currencies[ticker] = quote.currency
        if priced_by is not None:
            priced_by[ticker] = "tencent"
        for waiting in (not_final, look_again):
            if waiting is not None:
                waiting.pop(ticker, None)
        if estimate and estimated is not None:
            estimated[ticker] = estimate
        print(f"  {ticker} 价格取自腾讯行情（{symbol}）")


def _tencent_check(ticker, quote, status, date_str, now):
    """``_quote_check`` for a Tencent quote, which must also be a closing row to be booked.

    A Tencent row stands for the close of the session its exchange time belongs to only when that time is on
    the session's own date, at or after the session's closing print (``SessionClock.close_print``: the
    mainland 15:00, Hong Kong's closing auction 16:08, 12:08 on a half day, New York's bell 16:00, 13:00 on an
    early-close day) and, in New York, less than a minute after the bell: Tencent does not say whether a later
    New York price is an after-hours trade (on 2026-11-18 Eastmoney and Yahoo stamped the official close
    16:00:00 to 16:00:39). A row stamped before the print (mid-session, before the closing auction, a
    placeholder before the open) is never a close however long ago it was stamped: Today's rule that books the
    day's last price once the print's usual delay has passed (``NO_CLOSING_PRINT``) is not applied to Tencent.
    The close of an earlier session than the one being booked is carried forward (``NOT_TRADED``) only from a
    row Tencent marks suspended ('S'): one it shows as trading but did not update says nothing about whether
    the holding traded since. The rule holds whatever day the snapshot runs; Today's own rules do not change.
    """
    problem, final, estimate, look = _quote_check(ticker, _tencent_meta(quote), date_str, now, quote)
    if problem is not None:
        return problem, final, estimate, look
    from core.market import quotes, tencent
    from core.market.calendar import ZONES
    from core.market.instruments import US, parse
    market = parse(ticker).market
    clock = _judging_clock(market, date_str, now)
    session = clock.session_of(market, quote.quote_time)
    printed = clock.close_print(market, session)
    local = quote.quote_time.astimezone(ZONES[market])
    late = market == US and printed is not None and quote.quote_time >= printed + quotes.LIVE_MAX_AGE
    if printed is None or local.date() != session or quote.quote_time < printed or late:
        bound = f"{printed.astimezone(ZONES[market]):%H:%M}" if printed is not None else "unknown"
        return (f"Tencent's row is stamped {local:%Y-%m-%d %H:%M:%S} local time: not the closing row of the "
                f"{session} session (its close prints at {bound}"
                f"{', a New York row within a minute of it' if market == US else ''})"), None, None, None
    if session < clock.session_of(market) and status != tencent.SUSPENDED:
        return (f"Tencent's row is the close of {session}, before the session being booked, and does not mark the "
                "holding suspended: its price is not carried forward"), None, None, None
    return None, None, estimate, None


def _tencent_meta(quote):
    """What ``_quote_check`` reads of a chart's ``meta``, from a Tencent quote: its price, currency and time."""
    return {"regularMarketPrice": float(quote.price) if quote.price is not None else None, "currency": quote.currency,
            "regularMarketTime": int(quote.quote_time.timestamp()) if quote.quote_time is not None else None}


# Tencent's quotes (``qt.gtimg.cn``), read as Today reads them (``core.market.tencent``: ``QUOTE_URL``, ``HEADERS``,
# ``BATCH``, ``parse_response``, ``quote_from_fields``, ``row_status``), here with ``requests``. Reached directly
# unless ``TRACKER_TENCENT_PROXY=1``, as Today reaches it; ``tencent_url`` in config.json replaces the endpoint.
TENCENT_URL = "https://qt.gtimg.cn/"
# The pipeline stops this script after this many seconds (``portfolio_daily_update.SNAPSHOT_TIMEOUT``; a test keeps
# the two equal). Tencent answers in about 0.2 s; its request gets at most ``TENCENT_TIMEOUT`` (connect, read) and
# is made only while that and ``RATES_RESERVE`` (the exchange-rate requests after it: Yahoo's two at their
# 15-second timeout) still fit, so a slow Yahoo leaves the run to end, or to ask for its retry, as before.
CHILD_TIMEOUT = 120
TENCENT_TIMEOUT = (2.0, 4.0)
RATES_RESERVE = 30.0
_monotonic = time.monotonic
_run_started = None   # set by main(): the moment the child's time counts from; None when imported


def _tencent_timeout():
    """``(connect, read)`` seconds for the next Tencent request, or None when the run has no time left for it."""
    if _run_started is None:
        return TENCENT_TIMEOUT
    left = CHILD_TIMEOUT - (_monotonic() - _run_started) - RATES_RESERVE
    if left < TENCENT_TIMEOUT[0]:
        return None
    return min(TENCENT_TIMEOUT[0], left), min(TENCENT_TIMEOUT[1], left)


def fetch_tencent_quotes(tickers, config, now):
    """``{ticker: (Tencent symbol, LiveQuote, status flag)}`` for ``tickers``; a ticker Tencent has no symbol for
    is left out, and so is every ticker when Tencent cannot be reached or the run has no time left for it (the
    script says why)."""
    import ledger_bridge  # noqa: F401  (puts the dashboard package, or the image's core/, on the path)
    from core.market import tencent
    from core.market.instruments import InstrumentError, parse
    wanted = {}
    for ticker in tickers:
        try:
            instrument = parse(ticker)
        except InstrumentError:
            instrument = None
        if instrument is None or not instrument.tencent_symbol:
            print(f"  ⚠️ 腾讯行情没有 {ticker} 对应的代码", file=sys.stderr)
            continue
        wanted[ticker] = instrument
    # A key set to None drops what the environment names for it (as for Eastmoney's rates).
    proxies = (None if os.environ.get("TRACKER_TENCENT_PROXY") == "1"
               else {"http": None, "https": None, "all": None})
    symbols = list(dict.fromkeys(instrument.tencent_symbol for instrument in wanted.values()))
    rows = {}
    try:
        for start in range(0, len(symbols), tencent.BATCH):
            timeout = _tencent_timeout()
            if timeout is None:
                print(f"  ⚠️ 未查询腾讯行情: 快照已运行 {_monotonic() - _run_started:.0f} 秒，"
                      f"剩余时间要留给汇率请求", file=sys.stderr)
                return {}
            r = requests.get(config.get("tencent_url") or TENCENT_URL, headers=tencent.HEADERS, proxies=proxies,
                             timeout=timeout, params={"q": ",".join(symbols[start:start + tencent.BATCH])})
            r.raise_for_status()
            rows.update(tencent.parse_response(r.content))
    except Exception as error:
        print(f"  ⚠️ 获取腾讯行情失败: {error}", file=sys.stderr)
        return {}
    return {ticker: (instrument.tencent_symbol,
                     tencent.quote_from_fields(instrument, rows.get(instrument.tencent_symbol), now),
                     tencent.row_status(rows.get(instrument.tencent_symbol)))
            for ticker, instrument in wanted.items()}


def missing_prices(holdings, prices):
    """Held tickers (quantity other than 0) without a usable price, sorted."""
    missing = set()
    for group in holdings.get("groups", {}).values():
        for pos in group.get("positions", []):
            if pos.get("quantity") == 0:
                continue
            if usable_price(prices.get(pos.get("ticker"))) is None:
                missing.add(str(pos.get("ticker")))
    return sorted(missing)


# Today's sources of a rate, in Today's order (``core.market.runtime``): Yahoo's onshore rate first, Eastmoney's
# offshore one (CNH) next. Eastmoney is reached directly unless ``TRACKER_EASTMONEY_PROXY=1``, as Today reaches
# it; ``eastmoney_url`` in config.json replaces its endpoint. The request and its reading are Today's
# (``core.market.sources_eastmoney``: ``QUOTE_URL``, ``FIELDS``, ``HEADERS``, ``FX_SECIDS``, ``parse_rows``,
# ``fx_from_row``), here with ``requests``, the engine's only dependency. Tencent has no rates the snapshot can use
# (``core.market.tencent``).
EASTMONEY_URL ="https://push2.eastmoney.com/api/qt/ulist.np/get"
EASTMONEY_FIELDS = "f2,f5,f12,f13,f14,f15,f16,f17,f18,f124"
EASTMONEY_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/",
                     "Accept": "application/json,text/plain,*/*"}
EASTMONEY_FX = {"USD": "133.USDCNH", "HKD": "133.HKDCNH"}


class _YahooRates:
    """Yahoo's chart of each configured symbol (``fx_tickers``), read as ``sources_yahoo.fx_from_chart`` reads it:
    a rate the source gives in CNY. A symbol that cannot be fetched is a rate with a fixed reason and no value."""

    name = "yahoo"

    def __init__(self, config, symbols, now):
        proxy = config.get("proxy", "")
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.base = config.get("yahoo_base", "https://query1.finance.yahoo.com/v8/finance/chart")
        self.symbols, self.now = symbols, now

    def fetch_fx(self, currencies):
        from core.market import quotes
        from core.provider_errors import classify
        rates = {}
        for currency in currencies:
            label = f"{self.name}:{self.symbols[currency]}"
            try:
                r = requests.get(f"{self.base}/{self.symbols[currency]}", headers={"User-Agent": "Mozilla/5.0"},
                                 proxies=self.proxies, timeout=15, params={"interval": "1d", "range": "1d"})
                r.raise_for_status()
                meta = r.json()["chart"]["result"][0]["meta"]
            except Exception as error:
                # What the exception says goes to this script's own output; the rate gets a fixed text.
                print(f"  ⚠️ 获取汇率 {self.symbols[currency]} 失败: {error}", file=sys.stderr)
                rates[currency] = quotes.build_fx(currency, label, self.now, reason=classify(error))
                continue
            if meta.get("currency") != "CNY":
                rates[currency] = quotes.build_fx(currency, label, self.now, reason="数据源返回的不是人民币汇率")
            else:
                rates[currency] = quotes.build_fx(currency, label, self.now, rate=meta.get("regularMarketPrice"),
                                                  quote_time=quotes.from_epoch(meta.get("regularMarketTime")))
        return rates


class _EastmoneyRates:
    """Eastmoney's offshore rates (``ulist.np``), read as ``sources_eastmoney.fx_from_row`` reads them."""

    name = "eastmoney"

    def __init__(self, config, now, only=None):
        self.url = config.get("eastmoney_url") or EASTMONEY_URL
        # Directly, or through the environment's proxy when asked to (``proxies=None``). A key set to None drops
        # what the environment names for it: HTTP_PROXY, HTTPS_PROXY and ALL_PROXY, in either case.
        self.proxies = (None if os.environ.get("TRACKER_EASTMONEY_PROXY") == "1"
                        else {"http": None, "https": None, "all": None})
        self.now, self.only = now, only

    def fetch_fx(self, currencies):
        from core.market import quotes
        wanted = {currency: EASTMONEY_FX[currency] for currency in currencies
                  if currency in EASTMONEY_FX and (self.only is None or currency in self.only)}
        if not wanted:
            return {}
        r = requests.get(self.url, headers=EASTMONEY_HEADERS, proxies=self.proxies, timeout=15,
                         params={"fltt": "2", "invt": "2", "fields": EASTMONEY_FIELDS,
                                 "secids": ",".join(wanted.values())})
        r.raise_for_status()
        rows = _eastmoney_rows(r.json())
        rates = {}
        for currency, secid in wanted.items():
            row, label = rows.get(secid), f"{self.name}:{currency}CNH"
            rates[currency] = (quotes.build_fx(currency, label, self.now, reason="数据源没有这个汇率") if row is None
                               else quotes.build_fx(currency, label, self.now, rate=row.get("f2"),
                                                    quote_time=quotes.from_epoch(row.get("f124"))))
        return rates


def _eastmoney_rows(payload):
    """A ``ulist.np`` answer by ``secid`` (``sources_eastmoney.parse_rows``); ``ValueError`` when it is not one."""
    if not isinstance(payload, dict):
        raise ValueError("unexpected response")
    data = payload.get("data")
    if payload.get("rc") == 102 and data is None:
        return {}   # not one of the secids is known
    if payload.get("rc") != 0:
        raise ValueError("unexpected response")
    rows = data.get("diff") if isinstance(data, dict) else None
    if rows is None:
        return {}
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError("unexpected response")
    return {f"{row.get('f13')}.{row.get('f12')}": row for row in rows if isinstance(row, dict)}


def fetch_fx_rates(config, estimated=None, needed=None):
    """Fetch the CNY rate of each currency in ``fx_tickers`` (HKD/CNY and USD/CNY by default).

    The rate is the one Today takes (``core.market.quotes.QuoteService``, the same function): the first
    source's verified rate (current, delayed or the weekend's: ``derive_fx_status``), Yahoo before Eastmoney; a
    rate that is stale or has no time yields to the next source. Only when no source has a verified rate is the
    latest of them used, as an estimate, the way Today marks what it converts 估算: ``estimated`` (a dict), when
    given, gets ``currency -> reason`` for it. A currency no source has a rate for is left out.

    The sources are those ``TRACKER_QUOTE_SOURCE`` selects for Today (``core.market.runtime``: ``yahoo`` or
    ``eastmoney`` alone, else both; Tencent has no rates, so ``tencent`` leaves them with both). The second is
    asked only for the currencies in ``needed`` (all when None).
    """
    import ledger_bridge  # noqa: F401  (puts the dashboard package, or the image's core/, on the path)
    from core.market import quotes
    from core.market.calendar import SessionClock
    fx_tickers = config.get("fx_tickers", {"HKD_CNY": "HKDCNY=X", "USD_CNY": "USDCNY=X"})
    symbols = {key.split("_")[0]: symbol for key, symbol in fx_tickers.items()}   # "HKD_CNY" -> "HKD"
    now = _now()
    choice = os.environ.get("TRACKER_QUOTE_SOURCE", "").strip().lower()
    names = {"eastmoney": ["eastmoney"], "yahoo": ["yahoo"]}.get(choice, ["yahoo", "eastmoney"])
    sources = {"yahoo": _YahooRates(config, symbols, now),
               "eastmoney": _EastmoneyRates(config, now, only=None if names[0] == "eastmoney" else needed)}
    service = quotes.QuoteService([], SessionClock(lambda: now), fx_sources=[sources[name] for name in names])
    chosen = service._fetch_fx(list(symbols), force=True) if symbols else {}

    rates = {"CNY": 1.0}  # default
    for key in fx_tickers:
        currency = key.split("_")[0]
        rate = chosen[currency]
        status, reason = quotes.derive_fx_status(rate, now)
        if status == quotes.UNAVAILABLE:
            print(f"  ⚠️ 获取汇率 {key} 失败: {reason}", file=sys.stderr)
            continue
        rates[currency] = float(rate.rate)
        if not rate.source.startswith("yahoo:"):
            print(f"  汇率 {currency}/CNY 取自 {rate.source}")
        if status in (quotes.STALE, quotes.TIME_UNKNOWN) and estimated is not None:
            estimated[currency] = reason
    return rates


def _decimal(value):
    """A number of the holdings, a quote, a rate or the history as ``Decimal``, read the way Today reads it:
    a float through ``str``, so 7.1234 stays 7.1234 (``core.market.quotes.to_decimal``)."""
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("a value of the holdings or the history is not a finite number")
    return number


def _money(value):
    """A money figure for the snapshot file: rounded half up to the cent (``core.market.valuation.cents``);
    None stays None (unavailable)."""
    import ledger_bridge  # noqa: F401  (puts the dashboard package, or the image's core/, on the path)
    from core.market.valuation import cents
    return None if value is None else float(cents(value))


def calculate_snapshot(holdings, prices, currencies, fx_rates, prev_snapshot, all_snapshots, history_values=None,
                       price_estimates=None, rate_estimates=None, holdings_source=None, priced_by=None):
    """Calculate full portfolio snapshot with all metrics.

    Money is ``Decimal`` and rounded as Today rounds it (``core.market.valuation``): half up to the cent
    where it is first computed (each position's CNY value from its unrounded value in its own currency,
    each cash balance's CNY value), and every total is the sum of those rounded figures. It is computed
    from the ledger's own figures when the holdings carry them (``exact``, ``ledger_bridge.ledger_holdings``)
    with Today's working precision (``core.market.today.WORKING_DIGITS``).

    ``price_estimates`` (``ticker -> reason``, from ``fetch_prices``) names prices booked as estimates and
    ``rate_estimates`` (``currency -> reason``, from ``fetch_fx_rates``) rates used as estimates. A position
    valued from one gets ``estimated_reason``; the summary gets ``estimated`` (whether any figure of the day
    rests on an estimate) and ``estimated_reasons`` (``"<ticker>：<reason>"``, ``"汇率 USD/CNY：<reason>"``
    for a rate that converts a position or a cash balance), as Today marks its rows 估算.

    ``priced_by`` (``ticker -> "yahoo" | "tencent"``, from ``fetch_prices``) is recorded as each
    position's ``price_source``; a position without one (a snapshot made before sources were recorded) was
    priced from Yahoo.

    ``holdings_source`` (``"ledger"`` or ``"legacy_files"``, from ``_generate``) is recorded as the
    snapshot's ``holdings_source``. Holdings from the legacy files, not confirmed into the ledger, have no
    confirmed total in Today: the snapshot is marked with ``LEGACY_HOLDINGS`` first among its reasons.
    """
    import ledger_bridge  # noqa: F401  (puts the dashboard package, or the image's core/, on the path)
    from core.market.today import WORKING_DIGITS
    with localcontext() as context:
        context.prec = WORKING_DIGITS
        return _snapshot_of(holdings, prices, currencies, fx_rates, prev_snapshot, all_snapshots, history_values,
                            price_estimates, rate_estimates, holdings_source, priced_by or {})


def _snapshot_of(holdings, prices, currencies, fx_rates, prev_snapshot, all_snapshots, history_values,
                 price_estimates, rate_estimates, holdings_source, priced_by):
    """``calculate_snapshot``'s work, in its decimal context."""
    from core.market.valuation import cents
    price_estimates = price_estimates or {}
    rate_estimates = rate_estimates or {}
    estimated_reasons = [LEGACY_HOLDINGS] if holdings_source == "legacy_files" else []

    def rate_note(currency):
        """Why ``currency``'s rate is an estimate, as a reason line, or None (CNY needs no rate)."""
        reason = rate_estimates.get(currency) if currency != "CNY" else None
        return f"汇率 {currency}/CNY：{reason}" if reason else None
    date_str = holdings["date"]
    history_values = ordered_history(
        history_values, [*(all_snapshots or []), *([prev_snapshot] if prev_snapshot else [])], date_str
    )
    currency_map_cfg = {
        "SHA": "CNY", "SHE": "CNY", "HKG": "HKD", "NASDAQ": "USD", "NYSE": "USD"
    }

    snapshot = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "fx_rates": fx_rates,
        "groups": {},
        "summary": {}
    }

    grand_total_value = Decimal(0)
    grand_total_cost = Decimal(0)

    for group_name, group_data in holdings.get("groups", {}).items():
        positions_out = []
        group_positions_value = Decimal(0)
        # The ledger's own figures, every digit (ledger_bridge.ledger_holdings); the floats otherwise.
        exact = (holdings.get("exact") or {}).get(group_name) or {}

        for pos in group_data.get("positions", []):
            ticker = pos["ticker"]
            exchange = ticker.split(":")[0]
            currency = currencies.get(ticker, currency_map_cfg.get(exchange, "CNY"))
            quantity = finite_number(pos["quantity"], f"{ticker} quantity")
            if quantity < 0:
                raise ValueError(f"{ticker}: negative quantity is unsupported")
            if quantity == 0:
                continue
            fx = fx_rate(currency, fx_rates)
            current_price = finite_number(prices.get(ticker), f"{ticker} price")
            if current_price <= 0:
                raise ValueError(f"{ticker}: missing or non-positive price; snapshot refused")

            rate = _decimal(fx)
            held = (exact.get("positions") or {}).get(ticker) or {}
            exact_quantity = _decimal(held.get("quantity", quantity))
            cost_price = _decimal(held.get("cost_price", finite_number(pos["cost_price"], f"{ticker} cost_price")))
            market_value_local = exact_quantity * _decimal(current_price)
            cost_value_local = exact_quantity * cost_price
            market_value_cny = cents(market_value_local * rate)
            cost_value_cny = cents(cost_value_local * rate)
            profit_cny = cents((market_value_local - cost_value_local) * rate)
            # None without a positive cost
            profit_pct = return_pct(float((market_value_local - cost_value_local) * rate), float(cost_value_local * rate))

            positions_out.append({
                "name": pos["name"],
                "ticker": ticker,
                "quantity": pos["quantity"],
                "cost_price": pos["cost_price"],
                "current_price": current_price,
                "currency": currency,
                "fx_rate": fx,
                "market_value_cny": float(market_value_cny),
                "cost_value_cny": float(cost_value_cny),
                "profit_cny": float(profit_cny),
                "profit_pct": profit_pct,
                "weight_in_group": 0  # calculated later
            })
            if ticker in priced_by:
                positions_out[-1]["price_source"] = priced_by[ticker]
            notes = [(price_estimates[ticker], f"{ticker}：{price_estimates[ticker]}")] if price_estimates.get(ticker) else []
            if rate_note(currency):
                notes.append((rate_note(currency), rate_note(currency)))
            if notes:
                positions_out[-1]["estimated_reason"] = "；".join(note for note, _ in notes)
                estimated_reasons.extend(line for _, line in notes if line not in estimated_reasons)
            group_positions_value += market_value_cny

        fund = finite_number(group_data.get("fund", 0), "fund")
        _cash, balances, _values = value_cash(group_data, fx_rates)   # checks the balances and their rates
        exact_cash = exact.get("cash_balances") or {}
        cash_values = {currency: cents(_decimal(exact_cash.get(currency, amount)) * _decimal(fx_rate(currency, fx_rates)))
                       if amount else Decimal(0) for currency, amount in balances.items()}
        cash = sum(cash_values.values(), Decimal(0))
        for currency, amount in balances.items():
            if amount and rate_note(currency) and rate_note(currency) not in estimated_reasons:
                estimated_reasons.append(rate_note(currency))
        group_total = group_positions_value + cents(_decimal(exact.get("fund", fund))) + cash
        cost_basis = finite_number(group_data.get("cost_basis", 0), "cost_basis")
        group_profit = group_total - _decimal(exact.get("cost_basis", cost_basis))
        group_return_pct = return_pct(float(group_profit), cost_basis)  # None without positive contributed capital

        # Calculate weights
        for p in positions_out:
            p["weight_in_group"] = (round(p["market_value_cny"] / float(group_total) * 100, 1)
                                    if group_total != 0 else 0)

        snapshot["groups"][group_name] = {
            "cost_basis": cost_basis,
            "positions": positions_out,
            "fund": fund,
            "cash": float(cash),
            "cash_balances": balances,
            "cash_values_cny": {key: float(value) for key, value in cash_values.items()},
            "positions_value": float(group_positions_value),
            "total_value": float(group_total),
            "profit": _money(group_profit),
            "return_pct": group_return_pct
        }

        grand_total_value += group_total
        grand_total_cost += _decimal(exact.get("cost_basis", cost_basis))

    # Summary
    grand_profit = grand_total_value - grand_total_cost
    grand_return_pct = return_pct(float(grand_profit), float(grand_total_cost))

    # Daily change — prefer CSV history (more complete than snapshot files). Without an earlier row or
    # snapshot there is nothing to compare with: the day's changes are unavailable (None), never 0.
    prev_value = prev_cost = prev_date = None
    if history_values:
        prev_entries = [(d, v, c) for d, v, c in history_values if d < date_str]
        if prev_entries:
            prev_date, prev_value, prev_cost = prev_entries[-1]
            prev_value, prev_cost = _decimal(prev_value), _decimal(prev_cost)
    if prev_date is None and prev_snapshot:
        # CSV had no prior entry — fall back to snapshot file
        prev_value = _decimal(prev_snapshot["summary"]["total_value"])
        prev_cost = _decimal(prev_snapshot["summary"].get("total_cost", grand_total_cost))
        prev_date = prev_snapshot.get("date")
    daily_change = daily_change_pct = capital_change = market_daily_change = market_daily_change_pct = None
    if prev_value is not None:
        daily_change = grand_total_value - prev_value
        # Changes are percentages of the starting value only when it is positive; a
        # net liability would flip their sign, so they are unavailable (None) then.
        daily_change_pct = return_pct(float(daily_change), float(prev_value))

        # Capital change = cost difference vs previous day
        capital_change = grand_total_cost - prev_cost
        market_daily_change = daily_change - capital_change
        market_daily_change_pct = return_pct(float(market_daily_change), float(prev_value))

    # ── Build history with capital-adjusted daily returns ──
    # history_values is list of (date, total_value, total_cost)
    hist = [(d, _decimal(v), _decimal(c)) for d, v, c in (history_values or []) if d < date_str]
    # Append today
    hist.append((date_str, grand_total_value, grand_total_cost))

    # Compute market_daily_return for each day (excluding capital flows)
    # capital_change = cost[i] - cost[i-1]; market_change = value_change - capital_change
    market_daily_returns = []  # (date, market_return_ratio, market_change_amount)
    for i in range(1, len(hist)):
        prev_d, prev_v, prev_c = hist[i - 1]
        curr_d, curr_v, curr_c = hist[i]
        cap_change = curr_c - prev_c  # capital injection/withdrawal
        value_change = curr_v - prev_v
        mkt_change = value_change - cap_change
        mkt_return = float(mkt_change) / float(prev_v) if prev_v != 0 else 0
        market_daily_returns.append((curr_d, mkt_return, mkt_change))

    current_drawdown, max_drawdown = drawdowns([r for _, r, _ in market_daily_returns])

    # ── Monthly stats (market-only) ──
    month_first_day = date_str[:8] + "01"
    # month_start_value = total_value at end of previous month
    prev_month_entries = [(d, v) for d, v, c in hist if d < month_first_day]
    if prev_month_entries:
        month_start_value = prev_month_entries[-1][1]
    else:
        # Tracking began this month: the month starts at its first recorded value
        # (today's on the very first snapshot), as in the tracker API. The market
        # changes summed below are measured from that same row.
        month_start_value = hist[0][1]
    # Sum market-only changes this month
    month_market_changes = [mc for d, r, mc in market_daily_returns if d >= month_first_day]
    month_market_change = sum(month_market_changes, Decimal(0))
    month_return_pct = return_pct(float(month_market_change), float(month_start_value))
    # month_change (net, for display) = actual value change including capital
    month_net_change = grand_total_value - month_start_value

    # ── Quantitative metrics (using market-only returns) ──
    daily_returns = [r for _, r, _ in market_daily_returns]

    import math
    sharpe_ratio = 0.0
    volatility_annual = 0.0
    win_rate = 0.0
    avg_win = 0.0
    avg_loss = 0.0
    profit_loss_ratio = 0.0
    if len(daily_returns) >= 5:
        mean_r = sum(daily_returns) / len(daily_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns))
        risk_free_daily = 0.02 / 252
        sharpe_ratio = round(((mean_r - risk_free_daily) / std_r * math.sqrt(252)) if std_r > 0 else 0, 2)
        volatility_annual = round(std_r * math.sqrt(252) * 100, 2)
        wins = [r for r in daily_returns if r > 0]
        losses = [r for r in daily_returns if r < 0]
        win_rate = round(len(wins) / len(daily_returns) * 100, 1)
        avg_win = round(sum(wins) / len(wins) * 100, 3) if wins else 0
        avg_loss = round(sum(losses) / len(losses) * 100, 3) if losses else 0
        profit_loss_ratio = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0

    snapshot["ledger_revision"] = holdings.get("ledger_revision")
    if holdings_source is not None:
        snapshot["holdings_source"] = holdings_source
    snapshot["summary"] = {
        "total_value": _money(grand_total_value),
        "total_cost": float(grand_total_cost),
        "total_profit": _money(grand_profit),
        "total_return_pct": grand_return_pct,
        "prev_date": prev_date,
        "prev_total_value": _money(prev_value),
        "daily_change": _money(daily_change),
        "daily_change_pct": daily_change_pct,
        "capital_change": _money(capital_change),
        "market_daily_change": _money(market_daily_change),
        "market_daily_change_pct": market_daily_change_pct,
        "max_drawdown_pct": max_drawdown,
        "current_drawdown_pct": current_drawdown,
        "return_method": "daily_end_of_period_flow_approximation",
        "capital_flow_source": "cost_basis_delta",
        "month_start_value": _money(month_start_value),
        "month_change": _money(month_net_change),
        "month_market_change": _money(month_market_change),
        "month_return_pct": month_return_pct,
        # Quantitative metrics
        "sharpe_ratio": sharpe_ratio,
        "volatility_annual": volatility_annual,
        "win_rate": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "profit_loss_ratio": profit_loss_ratio,
        "trading_days": len(daily_returns),
        # Whether a figure of the day rests on an estimated input, and which (Today: 估算).
        "estimated": bool(estimated_reasons),
        "estimated_reasons": estimated_reasons,
    }

    return snapshot


def save_snapshot(snapshot):
    """Save snapshot to file."""
    path = os.path.join(PORTFOLIO_DIR, "snapshots", f"{snapshot['date']}.json")
    with atomic_text_writer(path) as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"  ✅ 快照已保存: {path}")
    return path


def sync_to_qr(snapshot, config):
    """Update QR trading-assistant portfolio.json for backward compatibility."""
    qr_path = config.get("qr_portfolio_path", "")
    if not qr_path or not os.path.exists(os.path.dirname(qr_path)):
        return

    # Merge positions across groups
    merged = {}
    for group_name, group_data in snapshot["groups"].items():
        for pos in group_data["positions"]:
            symbol = pos["ticker"].split(":")[1]
            exchange = pos["ticker"].split(":")[0]
            market_map = {"SHA": "a_share", "SHE": "a_share", "HKG": "hk_stock", "NASDAQ": "us_stock", "NYSE": "us_stock"}
            key = (market_map.get(exchange, "a_share"), symbol)
            if key in merged:
                existing = merged[key]
                new_quantity = existing["quantity"] + pos["quantity"]
                existing["cost_price"] = (
                    existing["cost_price"] * existing["quantity"] + pos["cost_price"] * pos["quantity"]
                ) / new_quantity if new_quantity else 0
                existing["quantity"] = new_quantity
                existing["available_qty"] += pos["quantity"]
            else:
                merged[key] = {
                    "symbol": symbol,
                    "name": pos["name"],
                    "market": market_map.get(exchange, "a_share"),
                    "quantity": pos["quantity"],
                    "available_qty": pos["quantity"],
                    "cost_price": pos["cost_price"],
                    "current_price": pos.get("current_price", 0),
                    "side": "long"
                }

    total_cash = sum(g.get("cash", 0) + g.get("fund", 0) for g in snapshot["groups"].values())

    qr_data = {
        "positions": list(merged.values()),
        "cash": total_cash,
        "updated_at": snapshot["generated_at"]
    }
    with atomic_text_writer(qr_path) as f:
        json.dump(qr_data, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"  ✅ QR portfolio.json 已同步")


def load_all_snapshots():
    """Load all historical snapshots for drawdown/monthly calc."""
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
    snapshots = []
    for f in sorted(glob.glob(os.path.join(snap_dir, "*.json"))):
        try:
            with open(f, "r") as fh:
                snapshots.append(json.load(fh))
        except:
            pass
    return snapshots


def load_all_history_values():
    """Load all historical data from history.csv for accurate peak/drawdown/monthly calcs."""
    csv_path = os.path.join(PORTFOLIO_DIR, "history.csv")
    values = []  # list of (date, total_value, total_cost)
    if os.path.exists(csv_path):
        with open(csv_path, "r") as f:
            header = f.readline().strip().split(",")
            date_idx = header.index("date") if "date" in header else 0
            val_idx = header.index("total_value") if "total_value" in header else 1
            cost_idx = header.index("total_cost") if "total_cost" in header else 2
            for line in f:
                parts = line.strip().split(",")
                if len(parts) > max(val_idx, cost_idx):
                    try:
                        values.append((
                            parts[date_idx],
                            float(parts[val_idx]),
                            float(parts[cost_idx]) if cost_idx < len(parts) else 0
                        ))
                    except ValueError:
                        pass
    return values


def main():
    global _run_started
    _run_started = _monotonic()   # the pipeline's time limit counts from here (``CHILD_TIMEOUT``)
    parser = argparse.ArgumentParser(description="Generate portfolio daily snapshot")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print without saving")
    args = parser.parse_args()

    print(f"📊 生成 {args.date} 资产快照...")
    # A dry run writes nothing and takes no lock.
    if args.dry_run:
        config, holdings = _read_inputs(args.date, persist=False)
        return _generate(args, config, holdings, _fetch_quotes(holdings, config, args.date))
    try:
        return _locked_snapshot(args)
    except LockUnavailable as error:
        sys.exit(str(error))


def _read_inputs(date_str, *, persist=True):
    config = load_config()
    holdings = load_holdings(date_str, config, persist=persist)
    if not holdings:
        sys.exit(1)
    return config, holdings


def _fingerprint(config, holdings):
    """Content of the inputs the downloads depend on (holdings include the ledger revision)."""
    text = json.dumps([config, holdings], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _converted(holdings, currencies):
    """The currencies ``calculate_snapshot`` converts: of each position held and each cash balance not 0."""
    used = set()
    for group in holdings.get("groups", {}).values():
        for pos in group.get("positions", []):
            if pos.get("quantity") != 0:
                exchange = str(pos.get("ticker")).partition(":")[0]
                used.add(currencies.get(pos.get("ticker"), EXCHANGE_CURRENCIES.get(exchange, "CNY")))
        try:
            used.update(currency for currency, amount in cash_balances(group).items() if amount)
        except ValueError:
            pass   # the valuation refuses such cash itself
    return used - {"CNY"}


def _fetch_quotes(holdings, config, date_str=None):
    print("  获取实时价格...")
    not_final, estimated, rate_estimates, look_again, priced_by = {}, {}, {}, {}, {}
    prices, currencies = fetch_prices(holdings, config, date_str, not_final, estimated, look_again, priced_by)
    # A second source is asked only for a rate the snapshot converts, and not before a wait already known.
    missing = missing_prices(holdings, prices)
    waiting = bool(missing) and all(ticker in not_final for ticker in missing)
    fx_rates = fetch_fx_rates(config, rate_estimates, set() if waiting else _converted(holdings, currencies))
    print(f"  汇率: USD/CNY={fx_rates.get('USD', '?')}, HKD/CNY={fx_rates.get('HKD', '?')}")
    return prices, currencies, fx_rates, not_final, estimated, rate_estimates, look_again, priced_by


def _locked_snapshot(args):
    """Read and write under the portfolio lock, but download quotes without it.

    Cloning holdings and rewriting history.csv are read-modify-write steps, so
    they need the lock every other writer takes. The daily pipeline starts this
    script while it holds the lock for the whole run; that inherited lock is
    verified (see portfolio_write_lock), and then everything stays inside it.

    Run on its own, the snapshot releases the lock while it downloads quotes and
    exchange rates, which can take minutes, and checks under the lock again that
    the holdings and configuration it priced are still current. After one more
    attempt with changed inputs it gives up instead of saving stale holdings.
    Everything it writes is computed and written in that final locked step.
    """
    with portfolio_write_lock(PORTFOLIO_DIR, accept_inherited=True) as inherited:
        config, holdings = _read_inputs(args.date)
        if inherited:
            return _generate(args, config, holdings, _fetch_quotes(holdings, config, args.date))
    for _attempt in range(2):
        quotes = _fetch_quotes(holdings, config, args.date)
        with portfolio_write_lock(PORTFOLIO_DIR):
            current = _read_inputs(args.date)
            if _fingerprint(*current) == _fingerprint(config, holdings):
                return _generate(args, *current, quotes)
        print("  持仓或配置在获取行情期间发生变化，重新获取行情...")
        config, holdings = current
    sys.exit("❌ 持仓在两次获取行情期间都发生了变化，未保存快照；请稍后重新运行。")


MISSING_PRICE_MESSAGE = "以下持仓没有取得有效价格"
# Why a snapshot of holdings from the legacy files is marked: Today has no confirmed total for them.
LEGACY_HOLDINGS = "持仓来自旧版持仓文件，尚未迁移到账本"
# The day change of a snapshot without an earlier history row or snapshot (portfolio_report.py says the same).
NO_EARLIER_DAY = "不可用（没有上一日的记录可比）"

HISTORY_HEADER = ("date,total_value,total_cost,total_profit,return_pct,daily_change,daily_change_pct,"
                  "max_drawdown_pct,capital_change,market_daily_change,market_daily_change_pct\n")


def history_row(snapshot, date_str):
    """The ``history.csv`` line of a snapshot, from its saved summary alone."""
    s = snapshot["summary"]
    # An unavailable figure (a percentage without a positive base, a change without an earlier day) is an
    # empty field, never the text "None".
    def csv_pct(value):
        return "" if value is None else value
    return (
        f"{date_str},{s['total_value']},{s['total_cost']},{s['total_profit']},"
        f"{csv_pct(s['total_return_pct'])},{csv_pct(s['daily_change'])},{csv_pct(s['daily_change_pct'])},"
        f"{s['max_drawdown_pct']},{csv_pct(s['capital_change'])},{csv_pct(s['market_daily_change'])},"
        f"{csv_pct(s['market_daily_change_pct'])}\n"
    )


def upsert_history(snapshot, date_str, portfolio_dir=None):
    """Replace or append the day's row in ``history.csv``. The caller holds the portfolio lock.

    Needs only the saved snapshot, so a pipeline that finds the snapshot JSON
    without its history row can complete the day without fetching prices.
    """
    history_path = os.path.join(str(portfolio_dir or PORTFOLIO_DIR), "history.csv")
    CSV_HEADER = HISTORY_HEADER
    new_row = history_row(snapshot, date_str)
    if os.path.exists(history_path):
        with open(history_path, "r") as f:
            lines = f.readlines()
        # Upgrade header if needed
        existing_header = lines[0] if lines else CSV_HEADER
        if "capital_change" not in existing_header:
            # Migrate old rows: compute capital_change/market_daily_change from cost diffs
            data_lines = [l for l in lines[1:] if l.strip() and not l.startswith("date,")]
            migrated = []
            prev_cost = None
            for line in data_lines:
                parts = line.strip().split(",")
                if len(parts) >= 8:
                    try:
                        v, c, dc = float(parts[1]), float(parts[2]), float(parts[5])
                        pc = prev_cost if prev_cost is not None else c
                        cap = c - pc
                        mkt = dc - cap
                        pv_prev = float(parts[1]) - dc  # approx prev value
                        mkt_pct = round(mkt / pv_prev * 100, 4) if pv_prev else 0
                        prev_cost = c
                        migrated.append(parts[:8] + [str(round(cap, 2)), str(round(mkt, 2)), str(mkt_pct)] )
                    except (ValueError, IndexError):
                        prev_cost = None
                        migrated.append(parts[:8] + ["0", parts[5], parts[6]])
            with atomic_text_writer(history_path) as f:
                f.write(CSV_HEADER)
                for row in migrated:
                    if not row[0].startswith(date_str):
                        f.write(",".join(row) + "\n")
                f.write(new_row)
        else:
            # Header already has new columns
            data_lines = [l for l in lines if not l.startswith("date,") and not l.startswith(f"{date_str},")]
            with atomic_text_writer(history_path) as f:
                f.write(CSV_HEADER)
                f.writelines(data_lines)
                f.write(new_row)
    else:
        with atomic_text_writer(history_path) as f:
            f.write(CSV_HEADER)
            f.write(new_row)


def _generate(args, config, holdings, quotes):
    """Value the holdings and save the snapshot, or exit non-zero without writing it.

    A missing price means no snapshot, never a price of 0: the exit message
    lists every held ticker without one. When every missing price is a close
    not final yet, it says when the snapshot can be made instead (and ends
    with ``CLOSE_WAIT_MARKER``, the moment and the markets, for the routine;
    with ``CLOSE_CHECK_MARKER`` and the moment to look again when a closing
    print may make it earlier).
    A value the accounting rejects (such as a missing exchange rate) also ends
    with a message instead of a traceback.
    """
    date_str = args.date
    prices, currencies, fx_rates, *rest = quotes
    not_final = rest[0] if rest else {}
    price_estimates = rest[1] if len(rest) > 1 else {}
    rate_estimates = rest[2] if len(rest) > 2 else {}
    look_again = rest[3] if len(rest) > 3 else {}
    priced_by = rest[4] if len(rest) > 4 else {}

    if holdings.get("date") not in (None, date_str):
        # The snapshot is saved under the holdings' date: a file of another day must not become this one.
        sys.exit(f"❌ 未保存 {date_str} 快照：持仓的日期（{str(holdings.get('date'))[:10]}）与请求的日期不符；"
                 "请检查持仓文件。")
    holdings = {**holdings, "date": date_str}
    missing = missing_prices(holdings, prices)
    if missing and all(ticker in not_final for ticker in missing):
        until = max(not_final[ticker] for ticker in missing)
        markets = sorted({CALENDAR_MARKETS[ticker.partition(":")[0]] for ticker in missing})
        # A closing print may reach the source before ``until``: look again then, once every other close
        # missing is final. ``until`` stays the moment the day's last price is booked at the latest.
        looks = [look_again[ticker] for ticker in missing if ticker in look_again]
        fixed = [not_final[ticker] for ticker in missing if ticker not in look_again]
        check = max([min(looks), *fixed]) if looks else None     # never after ``until``
        marker =(f"{CLOSE_WAIT_MARKER}{until.astimezone(SHANGHAI_ZONE).isoformat(timespec='seconds')} "
                  f"MARKETS={','.join(markets)}")
        if check is not None:
            marker += f" {CLOSE_CHECK_MARKER}{check.astimezone(SHANGHAI_ZONE).isoformat(timespec='seconds')}"
        reason = close_wait_reason(markets, until, date_str, latest=check is not None)
        sys.exit(f"⏳ 未保存 {date_str} 快照：{reason}（{', '.join(missing)}）。"
                 f"收盘价确定之前不会记入盘中价。\n{marker}")
    if missing:
        sys.exit(f"❌ 未保存 {date_str} 快照：{MISSING_PRICE_MESSAGE}：{', '.join(missing)}。"
                 "缺少价格时不会按 0 计算；请检查网络或代理后重新运行。")

    prev_snapshot = load_previous_snapshot(date_str)
    all_snapshots = load_all_snapshots()
    history_values = load_all_history_values()

    try:
        snapshot = calculate_snapshot(holdings, prices, currencies, fx_rates, prev_snapshot, all_snapshots, history_values,
                                      price_estimates=price_estimates, rate_estimates=rate_estimates,
                                      priced_by=priced_by,
                                      # load_holdings: the confirmed ledger's holdings carry its revision
                                      holdings_source="ledger" if holdings.get("ledger_revision") is not None
                                      else "legacy_files")
    except ValueError as error:
        sys.exit(f"❌ 未保存 {date_str} 快照：{error}")

    if args.dry_run:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        save_snapshot(snapshot)
        sync_to_qr(snapshot, config)
        upsert_history(snapshot, date_str)

    # Print summary
    s = snapshot["summary"]
    print(f"\n{'='*50}")
    print(f"  总资产: ¥{s['total_value']/10000:.2f}万  成本: ¥{s['total_cost']/10000:.1f}万")
    print(f"  总盈亏: ¥{s['total_profit']/10000:.2f}万 ({percent_text(s['total_return_pct'])})")
    # Flow-adjusted change; an inferred deposit/withdrawal is printed separately, not as a gain.
    if s["market_daily_change"] is None:
        print(f"  日变动: {NO_EARLIER_DAY}")
    else:
        print(f"  日变动: ¥{s['market_daily_change']/10000:.2f}万 ({percent_text(s['market_daily_change_pct'])})")
    if s["capital_change"]:
        print(f"  出入金: ¥{s['capital_change']/10000:.2f}万")
    print(f"  月收益: {percent_text(s['month_return_pct'])}  最大回撤: {s['max_drawdown_pct']:.2f}%")
    for reason in s.get("estimated_reasons") or []:
        print(f"  估算: {reason}")
    for gname, gdata in snapshot["groups"].items():
        print(f"  [{gname}] ¥{gdata['total_value']/10000:.2f}万 ({percent_text(gdata['return_pct'])})")
    print(f"{'='*50}")
    return snapshot


if __name__ == "__main__":
    main()
