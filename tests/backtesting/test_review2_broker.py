"""Broker findings of the second engine review (2, 8, 9, 10), reproduced on the
SimulatedBroker directly with hand-made bars and zero fees/slippage."""
from types import SimpleNamespace

import pytest

from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.strategies.base import Order, Position

NO_FEES = dict(slippage_pct=0.0, commission_buy=0.0, commission_sell=0.0, min_commission=0.0)


def broker(cash=100_000.0, **config):
    return SimulatedBroker(initial_cash=cash, config=BrokerConfig(**{**NO_FEES, **config}))


def holding(b, symbol, quantity, cost=10.0):
    b._positions[symbol] = Position(symbol=symbol, quantity=quantity, avg_cost=cost)


def bar(date, o, h, l, c, volume=1_000_000.0, prev_close=None):
    return SimpleNamespace(date=date, open=o, high=h, low=l, close=c, volume=volume,
                           prev_close=prev_close if prev_close is not None else o)


def order(kind, direction, quantity, price=0.0, oid=None, follow=False, day="2024-01-02"):
    return Order(order_id=oid or f"{kind}-{direction}", symbol=SYMBOL, direction=direction, order_type=kind,
                 quantity=quantity, price=price, timestamp=day, follow_position=follow)


SYMBOL = "AAPL"          # no T+1, no price limits: only the order of events matters


def sells(trades):
    return [(t.order_id, t.quantity, round(t.price, 4), t.reason) for t in trades if t.direction == "sell"]


# ------------------------------------------------------------------ #
#  Finding 2: chronology inside one bar
# ------------------------------------------------------------------ #

def test_opening_gap_through_the_stop_fills_the_stop_before_a_limit_sell():
    # Reviewer's reproduction: 100 bought at 10, limit sell 10.5 and stop 9.5;
    # the bar opens at 9 (through the stop) and trades up to 10.6 later.
    b = broker()
    holding(b, SYMBOL, 100)
    limit = b.submit_order(order("limit", "sell", 100, 10.5))
    stop = b.submit_order(order("stop", "sell", 100, 9.5))
    trades, changed = b.process_bar(SYMBOL, bar("2024-01-03", 9.0, 10.6, 8.9, 10.0))

    assert sells(trades) == [("stop-sell", 100, 9.0, "stop")]
    assert b.cash == pytest.approx(100_000 + 900)
    assert stop.status == "filled"
    assert limit.status == "cancelled" and limit.reason == "position_closed"
    assert limit in changed and b.pending_orders() == []


def test_stop_and_limit_sell_both_touched_inside_the_bar_assume_the_stop():
    # The open is between the two levels; the path inside the bar is unknown.
    b = broker()
    holding(b, SYMBOL, 100)
    b.submit_order(order("limit", "sell", 100, 10.5))
    b.submit_order(order("stop", "sell", 100, 9.5))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 10.0, 10.6, 9.4, 10.0))
    assert sells(trades) == [("stop-sell", 100, 9.5, "stop")]


def test_limit_sell_alone_touched_fills_at_the_limit():
    b = broker()
    holding(b, SYMBOL, 100)
    b.submit_order(order("limit", "sell", 100, 10.5))
    stop = b.submit_order(order("stop", "sell", 100, 9.5))
    trades, changed = b.process_bar(SYMBOL, bar("2024-01-03", 10.0, 10.6, 9.8, 10.4))
    assert sells(trades) == [("limit-sell", 100, 10.5, "signal")]
    assert stop.status == "cancelled" and stop.reason == "position_closed" and stop in changed


def test_limit_sell_at_or_below_the_open_fills_at_the_open_before_an_intrabar_stop():
    # The open is the first price of the bar: a sell limit it already satisfies
    # filled there, before the low reached the stop.
    b = broker()
    holding(b, SYMBOL, 100)
    b.submit_order(order("limit", "sell", 100, 10.5))
    b.submit_order(order("stop", "sell", 100, 9.5))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 10.8, 11.0, 9.4, 10.0))
    assert sells(trades) == [("limit-sell", 100, 10.8, "signal")]


def test_stop_gapped_through_goes_before_a_limit_sell_the_open_also_satisfies():
    # limit 9.0 <= open 9.2 <= stop 9.5: both fill at the open; the gapped stop first.
    b = broker()
    holding(b, SYMBOL, 100)
    b.submit_order(order("limit", "sell", 100, 9.0))
    b.submit_order(order("stop", "sell", 100, 9.5))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 9.2, 9.3, 9.0, 9.1))
    assert sells(trades) == [("stop-sell", 100, 9.2, "stop")]


