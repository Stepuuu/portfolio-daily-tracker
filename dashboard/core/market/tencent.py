"""Tencent's quote format (``qt.gtimg.cn``): the answer, and a row read as a ``LiveQuote``.

No network and no ``httpx`` here: Today's ``sources_tencent.TencentSource`` and the daily snapshot
(``engine/scripts/portfolio_snapshot.py``, whose image has only ``requests``) read the same answers
with these functions.

``GET https://qt.gtimg.cn/?q=sh600519,hk00700,usAAPL`` answers in GBK, one statement per symbol it
knows, the fields separated by ``~``::

    v_sh600519="1~贵州茅台~600519~1460.21~1442.66~1442.00~42692~...";

A symbol it does not know is left out; when it knows none of them the answer is
``v_pv_none_match="1";``. The fields read (positions checked against live answers on 2026-11-19 for
Shanghai, Shenzhen, STAR and ChiNext shares, ETFs, B shares, every index of
``instruments.INDICES``, Hong Kong shares and funds, US shares and ETFs)::

    field  mainland (sh, sz)           Hong Kong (hk)               US (us)
    0      market id 1 / 51            100                          200
    1      name                        name                         name
    2      code                        code                         symbol.exchange (AAPL.OQ, .DJI)
    3      latest price                (the previous close when nothing has traded, see below)
    4      previous close
    5      open
    6      volume: lots of 100 for     shares                       shares, pre-market included
           A-share listings
    30     YYYYMMDDHHMMSS Shanghai     YYYY/MM/DD HH:MM:SS HK       YYYY-MM-DD HH:MM:SS New York
    33/34  day high / day low
    40     status: '' trading, 'S' suspended, 'D' delisted, 'U' not listed
    82     currency                    75: currency                 35: currency

The time is the data's own, not the time of the request. Seen between 22:30 and 23:00 Shanghai,
hours after the closes: Shanghai rows stamped 16:14, Shenzhen 16:15 (refreshes after the 15:00
close, as Eastmoney's 15:34 and 16:11), Hong Kong shares 16:08:07-16:08:13 (the closing auction, as
Eastmoney and Yahoo stamp it), HSTECH 16:08:30 and the Hang Seng Index 18:31:05; a Hong Kong share
suspended since 2026-11-06 still showed 2026/11/06 16:08:54. During New York's session US rows were
seconds old. Whether Hong Kong prices arrive delayed during the session was not observed; since the
stamps are the data's own, a delayed price should carry its older time and be ``delayed``.

A row that has not traded in the session repeats the previous close as its price, with open, high
and low 0: Hong Kong shares without a trade that day, A shares suspended ('S'), delisted ('D') or
never listed ('U', those two stamped 09:00:00 the same day), and US shares half a minute before
the open (AAPL stamped 09:30:00, the pre-market's volume in field 6). So, as for Eastmoney, the previous
close stands in for the price (``price_basis`` ``prev_close``) only when the row shows no open, high
or low, no volume (US: whatever the volume), and a price equal to the previous close. A delisted or
never listed row, a status this module does not know, and a price without an open, high or low
that differs from the previous close (BRK.A at 09:30:01: one trade, open, high and low still 0) are
unavailable: the next source is asked.

Tencent does not name its price the regular session's (Yahoo does), so its quotes do not claim it:
a New York stamp later than the close-print window is not taken as the close (``quotes.assess``).
Before the open field 3 did not follow pre-market trades; after-hours was not observed. The daily
snapshot books a Tencent row as a close only by a stricter rule (``portfolio_snapshot._tencent_check``).

Not read: Beijing (``bj``, market id 62), which the ledger has no prefix for; currency rates.
Tencent answers ``whUSDCNY`` and ``whHKDCNY`` (market id 310, a rate and a Shanghai time), but on
2026-11-19 at 22:41 Shanghai its USD rate was 6.6680 against 6.6572 for Yahoo's onshore USDCNY=X
(0.16 % apart, HKD 0.8496 against 0.8499), its previous close was 0 and what its other fields
mean is not documented: rates stay with Yahoo and Eastmoney.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Union

from .calendar import ZONES
from .instruments import CN, HK, US, Instrument
from .quotes import PREV_CLOSE, LiveQuote, build_quote, positive, to_decimal, unavailable

QUOTE_URL = 'https://qt.gtimg.cn/'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}
# Symbols per request: about 600 characters of query, well within what the endpoint takes.
BATCH = 60
NONE_FOUND = 'pv_none_match'

MARKET_IDS = {'sh': '1', 'sz': '51', 'hk': '100', 'us': '200'}
PRICE, PREV, OPEN, VOLUME, TIME, HIGH, LOW, STATUS = 3, 4, 5, 6, 30, 33, 34, 40
CURRENCY = {CN: 82, HK: 75, US: 35}
TIME_FORMATS = {CN: '%Y%m%d%H%M%S', HK: '%Y/%m/%d %H:%M:%S', US: '%Y-%m-%d %H:%M:%S'}

TRADING, SUSPENDED, DELISTED, NOT_LISTED = '', 'S', 'D', 'U'
STATUS_REASONS = {DELISTED: '数据源标记该证券已退市，不提供价格', NOT_LISTED: '数据源标记该证券尚未上市，不提供价格'}
UNKNOWN_STATUS = '数据源标记了未识别的交易状态，不采用其价格'
UNTRADED = '本交易日尚无成交（或停牌），按昨收价估值'
SUSPENDED_REASON = '数据源标记停牌，本交易日没有成交，按昨收价估值'
NO_DAY_RANGE = '数据源给出的最新价没有本交易日的开盘价和最高最低价，不能确认是成交价'
MISMATCH = '数据源返回的数据与请求的代码不符'

_STATEMENT = re.compile(r'v_([A-Za-z0-9_.]+)="([^"]*)"')

logger = logging.getLogger(__name__)


def parse_response(body: Union[bytes, str]) -> Dict[str, List[str]]:
    """The answer's rows by symbol (``sh600519`` -> its fields); ``{}`` when Tencent knows none of them.

    ``ValueError`` when the body holds no statement at all (a block page, an empty answer).
    """
    text = body.decode('gb18030', errors='replace') if isinstance(body, bytes) else str(body)
    rows = {symbol: value.split('~') for symbol, value in _STATEMENT.findall(text)}
    if not rows:
        raise ValueError('unexpected response')
    rows.pop(NONE_FOUND, None)
    return rows


def _same_listing(symbol: str, fields: List[str]) -> bool:
    """True when the row is the one asked for: its market id and its code (US: before the exchange suffix)."""
    if fields[0].strip() != MARKET_IDS.get(symbol[:2]):
        return False
    code, answered = symbol[2:], fields[2].strip()
    if symbol.startswith('us') and answered != code:
        answered = answered.rsplit('.', 1)[0]
    return answered == code


def quote_time(market: str, text: str) -> Optional[datetime]:
    """The row's time in its exchange's zone, or ``None`` when it does not read as one."""
    try:
        return datetime.strptime(text.strip(), TIME_FORMATS[market]).replace(tzinfo=ZONES[market])
    except (KeyError, ValueError):
        return None


def row_status(fields: Optional[List[str]]) -> Optional[str]:
    """The row's status (``TRADING``, ``SUSPENDED``, ``DELISTED``, ``NOT_LISTED`` or another letter); ``None``
    without a row long enough to have one."""
    return fields[STATUS].strip() if fields is not None and len(fields) > STATUS else None


def _zero(value: Optional[Decimal]) -> bool:
    return value is None or value == 0


def quote_from_fields(instrument: Instrument, fields: Optional[List[str]], fetched_at: datetime, *,
                      source: str = 'tencent') -> LiveQuote:
    """A ``LiveQuote`` of ``instrument`` from its row (``None``: Tencent left it out)."""
    ticker = instrument.ticker
    symbol = instrument.tencent_symbol or ''
    if fields is None:
        return unavailable(ticker, source, fetched_at, '数据源没有这只证券', currency=instrument.currency)
    market = instrument.market
    if len(fields) <= max(CURRENCY[market], STATUS) or not _same_listing(symbol, fields):
        logger.warning('tencent row for %s is not the listing asked for', ticker)
        return unavailable(ticker, source, fetched_at, MISMATCH, currency=instrument.currency)
    if fields[CURRENCY[market]].strip() != instrument.currency:
        # A listing in another currency (Hong Kong's USD counters) is not valued in the ledger's.
        return unavailable(ticker, source, fetched_at, '数据源返回的币种与该交易所不符', currency=instrument.currency)
    price, prev_close = positive(fields[PRICE]), positive(fields[PREV])
    volume = to_decimal(fields[VOLUME])
    if volume is not None and market == CN and instrument.kind == 'equity':
        volume *= 100
    common = dict(name=fields[1], prev_close=prev_close, currency=instrument.currency,
                  quote_time=quote_time(market, fields[TIME]))
    status = fields[STATUS].strip()
    if status not in (TRADING, SUSPENDED):
        # A delisted or never listed row repeats an old price under today's date.
        return build_quote(ticker, source, fetched_at, reason=STATUS_REASONS.get(status, UNKNOWN_STATUS), **common)
    if all(positive(fields[index]) is None for index in (OPEN, HIGH, LOW)):
        # Nothing traded in the session: the row repeats the previous close as its price. Only that row
        # establishes it (no volume either, except in New York, where the pre-market's counts); a price
        # without a day range that is not the previous close is not a price this reading can place.
        quiet = market == US or _zero(volume)
        if quiet and (price is None or price == prev_close):
            if prev_close is None:
                return build_quote(ticker, source, fetched_at, reason='数据源未给出最新价（本交易日尚无成交或停牌）',
                                   **common)
            return build_quote(ticker, source, fetched_at, price=prev_close, price_basis=PREV_CLOSE,
                               reason=SUSPENDED_REASON if status == SUSPENDED else UNTRADED, **common)
        return build_quote(ticker, source, fetched_at, reason=NO_DAY_RANGE, **common)
    return build_quote(ticker, source, fetched_at, price=price, open=fields[OPEN], high=fields[HIGH],
                       low=fields[LOW], volume=volume, **common)
