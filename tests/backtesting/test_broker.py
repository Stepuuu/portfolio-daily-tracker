from types import SimpleNamespace

from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.strategies.base import Order


def make_bar(date="2024-01-02", open_=10.0, high=10.5, low=9.5, close=10.2, volume=1_000_000, prev_close=10.0):
    return SimpleNamespace(
        date=date,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        prev_close=prev_close,
    )


def make_order(direction, quantity=100, price=0.0, order_type="market"):
    return Order(
        order_id=f"{direction}-1",
        symbol="600000",
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        timestamp="2024-01-01",
    )


def test_market_buy_fills_and_updates_cash_position():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    order = broker.submit_order(make_order("buy", quantity=1000))

    trades, changed = broker.process_bar("600000", make_bar())

    assert len(trades) == 1
    assert changed == [order]
    assert order.status == "filled"
    assert broker.get_position("600000").quantity == 1000
    assert broker.cash < 100_000


def test_t_plus_one_blocks_same_day_sell():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    broker.submit_order(make_order("buy", quantity=1000))
    broker.process_bar("600000", make_bar(date="2024-01-02"))

    sell = broker.submit_order(make_order("sell", quantity=1000))
    trades, changed = broker.process_bar("600000", make_bar(date="2024-01-02"))

    assert trades == []
    assert sell.status == "queued"
    assert changed == [sell]
    assert broker.get_position("600000").quantity == 1000


def test_one_word_limit_blocks_fill():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    broker.submit_order(make_order("buy", quantity=1000))
    limit_up_bar = make_bar(open_=11.0, high=11.0, low=11.0, close=11.0, prev_close=10.0)

    trades, changed = broker.process_bar("600000", limit_up_bar)

    assert trades == []
    assert changed[0].status == "queued"
    assert broker.get_position("600000") is None


def test_force_close_realizes_position():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    broker.submit_order(make_order("buy", quantity=1000))
    broker.process_bar("600000", make_bar(open_=10.0, close=10.0))

    trade = broker.force_close("600000", price=11.0, timestamp="2024-01-03")

    assert trade is not None
    assert trade.direction == "sell"
    assert broker.get_position("600000") is None
    assert broker.cash > 100_000
