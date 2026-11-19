"""Order life-cycle: unfilled buys expire after one bar, sells retry for up to
20 bars, every unfilled signal is logged, and strategies can see pending orders."""
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Order, Strategy
from backtesting.strategies.examples import DualMaStrategy


def frame(rows, start="2024-01-02"):
    """rows: list of (open, high, low, close) or a single price for a one-price bar."""
    rows = [(r, r, r, r) if isinstance(r, (int, float)) else r for r in rows]
    dates = pd.date_range(start, periods=len(rows), freq="B")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=dates)
    df["volume"] = 1_000_000.0
    df["amount"] = df["close"] * 1_000_000.0
    df.index.name = "date"
    return df


def limit_ups(start, days):
    prices, price = [], start
    for _ in range(days):
        price = round(price * 1.1, 2)
        prices.append(price)
    return prices


def run(strategy, df, symbol="600000", cash=100_000, warmup=1, **params):
    engine = BacktestEngine(initial_cash=cash, broker_config=BrokerConfig(slippage_pct=0.0), use_cache=False)
    engine.add_data(symbol, "", "", df=df, adjust="", warmup=warmup)
    engine.add_strategy(strategy, **params)
    return engine.run()


class BuyOnce(Strategy):
    name = "BuyOnce"
    parameters = {"position_pct": 0.95}

    def on_init(self):
        self.did_buy = False

    def on_bar(self):
        if not self.did_buy:
            self.buy(pct_cash=self.parameters["position_pct"])
            self.did_buy = True


class BuyThenSell(Strategy):
    """Buys on the first bar, asks to close on bar `sell_at` (an index)."""
    name = "BuyThenSell"
    parameters = {"sell_at": 3}

    def on_init(self):
        self.pending_seen = []

    def on_bar(self):
        index = self.data.current_index
        if index == self.data.warmup:
            self.buy(quantity=1000)
        elif index >= self.parameters["sell_at"] and self.get_position():
            self.close_position()
            self.pending_seen.append(len(self.get_pending_orders(direction="sell")))


def buys(result):
    return [(t.timestamp, t.price) for t in result.trades if t.direction == "buy"]


def test_buy_blocked_by_a_limit_up_streak_expires_after_one_bar():
    # Signal at 10.00, five one-price limit-up boards (16.10), then a -10% open at 14.50,
    # still 45% above the signal.
    df = frame([10.0, 10.0] + limit_ups(10.0, 5) + [(14.5, 14.8, 14.3, 14.6), 14.6])
    result = run(BuyOnce, df)

    assert buys(result) == []                      # never an entry at +40%
    assert len(result.unfilled) == 1
    entry = result.unfilled[0]
    assert (entry["reason"], entry["outcome"], entry["bars_waited"]) == ("limit_up", "expired", 1)
    assert entry["signal_date"] == "2024-01-03" and entry["date"] == "2024-01-04"
    assert "unfilled_signals" in [c["code"] for c in result.caveats]


def test_sell_blocked_by_limit_down_retries_until_it_fills():
    falls, price = [], 10.0
    for _ in range(3):
        price = round(price * 0.9, 2)
        falls.append(price)
    df = frame([10.0, 10.0, 10.0, 10.0] + falls + [(7.2, 7.5, 7.0, 7.3), 7.3])
    result = run(BuyThenSell, df, sell_at=3)

    sells = [t for t in result.trades if t.direction == "sell" and t.order_id != "FORCE_CLOSE"]
    assert [(str(t.timestamp.date()), t.price) for t in sells] == [("2024-01-11", 7.2)]
    entry = result.unfilled[0]
    assert (entry["reason"], entry["outcome"], entry["bars_waited"]) == ("limit_down", "filled_late", 3)
    assert entry["filled_date"] == "2024-01-11"
    # The strategy saw one pending sell each bar instead of stacking new ones.
    assert result._strategy.pending_seen == [1, 1, 1, 1]


def test_sell_gives_up_after_twenty_bars_and_is_logged():
    falls, price = [], 10.0
    for _ in range(22):
        price = round(price * 0.9, 2)
        falls.append(price)
    df = frame([10.0, 10.0, 10.0, 10.0] + falls)
    result = run(BuyThenSell, df, sell_at=3)

    entry = next(u for u in result.unfilled if u["direction"] == "sell")
    assert (entry["reason"], entry["outcome"], entry["bars_waited"]) == ("limit_down", "expired", 20)


