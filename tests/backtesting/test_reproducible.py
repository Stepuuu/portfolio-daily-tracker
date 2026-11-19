"""The same settings give the same trades whether the bars were just downloaded
or come from the local cache."""
import math

import numpy as np
import pandas as pd

from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Strategy

FEATURES = ["prev_close", "change", "returns", "ma_5", "ma_20", "ma_60", "ema_12", "macd_line",
            "rsi_14", "hist_vol_20", "bb_upper_20", "atr_14", "volume_ma_20", "vwap"]


def provider_history():
    """What a provider returns: base columns plus its own change and turnover columns."""
    dates = pd.date_range("2022-06-01", "2024-12-31", freq="B")
    n = len(dates)
    close = 10 + 2 * np.sin(np.arange(n) / 9.0) + np.arange(n) * 0.004
    df = pd.DataFrame({
        "open": close * 0.998, "high": close * 1.012, "low": close * 0.988, "close": close,
        "volume": 2_000_000.0 + (np.arange(n) % 5) * 10_000, "amount": close * 2_000_000.0,
        "turnover": 1.5,
    }, index=dates).round(4)
    df["pct_change"] = (df["close"].pct_change() * 100).round(2)
    df["change"] = df["close"].diff().round(2)
    df.index.name = "date"
    return df


class Provider:
    def __init__(self):
        self.history = provider_history()
        self.calls = []

    def __call__(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, start_date, end_date, adjust))
        return self.history.loc[start_date:end_date].copy()


class FactorStrategy(Strategy):
    """Trades on cached feature columns read through get_factor."""
    name = "FactorStrategy"

    def on_bar(self):
        fast, slow, rsi = (self.data.get_factor(name) for name in ("ma_5", "ma_20", "rsi_14"))
        if any(math.isnan(value) for value in (fast, slow, rsi)):
            return
        holding = self.get_position()
        if not holding and fast > slow and rsi < 70:
            self.buy(pct_cash=0.9)
        elif holding and fast < slow:
            self.close_position()


def run(provider, monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    monkeypatch.setattr(engine._loader, "get_daily", provider)
    engine.add_data("600000", "2023-06-01", "2024-12-31", warmup=60)
    engine.add_strategy(FactorStrategy)
    return engine, engine.run()


def fills(result):
    return [(t.timestamp, t.direction, t.quantity, round(t.price, 6), round(t.commission, 6)) for t in result.trades]


def test_download_then_cache_give_identical_features_and_trades(monkeypatch):
    provider = Provider()
    first_engine, first = run(provider, monkeypatch)
    second_engine, second = run(provider, monkeypatch)

    assert len(provider.calls) == 1, "the second run must come from the cache"
    assert len(fills(first)) >= 4
    assert fills(first) == fills(second)
    assert first.to_dict()["total_return"] == second.to_dict()["total_return"]
    first_df = first_engine._data_feeds["600000"]._df
    second_df = second_engine._data_feeds["600000"]._df
    pd.testing.assert_frame_equal(first_df[FEATURES], second_df[FEATURES])


def test_features_are_computed_from_past_bars_only():
    # Changing a later bar must not change any feature value of an earlier bar.
    history = provider_history().loc["2023-01-02":"2023-12-29"]
    changed = history.copy()
    changed.iloc[-1, changed.columns.get_loc("close")] *= 1.5
    changed.iloc[-1, changed.columns.get_loc("high")] *= 1.5

    def features(df):
        engine = BacktestEngine(initial_cash=100_000, use_cache=False)
        engine.add_data("600000", "", "", df=df, warmup=0)
        return engine._data_feeds["600000"]._df[FEATURES]

    pd.testing.assert_frame_equal(features(history).iloc[:-1], features(changed).iloc[:-1])
