"""Third review: annualisation never overflows (PART 1 (B) service.py:1798, the
statistics side in analyzer/stats.py)."""
import pandas as pd
import pytest

from backtesting.analyzer.stats import BacktestStats
from backtesting.broker.simulated import BrokerConfig
from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Strategy


def test_a_tenfold_rise_in_one_day_has_no_annualised_return_instead_of_overflowing():
    # 1,000 -> 10,000 between two consecutive days: (10) ** 365.25 is beyond a float.
    stats = BacktestStats(equity_curve=[("2024-01-02", 1_000.0, 1_000.0, 0.0),
                                        ("2024-01-03", 10_000.0, 0.0, 10_000.0)],
                          trades=[], initial_cash=1_000.0)
    out = stats.to_dict()
    assert out["total_return"] == pytest.approx(9.0)
    assert out["annualized_return"] is None and "annualized_return" in out["undefined"]
    assert out["calmar_ratio"] is None


class BuyFirst(Strategy):
    """All the cash into the stock at the first open, like the service's holding run."""

    def on_init(self):
        self.done = False

    def on_bar(self):
        if not self.done:
            self.buy(pct_cash=1.0)
            self.done = True


def test_the_reviewers_two_sessions_give_a_result_not_an_exception():
    # Reviewer's reproduction: two US sessions, opens [1, 1], closes [1, 10]; holding
    # from the first open made the statistics raise OverflowError.
    index = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03"]), name="date")
    frame = pd.DataFrame({"open": [1.0, 1.0], "high": [1.0, 10.0], "low": [1.0, 1.0], "close": [1.0, 10.0],
                          "volume": 1e6, "amount": 1e6}, index=index)
    e = BacktestEngine(initial_cash=1_000.0, use_cache=False, broker_config=BrokerConfig(
        slippage_pct=0.0, commission_buy=0.0, commission_sell=0.0, min_commission=0.0))
    e.add_data("AAPL", "", "", df=frame, adjust="qfq", warmup=0)
    e.add_strategy(BuyFirst)
    result = e.run()
    payload = result.to_dict()                    # used to raise OverflowError
    assert payload["total_return"] == pytest.approx(9.0, abs=0.01)
    assert payload["annualized_return"] is None and "annualized_return" in payload["undefined"]
    assert "不可用" in next(line for line in result.summary().splitlines() if line.startswith("年化收益"))
