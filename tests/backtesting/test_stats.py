"""Numeric checks for BacktestStats on synthetic equity curves."""
import json
import math

import numpy as np
import pandas as pd
import pytest

from backtesting.analyzer.stats import BacktestStats
from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Strategy, Trade

RF_DAILY = 0.03 / 252


def stats_for_returns(returns, initial_cash=100_000.0):
    dates = pd.date_range("2024-01-01", periods=len(returns) + 1, freq="B")
    values = [initial_cash]
    for value in returns:
        values.append(values[-1] * (1 + value))
    curve = [(date, total, total, 0.0) for date, total in zip(dates, values)]
    return BacktestStats(equity_curve=curve, trades=[], initial_cash=initial_cash)


def expected_downside_and_sortino(returns):
    excess = np.array(returns) - RF_DAILY
    downside = math.sqrt(np.mean(np.minimum(excess, 0.0) ** 2))
    return downside * math.sqrt(252), excess.mean() / downside * math.sqrt(252)


def test_sortino_for_alternating_gains_and_losses_is_not_zero():
    returns = [0.012, -0.010] * 60
    stats = stats_for_returns(returns)
    downside, sortino = expected_downside_and_sortino(returns)

    assert stats.downside_deviation == pytest.approx(downside, rel=1e-9)
    assert stats.sortino_ratio == pytest.approx(sortino, rel=1e-9)
    # Hand-checked: mean excess 0.000881 / downside 0.007155 * sqrt(252).
    assert stats.sortino_ratio == pytest.approx(1.954, abs=0.005)
    # Same excess return and annualisation as the Sharpe ratio next to it.
    excess = pd.Series(returns) - RF_DAILY
    assert stats.sharpe_ratio == pytest.approx(excess.mean() / excess.std() * math.sqrt(252), rel=1e-9)


def test_sortino_counts_a_single_losing_day_over_all_periods():
    # 61 returns: Sharpe and Sortino are only reported from 60 daily returns on.
    returns = [0.01] * 30 + [-0.02] + [0.01] * 30
    stats = stats_for_returns(returns)
    downside, sortino = expected_downside_and_sortino(returns)

    assert stats.downside_deviation == pytest.approx(downside, rel=1e-9)
    assert stats.sortino_ratio == pytest.approx(sortino, rel=1e-9)
    assert stats.sortino_ratio > stats.sharpe_ratio > 0


def test_sortino_is_undefined_without_any_downside():
    # Zero downside deviation: the ratio has no value; it is None (null), not 0
    # (review finding 11).
    stats = stats_for_returns([0.01] * 60)

    assert stats.downside_deviation == 0.0
    assert stats.sortino_ratio is None
    assert stats.to_dict()["sortino_ratio"] is None and stats.undefined["sortino_ratio"]


# ------------------------------------------------------------------ #
#  Ratios need enough data; CAGR uses calendar days
# ------------------------------------------------------------------ #

def test_sharpe_and_sortino_are_omitted_below_60_daily_returns():
    short = stats_for_returns([0.012, -0.010] * 29 + [0.01])       # 59 returns
    assert short.sharpe_ratio is None and short.sortino_ratio is None
    assert short.to_dict()["sharpe_ratio"] is None and short.to_dict()["sortino_ratio"] is None
    assert short.volatility > 0                                       # still reported
    enough = stats_for_returns([0.012, -0.010] * 30)                  # 60 returns
    assert enough.sharpe_ratio is not None and enough.sortino_ratio is not None


def test_volatility_is_the_daily_standard_deviation_times_sqrt_252():
    returns = [0.012, -0.010] * 40
    stats = stats_for_returns(returns)
    assert stats.volatility == pytest.approx(np.std(returns) * math.sqrt(252), rel=1e-9)


def test_cagr_uses_calendar_days():
    # Two calendar years of A-share trading days (about 242 a year, not 252).
    dates = pd.bdate_range("2022-01-04", "2024-01-04")[::1]
    dates = dates[np.linspace(0, len(dates) - 1, 485).round().astype(int)]
    values = np.linspace(100_000, 121_000, len(dates))
    curve = [(d, v, v, 0.0) for d, v in zip(dates, values)]
    stats = BacktestStats(equity_curve=curve, trades=[], initial_cash=100_000)

    years = (dates[-1] - dates[0]).days / 365.25
    assert stats.total_return == pytest.approx(0.21)
    assert stats.annualized_return == pytest.approx(1.21 ** (1 / years) - 1, rel=1e-9)
    assert stats.annualized_return == pytest.approx(0.10, rel=2e-3)


