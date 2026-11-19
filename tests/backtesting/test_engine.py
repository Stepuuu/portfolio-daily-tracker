import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Strategy


class BuyOnceStrategy(Strategy):
    name = "BuyOnce"
    parameters = {"position_pct": 0.5}

    def on_init(self):
        self.did_buy = False

    def on_bar(self):
        if not self.did_buy:
            self.buy(pct_cash=self.parameters["position_pct"])
            self.did_buy = True


class LookaheadProbeStrategy(Strategy):
    name = "LookaheadProbe"

    def on_init(self):
        self.did_buy = False
        self.signal_close = None

    def on_bar(self):
        if not self.did_buy:
            self.signal_close = self.data.close()
            self.buy(quantity=100)
            self.did_buy = True


def make_df():
    dates = pd.date_range("2024-01-01", periods=8, freq="B")
    df = pd.DataFrame(
        {
            "open": [10, 11, 12, 13, 14, 15, 16, 17],
            "high": [11, 12, 13, 14, 15, 16, 17, 18],
            "low": [9, 10, 11, 12, 13, 14, 15, 16],
            "close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5],
            "volume": [1_000_000] * 8,
            "amount": [10_000_000] * 8,
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def test_engine_runs_strategy_and_force_closes_position():
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("TEST", "", "", df=make_df(), warmup=1)
    engine.add_strategy(BuyOnceStrategy)

    result = engine.run()

    assert len(result.trades) >= 2
    assert result.equity_df.empty is False
    assert result.stats.total_trades >= 1


def test_market_order_submitted_on_signal_bar_fills_next_bar_open():
    df = make_df()
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("TEST", "", "", df=df, warmup=1)
    engine.add_strategy(LookaheadProbeStrategy)

    result = engine.run()
    buy_trade = next(trade for trade in result.trades if trade.direction == "buy")

    assert result._strategy.signal_close == df["close"].iloc[1]
    assert buy_trade.timestamp == df.index[2]
    assert buy_trade.price == df["open"].iloc[2] * (1 + engine.broker.config.slippage_pct)


def warmup_start(start, warmup):
    """The earlier start the engine asks the loader for, to hold the warm-up bars."""
    days = BacktestEngine.warmup_calendar_days(warmup)
    return (pd.Timestamp(start) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def make_range_df(start, end):
    dates = pd.date_range(start, end, freq="B")
    n = len(dates)
    df = pd.DataFrame(
        {
            "open": [10.0] * n,
            "high": [10.5] * n,
            "low": [9.5] * n,
            "close": [10.0] * n,
            "volume": [1_000_000.0] * n,
            "amount": [10_000_000.0] * n,
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def test_partial_cache_hit_downloads_the_full_requested_range(monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    engine._store.save_daily("600000", make_range_df("2023-06-01", "2023-06-21"), "qfq")
    full = make_range_df("2020-01-01", "2024-01-01")
    calls = []

    def fake_get_daily(symbol, start_date, end_date, adjust="qfq"):
        calls.append((symbol, start_date, end_date, adjust))
        return full.copy()

    monkeypatch.setattr(engine._loader, "get_daily", fake_get_daily)

    engine.add_data("600000", "2020-01-01", "2024-01-01", warmup=1)

    # The download covers the requested range plus room for the warm-up bar before it,
    # under the canonical key (the bare code stays readable as an alias).
    load_start = warmup_start("2020-01-01", 1)
    assert calls == [("SHA:600000", load_start, "2024-01-01", "qfq")]
    assert engine._data_feeds["600000"].total_bars == len(full)
    assert [(s, e) for s, e, _ in engine._store.get_coverage("SHA:600000", "qfq")] == [(load_start, "2024-01-01")]
    assert engine.coverage["600000"].ticker == "SHA:600000" and engine.coverage["600000"].cached is False


def test_full_cache_hit_does_not_download(monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    full = make_range_df("2020-01-01", "2024-01-01")
    engine._store.save_daily("600000", full, "qfq")
    # Saved bars alone are not coverage; the download that wrote them records it.
    engine._store.record_coverage("600000", "qfq", "2020-01-01", "2024-01-01")

    def fail_get_daily(*args, **kwargs):
        raise AssertionError("cache covers the range; no download expected")

    monkeypatch.setattr(engine._loader, "get_daily", fail_get_daily)

    engine.add_data("600000", "2020-01-01", "2024-01-01", warmup=1)

    assert engine._data_feeds["600000"].total_bars == len(full)


class CountingLoader:
    """Stands in for DataLoader.get_daily: serves `frame` or raises `error`."""

    def __init__(self, frame=None, error=None):
        self.frame = frame
        self.error = error
        self.calls = []

    def __call__(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, start_date, end_date, adjust))
        if self.error is not None:
            raise self.error
        return self.frame.copy()


def add_with(monkeypatch, loader, start="2024-01-01", end="2024-12-31"):
    engine = BacktestEngine(initial_cash=100_000)
    monkeypatch.setattr(engine._loader, "get_daily", loader)
    engine.add_data("600000", start, end, warmup=1)
    return engine


def test_interior_hole_in_the_cache_is_downloaded(monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    for start, end in (("2024-01-02", "2024-01-15"), ("2024-12-18", "2024-12-31")):
        engine._store.save_daily("600000", make_range_df(start, end), "qfq")
        engine._store.record_download("600000", "qfq", start, end, fetched_at="2025-01-05T09:00:00")
    full = make_range_df("2024-01-01", "2024-12-31")
    loader = CountingLoader(full)

    add_with(monkeypatch, loader)

    assert loader.calls == [("SHA:600000", warmup_start("2024-01-01", 1), "2024-12-31", "qfq")]
    assert len(add_with(monkeypatch, loader)._data_feeds["600000"]._df) == len(full)
    assert len(loader.calls) == 1


def test_missing_tail_in_the_cache_is_downloaded(monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    engine._store.save_daily("600000", make_range_df("2024-01-01", "2024-03-22"), "qfq")
    engine._store.record_download("600000", "qfq", "2024-01-01", "2024-03-22", fetched_at="2024-03-23T09:00:00")
    loader = CountingLoader(make_range_df("2024-01-01", "2024-03-29"))

    engine = add_with(monkeypatch, loader, end="2024-03-31")

    assert len(loader.calls) == 1
    assert engine._data_feeds["600000"]._df.index[-1] == pd.Timestamp("2024-03-29")


def test_late_listing_is_downloaded_once(monkeypatch):
    # Listed 2024-06-03: the full-year request returns fewer bars than the range,
    # and the requested interval is still recorded, so later runs use the cache.
    listed = make_range_df("2024-06-03", "2024-12-31")
    loader = CountingLoader(listed)

    for _ in range(3):
        engine = add_with(monkeypatch, loader)
        assert len(engine._data_feeds["600000"]._df) == len(listed)

    assert len(loader.calls) == 1


def test_suspension_before_the_end_is_downloaded_once(monkeypatch):
    # No bars after 2024-11-15 (suspended); the request through 2024-12-31 is covered.
    suspended = make_range_df("2024-01-01", "2024-11-15")
    loader = CountingLoader(suspended)

    for _ in range(2):
        add_with(monkeypatch, loader)

    assert len(loader.calls) == 1


def test_offline_run_uses_a_covered_cache(monkeypatch):
    full = make_range_df("2024-01-01", "2024-12-31")
    add_with(monkeypatch, CountingLoader(full))
    offline = CountingLoader(error=ConnectionError("network unreachable"))

    engine = add_with(monkeypatch, offline)

    assert offline.calls == []
    assert len(engine._data_feeds["600000"]._df) == len(full)


@pytest.mark.parametrize("recorded", [True, False], ids=["partial-download", "legacy-cache"])
def test_offline_run_on_an_uncovered_range_fails_clearly(monkeypatch, recorded):
    engine = BacktestEngine(initial_cash=100_000)
    engine._store.save_daily("600000", make_range_df("2024-01-01", "2024-06-28"), "qfq")
    if recorded:
        engine._store.record_download("600000", "qfq", "2024-01-01", "2024-06-30", fetched_at="2024-07-01T09:00:00")
    offline = CountingLoader(error=ConnectionError("network unreachable"))
    monkeypatch.setattr(engine._loader, "get_daily", offline)

    with pytest.raises(RuntimeError) as excinfo:
        engine.add_data("600000", "2024-01-01", "2024-12-31", warmup=1)

    message = str(excinfo.value)
    assert "600000" in message and "2024-01-01~2024-12-31" in message
    assert "ConnectionError" in message
    assert ("2024-01-01~2024-06-30" if recorded else "无") in message
    assert len(offline.calls) == 1
    # Never run on the cached fragment.
    assert engine._data_feeds == {}


class NeverTradeStrategy(Strategy):
    name = "NeverTrade"

    def on_init(self):
        self.first_bar_close = None

    def on_bar(self):
        if self.first_bar_close is None:
            self.first_bar_close = self.data.close()


def test_benchmark_return_starts_at_the_first_tradable_bar():
    # The price halves during the 5 warmup bars, then gains 10% while the strategy can act.
    closes = [20.0, 18.0, 16.0, 14.0, 12.0, 10.0, 10.2, 10.4, 10.6, 10.8, 11.0]
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
            "amount": [10_000_000] * len(closes),
        },
        index=dates,
    )
    df.index.name = "date"
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("TEST", "", "", df=df, warmup=5)
    engine.add_strategy(NeverTradeStrategy)

    result = engine.run()

    assert result._strategy.first_bar_close == 10.0
    assert result.stats.total_return == 0.0
    assert result.stats.benchmark_return == pytest.approx(0.10)
    # Staying in cash through a warmup-period fall is not outperformance.
    assert result.stats.alpha == pytest.approx(-0.10)
    assert result.to_dict()["benchmark_return"] == pytest.approx(0.10)


def flat_df(closes):
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    df = pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1_000_000.0] * len(closes), "amount": [10_000_000.0] * len(closes)},
        index=dates,
    )
    df.index.name = "date"
    return df


def test_zero_warmup_benchmark_spans_every_bar():
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("TEST", "", "", df=flat_df([10.0 + i for i in range(11)]), warmup=0)
    engine.add_strategy(NeverTradeStrategy)

    result = engine.run()

    assert result.stats.benchmark_return == pytest.approx(1.0)
    assert result.stats.alpha == pytest.approx(-1.0)


@pytest.mark.parametrize("warmup", [-1, -60])
def test_negative_warmup_is_rejected_before_loading_data(monkeypatch, warmup):
    # iloc[-1:] would leave one benchmark price and report a 0% benchmark.
    engine = BacktestEngine(initial_cash=100_000)

    def fail_get_daily(*args, **kwargs):
        raise AssertionError("an invalid warmup must be rejected before any download")

    monkeypatch.setattr(engine._loader, "get_daily", fail_get_daily)

    with pytest.raises(ValueError, match="warmup"):
        engine.add_data("TEST", "", "", df=flat_df([10.0 + i for i in range(11)]), warmup=warmup)
    with pytest.raises(ValueError, match="warmup"):
        engine.add_data("600000", "2024-01-01", "2024-03-31", warmup=warmup)
    assert engine._data_feeds == {}