def test_partial_stop_then_limit_sell_in_one_bar_follow_the_assumed_path():
    # Stop for 40 of 100 shares and a limit sell for the rest, both touched:
    # the low (stop) is assumed first, then the high reaches the limit.
    b = broker()
    holding(b, SYMBOL, 100)
    b.submit_order(order("limit", "sell", 60, 10.5))
    b.submit_order(order("stop", "sell", 40, 9.5))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 10.0, 10.6, 9.4, 10.0))
    assert sells(trades) == [("stop-sell", 40, 9.5, "stop"), ("limit-sell", 60, 10.5, "signal")]


def test_market_sell_goes_at_the_open_before_any_stop():
    b = broker()
    holding(b, SYMBOL, 100)
    stop = b.submit_order(order("stop", "sell", 0, 9.5, follow=True))
    b.submit_order(order("market", "sell", 100))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 9.0, 9.2, 8.9, 9.0))
    assert sells(trades) == [("market-sell", 100, 9.0, "signal")]
    assert stop.status == "cancelled"


def test_buy_limits_keep_their_fill_rules():
    # There are no buy stops (exit orders must be sells); a buy limit fills at
    # min(limit, open), whether the open or the low reaches it.
    b = broker()
    assert b.submit_order(order("stop", "buy", 100, 10.5)).status == "rejected"
    first = b.submit_order(order("limit", "buy", 100, 10.5, oid="at-open"))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 10.2, 10.4, 10.0, 10.1))
    assert [(t.order_id, t.price) for t in trades] == [("at-open", 10.2)]
    assert first.status == "filled"
    b.submit_order(order("limit", "buy", 100, 9.8, oid="inside", day="2024-01-03"))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-04", 10.2, 10.4, 9.7, 10.1))
    assert [(t.order_id, t.price) for t in trades] == [("inside", 9.8)]


# ------------------------------------------------------------------ #
#  Finding 8: one participation budget per symbol and bar
# ------------------------------------------------------------------ #

def fills(trades):
    return [(t.order_id, t.direction, t.quantity) for t in trades]


def test_split_orders_share_the_bars_volume_cap():
    # Reviewer's reproduction: volume 1,000 allows at most 100 shares (10%);
    # two 100-share buys filled 200.
    b = broker()
    first = b.submit_order(order("market", "buy", 100, oid="b1"))
    second = b.submit_order(order("market", "buy", 100, oid="b2"))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 10.0, 10.0, 10.0, 10.0, volume=1_000))
    assert fills(trades) == [("b1", "buy", 100)]
    assert first.status == "filled"
    assert second.status == "expired" and second.reason == "volume_cap"


def test_the_budget_is_consumed_in_order_of_submission():
    b = broker()
    for oid in ("b1", "b2", "b3"):
        b.submit_order(order("market", "buy", 60, oid=oid))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 10.0, 10.0, 10.0, 10.0, volume=1_000))
    assert fills(trades) == [("b1", "buy", 60), ("b2", "buy", 40)]
    assert [(u["order_id"], u["reason"], u["outcome"]) for u in b.unfilled] == [
        ("b2", "volume_cap", "partial"), ("b3", "volume_cap", "expired")]


def test_sells_share_the_budget_and_the_rest_waits_for_the_next_bar():
    b = broker()
    holding(b, SYMBOL, 300)
    b.submit_order(order("market", "sell", 100, oid="s1"))
    b.submit_order(order("market", "sell", 100, oid="s2"))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 10.0, 10.0, 10.0, 10.0, volume=1_000))
    assert fills(trades) == [("s1", "sell", 100)]
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-04", 10.0, 10.0, 10.0, 10.0, volume=1_000))
    assert fills(trades) == [("s2", "sell", 100)]          # a fresh budget on the next bar


def test_a_triggered_stop_and_a_limit_sell_share_the_budget():
    b = broker()
    holding(b, SYMBOL, 300)
    b.submit_order(order("stop", "sell", 100, 9.5, oid="stop"))
    b.submit_order(order("limit", "sell", 100, 10.5, oid="limit"))
    trades, _ = b.process_bar(SYMBOL, bar("2024-01-03", 10.0, 10.6, 9.4, 10.0, volume=1_000))
    assert fills(trades) == [("stop", "sell", 100)]