def test_zero_volume_bar_is_a_suspension_and_the_buy_expires():
    df = frame([10.0, 10.0, 10.0, 10.0, 10.0])
    df.iloc[2, df.columns.get_loc("volume")] = 0.0
    result = run(BuyOnce, df)

    assert buys(result) == []
    assert [(u["reason"], u["outcome"]) for u in result.unfilled] == [("suspended", "expired")]


def test_missing_bar_of_an_auxiliary_symbol_means_suspended():
    class BuyAuxiliary(Strategy):
        def on_init(self):
            self.sent = False

        def on_bar(self):
            if not self.sent:
                self.buy(symbol="BBB", quantity=100)
                self.sent = True

    primary = frame([10.0] * 6)
    auxiliary = primary.drop(primary.index[2])           # no BBB bar on the fill day
    engine = BacktestEngine(initial_cash=100_000, broker_config=BrokerConfig(slippage_pct=0.0), use_cache=False)
    engine.add_data("AAA", "", "", df=primary, warmup=1)
    engine.add_data("BBB", "", "", df=auxiliary, warmup=1, is_primary=False)
    engine.add_strategy(BuyAuxiliary)
    result = engine.run()

    assert [t for t in result.trades if t.symbol == "BBB"] == []
    assert [(u["symbol"], u["reason"], u["outcome"]) for u in result.unfilled] == [("BBB", "suspended", "expired")]


def test_gap_up_beyond_the_cash_fills_what_is_affordable_and_logs_the_rest():
    # Sized at the signal close (950 shares at 10), the open gaps to 12.
    df = frame([10.0, 10.0, (12.0, 12.2, 11.9, 12.0), 12.0])
    result = run(BuyOnce, df, symbol="AAPL", cash=10_000)

    assert [t.quantity for t in result.trades if t.direction == "buy"] == [833]
    entry = result.unfilled[0]
    assert (entry["reason"], entry["outcome"], entry["quantity"]) == ("insufficient_cash", "partial", 117)


def test_broker_buy_expires_and_sell_retries():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    board = SimpleNamespace(date="2024-01-02", open=11.0, high=11.0, low=11.0, close=11.0,
                            volume=1_000_000, prev_close=10.0)
    order = broker.submit_order(Order(order_id="b1", symbol="600000", direction="buy", order_type="market",
                                      quantity=100, price=0.0, timestamp="2024-01-01"))
    _, changed = broker.process_bar("600000", board)
    assert changed == [order] and order.status == "expired" and order.reason == "limit_up"
    assert broker.pending_orders() == []


def test_dual_ma_exposure_never_exceeds_position_pct():
    # A golden cross on bar 6, a one-price limit-up board on the fill day (bar 7),
    # then trading resumes at the board's price. Re-sending the buy while the
    # first one waited used to put both in the market: about 99% invested.
    closes = [10.0, 9.8, 9.6, 9.5, 9.4, 9.3, 9.8]
    rows = [(c, round(c * 1.01, 2), round(c * 0.99, 2), c) for c in closes]
    rows += [10.78]                                    # +10% one-price board
    rows += [(10.78, 10.9, 10.7, 10.8), (10.8, 10.95, 10.75, 10.9), (10.9, 11.0, 10.8, 10.95)]
    df = frame(rows)
    params = {"short_period": 2, "long_period": 3, "atr_period": 2, "atr_multiplier": 50.0, "position_pct": 0.5}
    result = run(DualMaStrategy, df, warmup=0, **params)

    buy_trades = [t for t in result.trades if t.direction == "buy"]
    assert buy_trades, "the strategy should enter once the board opens"
    equity = {row[0]: row for row in result._broker.equity_curve}
    for trade in buy_trades:
        total, cash, market_value = equity[trade.timestamp][1:4]
        assert market_value / total <= 0.5 + 0.01


# ------------------------------------------------------------------ #
#  Stop and target orders inside the bar
# ------------------------------------------------------------------ #

class EnterWithExits(Strategy):
    """Buys 1,000 shares on the first tradable bar and sets a stop and/or target."""
    name = "EnterWithExits"
    parameters = {"stop": None, "target": None}

    def on_bar(self):
        if self.data.current_index == self.data.warmup:
            self.buy(quantity=1000)
            if self.parameters["stop"]:
                self.set_stop(self.parameters["stop"])
            if self.parameters["target"]:
                self.set_target(self.parameters["target"])


