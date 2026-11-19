"""Tencent batch quotes for mainland, Hong Kong and US listings (``qt.gtimg.cn``).

The format, the field positions and what was observed of them are in ``core.market.tencent``, which
the daily snapshot reads too. One request prices up to ``tencent.BATCH`` symbols.

Like Eastmoney, Tencent is reached directly by default, whatever proxy the environment names: on
mainland hosts where Eastmoney's endpoints failed (2026-11-19) it answered directly in about 0.2 s,
through the outbound proxy in about 2 s. ``use_env_proxy`` (``TRACKER_TENCENT_PROXY=1``) is for hosts
without a direct route. Its timeout is short, longer through a proxy: in the default order it is asked
between Eastmoney and Yahoo, so a hanging Tencent should cost a page as little as possible (and, as
any source that fails, it is then left alone for a while: ``QuoteService``).

It has no currency rates the app uses (see ``core.market.tencent``), so it is not an ``FxSource``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import httpx

from core.provider_errors import log_failure

from .http import open_client
from .instruments import Instrument, InstrumentError, parse
from .quotes import LiveQuote, SourceError, unavailable, utc_now
from .tencent import BATCH, HEADERS, QUOTE_URL, parse_response, quote_from_fields

logger = logging.getLogger(__name__)


class TencentSource:
    """``QuoteSource`` backed by batched requests to ``qt.gtimg.cn``."""

    name = 'tencent'

    def __init__(self, *, timeout: Optional[float] = None, use_env_proxy: bool = False,
                 transport: Optional[httpx.BaseTransport] = None,
                 now_fn: Callable[[], datetime] = utc_now):
        if timeout is None:
            timeout = 4.0 if use_env_proxy else 3.0
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 2.0))
        self._use_env_proxy = use_env_proxy
        self._transport = transport
        self._now_fn = now_fn

    def _request(self, symbols: Sequence[str]) -> Dict[str, List[str]]:
        rows: Dict[str, List[str]] = {}
        with open_client(timeout=self._timeout, headers=HEADERS, transport=self._transport,
                         use_env_proxy=self._use_env_proxy) as client:
            for start in range(0, len(symbols), BATCH):
                response = client.get(QUOTE_URL, params={'q': ','.join(symbols[start:start + BATCH])})
                response.raise_for_status()
                rows.update(parse_response(response.content))
        return rows

    def fetch(self, tickers: Sequence[str]) -> Dict[str, LiveQuote]:
        fetched_at = self._now_fn()
        results: Dict[str, LiveQuote] = {}
        wanted: List[Tuple[Instrument, str]] = []
        for ticker in tickers:
            try:
                instrument = parse(ticker)
            except InstrumentError as exc:
                results[ticker] = unavailable(ticker, self.name, fetched_at, f'无法识别的代码：{exc}')
                continue
            symbol = instrument.tencent_symbol
            if symbol:
                wanted.append((instrument, symbol))
            else:
                results[ticker] = unavailable(ticker, self.name, fetched_at,
                                              instrument.symbol_problem or '代码格式不属于该交易所',
                                              currency=instrument.currency)
        if not wanted:
            return results
        try:
            rows = self._request(list(dict.fromkeys(symbol for _, symbol in wanted)))
        except (httpx.HTTPError, ValueError) as exc:
            # The exception's class and message are the library's or the
            # server's words: only a fixed text reaches the reason.
            raise SourceError(log_failure(logger, 'tencent quotes', exc)) from None
        for instrument, symbol in wanted:
            results[instrument.ticker] = quote_from_fields(instrument, rows.get(symbol), fetched_at, source=self.name)
        return results