def test_the_limit_queue_allowance_is_shared_too():
    # A-share bar opening at limit-up: 3% of 100,000 = 3,000 shares for all buys together.
    b = broker()
    for oid in ("q1", "q2"):
        b.submit_order(Order(order_id=oid, symbol="600000", direction="buy", order_type="market",
                             quantity=2_000, price=0.0, timestamp="2024-01-02"))
    trades, _ = b.process_bar("600000", bar("2024-01-03", 11.03, 11.03, 10.80, 11.03,
                                            volume=100_000, prev_close=10.03))
    assert fills(trades) == [("q1", "buy", 2_000), ("q2", "buy", 1_000)]
    assert [(u["order_id"], u["reason"]) for u in b.unfilled] == [("q2", "limit_queue")]


# ------------------------------------------------------------------ #
#  Finding 9: a triggered stop stays triggered
# ------------------------------------------------------------------ #

def test_stop_capped_by_volume_sells_the_rest_at_the_next_open():
    # Reviewer's reproduction: a 200-share stop at 9.5 triggers, the volume
    # allows 100; the next bar rebounds above the stop and nothing more sold.
    b = broker()
    holding(b, SYMBOL, 200)
    stop = b.submit_order(order("stop", "sell", 200, 9.5, oid="stop"))
    trades, changed = b.process_bar(SYMBOL, bar("2024-01-03", 10.0, 10.0, 9.4, 9.6, volume=1_000))
    assert sells(trades) == [("stop", 100, 9.5, "stop")]
    assert stop.status == "triggered" and stop not in b.pending_orders()
    (rest,) = b.pending_orders()
    assert (rest.order_type, rest.direction, rest.quantity) == ("market", "sell", 100)
    assert rest in changed and stop in changed

    trades, _ = b.process_bar(SYMBOL, bar("2024-01-04", 10.0, 10.5, 10.0, 10.4, volume=1_000))
    assert sells(trades) == [(rest.order_id, 100, 10.0, "stop")]
    assert b.get_position(SYMBOL) is None and b.pending_orders() == []
    (entry,) = b.unfilled
    assert (entry["order_id"], entry["order_type"], entry["reason"], entry["outcome"]) == (
        "stop", "stop", "volume_cap", "filled_late")
    assert entry["carried_by"] == rest.order_id and entry["triggered_date"] == "2024-01-03"
    assert entry["filled_date"] == "2024-01-04" and entry["filled_quantity"] == 100


def test_stop_blocked_by_t_plus_one_sells_at_the_next_open_after_a_rebound():
    b = broker()
    b.submit_order(Order(order_id="buy", symbol="600000", direction="buy", order_type="market",
                         quantity=1_000, price=0.0, timestamp="2024-01-02"))
    b.submit_order(Order(order_id="stop", symbol="600000", direction="sell", order_type="stop",
                         quantity=0, price=9.5, timestamp="2024-01-02", follow_position=True))
    trades, _ = b.process_bar("600000", bar("2024-01-03", 10.0, 10.2, 9.4, 9.8, prev_close=10.0))
    assert [(t.direction, t.quantity) for t in trades] == [("buy", 1_000)]
    (rest,) = b.pending_orders()
    assert (rest.order_type, rest.quantity, rest.reason) == ("market", 1_000, "t_plus_one")

    trades, _ = b.process_bar("600000", bar("2024-01-04", 10.0, 10.5, 10.0, 10.4, prev_close=9.8))
    assert [(t.direction, t.quantity, t.price, t.reason) for t in trades] == [("sell", 1_000, 10.0, "stop")]


def test_stop_blocked_by_a_limit_down_board_sells_when_the_board_opens():
    b = broker()
    holding(b, "600000", 1_000)
    b.submit_order(Order(order_id="stop", symbol="600000", direction="sell", order_type="stop",
                         quantity=0, price=9.5, timestamp="2024-01-02", follow_position=True))
    trades, _ = b.process_bar("600000", bar("2024-01-03", 9.0, 9.0, 9.0, 9.0, prev_close=10.0))
    assert trades == [] and b.pending_orders()[0].order_type == "market"
    trades, _ = b.process_bar("600000", bar("2024-01-04", 9.6, 9.9, 9.55, 9.8, prev_close=9.0))
    assert [(t.quantity, t.price, t.reason) for t in trades] == [(1_000, 9.6, "stop")]