ENTRY = [10.0, 10.0, (10.0, 10.2, 9.9, 10.0)]          # signal on bar 1, entry at 10 on bar 2


def exits(result):
    return [(str(t.timestamp.date()), t.price, t.reason) for t in result.trades
            if t.direction == "sell" and t.reason != "end_mark"]


def test_stop_gapped_through_fills_at_the_open():
    df = frame(ENTRY + [(9.0, 9.2, 8.9, 9.1), 9.1])
    result = run(EnterWithExits, df, symbol="AAPL", stop=9.5)
    assert exits(result) == [("2024-01-05", 9.0, "stop")]


def test_stop_touched_inside_the_bar_fills_at_the_stop():
    df = frame(ENTRY + [(10.0, 10.1, 9.3, 9.6), 9.6])
    result = run(EnterWithExits, df, symbol="AAPL", stop=9.5)
    assert exits(result) == [("2024-01-05", 9.5, "stop")]


def test_target_touched_inside_the_bar_fills_at_the_target():
    df = frame(ENTRY + [(10.0, 10.6, 9.8, 10.4), 10.4])
    result = run(EnterWithExits, df, symbol="AAPL", target=10.5)
    assert exits(result) == [("2024-01-05", 10.5, "target")]


def test_stop_and_target_in_one_bar_the_stop_goes_first():
    df = frame(ENTRY + [(10.0, 10.6, 9.4, 10.0), 10.0])
    result = run(EnterWithExits, df, symbol="AAPL", stop=9.5, target=10.5)
    assert exits(result) == [("2024-01-05", 9.5, "stop")]
    assert result._broker.pending_orders() == []          # the target was cancelled with the position


def test_open_above_the_target_fills_the_target_at_the_open_first():
    # The open is the first price of the bar: the target was reached before any low.
    df = frame(ENTRY + [(10.8, 11.0, 9.4, 10.0), 10.0])
    result = run(EnterWithExits, df, symbol="AAPL", stop=9.5, target=10.5)
    assert exits(result) == [("2024-01-05", 10.8, "target")]


def test_stop_respects_t_plus_one():
    # The entry bar touches the stop; A-share shares bought today cannot be sold.
    # The stop stays triggered: the blocked shares become a market sell that
    # fills at the next open (9.6), not at the stop price (review finding 9).
    df = frame([10.0, 10.0, (10.0, 10.2, 9.4, 9.8), (9.6, 9.7, 9.45, 9.5), 9.5])
    result = run(EnterWithExits, df, symbol="600000", stop=9.5)
    assert exits(result) == [("2024-01-05", 9.6, "stop")]
    entry = next(u for u in result.unfilled if u["order_type"] == "stop")
    assert (entry["reason"], entry["outcome"]) == ("t_plus_one", "filled_late")

    us = run(EnterWithExits, df, symbol="AAPL", stop=9.5)
    assert exits(us) == [("2024-01-04", 9.5, "stop")]


def test_stop_blocked_by_a_limit_down_board_fills_at_the_next_open():
    df = frame(ENTRY + [9.0, (8.8, 9.0, 8.7, 8.9), 8.9])
    result = run(EnterWithExits, df, symbol="600000", stop=9.5)
    assert exits(result) == [("2024-01-08", 8.8, "stop")]
    entry = next(u for u in result.unfilled if u["order_type"] == "stop")
    assert (entry["reason"], entry["outcome"]) == ("limit_down", "filled_late")


def test_set_stop_replaces_the_working_stop():
    class Trailing(Strategy):
        def on_bar(self):
            index = self.data.current_index
            if index == self.data.warmup:
                self.buy(quantity=1000)
                self.set_stop(8.0)
            elif self.get_position():
                self.set_stop(round(self.data.close() - 0.5, 2))

    df = frame(ENTRY + [(10.5, 10.6, 10.4, 10.5), (10.4, 10.4, 9.9, 10.0), 10.0])
    result = run(Trailing, df, symbol="AAPL")
    # Stop raised to 10.00 after bar 3 (close 10.5): bar 4 trades down to 9.9.
    assert exits(result) == [("2024-01-08", 10.0, "stop")]
    assert len([o for o in result._broker.pending_orders()]) == 0
