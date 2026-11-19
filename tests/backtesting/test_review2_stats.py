"""Finding 11 of the second engine review: a statistic whose denominator is zero
(or whose sample is empty) is undefined - None with an explanation - never 0."""
import json

import pandas as pd
import pytest

from backtesting.analyzer.stats import BacktestStats
from backtesting.strategies.base import Trade


def curve(values, start="2024-01-01"):
    dates = pd.date_range(start, periods=len(values), freq="B")
    return [(d, v, v, 0.0) for d, v in zip(dates, values)]


def stats(values, trades=(), benchmark=None, initial_cash=100.0):
    return BacktestStats(equity_curve=curve(values), trades=list(trades), initial_cash=initial_cash,
                         benchmark_prices=benchmark)


def trade(direction, qty, price, day, pnl=0.0):
    return Trade(trade_id=f"{direction}{day}", order_id=f"o{day}", symbol="600000", direction=direction,
                 quantity=qty, price=price, commission=0.0,
                 timestamp=pd.Timestamp("2024-01-01") + pd.Timedelta(days=day), pnl=pnl)


def test_constant_positive_returns_leave_sharpe_and_sortino_undefined():
    # Reviewer's reproduction: 61 observations value[k] = 100 * 1.01^k. Every
    # excess return is positive and equal; both denominators are zero.
    s = stats([100 * 1.01 ** k for k in range(61)], benchmark=[10.0, 11.0])
    assert s.sharpe_ratio is None and s.sortino_ratio is None
    payload = s.to_dict()
    assert payload["sharpe_ratio"] is None and payload["sortino_ratio"] is None
    assert "标准差为 0" in payload["undefined"]["sharpe_ratio"]
    assert "下行偏差为 0" in payload["undefined"]["sortino_ratio"]
    assert payload["calmar_ratio"] is None and "回撤" in payload["undefined"]["calmar_ratio"]
    json.dumps(payload, allow_nan=False)


def test_too_few_returns_is_explained_differently_from_a_zero_denominator():
    s = stats([100 * 1.01 ** k for k in range(10)])
    assert s.to_dict()["sharpe_ratio"] is None
    assert "60" in s.undefined["sharpe_ratio"]


def test_defined_ratios_are_still_reported():
    s = stats([100, 101, 99, 102, 100, 103] * 11, benchmark=[10.0, 11.0])
    payload = s.to_dict()
    for key in ("sharpe_ratio", "sortino_ratio", "calmar_ratio", "volatility", "annualized_return",
                "benchmark_return", "alpha"):
        assert payload[key] is not None, key
    assert "sharpe_ratio" not in payload["undefined"]


def test_trade_ratios_without_a_denominator_are_undefined():
    only_wins = stats([100.0] * 10, trades=[trade("buy", 100, 10.0, 1), trade("sell", 100, 11.0, 2, pnl=100.0)])
    payload = only_wins.to_dict()
    assert payload["profit_factor"] is None and "亏损" in payload["undefined"]["profit_factor"]
    assert payload["payoff_ratio"] is None and payload["avg_loss"] is None
    assert payload["win_rate"] == 1.0 and payload["avg_profit"] == 100.0

    none = stats([100.0] * 10).to_dict()
    for key in ("win_rate", "profit_factor", "payoff_ratio", "avg_profit", "avg_loss", "avg_holding_days"):
        assert none[key] is None, key
        assert none["undefined"][key], key


def test_flat_equity_has_no_drawdown_and_an_undefined_calmar():
    payload = stats([100.0] * 70).to_dict()
    assert payload["max_drawdown"] == 0.0
    assert payload["calmar_ratio"] is None and payload["sharpe_ratio"] is None


def test_without_a_benchmark_the_excess_return_is_undefined_not_the_total_return():
    payload = stats([100.0, 110.0]).to_dict()
    assert payload["total_return"] == pytest.approx(0.10)
    assert payload["benchmark_return"] is None and payload["alpha"] is None
    assert "基准" in payload["undefined"]["alpha"]


def test_a_single_observation_has_no_returns_and_no_annualisation():
    payload = stats([100.0]).to_dict()
    for key in ("annualized_return", "volatility", "var_95", "downside_deviation"):
        assert payload[key] is None, key
    json.dumps(payload, allow_nan=False)


def test_report_shows_undefined_statistics_as_unavailable():
    from types import SimpleNamespace

    from backtesting.analyzer.report import ReportGenerator

    s = stats([100 * 1.01 ** k for k in range(61)])
    result = SimpleNamespace(stats=s, equity_df=pd.DataFrame(), strategy_name="S", run_date="d", run_id="r",
                             primary_symbol="600000", strategy_params={}, trades=[],
                             _broker=SimpleNamespace(initial_cash=100.0))
    markdown = ReportGenerator(result).to_markdown()
    assert "| 夏普比率 | 不可用 |" in markdown and "| 卡玛比率 | 不可用 |" in markdown
    assert "标准差为 0" in markdown
    assert "不可用" in ReportGenerator(result).to_html()
