"""Finding 15 of the second engine review: fee limitations that
fee_schedules.json documents as not modelled are disclosed."""
from types import SimpleNamespace

import pytest

from backtesting.analyzer.caveats import build_caveats
from backtesting.broker.fees import default_schedule, fees_assumption_text, not_modelled
from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.data.symbols import resolve
from backtesting.strategies.base import Order


def caveats_for(*symbols):
    return build_caveats(unfilled=[], symbols=list(symbols), config=BrokerConfig())


def fee_caveats(*symbols):
    return [c for c in caveats_for(*symbols) if c["code"] == "fee_not_modelled"]


def test_hk_etfs_resolve_as_stocks_so_the_exemption_cannot_be_modelled():
    # symbols.resolve() does not tell HK ETFs apart (kind "stock"): the stamp-duty
    # exemption is disclosed instead of modelled.
    assert resolve("HKG:02800").kind == "stock"


def test_the_reviewers_hk_etf_round_trip_is_charged_stamp_duty_and_says_so():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    bar = SimpleNamespace(date="2024-06-03", open=20.0, high=20.0, low=20.0, close=20.0,
                          volume=10_000_000.0, prev_close=20.0)
    for oid, direction in (("b", "buy"), ("s", "sell")):
        broker.submit_order(Order(order_id=oid, symbol="HKG:02800", direction=direction, order_type="market",
                                  quantity=1_000, price=0.0, timestamp="2024-06-02"))
        broker.process_bar("HKG:02800", bar)
    assert sum(t.fees.get("stamp_duty", 0.0) for t in broker.trades) == pytest.approx(40.0)
    assert broker.cash == pytest.approx(99_946.60)

    (item,) = fee_caveats("HKG:02800")
    assert item["level"] == "warning"
    assert "ETF" in item["text"] and "印花税" in item["text"]


def test_us_runs_disclose_the_finra_taf():
    (item,) = fee_caveats("AAPL")
    assert "TAF" in item["text"] and item["level"] == "info"


def test_every_market_the_schedule_marks_as_not_modelled_has_a_caveat():
    schedule = default_schedule()
    for market, symbol in (("a_share", "600519"), ("hk", "00700"), ("us", "AAPL")):
        assert schedule.ignored(market), market
        assert not_modelled(market), market
        assert len(fee_caveats(symbol)) == 1, market
    assert len(fee_caveats("600519", "00700", "AAPL")) == 3


def test_the_fee_assumption_text_lists_what_is_not_modelled():
    text = fees_assumption_text()
    assert default_schedule().version in text
    for market in ("a_share", "hk", "us"):
        for item in default_schedule().ignored(market):
            assert item in text
    assert "HK ETF" in text and "FINRA" in text