def test_the_rest_of_a_stop_follows_the_normal_sell_retry_rules():
    b = broker(sell_retry_bars=2)
    holding(b, "600000", 1_000)
    b.submit_order(Order(order_id="stop", symbol="600000", direction="sell", order_type="stop",
                         quantity=0, price=9.5, timestamp="2024-01-02", follow_position=True))
    prev = 10.0
    for day in ("2024-01-03", "2024-01-04", "2024-01-05"):
        price = round(prev * 0.9, 2)
        b.process_bar("600000", bar(day, price, price, price, price, prev_close=prev))
        prev = price
    assert b.pending_orders() == []
    (entry,) = b.unfilled
    assert (entry["reason"], entry["outcome"], entry["bars_waited"]) == ("limit_down", "expired", 2)
    assert b.get_position("600000").quantity == 1_000


def test_engine_run_discloses_a_carried_stop_and_a_trailing_stop_does_not_cancel_it():
    import pandas as pd
    from backtesting.engine import BacktestEngine
    from backtesting.strategies.base import Strategy

    class Trailing(Strategy):
        def on_bar(self):
            if self.data.current_index == self.data.warmup:
                self.buy(quantity=1000)
                self.set_stop(9.5)
            elif self.get_position():
                self.set_stop(round(self.data.close() - 1.0, 2))     # moves the stop every bar

    rows = [10.0, 10.0, (10.0, 10.2, 9.4, 9.8), (10.0, 10.5, 10.0, 10.4), 10.4, 10.4]
    rows = [(r, r, r, r) if isinstance(r, float) else r for r in rows]
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                      index=pd.date_range("2024-01-02", periods=len(rows), freq="B"))
    df["volume"], df["amount"] = 1_000_000.0, 1e7
    df.index.name = "date"
    engine = BacktestEngine(initial_cash=100_000, broker_config=BrokerConfig(**NO_FEES), use_cache=False)
    engine.add_data("600000", "", "", df=df, adjust="", warmup=1)
    engine.add_strategy(Trailing)
    result = engine.run()

    exits = [(str(t.timestamp.date()), t.price, t.reason) for t in result.trades if t.direction == "sell"]
    assert exits == [("2024-01-05", 10.0, "stop")]
    caveat = next(c for c in result.caveats if c["code"] == "stop_carried")
    assert "市价" in caveat["text"] and "T+1" in caveat["text"]


# ------------------------------------------------------------------ #
#  Finding 10: lot rules apply to every sell
# ------------------------------------------------------------------ #

from backtesting.broker.market_rules import lot_rule  # noqa: E402


@pytest.mark.parametrize("symbol,quantity,position,valid", [
    # A-share main board, board lot 100: whole lots, the whole odd remainder
    # (alone or with whole lots), or the whole position.
    ("600000", 300, 300, True),
    ("600000", 100, 350, True),
    ("600000", 150, 350, True),       # one lot plus the entire odd 50
    ("600000", 50, 350, True),        # the entire odd remainder alone
    ("600000", 350, 350, True),
    ("600000", 120, 350, False),      # part of the odd remainder
    ("600000", 30, 350, False),
    ("600000", 1.5, 300, False),      # no fractional shares
    ("600000", 100.5, 350, False),
    ("600000", 0, 300, False),
    ("600000", 400, 300, False),      # more than held
    # STAR (200 then 1-share steps): at least 200, or a balance below 200 at once.
    ("688981", 237, 437, True),
    ("688981", 230, 437, True),
    ("688981", 199, 437, False),
    ("688981", 137, 137, True),
    ("688981", 50, 250, False),
    ("688981", 200.5, 437, False),
    # BSE (100 then 1): at least 100, or the whole balance below 100.
    ("830799", 101, 300, True),
    ("830799", 99, 300, False),
    ("830799", 99, 99, True),
    # HK (assumed board lot 100): same board-lot rule.
    ("00700", 100, 150, True),
    ("00700", 50, 150, True),
    ("00700", 30, 150, False),
    # US: single shares, no fractions.
    ("AAPL", 1, 3, True),
    ("AAPL", 1.5, 3, False),
])
def test_valid_sell_quantities(symbol, quantity, position, valid):
    assert lot_rule(symbol).valid_sell(quantity, position) is valid


