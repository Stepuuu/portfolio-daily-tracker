"""Slippage never moves a fill beyond the price limit: nothing trades above the limit-up price or below the
limit-down price, whatever the assumed slippage. Fictional bars on a 10% board (previous close 10.00: limit
up 11.00, limit down 9.00); each bar opens at the board but trades away from it, so it is not a one-price
board and the order fills (as queue volume)."""
from types import SimpleNamespace

from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.strategies.base import Order, Position

SYMBOL = "600000"


def bar(open_, high, low, close, prev_close=10.0, date="2024-01-02"):
    return SimpleNamespace(date=date, open=open_, high=high, low=low, close=close, volume=1_000_000,
                           prev_close=prev_close)


def order(direction, kind="market", price=0.0, qty=1000):
    return Order(order_id=f"{direction}-{kind}", symbol=SYMBOL, direction=direction, order_type=kind,
                 quantity=qty, price=price, timestamp="2024-01-01")


def fills(the_bar, the_order, basis="raw", slippage=0.001, holding=0):
    broker = SimulatedBroker(initial_cash=1_000_000, config=BrokerConfig(slippage_pct=slippage))
    broker.set_price_basis(SYMBOL, basis)
    if holding:
        broker._positions[SYMBOL] = Position(symbol=SYMBOL, quantity=holding, avg_cost=10.0)
    broker.submit_order(the_order)
    trades, _ = broker.process_bar(SYMBOL, the_bar)
    return trades


def test_a_market_buy_at_a_limit_up_open_fills_at_the_limit_not_above_it():
    [trade] = fills(bar(11.00, 11.00, 10.80, 10.90), order("buy"))
    assert trade.price == 11.00 and trade.quantity == 1000


def test_a_market_buy_just_below_the_limit_is_capped_at_the_limit():
    # 10.99 x 1.002 = 11.012: above the board, which cannot trade.
    [trade] = fills(bar(10.99, 11.00, 10.80, 10.90), order("buy"), slippage=0.002)
    assert trade.price == 11.00


def test_a_market_buy_far_from_the_limit_keeps_its_slippage():
    [trade] = fills(bar(10.00, 10.20, 9.90, 10.10), order("buy"))
    assert abs(trade.price - 10.01) < 1e-9


def test_a_market_sell_at_a_limit_down_open_fills_at_the_limit_not_below_it():
    [trade] = fills(bar(9.00, 9.20, 9.00, 9.10), order("sell"), holding=1000)
    assert trade.price == 9.00


def test_a_stop_that_gaps_to_the_limit_down_fills_at_the_limit_not_below_it():
    [trade] = fills(bar(9.00, 9.20, 9.00, 9.10), order("sell", "stop", price=9.50), holding=1000)
    assert trade.price == 9.00 and trade.reason == "stop"


def test_on_an_adjusted_series_a_buy_at_the_board_fills_at_the_highest_traded_price():
    # qfq prices: the board is found by ratio; nothing traded above the bar's high.
    [trade] = fills(bar(5.50, 5.50, 5.40, 5.45, prev_close=5.00), order("buy"), basis="adjusted")
    assert trade.price == 5.50
