"""Eastmoney batch quotes for mainland, Hong Kong and US listings.

One request to ``/api/qt/ulist.np/get`` prices every ticker. With ``fltt=2``
the fields are plain numbers, or ``"-"`` when the exchange has no value:

    f2    latest price            f15   day high
    f18   previous close          f16   day low
    f17   open                    f5    volume (lots of 100 for A-shares)
    f12   code                    f13   Eastmoney market id (the ``secid`` prefix)
    f14   name                    f124  time of the latest update, Unix seconds

``f124`` is the exchange-side update time, not the time of our request: it was
seconds old for actively traded names during a session, stood at the close
(US 16:00 New York) for a closed market, and does not advance for a security
that is not trading. ``scripts/measure_quote_latency.py`` re-checks this.
On 2026-11-18, 100 minutes after New York's close with after-hours trading
under way, every US stock had ``f124`` 16:00:00 and ``f2`` the official close;
12 minutes after the close ``f2`` was not the official close yet. Eastmoney
does not label ``f2`` the regular session's price, so these quotes do not
claim it (``LiveQuote.regular_session`` stays false): a New York stamp later
than the close-print window, or one before the close, is not taken as the
close (``quotes.assess``).

Before its first trade of a session, and while suspended, a security has
``f2`` ``"-"`` and no volume, open, high or low; only then is the previous
close ``f18`` used as its price (``price_basis`` ``prev_close``).

A security Eastmoney does not know is simply missing from ``diff``; when it
knows none of them the answer is ``{"rc": 102, "data": null}``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import httpx

from core.provider_errors import log_failure

from .http import open_client
from .instruments import CN, Instrument, InstrumentError, parse
from .quotes import (PREV_CLOSE, FxRate, LiveQuote, SourceError, build_fx, build_quote, from_epoch, positive,
                     to_decimal, unavailable, utc_now)

QUOTE_URL = 'https://push2.eastmoney.com/api/qt/ulist.np/get'
FIELDS = 'f2,f5,f12,f13,f14,f15,f16,f17,f18,f124'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/',
           'Accept': 'application/json,text/plain,*/*'}
# Offshore renminbi: the only spot rates this endpoint serves. The onshore
# rate differs by a few pips; the source label says which one was used.
FX_SECIDS = {'USD': '133.USDCNH', 'HKD': '133.HKDCNH'}
BATCH = 80
NONE_FOUND = 102   # ``rc`` when not one requested secid exists

logger = logging.getLogger(__name__)


def parse_rows(payload) -> Dict[str, dict]:
    """Index a ``ulist.np`` response by ``secid`` (``"<f13>.<f12>"``)."""
    if not isinstance(payload, dict):
        raise ValueError('unexpected response')
    data = payload.get('data')
    if payload.get('rc') == NONE_FOUND and data is None:
        return {}
    if payload.get('rc') != 0:
        raise ValueError('unexpected response')
    rows = data.get('diff') if isinstance(data, dict) else None
    if rows is None:
        return {}   # every requested secid was unknown
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError('unexpected response')
    return {f"{row.get('f13')}.{row.get('f12')}": row for row in rows if isinstance(row, dict)}


ACTIVITY_FIELDS = ('f5', 'f17', 'f15', 'f16')   # volume, open, high, low


def untraded(row: dict) -> bool:
    """True when the row shows no volume and no open, high or low.

    Each must be explicitly empty ('-', '' or null) or zero; a value that
    cannot be read is not evidence that nothing traded.
    """
    for field in ACTIVITY_FIELDS:
        value = row.get(field)
        if value is None or (isinstance(value, str) and value.strip() in ('', '-')):
            continue
        number = to_decimal(value)
        if number is None or number != 0:
            return False
    return True


def quote_from_row(instrument: Instrument, row: Optional[dict], fetched_at: datetime, *,
                   source: str = 'eastmoney', prev_close_when_untraded: bool = True) -> LiveQuote:
    if row is None:
        return unavailable(instrument.ticker, source, fetched_at, '数据源没有这只证券', currency=instrument.currency)
    price, prev_close = positive(row.get('f2')), positive(row.get('f18'))
    volume = to_decimal(row.get('f5'))
    if volume is not None and instrument.market == CN and instrument.kind == 'equity':
        volume *= 100
    common = dict(name=row.get('f14'), prev_close=prev_close, currency=instrument.currency,
                  quote_time=from_epoch(row.get('f124')))
    if price is None:
        # Before its first trade of the session, and while suspended, the
        # exchange publishes no latest price, only the previous close, and no
        # volume or day range. Only that payload establishes that nothing has
        # traded, so only then may the previous close stand in for the price
        # (as ``prev_close``, never live or closed). A missing price next to a
        # volume or a day range means the price is simply unknown.
        if not untraded(row):
            return build_quote(instrument.ticker, source, fetched_at,
                               reason='数据源未给出最新价，但本交易日已有成交，无法确定当前价格', **common)
        if prev_close is not None and prev_close_when_untraded:
            return build_quote(instrument.ticker, source, fetched_at, price=prev_close, price_basis=PREV_CLOSE,
                               reason='本交易日尚无成交（或停牌），按昨收价估值', **common)
        return build_quote(instrument.ticker, source, fetched_at,
                           reason='数据源未给出最新价（本交易日尚无成交或停牌）', **common)
    return build_quote(instrument.ticker, source, fetched_at, price=price, open=row.get('f17'),
                       high=row.get('f15'), low=row.get('f16'), volume=volume, **common)


class EastmoneySource:
    """``QuoteSource`` and ``FxSource`` backed by one batched HTTP request."""

    name = 'eastmoney'

    def __init__(self, *, timeout: float = 4.0, use_env_proxy: bool = False,
                 prev_close_when_untraded: bool = True,
                 transport: Optional[httpx.BaseTransport] = None,
                 now_fn: Callable[[], datetime] = utc_now):
        # Eastmoney is reached directly by default: a proxy configured for
        # foreign sites is usually slower or refused. ``use_env_proxy`` is for
        # hosts that have no direct route.
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 3.0))
        self._use_env_proxy = use_env_proxy
        self._prev_close_when_untraded = prev_close_when_untraded
        self._transport = transport
        self._now_fn = now_fn

    def _request(self, secids: Sequence[str]) -> Dict[str, dict]:
        rows: Dict[str, dict] = {}
        with open_client(timeout=self._timeout, headers=HEADERS, transport=self._transport,
                         use_env_proxy=self._use_env_proxy) as client:
            for start in range(0, len(secids), BATCH):
                response = client.get(QUOTE_URL, params={
                    'fltt': '2', 'invt': '2', 'fields': FIELDS, 'secids': ','.join(secids[start:start + BATCH])})
                response.raise_for_status()
                rows.update(parse_rows(response.json()))
        return rows

    def fetch(self, tickers: Sequence[str]) -> Dict[str, LiveQuote]:
        fetched_at = self._now_fn()
        results: Dict[str, LiveQuote] = {}
        wanted: List[Tuple[Instrument, Tuple[str, ...]]] = []
        for ticker in tickers:
            try:
                instrument = parse(ticker)
            except InstrumentError as exc:
                results[ticker] = unavailable(ticker, self.name, fetched_at, f'无法识别的代码：{exc}')
                continue
            secids = instrument.eastmoney_secids
            if secids:
                wanted.append((instrument, secids))
            else:
                results[ticker] = unavailable(ticker, self.name, fetched_at,
                                              instrument.symbol_problem or '代码格式不属于该交易所',
                                              currency=instrument.currency)
        if not wanted:
            return results
        try:
            rows = self._request(list(dict.fromkeys(secid for _, secids in wanted for secid in secids)))
        except (httpx.HTTPError, ValueError) as exc:
            # The exception's class and message are the library's or the
            # server's words: only a fixed text reaches the reason.
            raise SourceError(log_failure(logger, 'eastmoney quotes', exc)) from None
        for instrument, secids in wanted:
            row = next((rows[secid] for secid in secids if secid in rows), None)
            results[instrument.ticker] = quote_from_row(
                instrument, row, fetched_at, source=self.name,
                prev_close_when_untraded=self._prev_close_when_untraded)
        return results

    def fetch_fx(self, currencies: Sequence[str]) -> Dict[str, FxRate]:
        fetched_at = self._now_fn()
        wanted = {currency: FX_SECIDS[currency] for currency in currencies if currency in FX_SECIDS}
        if not wanted:
            return {}
        try:
            rows = self._request(list(wanted.values()))
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(log_failure(logger, 'eastmoney rates', exc)) from None
        return {currency: fx_from_row(currency, rows.get(secid), fetched_at, source=self.name)
                for currency, secid in wanted.items()}


def fx_from_row(currency: str, row: Optional[dict], fetched_at: datetime, *, source: str = 'eastmoney') -> FxRate:
    label = f'{source}:{currency}CNH'
    if row is None:
        return build_fx(currency, label, fetched_at, reason='数据源没有这个汇率')
    return build_fx(currency, label, fetched_at, rate=row.get('f2'), quote_time=from_epoch(row.get('f124')))