# ------------------------------------------------------------------ #
#  Trade statistics are on round trips
# ------------------------------------------------------------------ #

def trade(direction, qty, price, day, pnl=0.0, reason="signal", symbol="600000", commission=0.0):
    return Trade(trade_id=f"{direction}{day}", order_id="FORCE_CLOSE" if reason == "end_mark" else f"o{day}",
                 symbol=symbol, direction=direction, quantity=qty, price=price, commission=commission,
                 timestamp=pd.Timestamp("2024-01-01") + pd.Timedelta(days=day), pnl=pnl, reason=reason)


def flat_curve(days=10):
    dates = pd.date_range("2024-01-01", periods=days, freq="D")
    return [(d, 100_000.0, 100_000.0, 0.0) for d in dates]


def stats_for_trades(trades):
    return BacktestStats(equity_curve=flat_curve(), trades=trades, initial_cash=100_000)


def test_one_round_trip_split_into_three_fills_is_one_trade():
    stats = stats_for_trades([
        trade("buy", 300, 10.0, 1),
        trade("sell", 100, 11.0, 2, pnl=100.0),
        trade("sell", 100, 11.0, 3, pnl=100.0),
        trade("sell", 100, 11.0, 4, pnl=100.0),
    ])
    assert stats.total_trades == 1
    (trip,) = stats.round_trips
    assert trip["pnl"] == pytest.approx(300.0)
    assert (trip["quantity"], trip["fills"], trip["entry_date"], trip["exit_date"]) == (300, 4, "2024-01-02", "2024-01-05")
    assert stats.win_rate == 1.0


def test_partial_entries_are_merged_into_one_round_trip():
    stats = stats_for_trades([
        trade("buy", 100, 10.0, 1), trade("buy", 200, 10.3, 2),
        trade("sell", 300, 10.0, 5, pnl=-60.0),
        trade("buy", 100, 10.0, 6), trade("sell", 100, 10.5, 7, pnl=50.0),
    ])
    assert stats.total_trades == 2
    assert [trip["pnl"] for trip in stats.round_trips] == [pytest.approx(-60.0), pytest.approx(50.0)]
    assert stats.round_trips[0]["entry_price"] == pytest.approx(10.2)


def test_zero_pnl_round_trips_are_neither_wins_nor_losses():
    stats = stats_for_trades([
        trade("buy", 100, 10.0, 1), trade("sell", 100, 11.0, 2, pnl=100.0),
        trade("buy", 100, 10.0, 3), trade("sell", 100, 9.5, 4, pnl=-50.0),
        trade("buy", 100, 10.0, 5), trade("sell", 100, 10.0, 6, pnl=0.0),
    ])
    assert stats.total_trades == 3
    assert (stats.winning_trades, stats.losing_trades, stats.breakeven_trades) == (1, 1, 1)
    assert stats.win_rate == pytest.approx(0.5)
    assert stats.avg_loss == pytest.approx(-50.0)


def test_payoff_ratio_and_profit_factor():
    stats = stats_for_trades([
        trade("buy", 100, 10.0, 1), trade("sell", 100, 11.0, 2, pnl=100.0),
        trade("buy", 100, 10.0, 3), trade("sell", 100, 12.0, 4, pnl=200.0),
        trade("buy", 100, 10.0, 5), trade("sell", 100, 9.5, 6, pnl=-50.0),
    ])
    assert stats.payoff_ratio == pytest.approx(150.0 / 50.0)          # average win / average loss
    assert stats.profit_factor == pytest.approx(300.0 / 50.0)         # gross profit / gross loss
    payload = stats.to_dict()
    assert payload["payoff_ratio"] == pytest.approx(3.0) and payload["profit_factor"] == pytest.approx(6.0)


