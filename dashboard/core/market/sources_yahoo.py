"""Yahoo Finance chart endpoint: Hong Kong and US fallback, and currencies.

``GET /v8/finance/chart/<symbol>?interval=1d&range=1d`` returns one result
whose ``meta`` block holds:

    regularMarketPrice     latest price of the regular session (never an
                           extended-hours trade: those are preMarket* /
                           postMarket*, not read here)
    regularMarketTime      time of that price, Unix seconds (exchange side)
    chartPreviousClose     close of the session before the one-day range
    regularMarketDayHigh / regularMarketDayLow / regularMarketVolume
    currency, symbol, shortName / longName

and ``indicators.quote[0].open`` holds the day's open. Hong Kong prices are
delayed by about 15 minutes; ``regularMarketTime`` shows it, so the quote is
labelled ``delayed`` rather than live. There is one request per symbol, run
on a small thread pool.

``USDCNY=X`` and ``HKDCNY=X`` are the onshore rates the snapshot engine uses.

Measured against the live endpoint (see ``scripts/measure_quote_latency.py``):
mainland and Hong Kong prices arrive about 15 minutes old, currency rates
about 10 seconds old.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Dict, Optional, Sequence
from urllib.parse import quote as urlquote

import httpx

from core.provider_errors import ERR_PARSE, log_failure

from .http import open_client
from .instruments import Instrument, InstrumentError, parse
from .quotes import FxRate, LiveQuote, SourceError, build_fx, build_quote, from_epoch, unavailable, utc_now

CHART_URL = 'https://query1.finance.yahoo.com/v8/finance/chart'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
FX_SYMBOLS = {'USD': 'USDCNY=X', 'HKD': 'HKDCNY=X'}
# Yahoo sometimes answers USDCNY=X under its canonical name. (There is no such
# alias for HKD: HKD=X is USD/HKD, a different pair.)
FX_ALIASES = {'USD': ('CNY=X',)}

logger = logging.getLogger(__name__)


def chart_meta(payload, symbol: str, aliases: Sequence[str] = ()) -> dict:
    """The ``meta`` block for ``symbol``; ``ValueError`` when it is not there."""
    try:
        result = payload['chart']['result'][0]
        meta = result['meta']
    except (KeyError, IndexError, TypeError):
        raise ValueError('unexpected response') from None
    accepted = {symbol.upper(), *(alias.upper() for alias in aliases)}
    if not isinstance(meta, dict) or str(meta.get('symbol', '')).upper() not in accepted:
        raise ValueError('response is for another symbol')
    try:
        opens = result['indicators']['quote'][0]['open']
        meta = {**meta, '_open': opens[-1]}
    except (KeyError, IndexError, TypeError):
        pass
    return meta


def quote_from_chart(instrument: Instrument, payload, fetched_at: datetime, *, source: str = 'yahoo') -> LiveQuote:
    symbol = instrument.yahoo_symbol or ''
    try:
        meta = chart_meta(payload, symbol)
    except ValueError as exc:
        logger.warning('yahoo chart for %s not usable: %s', instrument.ticker, exc)
        return unavailable(instrument.ticker, source, fetched_at, ERR_PARSE, currency=instrument.currency)
    currency = meta.get('currency')
    if instrument.kind == 'equity' and currency != instrument.currency:
        # A symbol that resolves to a listing in another currency is the wrong
        # security; its price must not be valued in the ledger's currency.
        return unavailable(instrument.ticker, source, fetched_at, '数据源返回的币种与该交易所不符',
                           currency=instrument.currency)
    return build_quote(instrument.ticker, source, fetched_at, price=meta.get('regularMarketPrice'),
                       prev_close=meta.get('chartPreviousClose'), name=meta.get('shortName') or meta.get('longName'),
                       open=meta.get('_open'), high=meta.get('regularMarketDayHigh'),
                       low=meta.get('regularMarketDayLow'), volume=meta.get('regularMarketVolume'),
                       currency=instrument.currency, quote_time=from_epoch(meta.get('regularMarketTime')),
                       # regularMarketPrice / regularMarketTime are the regular session's: Yahoo keeps
                       # extended-hours trades apart (preMarket* / postMarket*, not read here).
                       regular_session=True)


def fx_from_chart(currency: str, payload, fetched_at: datetime, *, source: str = 'yahoo') -> FxRate:
    symbol = FX_SYMBOLS[currency]
    label = f'{source}:{symbol}'
    try:
        meta = chart_meta(payload, symbol, FX_ALIASES.get(currency, ()))
    except ValueError as exc:
        logger.warning('yahoo chart for %s not usable: %s', symbol, exc)
        return build_fx(currency, label, fetched_at, reason=ERR_PARSE)
    if meta.get('currency') != 'CNY':
        return build_fx(currency, label, fetched_at, reason='数据源返回的不是人民币汇率')
    return build_fx(currency, label, fetched_at, rate=meta.get('regularMarketPrice'),
                    quote_time=from_epoch(meta.get('regularMarketTime')))


class _Failed:
    def __init__(self, reason: str, answered: bool):
        self.reason, self.answered = reason, answered


class YahooSource:
    """``QuoteSource`` and ``FxSource`` over the public chart endpoint."""

    name = 'yahoo'

    def __init__(self, *, timeout: float = 5.0, proxy: Optional[str] = None, base_url: str = CHART_URL,
                 max_workers: int = 8, transport: Optional[httpx.BaseTransport] = None,
                 now_fn: Callable[[], datetime] = utc_now):
        # Without an explicit proxy the usual HTTPS_PROXY variables apply:
        # Yahoo is commonly reachable only through one.
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 3.0))
        self._proxy = proxy or None
        self._base_url = base_url.rstrip('/')
        self._max_workers = max_workers
        self._transport = transport
        self._now_fn = now_fn

    def _client(self) -> httpx.Client:
        return open_client(timeout=self._timeout, headers=HEADERS, transport=self._transport, proxy=self._proxy)

    def _charts(self, symbols: Sequence[str]) -> Dict[str, object]:
        """Payload per symbol, or a ``_Failed`` saying why that one has none.

        Raises ``SourceError`` when no request got an answer from Yahoo at all.
        """
        def one(client: httpx.Client, symbol: str):
            try:
                response = client.get(f'{self._base_url}/{urlquote(symbol, safe="")}',
                                      params={'interval': '1d', 'range': '1d'})
                if response.status_code in (400, 404, 422):
                    return _Failed('数据源没有这只证券', answered=True)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                # Only a fixed text: the class and message are not ours.
                return _Failed(log_failure(logger, 'yahoo chart', exc), answered=False)
        symbols = list(dict.fromkeys(symbols))
        try:
            with self._client() as client, ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                charts = dict(zip(symbols, pool.map(lambda symbol: one(client, symbol), symbols)))
        except (httpx.HTTPError, ValueError) as exc:   # e.g. an unusable proxy setting
            raise SourceError(log_failure(logger, 'yahoo client', exc)) from None
        if charts and all(isinstance(chart, _Failed) and not chart.answered for chart in charts.values()):
            raise SourceError(next(iter(charts.values())).reason)
        return charts

    def fetch(self, tickers: Sequence[str]) -> Dict[str, LiveQuote]:
        fetched_at = self._now_fn()
        results: Dict[str, LiveQuote] = {}
        wanted: Dict[str, Instrument] = {}
        for ticker in tickers:
            try:
                instrument = parse(ticker)
            except InstrumentError as exc:
                results[ticker] = unavailable(ticker, self.name, fetched_at, f'无法识别的代码：{exc}')
                continue
            if instrument.yahoo_symbol:
                wanted[ticker] = instrument
            else:
                results[ticker] = unavailable(ticker, self.name, fetched_at,
                                              instrument.symbol_problem or '代码格式不属于该交易所',
                                              currency=instrument.currency)
        charts = self._charts([instrument.yahoo_symbol for instrument in wanted.values()]) if wanted else {}
        for ticker, instrument in wanted.items():
            payload = charts.get(instrument.yahoo_symbol)
            if isinstance(payload, _Failed):
                results[ticker] = unavailable(ticker, self.name, fetched_at, payload.reason,
                                              currency=instrument.currency)
            else:
                results[ticker] = quote_from_chart(instrument, payload, fetched_at, source=self.name)
        return results

    def fetch_fx(self, currencies: Sequence[str]) -> Dict[str, FxRate]:
        fetched_at = self._now_fn()
        wanted = [currency for currency in currencies if currency in FX_SYMBOLS]
        charts = self._charts([FX_SYMBOLS[currency] for currency in wanted]) if wanted else {}
        results: Dict[str, FxRate] = {}
        for currency in wanted:
            payload = charts.get(FX_SYMBOLS[currency])
            if isinstance(payload, _Failed):
                results[currency] = build_fx(currency, f'{self.name}:{FX_SYMBOLS[currency]}', fetched_at,
                                             reason=payload.reason)
            else:
                results[currency] = fx_from_chart(currency, payload, fetched_at, source=self.name)
        return results
