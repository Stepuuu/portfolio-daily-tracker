"""The process-wide market runtime: one ``QuoteService`` and one ``SessionClock``.

It is built on first use and does not depend on ``AgentService``, because
demo mode and the browser fixture never create one.

Environment:

``TRACKER_DEMO_MODE=1`` or ``TRACKER_QUOTE_SOURCE=fake``
    scripted quotes from ``PORTFOLIO_DIR/fake_quotes.json``; no network
``TRACKER_QUOTE_SOURCE=eastmoney`` / ``tencent`` / ``yahoo``
    use only that source for quotes (default: Eastmoney, then Tencent, then
    Yahoo). Tencent has no currency rates: with ``tencent`` the rates still
    come from Yahoo, then Eastmoney
``TRACKER_EASTMONEY_PROXY=1``
    reach Eastmoney through the proxy in the environment, not directly
``TRACKER_TENCENT_PROXY=1``
    reach Tencent through the proxy in the environment, not directly
``TRACKER_YAHOO_PROXY=<url>``
    proxy for Yahoo; otherwise the usual ``HTTPS_PROXY`` applies
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Optional

from core.ledger import portfolio_directory

from .calendar import SessionClock
from .quotes import QuoteService
from .sources_fake import FakeQuoteSource

QUOTE_TTL_SECONDS = 5


@dataclass
class MarketRuntime:
    quotes: QuoteService
    clock: SessionClock
    mode: str   # 'live' or 'fake'


def fake_requested() -> bool:
    return (os.environ.get('TRACKER_DEMO_MODE') == '1'
            or os.environ.get('TRACKER_QUOTE_SOURCE', '').strip().lower() == 'fake')


def build_runtime() -> MarketRuntime:
    if fake_requested():
        # The directory is looked up on every fetch so the source follows
        # PORTFOLIO_DIR; the scripted ``now`` drives the session clock too.
        # No cache: reading a file is free, and an edit to it (or a pinned
        # clock that never advances) must show on the next request.
        source = FakeQuoteSource(portfolio_directory)
        clock = SessionClock(source.now)
        return MarketRuntime(QuoteService([source], clock, ttl_seconds=0, fx_ttl_seconds=0), clock, 'fake')
    from .sources_eastmoney import EastmoneySource
    from .sources_tencent import TencentSource
    from .sources_yahoo import YahooSource
    choice = os.environ.get('TRACKER_QUOTE_SOURCE', '').strip().lower()
    eastmoney = EastmoneySource(use_env_proxy=os.environ.get('TRACKER_EASTMONEY_PROXY') == '1')
    tencent = TencentSource(use_env_proxy=os.environ.get('TRACKER_TENCENT_PROXY') == '1')
    yahoo = YahooSource(proxy=os.environ.get('TRACKER_YAHOO_PROXY') or None)
    # Tencent before Yahoo: where Eastmoney cannot be reached (some mainland
    # hosts), Tencent answers directly and at once, Yahoo only through a proxy.
    sources = {'eastmoney': [eastmoney], 'tencent': [tencent], 'yahoo': [yahoo]}.get(
        choice, [eastmoney, tencent, yahoo])
    clock = SessionClock()
    # Yahoo's onshore rates come first so totals agree with the daily
    # snapshot; Eastmoney's offshore rates are the labelled fallback. Tencent
    # has no rates (``core.market.tencent``): selected alone, it leaves the
    # rates with those two, as the snapshot does.
    fx_sources = [source for source in (yahoo, eastmoney) if source in sources] or [yahoo, eastmoney]
    return MarketRuntime(QuoteService(sources, clock, ttl_seconds=QUOTE_TTL_SECONDS, fx_sources=fx_sources),
                         clock, 'live')


_runtime: Optional[MarketRuntime] = None
_lock = threading.Lock()


def get_market_runtime() -> MarketRuntime:
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = build_runtime()
        return _runtime


def set_market_runtime(runtime: Optional[MarketRuntime]) -> None:
    """Install a runtime (tests) or pass ``None`` to rebuild on next use."""
    global _runtime
    with _lock:
        _runtime = runtime
