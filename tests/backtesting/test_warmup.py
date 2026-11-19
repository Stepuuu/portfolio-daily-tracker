"""Warm-up bars come from before the requested start; the requested window is
traded and measured in full."""
import math

import numpy as np
import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Strategy


def bars(start, end):
    dates = pd.date_range(start, end, freq="B")
    close = 10.0 + np.sin(np.arange(len(dates)) / 7.0) + np.arange(len(dates)) * 0.01
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                       "volume": 1_000_000.0, "amount": close * 1_000_000.0}, index=dates)
    df.index.name = "date"
    return df.round(4)


class Provider:
    """Stands in for DataLoader.get_daily: serves the part of `history` in range."""

    def __init__(self, history):
        self.history = history
        self.calls = []

    def __call__(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, start_date, end_date, adjust))
        return self.history.loc[start_date:end_date].copy()


class Probe(Strategy):
    name = "Probe"

    def on_init(self):
        self.first_date = None
        self.first_ma = None

    def on_bar(self):
        if self.first_date is None:
            self.first_date = self.data.current_bar.date
            self.first_ma = float(self.data.get_series("close", 60).mean()) if len(self.data.get_series("close", 60)) == 60 else math.nan


def engine_with(provider, monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    monkeypatch.setattr(engine._loader, "get_daily", provider)
    return engine


def test_warmup_is_loaded_before_the_requested_start(monkeypatch):
    history = bars("2023-01-02", "2024-12-31")
    provider = Provider(history)
    engine = engine_with(provider, monkeypatch)

    engine.add_data("600000", "2024-01-01", "2024-12-31", warmup=60)
    engine.add_strategy(Probe)
    result = engine.run()

    window = history.loc["2024-01-01":"2024-12-31"]
    first = window.index[0]
    # The loader was asked for an earlier start that holds at least 60 trading days.
    (_, asked_start, asked_end, _), = provider.calls
    assert asked_end == "2024-12-31"
    assert len(history.loc[asked_start:"2023-12-31"]) >= 60
    # Exactly 60 warm-up bars are kept, then the whole window.
    feed = engine._data_feeds["600000"]
    assert feed.total_bars == 60 + len(window)
    assert feed._df.index[60] == first
    # The strategy starts on the first requested bar with its indicators formed.
    assert result._strategy.first_date == first
    assert not math.isnan(result._strategy.first_ma)
    # Equity (and every statistic) covers the requested window only.
    assert result.equity_df.index[0] == first
    assert len(result.equity_df) == len(window)
    assert "warmup_short" not in [c["code"] for c in result.caveats]
    assert result.to_dict()["warmup"] == {
        "bars": 60, "available": 60, "requested_start": "2024-01-01", "first_date": "2024-01-01",
    }


def test_short_history_before_the_start_is_a_caveat_and_trading_starts_when_formed(monkeypatch):
    history = bars("2023-12-18", "2024-12-31")     # listed 10 trading days before the start
    engine = engine_with(Provider(history), monkeypatch)

    engine.add_data("600000", "2024-01-01", "2024-12-31", warmup=60)
    engine.add_strategy(Probe)
    result = engine.run()

    feed = engine._data_feeds["600000"]
    begins = feed._df.index[60]                    # 10 warm-up bars + 50 bars of the window
    assert result._strategy.first_date == begins
    assert result.equity_df.index[0] == begins
    caveat = next(c for c in result.caveats if c["code"] == "warmup_short")
    assert caveat["level"] == "warning"
    assert "10" in caveat["text"] and "60" in caveat["text"] and str(begins.date()) in caveat["text"]
    assert result.to_dict()["warmup"]["available"] == 10


def test_dataframe_with_a_start_uses_earlier_rows_as_warmup():
    history = bars("2023-06-01", "2025-03-31")
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("600000", "2024-01-01", "2024-12-31", df=history, warmup=20)
    engine.add_strategy(Probe)
    result = engine.run()

    window = history.loc["2024-01-01":"2024-12-31"]
    feed = engine._data_feeds["600000"]
    assert feed.total_bars == 20 + len(window)
    assert feed._df.index[-1] == window.index[-1]
    assert result.equity_df.index[0] == window.index[0]
    assert result.equity_df.index[-1] == window.index[-1]


def test_dataframe_without_a_start_keeps_its_first_bars_as_warmup():
    history = bars("2024-01-01", "2024-03-29")
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("600000", "", "", df=history, warmup=5)
    engine.add_strategy(Probe)
    result = engine.run()

    assert result._strategy.first_date == history.index[5]
    assert result.equity_df.index[0] == history.index[5]
    assert len(result.equity_df) == len(history) - 5


def test_covered_window_without_cached_warmup_runs_offline_with_a_caveat(monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    window = bars("2024-01-01", "2024-12-31")
    engine._store.save_daily("600000", window, "qfq")
    engine._store.record_coverage("600000", "qfq", "2024-01-01", "2024-12-31")
    calls = []

    def provider_down(*args, **kwargs):
        calls.append(args)
        raise ConnectionError("provider down")

    monkeypatch.setattr(engine._loader, "get_daily", provider_down)
    engine.add_data("600000", "2024-01-01", "2024-12-31", warmup=60)
    engine.add_strategy(Probe)
    result = engine.run()

    # The cached window holds none of the warm-up: the earlier bars are asked for once
    # (cr_g4d data/repository.py:404); the provider being down, the run uses the window.
    assert len(calls) == 1
    assert result._strategy.first_date == window.index[60]
    assert "warmup_short" in [c["code"] for c in result.caveats]


def test_cached_warmup_bars_are_used(monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    history = bars("2023-06-01", "2024-12-31")
    engine._store.save_daily("600000", history, "qfq")
    engine._store.record_coverage("600000", "qfq", "2023-06-01", "2024-12-31")
    monkeypatch.setattr(engine._loader, "get_daily", lambda *a, **k: pytest.fail("no download expected"))

    engine.add_data("600000", "2024-01-01", "2024-12-31", warmup=60)
    engine.add_strategy(Probe)
    result = engine.run()

    assert result._strategy.first_date == pd.Timestamp("2024-01-01")
    assert "warmup_short" not in [c["code"] for c in result.caveats]


def test_download_records_the_warmup_range_so_the_next_run_uses_the_cache(monkeypatch):
    history = bars("2023-01-02", "2024-12-31")
    provider = Provider(history)
    first = engine_with(provider, monkeypatch)
    first.add_data("600000", "2024-01-01", "2024-12-31", warmup=60)

    second = engine_with(provider, monkeypatch)
    second.add_data("600000", "2024-01-01", "2024-12-31", warmup=60)

    assert len(provider.calls) == 1
    assert second._data_feeds["600000"].total_bars == first._data_feeds["600000"].total_bars


def test_frame_without_a_date_index_is_rejected_by_the_quality_check():
    frame = bars("2024-01-01", "2024-03-29").reset_index(drop=True)
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    with pytest.raises(ValueError, match="DatetimeIndex"):
        engine.add_data("600000", "2024-02-01", "2024-03-29", df=frame, warmup=5)
