"""Backtests in demo mode: the demo's own tickers, synthetic bars, no network.

The demo portfolio (``engine/scripts/create_demo.py``) holds invented tickers
such as ``SHA:DEMO`` and ``NASDAQ:DEMO``. Their quotes are scripted in
``<PORTFOLIO_DIR>/fake_quotes.json`` and, when the demo was created with
``--ledger``, an opening balance in ``ledger.sqlite3`` lists them as positions.
Those two files are the list of tickers a demo may backtest; anything else is
refused, and nothing is ever downloaded.

Bars come from ``synthetic_bars``: a random walk seeded by the ticker's
SHA-256, generated once from a fixed start date to a fixed horizon and then
sliced, so a date has the same bar whatever range is asked for. Moves stay
inside the A-share 10% limit. The loader reports the source ``demo.synthetic``;
results carry the ``demo_data`` caveat saying the bars are not real.
"""
from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd

from backtesting.data.symbols import STOCK, Symbol

DEMO_SOURCE = "demo.synthetic"
EPOCH = "2015-01-05"
HORIZON = "2035-12-31"
MAX_MOVE = 0.09


class DemoDataError(RuntimeError):
    """No synthetic bars for this ticker (indices and unknown tickers)."""


def _portfolio_directory() -> Path:
    from core.ledger import portfolio_directory
    return Path(portfolio_directory())


def demo_tickers(directory: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """{ticker: {"name": ...}} from fake_quotes.json and the demo ledger's positions."""
    from core.market.instruments import InstrumentError, parse
    from core.market.sources_fake import FILE_NAME

    directory = Path(directory) if directory is not None else _portfolio_directory()
    found: Dict[str, Dict[str, str]] = {}

    def add(ticker, name):
        try:
            instrument = parse(ticker)
        except InstrumentError:
            return
        if instrument.kind == "index":
            return
        entry = found.setdefault(instrument.ticker, {"name": instrument.ticker})
        if name and entry["name"] == instrument.ticker:
            entry["name"] = str(name)

    try:
        script = json.loads((directory / FILE_NAME).read_text(encoding="utf-8"))
        quotes = script.get("quotes") if isinstance(script, dict) else None
        for ticker, quote in (quotes or {}).items():
            add(ticker, quote.get("name") if isinstance(quote, dict) else None)
    except (OSError, ValueError):
        pass
    try:
        from core.ledger import active_ledger
        ledger = active_ledger(directory)
        if ledger is not None:
            for account in ledger.state()["accounts"].values():
                for ticker, position in account.get("positions", {}).items():
                    add(ticker, position.get("name"))
    except Exception:       # a broken or locked demo ledger only hides its tickers
        pass
    return found


def demo_symbol(ticker: str) -> Symbol:
    """A Symbol for a demo ticker, routed to the synthetic loader."""
    from core.market.instruments import parse
    instrument = parse(ticker)
    return Symbol(ticker=instrument.ticker, exchange=instrument.exchange, code=instrument.code,
                  market=instrument.market, kind=STOCK, currency=instrument.currency,
                  provider=DEMO_SOURCE, provider_symbol=instrument.ticker, legacy_keys=())


@lru_cache(maxsize=16)
def _full_series(ticker: str) -> pd.DataFrame:
    seed = int.from_bytes(hashlib.sha256(ticker.encode("utf-8")).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(EPOCH, HORIZON)
    n = len(dates)
    phase = rng.uniform(0, 2 * math.pi)
    t = np.arange(n)
    # Slow regimes so trend and mean-reversion methods both have something to do.
    drift = 0.0002 + 0.0015 * np.sin(2 * math.pi * t / 260 + phase)
    returns = np.clip(drift + rng.normal(0, 0.016, n), -MAX_MOVE, MAX_MOVE)
    start_price = 20 + seed % 80
    close = start_price * np.exp(np.cumsum(np.log1p(returns)))
    prev = np.concatenate([[start_price], close[:-1]])
    open_ = prev * (1 + np.clip(rng.normal(0, 0.004, n), -0.03, 0.03))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    high = np.minimum(high, prev * (1 + MAX_MOVE))
    low = np.maximum(low, prev * (1 - MAX_MOVE))
    frame = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=dates).round(2)
    frame["high"] = frame[["open", "high", "close"]].max(axis=1)
    frame["low"] = frame[["open", "low", "close"]].min(axis=1)
    frame["volume"] = np.round(1_000_000 * (1 + 0.5 * np.abs(rng.normal(0, 1, n))), -2)
    frame["amount"] = (frame["volume"] * frame["close"]).round(2)
    frame.index.name = "date"
    return frame


def synthetic_bars(ticker: str, start, end) -> pd.DataFrame:
    """The demo bars of `ticker` in [start, end] (same values for a date whatever the range)."""
    frame = _full_series(str(ticker).strip().upper())
    return frame.loc[pd.Timestamp(start):pd.Timestamp(end)].copy()


class DemoLoader:
    """Stands in for DataLoader: synthetic bars for demo stock tickers, nothing else."""

    source = DEMO_SOURCE

    def __init__(self, now: Optional[Callable[[], pd.Timestamp]] = None):
        self._now = now or pd.Timestamp.now

    def get_daily(self, symbol, start_date, end_date, adjust="qfq") -> pd.DataFrame:
        ticker = symbol.ticker if isinstance(symbol, Symbol) else str(symbol).strip().upper()
        if ticker.startswith("IDX:") or ":" not in ticker:
            raise DemoDataError(f"演示模式没有 {ticker} 的行情")
        # Like a real source, today's bar is not complete yet.
        last = min(pd.Timestamp(end_date), pd.Timestamp(self._now()).normalize() - pd.Timedelta(days=1))
        frame = synthetic_bars(ticker, start_date, last)
        frame.attrs["source"] = DEMO_SOURCE
        return frame