def test_forced_final_close_is_flagged_and_kept_out_of_the_win_rate():
    stats = stats_for_trades([
        trade("buy", 100, 10.0, 1), trade("sell", 100, 11.0, 2, pnl=100.0),
        trade("buy", 100, 10.0, 3), trade("sell", 100, 9.7, 8, pnl=-30.0, reason="end_mark"),
    ])
    assert stats.total_trades == 2
    assert stats.closed_trades == 1
    assert [trip["end_mark"] for trip in stats.round_trips] == [False, True]
    assert stats.round_trips[1]["exit_reason"] == "end_mark"
    assert stats.win_rate == 1.0
    assert stats.max_single_loss == 0.0
    assert stats.end_mark_pnl == pytest.approx(-30.0)
    assert stats.to_dict()["profit_factor"] is None                   # no completed losing trade


def test_no_round_trips_leave_the_ratios_undefined():
    stats = stats_for_trades([])
    payload = stats.to_dict()
    assert payload["total_trades"] == 0
    assert payload["payoff_ratio"] is None and payload["profit_factor"] is None


# ------------------------------------------------------------------ #
#  In the engine result
# ------------------------------------------------------------------ #

class BuyAndHold(Strategy):
    name = "BuyAndHold"

    def on_init(self):
        self.done = False

    def on_bar(self):
        if not self.done:
            self.buy(pct_cash=0.5)
            self.done = True


def run_engine(symbol="600000", start="2024-01-02", periods=30, strategy=BuyAndHold):
    dates = pd.date_range(start, periods=periods, freq="B")
    close = [10.0 + 0.05 * i for i in range(periods)]
    df = pd.DataFrame({"open": close, "high": [c * 1.01 for c in close], "low": [c * 0.99 for c in close],
                       "close": close, "volume": 1_000_000.0, "amount": 1e7}, index=dates)
    df.index.name = "date"
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data(symbol, "", "", df=df, adjust="", warmup=1)
    engine.add_strategy(strategy)
    return engine.run()


def test_result_reports_round_trips_and_the_end_mark_separately():
    result = run_engine()
    payload = result.to_dict()

    assert payload["total_trades"] == 1 and payload["closed_trades"] == 0
    (trip,) = payload["round_trips"]
    assert trip["end_mark"] is True and trip["exit_reason"] == "end_mark"
    end_mark = payload["end_mark"]
    assert end_mark["date"] == str(result.equity_df.index[-1].date())
    assert end_mark["pnl"] == pytest.approx(trip["pnl"])
    assert [p["symbol"] for p in end_mark["positions"]] == ["600000"]
    assert result.trades[-1].reason == "end_mark"
    codes = [c["code"] for c in payload["caveats"]]
    assert "end_mark" in codes and "few_trades" in codes
    json.dumps(payload, allow_nan=False)


def test_result_states_how_figures_are_annualised():
    assumptions = run_engine().to_dict()["assumptions"]
    assert "365.25" in assumptions["annualization"] and "sqrt(252)" in assumptions["annualization"]
    assert "60" in assumptions["annualization"]
    assert assumptions["fee_schedule_version"]
    assert "TAF" in assumptions["fees"]


def test_period_reports_the_measured_dates():
    result = run_engine()
    period = result.to_dict()["period"]
    assert period["first_date"] == str(result.equity_df.index[0].date())
    assert period["last_date"] == str(result.equity_df.index[-1].date())
    assert period["bars"] == len(result.equity_df)
    assert period["calendar_days"] == (result.equity_df.index[-1] - result.equity_df.index[0]).days


def test_trades_before_the_first_fee_row_carry_a_fee_assumed_caveat():
    result = run_engine(start="2014-03-03")
    caveat = next(c for c in result.caveats if c["code"] == "fee_assumed")
    assert "2014" in caveat["text"]
    assert "fee_assumed" not in [c["code"] for c in run_engine().caveats]


def test_end_mark_on_a_limit_down_board_says_it_could_not_be_sold():
    dates = pd.date_range("2024-01-02", periods=6, freq="B")
    close = [10.0, 10.0, 10.0, 10.0, 10.0, 9.0]                   # the last bar is a one-price limit-down
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                       "volume": 1_000_000.0, "amount": 1e7}, index=dates)
    df.index.name = "date"
    engine = BacktestEngine(initial_cash=100_000, use_cache=False)
    engine.add_data("600000", "", "", df=df, adjust="", warmup=1)
    engine.add_strategy(BuyAndHold)
    result = engine.run()

    assert result.to_dict()["end_mark"]["positions"][0]["blocked"] == "limit_down"
    caveat = next(c for c in result.caveats if c["code"] == "end_mark")
    assert caveat["level"] == "warning" and "跌停" in caveat["text"]