@pytest.mark.parametrize("symbol,quantity,position,expected", [
    ("600000", 120, 350, 100),
    ("600000", 180, 350, 150),
    ("600000", 1.5, 300, 0),
    ("688981", 199.5, 437, 0),
    ("AAPL", 2.7, 10, 2),
])
def test_round_sell_gives_the_largest_valid_sell(symbol, quantity, position, expected):
    assert lot_rule(symbol).round_sell(quantity, position) == expected


def test_a_fractional_partial_sell_is_rejected():
    # Reviewer's reproduction: 300 A-shares held, sell 1.5 with ample volume
    # sold 1.5 shares and left 298.5.
    b = broker()
    holding(b, "600000", 300)
    sell = b.submit_order(Order(order_id="frac", symbol="600000", direction="sell", order_type="market",
                                quantity=1.5, price=0.0, timestamp="2024-01-02"))
    trades, changed = b.process_bar("600000", bar("2024-01-03", 10.0, 10.1, 9.9, 10.0, prev_close=10.0))
    assert trades == [] and sell.status == "rejected" and sell in changed
    assert b.get_position("600000").quantity == 300
    assert [(u["reason"], u["outcome"]) for u in b.unfilled] == [("odd_lot", "rejected")]


def test_part_of_an_odd_remainder_is_rejected_and_the_whole_remainder_sells():
    b = broker()
    holding(b, "600000", 350)
    bad = b.submit_order(Order(order_id="bad", symbol="600000", direction="sell", order_type="market",
                               quantity=120, price=0.0, timestamp="2024-01-02"))
    good = b.submit_order(Order(order_id="good", symbol="600000", direction="sell", order_type="market",
                                quantity=150, price=0.0, timestamp="2024-01-02"))
    trades, _ = b.process_bar("600000", bar("2024-01-03", 10.0, 10.1, 9.9, 10.0, prev_close=10.0))
    assert [(t.order_id, t.quantity) for t in trades] == [("good", 150)]
    assert bad.status == "rejected" and b.get_position("600000").quantity == 200


def test_a_volume_capped_sell_fills_whole_lots_and_keeps_its_rest_valid():
    # Sell 300 of 350 with 150 shares of volume left per bar: 150 now would leave
    # an invalid 150-of-200 order, so 100 fill, then 100, then 100.
    b = broker()
    holding(b, "600000", 350)
    b.submit_order(Order(order_id="s", symbol="600000", direction="sell", order_type="market",
                         quantity=300, price=0.0, timestamp="2024-01-02"))
    sold = []
    for day in ("2024-01-03", "2024-01-04", "2024-01-05"):
        trades, _ = b.process_bar("600000", bar(day, 10.0, 10.1, 9.9, 10.0, volume=1_500, prev_close=10.0))
        sold += [t.quantity for t in trades]
    assert sold == [100, 100, 100]
    assert b.get_position("600000").quantity == 50 and b.pending_orders() == []


def test_star_volume_capped_sell_leaves_a_sellable_rest():
    b = broker()
    holding(b, "688981", 437)
    b.submit_order(Order(order_id="s", symbol="688981", direction="sell", order_type="market",
                         quantity=400, price=0.0, timestamp="2024-01-02"))
    trades, _ = b.process_bar("688981", bar("2024-01-03", 10.0, 10.1, 9.9, 10.0, volume=3_000, prev_close=10.0))
    assert [t.quantity for t in trades] == [200]          # 300 would leave 100 of 237: not sellable
    trades, _ = b.process_bar("688981", bar("2024-01-04", 10.0, 10.1, 9.9, 10.0, volume=3_000, prev_close=10.0))
    assert [t.quantity for t in trades] == [200]


def test_a_stop_for_part_of_an_odd_remainder_is_rejected_when_it_triggers():
    b = broker()
    holding(b, "600000", 350)
    stop = b.submit_order(Order(order_id="stop", symbol="600000", direction="sell", order_type="stop",
                                quantity=120, price=9.5, timestamp="2024-01-02"))
    trades, _ = b.process_bar("600000", bar("2024-01-03", 10.0, 10.1, 9.4, 9.6, prev_close=10.0))
    assert trades == [] and stop.status == "rejected" and b.pending_orders() == []
    assert [(u["order_id"], u["reason"], u["outcome"]) for u in b.unfilled] == [("stop", "odd_lot", "rejected")]
