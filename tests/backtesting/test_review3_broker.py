"""Third review, part 2: the broker findings (broker/simulated.py:800 and :847),
on the SimulatedBroker directly with hand-made bars and zero fees/slippage."""
from types import SimpleNamespace

import pytest

from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.strategies.base import Order, Position

NO_FEES = dict(slippage_pct=0.0, commission_buy=0.0, commission_sell=0.0, min_commission=0.0)


def broker(cash=100_000.0, **config):
    return SimulatedBroker(initial_cash=cash, config=BrokerConfig(**{**NO_FEES, **config}))


def bar(date, o, h, l, c, volume=1_000.0, prev_close=10.0):
    return SimpleNamespace(date=date, open=o, high=h, low=l, close=c, volume=volume, prev_close=prev_close)


def order(symbol, kind, direction, quantity, price=0.0, oid=None):
    return Order(order_id=oid or f"{symbol}-{kind}-{direction}", symbol=symbol, direction=direction,
                 order_type=kind, quantity=quantity, price=price, timestamp="2024-01-02")


# ------------------------------------------------------------------ #
#  engine.py:646 / process_bars - the open of every symbol before
#  anything inside the bar
# ------------------------------------------------------------------ #

def test_process_bars_matches_the_open_of_every_symbol_before_inside_the_bar():
    # AAPL: a sell limit above the open fills inside the bar; MSFT: a market buy at
    # the open needs that cash. Whatever the order the bars are given in, the buy
    # only sees the cash there was at the open.
    for names in (("AAPL", "MSFT"), ("MSFT", "AAPL")):
        b = broker(cash=1.0)
        b._positions["AAPL"] = Position(symbol="AAPL", quantity=100, avg_cost=10.0)
        b.submit_order(order("AAPL", "limit", "sell", 100, price=11.0))
        b.submit_order(order("MSFT", "market", "buy", 100))
        days = {"AAPL": bar("2024-01-03", 10.0, 11.0, 10.0, 10.5, volume=1e6),
                "MSFT": bar("2024-01-03", 10.0, 10.0, 10.0, 10.0, volume=1e6)}
        results = b.process_bars({name: days[name] for name in names})
        assert [t.symbol for t in results["AAPL"][0]] == ["AAPL"]
        assert results["MSFT"][0] == [], names
        assert [u["reason"] for u in b.unfilled] == ["insufficient_cash"]


# ------------------------------------------------------------------ #
#  simulated.py:800 - a fill at the open does not depend on the
#  bar's later high or low
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("high", [10.5, 11.0], ids=["high_10.5", "high_at_limit_up"])
def test_an_opening_buy_is_not_limited_by_a_later_limit_up_high(high):
    # Reviewer's reproduction: previous close and open 10, volume 1,000 (10% = 100
    # shares). Raising only the later high from 10.5 to 11 (the limit-up price) made
    # the 100-share market buy expire under the 3% limit-queue cap.
    b = broker()
    b.submit_order(order("600000", "market", "buy", 100))
    trades, _ = b.process_bar("600000", bar("2024-01-03", 10.0, high, 10.0, 10.5))
    assert [(t.quantity, t.price) for t in trades] == [(100, 10.0)]
    assert b.unfilled == []


@pytest.mark.parametrize("low", [9.5, 9.0], ids=["low_9.5", "low_at_limit_down"])
def test_an_opening_sell_is_not_limited_by_a_later_limit_down_low(low):
    b = broker()
    b._positions["600000"] = Position(symbol="600000", quantity=100, avg_cost=10.0)
    b.submit_order(order("600000", "market", "sell", 100))
    trades, _ = b.process_bar("600000", bar("2024-01-03", 10.0, 10.0, low, 9.5))
    assert [(t.quantity, t.price) for t in trades] == [(100, 10.0)]


def test_a_stop_inside_a_bar_that_reaches_limit_down_still_meets_the_queue():
    # Inside the bar the path is unknown: a stop triggered on a day that reached the
    # limit-down price keeps the limit-queue cap (3% of 1,000 is less than a lot).
    b = broker()
    b._positions["600000"] = Position(symbol="600000", quantity=100, avg_cost=10.0)
    b.submit_order(order("600000", "stop", "sell", 100, price=9.5))
    trades, _ = b.process_bar("600000", bar("2024-01-03", 10.0, 10.0, 9.0, 9.2))
    assert trades == [] and b.unfilled[0]["reason"] == "limit_queue"


# ------------------------------------------------------------------ #
#  simulated.py:847 - one participation budget per listing and bar
# ------------------------------------------------------------------ #

def test_two_aliases_of_one_listing_share_the_volume_cap():
    # Reviewer's reproduction: simultaneous buys through 600000 and SHA:600000 filled
    # 200 shares against the same 100-share cap (10% of 1,000).
    b = broker()
    b.submit_order(order("600000", "market", "buy", 100))
    b.submit_order(order("SHA:600000", "market", "buy", 100))
    day = bar("2024-01-03", 10.0, 10.2, 9.9, 10.1)
    results = b.process_bars({"600000": day, "SHA:600000": day})

    filled = {symbol: sum(t.quantity for t in trades) for symbol, (trades, _) in results.items()}
    assert filled == {"600000": 100, "SHA:600000": 0}
    (missed,) = b.unfilled
    assert (missed["symbol"], missed["reason"]) == ("SHA:600000", "volume_cap")


def test_aliases_share_the_cap_when_matched_one_symbol_at_a_time():
    b = broker()
    b.submit_order(order("600000", "market", "buy", 100))
    b.submit_order(order("600000.SH", "market", "buy", 100))
    day = bar("2024-01-03", 10.0, 10.2, 9.9, 10.1)
    first, _ = b.process_bar("600000", day)
    second, _ = b.process_bar("600000.SH", day)
    assert sum(t.quantity for t in first + second) == 100


def test_different_listings_keep_their_own_cap():
    b = broker()
    b.submit_order(order("600000", "market", "buy", 100))
    b.submit_order(order("600036", "market", "buy", 100))
    day = bar("2024-01-03", 10.0, 10.2, 9.9, 10.1)
    results = b.process_bars({"600000": day, "600036": day})
    assert {symbol: sum(t.quantity for t in trades) for symbol, (trades, _) in results.items()} == {
        "600000": 100, "600036": 100}
