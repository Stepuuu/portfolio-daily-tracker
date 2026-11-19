"""The engine loads bars through BarRepository and turns data conditions into caveats."""
import pandas as pd
import pytest

from backtesting.data.repository import bars_sha256
from backtesting.engine import BacktestEngine, MarketDataUnavailableError, RunCancelled
from backtesting.strategies.base import Strategy


class BuyOnce(Strategy):
    name = "BuyOnce"

    def on_init(self):
        self.did_buy = False

    def on_bar(self):
        if not self.did_buy:
            self.buy(pct_cash=0.5)
            self.did_buy = True


def bars(start, end, price=10.0, step=0.01):
    dates = pd.bdate_range(start, end)
    close = [round(price + step * i, 2) for i in range(len(dates))]
    df = pd.DataFrame({"open": close, "high": [round(c * 1.01, 2) for c in close],
                       "low": [round(c * 0.99, 2) for c in close], "close": close,
                       "volume": 1_000_000.0, "amount": 10_000_000.0}, index=dates)
    df.index.name = "date"
    return df


class Loader:
    def __init__(self, frame=None, error=None):
        self.frame, self.error, self.calls = frame, error, []

    def __call__(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, start_date, end_date, adjust))
        if self.error is not None:
            raise self.error
        return self.frame.loc[start_date:end_date].copy()


def codes(result):
    return [c["code"] for c in result.caveats]


def test_a_legacy_cache_key_is_read_as_an_alias_without_downloading(monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    window = bars("2024-01-01", "2024-06-28")
    engine._store.save_daily("600000", window, "qfq")
    engine._store.record_coverage("600000", "qfq", "2024-01-01", "2024-06-28")
    loader = Loader(error=AssertionError("no download expected"))
    monkeypatch.setattr(engine._loader, "get_daily", loader)

    engine.add_data("600000", "2024-01-01", "2024-06-28", warmup=0)
    engine.add_strategy(BuyOnce)
    result = engine.run()

    coverage = result.to_dict()["coverage"]["600000"]
    assert loader.calls == []
    assert coverage["ticker"] == "SHA:600000" and coverage["store_key"] == "600000"
    assert coverage["cached"] is True and coverage["complete"] is True and coverage["offline"] is False
    # The cache stores 0 for the change and turnover columns this frame does not have.
    assert coverage["sha256"] == bars_sha256(window.assign(pct_change=0.0, turnover=0.0))
    # Adjusted prices are always stated, and so is the untaxed dividend.
    assert {"adjusted_prices", "dividend_tax_ignored"} <= set(codes(result))
    assert "offline_cache" not in codes(result) and "coverage_short" not in codes(result)


def test_a_partial_cache_runs_offline_only_when_allowed_and_says_so(monkeypatch):
    def engine_with_fragment():
        engine = BacktestEngine(initial_cash=100_000)
        engine._store.save_daily("SHA:600000", bars("2024-01-01", "2024-06-28"), "qfq")
        engine._store.record_download("SHA:600000", "qfq", "2024-01-01", "2024-06-30",
                                      fetched_at="2024-07-01T09:00:00")
        monkeypatch.setattr(engine._loader, "get_daily", Loader(error=ConnectionError("offline")))
        return engine

    with pytest.raises(MarketDataUnavailableError, match="ConnectionError"):
        engine_with_fragment().add_data("SHA:600000", "2024-01-01", "2024-12-31", warmup=0)

    engine = engine_with_fragment()
    engine.add_data("SHA:600000", "2024-01-01", "2024-12-31", warmup=0, allow_partial=True)
    engine.add_strategy(BuyOnce)
    result = engine.run()

    coverage = result.coverage["SHA:600000"]
    assert coverage.offline is True and coverage.complete is False and coverage.last == "2024-06-28"
    by_code = {c["code"]: c for c in result.caveats}
    assert by_code["offline_cache"]["level"] == "warning"
    assert "2024-01-01~2024-06-28" in by_code["offline_cache"]["text"]
    assert "2024-06-28 之后没有数据" in by_code["coverage_short"]["text"]
    assert result.period["last_date"] == "2024-06-28"


def split_series():
    frame = bars("2024-01-01", "2024-04-30", price=20.0, step=0.0)
    frame.loc["2024-03-01":, ["open", "high", "low", "close"]] /= 2   # an unadjusted 10送10
    return frame


def test_unadjusted_bars_with_a_split_carry_the_gap_dates(monkeypatch):
    engine = BacktestEngine(initial_cash=100_000)
    monkeypatch.setattr(engine._loader, "get_daily", Loader(split_series()))

    engine.add_data("600000", "2024-01-02", "2024-04-30", adjust="none", warmup=0)
    engine.add_strategy(BuyOnce)
    result = engine.run()

    assert result.coverage["600000"].suspected_corporate_actions == ["2024-03-01"]
    gap = next(c for c in result.caveats if c["code"] == "unadjusted_gaps")
    assert gap["level"] == "warning" and "2024-03-01" in gap["text"]
    assert "unadjusted_prices" in codes(result) and "adjusted_prices" not in codes(result)


def test_a_dataframe_with_a_split_is_scanned_too():
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("TEST", "", "", df=split_series(), adjust="", warmup=0)
    engine.add_strategy(BuyOnce)
    result = engine.run()

    assert result.coverage["TEST"].source == "dataframe"
    assert "unadjusted_gaps" in codes(result)
    # A frame passed in is not described as cached or adjusted data.
    assert "adjusted_prices" not in codes(result) and "offline_cache" not in codes(result)


def test_progress_is_reported_and_a_stop_request_cancels_the_run(monkeypatch):
    seen = []
    engine = BacktestEngine(initial_cash=100_000, progress=lambda stage, detail: seen.append((stage, detail)))
    monkeypatch.setattr(engine._loader, "get_daily", Loader(bars("2023-06-01", "2024-06-28")))
    engine.add_data("600000", "2024-01-01", "2024-06-28", warmup=5)
    engine.add_strategy(BuyOnce)
    engine.run()
    assert seen[0] == ("prepare_data", "download")
    assert [stage for stage, _ in seen[1:]] and {stage for stage, _ in seen[1:]} == {"simulate"}

    calls = {"n": 0}

    def stop_after_ten():
        calls["n"] += 1
        return calls["n"] > 10

    stopping = BacktestEngine(initial_cash=100_000, should_stop=stop_after_ten)
    stopping.add_data("TEST", "", "", df=bars("2024-01-01", "2024-06-28"), warmup=0)
    stopping.add_strategy(BuyOnce)
    with pytest.raises(RunCancelled):
        stopping.run()
