from types import SimpleNamespace

import pytest

from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.data.symbols import SymbolError
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
    # A blocked buy is not carried to later bars (it used to stay queued and fill
    # at whatever price the board opened at).
    assert changed[0].status == "expired"
    assert changed[0].reason == "limit_up"
    assert broker.pending_orders() == []
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


# A-share limit prices are prev_close * (1 +/- pct) rounded half-up to 0.01,
# so the board price is 11.03 for a previous close of 10.03 (not 11.033).


@pytest.mark.parametrize("symbol,prev_close,expected", [
    ("600000", 10.00, (11.00, 9.00)),
    ("600000", 10.03, (11.03, 9.03)),     # 11.033 -> 11.03, 9.027 -> 9.03
    ("600000", 10.05, (11.06, 9.05)),     # 11.055 -> 11.06, 9.045 -> 9.05 (half-up)
    ("300750", 10.03, (12.04, 8.02)),     # 20% board: 12.036 -> 12.04, 8.024 -> 8.02
])
def test_limit_prices_are_rounded_half_up_to_the_tick(symbol, prev_close, expected):
    broker = SimulatedBroker(initial_cash=100_000)

    assert broker._limit_prices(symbol, make_bar(prev_close=prev_close)) == expected


def test_one_word_limit_up_blocks_buy_when_limit_is_not_a_round_number():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    broker.submit_order(make_order("buy", quantity=1000))
    limit_up_bar = make_bar(open_=11.03, high=11.03, low=11.03, close=11.03, prev_close=10.03)

    trades, changed = broker.process_bar("600000", limit_up_bar)

    assert trades == []
    assert changed[0].status == "expired"
    assert changed[0].reason == "limit_up"
    assert broker.get_position("600000") is None
    assert broker.cash == 100_000


@pytest.mark.parametrize("prev_close,limit_down", [(10.03, 9.03), (10.05, 9.05)])
def test_one_word_limit_down_blocks_sell_when_limit_is_not_a_round_number(prev_close, limit_down):
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    broker.submit_order(make_order("buy", quantity=1000))
    broker.process_bar("600000", make_bar(date="2024-01-02"))
    sell = broker.submit_order(make_order("sell", quantity=1000))
    limit_down_bar = make_bar(date="2024-01-03", open_=limit_down, high=limit_down, low=limit_down,
                              close=limit_down, prev_close=prev_close)

    trades, changed = broker.process_bar("600000", limit_down_bar)

    assert trades == []
    assert sell.status == "queued"
    assert broker.get_position("600000").quantity == 1000


# Exchange-traded funds (ETF/LOF/REIT/closed-end) are quoted in 0.001 yuan, so their
# limit prices are rounded to 0.001: 1.02 * 1.1 = 1.122, not 1.12.
@pytest.mark.parametrize("symbol,prev_close,expected", [
    ("ETF:510300", 1.02, (1.122, 0.918)),
    ("510300", 1.02, (1.122, 0.918)),        # SSE ETF
    ("510300.SS", 1.02, (1.122, 0.918)),
    ("SHA:588000", 1.02, (1.122, 0.918)),    # SSE STAR-market ETF
    ("LOF:501018", 1.02, (1.122, 0.918)),    # SSE LOF
    ("REIT:508000", 3.456, (3.802, 3.110)),  # SSE REIT: 3.8016 -> 3.802, 3.1104 -> 3.110
    ("159915", 2.345, (2.580, 2.111)),       # SZSE ETF: 2.5795 -> 2.580 and 2.1105 -> 2.111 (half-up)
    ("161725.SZ", 1.02, (1.122, 0.918)),     # SZSE LOF
    ("SHE:180101", 1.02, (1.122, 0.918)),    # SZSE REIT
    ("600000", 1.02, (1.12, 0.92)),          # stocks keep the 0.01 tick
])
def test_fund_limit_prices_use_the_fund_tick(symbol, prev_close, expected):
    broker = SimulatedBroker(initial_cash=100_000)

    assert broker._limit_prices(symbol, make_bar(prev_close=prev_close)) == expected


def test_an_sse_15xxxx_code_is_not_an_szse_fund():
    # SHA:155001 is an SSE bond code: it does not resolve to a modelled
    # instrument, so with market="auto" it is an error rather than a guess
    # (review finding 3); with the market set to a_share it keeps the stock tick.
    with pytest.raises(SymbolError):
        SimulatedBroker(initial_cash=100_000)._limit_prices("SHA:155001", make_bar(prev_close=1.02))
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(market="a_share"))
    assert broker._limit_prices("SHA:155001", make_bar(prev_close=1.02)) == (1.12, 0.92)


def test_etf_bar_below_the_fund_limit_up_fills():
    # Previous close 1.02 gives an ETF limit-up of 1.122; a flat bar at 1.121 is
    # below it and must fill (a 0.01 tick would invent a 1.12 one-word limit-up).
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    order = broker.submit_order(Order(order_id="etf-1", symbol="ETF:510300", direction="buy",
                                      order_type="market", quantity=100, price=0.0, timestamp="2024-01-01"))
    bar = make_bar(open_=1.121, high=1.121, low=1.121, close=1.121, volume=1_000_000, prev_close=1.02)

    trades, _ = broker.process_bar("ETF:510300", bar)

    assert [trade.quantity for trade in trades] == [100]
    assert trades[0].price == pytest.approx(1.121)
    assert order.status == "filled"


def test_bar_touching_the_rounded_limit_up_only_fills_the_queue_share():
    # Opens at the rounded limit-up (10.03 x 1.1 = 11.033 -> 11.03) and trades below it:
    # not a one-price board, but the buy at the open queues at the limit. (A buy at an
    # open below the limit is not capped by the bar's later high: test_review3_broker.)
    broker = SimulatedBroker(initial_cash=1_000_000, config=BrokerConfig(slippage_pct=0.0))
    order = broker.submit_order(make_order("buy", quantity=5000))
    touching_bar = make_bar(open_=11.03, high=11.03, low=10.80, close=11.03, volume=100_000, prev_close=10.03)

    trades, _ = broker.process_bar("600000", touching_bar)

    # 3% of the bar volume instead of the usual 10% cap.
    assert [trade.quantity for trade in trades] == [3000]
    assert order.status == "partial_cancelled"
