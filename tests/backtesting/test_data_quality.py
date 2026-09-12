import pandas as pd
import pytest

from backtesting.data.quality import validate_daily_bars
from backtesting.engine import BacktestEngine
from tests.backtesting.test_engine import BuyOnceStrategy, make_df


def test_validate_daily_bars_accepts_clean_df():
    report = validate_daily_bars(make_df(), symbol="TEST")

    assert report.ok
    assert report.metrics["ohlc_violation_count"] == 0


def test_validate_daily_bars_rejects_ohlc_violation():
    df = make_df()
    df.loc[df.index[0], "high"] = 1.0

    report = validate_daily_bars(df, symbol="BAD")

    assert not report.ok
    assert any("OHLC consistency" in error for error in report.errors)


def test_validate_daily_bars_rejects_duplicate_index():
    df = make_df()
    df.index = pd.DatetimeIndex([df.index[0], *df.index[1:-1], df.index[1]])

    report = validate_daily_bars(df, symbol="DUP")

    assert not report.ok
    assert any("duplicate index" in error for error in report.errors)


def test_engine_rejects_bad_backtest_input():
    df = make_df()
    df.loc[df.index[0], "close"] = -1
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)

    with pytest.raises(ValueError, match="数据质量检查失败"):
        engine.add_data("BAD", "", "", df=df, warmup=1)


def test_engine_exposes_data_quality_report():
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("TEST", "", "", df=make_df(), warmup=1)
    engine.add_strategy(BuyOnceStrategy)

    result = engine.run()

    assert result.data_quality_reports["TEST"].ok
