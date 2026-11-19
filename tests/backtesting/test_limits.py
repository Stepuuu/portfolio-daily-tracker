"""Price limits: dated board table, ratio detection on the same series, and
one-price boards detected by pattern (ST status is unknown)."""
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting.broker.market_rules import price_limit_pct
from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Order, Position, Strategy


def bar(date="2024-01-02", open_=10.0, high=None, low=None, close=None, volume=1_000_000, prev_close=10.0):
    high = open_ if high is None else high
    low = open_ if low is None else low
    close = open_ if close is None else close
    return SimpleNamespace(date=date, open=open_, high=high, low=low, close=close,
                           volume=volume, prev_close=prev_close)


def buy(symbol, qty=1000):
    return Order(order_id="b", symbol=symbol, direction="buy", order_type="market",
                 quantity=qty, price=0.0, timestamp="2024-01-01")


def sell(symbol, qty=1000):
    return Order(order_id="s", symbol=symbol, direction="sell", order_type="market",
                 quantity=qty, price=0.0, timestamp="2024-01-01")


def filled(symbol, the_bar, basis="raw", order=None, holding=0):
    b = SimulatedBroker(initial_cash=1_000_000, config=BrokerConfig(slippage_pct=0.0))
    b.set_price_basis(symbol, basis)
    if holding:
        b._positions[symbol] = Position(symbol=symbol, quantity=holding, avg_cost=10.0)
    b.submit_order(order or buy(symbol))
    trades, _ = b.process_bar(symbol, the_bar)
    return sum(t.quantity for t in trades)


@pytest.mark.parametrize("symbol,when,pct", [
    ("600000", "2024-01-02", 0.10),
    ("SHE:000001", "2024-01-02", 0.10),
    ("300750", "2020-08-21", 0.10),    # ChiNext before the 2020-08-24 reform
    ("300750", "2020-08-24", 0.20),
    ("301001", "2023-03-01", 0.20),
    ("688981", "2019-07-22", 0.20),    # STAR
    ("830799", "2022-01-04", 0.30),    # BSE
    ("920118", "2025-01-02", 0.30),    # new BSE codes
    ("BJ:430047", "2022-01-04", 0.30),
    ("510300", "2024-01-02", 0.10),    # ETF
    ("AAPL", "2024-01-02", None),
    ("00700", "2024-01-02", None),
])
def test_dated_board_limit_table(symbol, when, pct):
    assert price_limit_pct(symbol, when) == pct


# --- ST one-price boards (status unknown: detected by pattern) ---

def test_st_one_price_limit_up_at_5_percent_blocks_a_buy():
    assert filled("600010", bar(open_=10.50, prev_close=10.00)) == 0


def test_st_one_price_limit_down_at_5_percent_blocks_a_sell():
    assert filled("600010", bar(open_=9.50, prev_close=10.00), order=sell("600010"), holding=1000) == 0


def test_one_price_bar_below_the_pattern_threshold_fills():
    assert filled("600010", bar(open_=10.40, prev_close=10.00)) == 1000      # +4%


def test_one_price_pattern_does_not_apply_to_a_20_percent_board():
    # ChiNext after the reform: +5% (or +10%) on one price is not a limit day.
    assert filled("300750", bar(date="2021-06-01", open_=10.50, prev_close=10.00)) == 1000
    assert filled("300750", bar(date="2021-06-01", open_=11.00, prev_close=10.00)) == 1000


# --- ChiNext 10% before 2020-08-24 ---

def test_2019_chinext_one_price_10_percent_board_is_blocked():
    assert filled("300750", bar(date="2019-06-03", open_=11.00, prev_close=10.00)) == 0


def test_2019_chinext_touching_10_percent_only_fills_the_queue_share():
    # Opens at +10% (the 2019 ChiNext limit) and trades below it: the buy at the open queues.
    touching = bar(date="2019-06-03", open_=11.00, high=11.00, low=10.40, close=11.00,
                   volume=100_000, prev_close=10.00)
    assert filled("300750", touching, order=buy("300750", 5000)) == 3000


# --- BSE 920xxx is a 30% board ---

def test_920_code_plus_15_percent_one_price_bar_fills():
    assert filled("920118", bar(date="2025-01-02", open_=11.50, prev_close=10.00)) == 1000


def test_920_code_plus_30_percent_one_price_bar_is_blocked():
    assert filled("920118", bar(date="2025-01-02", open_=13.00, prev_close=10.00)) == 0


# --- adjusted prices: compare the ratio on the same series ---

def test_star_limit_day_at_a_low_adjusted_price_is_detected_by_ratio():
    # Real 10.00 -> 12.00 (+20%); in a qfq series rounded to 0.01 this is 0.33 -> 0.39
    # (+18.2%). Rounding 0.33 x 1.2 = 0.396 gives a "limit" of 0.40 the bar never reaches.
    limit_day = bar(date="2024-01-02", open_=0.39, prev_close=0.33, volume=10_000_000)
    assert filled("688001", limit_day, basis="adjusted", order=buy("688001", 1000)) == 0
    # On an unadjusted series the same bar is below the 0.40 limit and fills.
    assert filled("688001", limit_day, basis="raw", order=buy("688001", 1000)) == 1000


def test_main_board_touching_the_limit_at_a_low_adjusted_price_fills_the_queue_share():
    # qfq 0.35 -> open 0.38 (+8.6%) is the 10% limit at the real price level.
    touching = bar(open_=0.38, high=0.38, low=0.355, close=0.38, volume=100_000, prev_close=0.35)
    assert filled("600010", touching, basis="adjusted", order=buy("600010", 5000)) == 3000
    assert filled("600010", touching, basis="raw", order=buy("600010", 5000)) == 5000


def test_existing_raw_tick_rule_still_distinguishes_one_tick_below_the_limit():
    # ETF limit-up 1.122 for a previous close of 1.02; one tick below fills.
    assert filled("ETF:510300", bar(open_=1.121, prev_close=1.02), order=buy("ETF:510300", 100)) == 100
    assert filled("ETF:510300", bar(open_=1.122, prev_close=1.02), order=buy("ETF:510300", 100)) == 0


class BuyEveryBar(Strategy):
    name = "BuyEveryBar"

    def on_bar(self):
        if not self.get_position():
            self.buy(quantity=1000)


def test_engine_compares_adjusted_series_by_ratio():
    # STAR stock, qfq prices: day 3 is a one-price +20% board at 0.39 from 0.33.
    closes = [0.30, 0.31, 0.33, 0.39, 0.40, 0.41]
    dates = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                       "volume": [10_000_000.0] * len(closes), "amount": [1e6] * len(closes)}, index=dates)
    df.index.name = "date"
    df.loc[dates[4], ["high"]] = 0.41
    df.loc[dates[4], ["low"]] = 0.39
    engine = BacktestEngine(initial_cash=100_000, broker_config=BrokerConfig(slippage_pct=0.0), use_cache=False)
    engine.add_data("688001", "", "", df=df, adjust="qfq", warmup=2)
    engine.add_strategy(BuyEveryBar)

    result = engine.run()

    buy_dates = [t.timestamp for t in result.trades if t.direction == "buy"]
    assert dates[3] not in buy_dates
    assert buy_dates and min(buy_dates) == dates[4]
