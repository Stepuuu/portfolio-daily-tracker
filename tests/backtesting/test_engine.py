import pandas as pd

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
