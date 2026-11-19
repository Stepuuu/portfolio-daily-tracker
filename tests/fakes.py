"""Shared test doubles for the market layer. Fictional data only.

    clock = FakeClock('2026-11-17 10:00')         # Shanghai wall time
    source = ScriptedSource({'SHA:600001': quote('SHA:600001', '10.50', prev_close='10', at=clock.now())})
    service = QuoteService([source], clock.session())
    runtime = fake_runtime(clock, source)          # for set_market_runtime()
    ledger = build_ledger(tmp_path, [opening('Example', cash={'CNY': '1000'})])
    write_fake_quotes(tmp_path, {...})             # for the file-driven FakeQuoteSource
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from core.ledger import Ledger
from core.market.calendar import SHANGHAI, SessionClock
from core.market.quotes import FxRate, LiveQuote, QuoteService, SourceError, build_fx, build_quote
from core.market.runtime import MarketRuntime
from core.market.sources_fake import FILE_NAME

DAY = '2026-11-16'   # a Monday; the default date of ledger events built here


def shanghai(text: str) -> datetime:
    """``'2026-11-17 10:00'`` (or with seconds) as an aware Shanghai time."""
    return datetime.fromisoformat(text).replace(tzinfo=SHANGHAI)


class FakeClock:
    """A settable ``now`` for ``SessionClock`` and for sources."""

    def __init__(self, at: str = '2026-11-17 10:00'):
        self.moment = shanghai(at)

    def __call__(self) -> datetime:
        return self.moment

    def now(self) -> datetime:
        return self.moment

    def set(self, at: str) -> None:
        self.moment = shanghai(at)

    def advance(self, **delta) -> None:
        self.moment += timedelta(**delta)

    def session(self, holidays=None) -> SessionClock:
        return SessionClock(self, holidays)


def quote(ticker: str, price=None, *, prev_close=None, at: Optional[datetime] = None,
          fetched_at: Optional[datetime] = None, source: str = 'scripted', currency: Optional[str] = None,
          **fields) -> LiveQuote:
    """A source-side quote; ``at`` is the exchange time (``None``: unknown).

    Like the scripted file source, it is the regular session's price unless the
    test says ``regular_session=False`` (a New York extended-hours price).
    """
    fields.setdefault('regular_session', True)
    return build_quote(ticker, source, fetched_at or at or shanghai('2026-11-17 10:00'), price=price,
                       prev_close=prev_close, quote_time=at, currency=currency, **fields)


def fx_rate(currency: str, rate=None, *, at: Optional[datetime] = None, source: str = 'scripted',
            reason: Optional[str] = None) -> FxRate:
    return build_fx(currency, source, at or shanghai('2026-11-17 10:00'), rate=rate, quote_time=at, reason=reason)


class ScriptedSource:
    """An in-memory ``QuoteSource`` / ``FxSource`` that records what it was asked.

    ``quotes`` and ``rates`` can be changed between calls. Set ``down`` to a
    message to make the whole source fail the way an unreachable one does.
    """

    def __init__(self, quotes: Optional[Mapping[str, LiveQuote]] = None,
                 rates: Optional[Mapping[str, FxRate]] = None, name: str = 'scripted'):
        self.name = name
        self.quotes: Dict[str, LiveQuote] = dict(quotes or {})
        self.rates: Dict[str, FxRate] = dict(rates or {})
        self.down: Optional[str] = None
        self.calls: List[tuple] = []
        self.fx_calls: List[tuple] = []

    def fetch(self, tickers: Sequence[str]) -> Dict[str, LiveQuote]:
        self.calls.append(tuple(tickers))
        if self.down:
            raise SourceError(self.down)
        return {ticker: self.quotes[ticker] for ticker in tickers if ticker in self.quotes}

    def fetch_fx(self, currencies: Sequence[str]) -> Dict[str, FxRate]:
        self.fx_calls.append(tuple(currencies))
        if self.down:
            raise SourceError(self.down)
        return {currency: self.rates[currency] for currency in currencies if currency in self.rates}


def fake_runtime(clock: FakeClock, *sources, ttl_seconds: float = 5) -> MarketRuntime:
    """A ``MarketRuntime`` over the given sources, for ``set_market_runtime``."""
    session = clock.session()
    return MarketRuntime(QuoteService(list(sources), session, ttl_seconds=ttl_seconds, fx_ttl_seconds=ttl_seconds),
                         session, 'fake')


# -- ledger -------------------------------------------------------------------

def opening(account: str = 'Example', *, cash: Optional[Mapping[str, str]] = None,
            positions: Iterable[Mapping] = (), date: str = DAY, contributed_cny: str = '0',
            fund_cny: str = '0') -> dict:
    return {'kind': 'opening', 'date': date, 'account': account, 'cash_balances': dict(cash or {}),
            'positions': [dict(position) for position in positions], 'contributed_cny': contributed_cny,
            'fund_cny': fund_cny}


def position(ticker: str, quantity: str, cost_price: str, name: Optional[str] = None) -> dict:
    entry = {'ticker': ticker, 'quantity': quantity, 'cost_price': cost_price}
    if name:
        entry['name'] = name
    return entry


def trade(kind: str, ticker: str, quantity: str, price: str, *, currency: str = 'CNY', fee: str = '0',
          account: str = 'Example', date: str = DAY) -> dict:
    return {'kind': kind, 'date': date, 'account': account, 'ticker': ticker, 'currency': currency,
            'quantity': quantity, 'price': price, 'fee': fee}


def book(ledger: Ledger, events: Sequence[dict]) -> dict:
    """Preview and confirm ``events``; return the receipt."""
    return ledger.confirm(ledger.propose(list(events))['id'])


def build_ledger(directory, events: Sequence[dict] = ()) -> Ledger:
    """A ledger at ``directory/ledger.sqlite3`` with ``events`` confirmed."""
    ledger = Ledger(Path(directory) / 'ledger.sqlite3')
    if events:
        book(ledger, events)
    return ledger


# -- the file-driven fake source ---------------------------------------------

def write_fake_quotes(directory, quotes: Optional[Mapping[str, Mapping]] = None, *,
                      fx: Optional[Mapping[str, Mapping]] = None, now: Optional[str] = None,
                      outage=None) -> Path:
    """Write ``fake_quotes.json`` (see ``core.market.sources_fake``)."""
    script: dict = {'quotes': dict(quotes or {}), 'fx': dict(fx or {})}
    if now is not None:
        script['now'] = now
    if outage is not None:
        script['outage'] = outage
    path = Path(directory) / FILE_NAME
    path.write_text(json.dumps(script, ensure_ascii=False), encoding='utf-8')
    return path
