"""Lot rules per market and board, and the lot_unaffordable caveat."""
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting.broker.market_rules import lot_rule
from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Order, Position, Strategy


@pytest.mark.parametrize("symbol,wanted,expected", [
    ("600000", 250, 200),          # main board: 100-share lots
    ("600000", 99, 0),
    ("SHE:000001", 1999, 1900),
    ("300750", 150, 100),          # ChiNext: 100-share lots
    ("510300", 150, 100),          # ETF: 100-share lots
    ("688981", 199, 0),            # STAR: at least 200 ...
    ("688981", 200, 200),
    ("SHA:688981", 237, 237),      # ... then 1-share steps
    ("830799", 99, 0),             # BSE: at least 100 ...
    ("BJ:430047", 101, 101),       # ... then 1-share steps
    ("920118", 150, 150),
    ("00700", 150, 100),           # HK default lot 100 (an assumption)
    ("HKG:00700", 99, 0),
    ("AAPL", 3, 3),                # US: single shares
    ("NASDAQ:AAPL", 1, 1),
])
def test_buy_quantities_follow_the_board_lot_rule(symbol, wanted, expected):
    assert lot_rule(symbol).round_buy(wanted) == expected


def test_hk_lot_is_marked_as_an_assumption_and_an_override_wins():
    assert lot_rule("00700").assumed is True
    assert lot_rule("600000").assumed is False
    override = lot_rule("00700", override=500)
    assert (override.minimum, override.step, override.assumed) == (500, 500, False)
    assert override.round_buy(1499) == 1000


@pytest.mark.parametrize("symbol,position,wanted,expected", [
    ("600000", 150, 150, 150),      # the odd remainder may be sold in one go
    ("600000", 150, 500, 150),
    ("600000", 350, 120, 100),      # a partial sell stays in whole lots
    ("688981", 237, 237, 237),
    ("688981", 437, 230, 230),
])
def test_sells_may_sell_an_odd_remainder(symbol, position, wanted, expected):
    assert lot_rule(symbol).round_sell(wanted, position) == expected


def bar(date, price, volume=10_000_000):
    return SimpleNamespace(date=date, open=price, high=price * 1.01, low=price * 0.99,
                           close=price, volume=volume, prev_close=price)


def test_broker_sells_an_odd_lot_position_in_full():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    broker._positions["600000"] = Position(symbol="600000", quantity=150, avg_cost=10.0)
    broker.submit_order(Order(order_id="s", symbol="600000", direction="sell", order_type="market",
                              quantity=150, price=0.0, timestamp="2024-01-01"))
    trades, _ = broker.process_bar("600000", bar("2024-01-02", 10.0))
    assert [t.quantity for t in trades] == [150]
    assert broker.get_position("600000") is None


class BuyOnce(Strategy):
    name = "BuyOnce"
    parameters = {"position_pct": 0.95}

    def on_init(self):
        self.did_buy = False

    def on_bar(self):
        if not self.did_buy:
            self.buy(pct_cash=self.parameters["position_pct"])
            self.did_buy = True


def frame(price, periods=8):
    dates = pd.date_range("2024-01-02", periods=periods, freq="B")
    df = pd.DataFrame({"open": [price] * periods, "high": [price * 1.01] * periods,
                       "low": [price * 0.99] * periods, "close": [price] * periods,
                       "volume": [10_000_000.0] * periods, "amount": [price * 1e7] * periods}, index=dates)
    df.index.name = "date"
    return df


def run(symbol, price, cash, **config):
    engine = BacktestEngine(initial_cash=cash, broker_config=BrokerConfig(slippage_pct=0.0, **config), use_cache=False)
    engine.add_data(symbol, "", "", df=frame(price), warmup=1)
    engine.add_strategy(BuyOnce)
    return engine.run()


def buys(result):
    return [t.quantity for t in result.trades if t.direction == "buy"]


def test_star_market_buys_in_single_shares_above_200():
    # 95,000 budget at 50: 1,900 shares plus fees do not fit, 1,899 do.
    assert buys(run("688981", 50.0, 100_000)) == [1899]


def test_bse_buys_in_single_shares_above_100():
    assert buys(run("830799", 7.3, 10_000)) == [1300]   # 9,500 / 7.3 = 1301.4 -> 1,300 after fees


@pytest.mark.parametrize("symbol,price,cash", [
    ("600519", 1700.0, 100_000),     # one A-share lot costs 170,000
    ("NASDAQ:BRK.A", 600_000.0, 100_000),
])
def test_unaffordable_lot_is_reported_instead_of_a_silent_zero(symbol, price, cash):
    result = run(symbol, price, cash)

    assert buys(result) == []
    codes = [c["code"] for c in result.caveats]
    assert "lot_unaffordable" in codes
    caveat = next(c for c in result.caveats if c["code"] == "lot_unaffordable")
    assert caveat["level"] == "warning" and caveat["text"]
    assert [u["reason"] for u in result.unfilled] == ["lot_unaffordable"]
    payload = result.to_dict()
    assert payload["caveats"] == result.caveats
    assert payload["unfilled"][0]["outcome"] == "not_submitted"
    assert payload["unfilled"][0]["signal_date"] == "2024-01-03"


def test_affordable_run_has_no_lot_caveat():
    result = run("600000", 10.0, 100_000)
    assert buys(result) == [9400]
    assert "lot_unaffordable" not in [c["code"] for c in result.caveats]


def test_hk_default_lot_is_stated_as_an_assumption():
    result = run("HKG:00700", 300.0, 100_000)
    assert buys(result) == [300]
    assert "lot_assumed" in [c["code"] for c in result.caveats]
    explicit = run("HKG:00700", 300.0, 100_000, lot_size=100)
    assert "lot_assumed" not in [c["code"] for c in explicit.caveats]
