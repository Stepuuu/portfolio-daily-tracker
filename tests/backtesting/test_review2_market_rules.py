"""Finding 3 of the second engine review: market rules, lots, limits, T+1 and
fees follow the canonical instrument, whichever accepted spelling is typed."""
from types import SimpleNamespace

import pytest

from backtesting.broker import market_rules
from backtesting.broker.fees import compute_fees
from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.data.symbols import SymbolError
from backtesting.strategies.base import Order

ALIASES = {
    "SHA:600519": ["600519", "SH600519", "SH:600519", "SSE:600519", "600519.SH", "600519.SS", "sh600519"],
    "SHE:000001": ["000001", "SZ000001", "SZ:000001", "SZSE:000001", "000001.SZ"],
    "SHA:688981": ["688981", "SH688981", "SSE:688981", "688981.SS"],          # STAR: 200 + 1, 20%
    "SHE:300750": ["300750", "SZ300750", "SZSE:300750", "300750.SZ"],         # ChiNext: 20% from 2020-08-24
    "SHA:510300": ["510300", "ETF:510300", "SH510300", "SSE:510300", "510300.SS"],   # ETF: 0.001 tick
    "SHE:159915": ["159915", "ETF:159915", "SZ159915", "159915.SZ"],
    "HKG:00700": ["00700", "0700", "700.HK", "0700.HK", "HKG:700", "HKEX:00700", "HK:00700"],
    "US:AAPL": ["AAPL", "NASDAQ:AAPL", "AAPL.US", "aapl"],
}
CASES = [(canonical, alias) for canonical, aliases in ALIASES.items() for alias in aliases]


def rules(symbol):
    lot = market_rules.lot_rule(symbol)
    return {
        "market": market_rules.market_for(symbol),
        "kind": market_rules.instrument_kind(symbol),
        "lot": (lot.minimum, lot.step, lot.assumed),
        "tick": market_rules.price_tick(symbol),
        "limit_2019": market_rules.price_limit_pct(symbol, "2019-06-03"),
        "limit_2024": market_rules.price_limit_pct(symbol, "2024-06-03"),
        "st_pattern": market_rules.st_pattern_applies(symbol, "2024-06-03"),
        "buy_fees": compute_fees(symbol, "buy", 1000, 20.0, "2024-06-03").parts(),
        "sell_fees": compute_fees(symbol, "sell", 1000, 20.0, "2024-06-03").parts(),
    }


@pytest.mark.parametrize("canonical,alias", CASES)
def test_every_accepted_spelling_gets_the_rules_of_its_canonical_ticker(canonical, alias):
    assert rules(alias) == rules(canonical)


def test_the_canonical_rules_are_the_expected_ones():
    assert rules("SHA:600519")["lot"] == (100, 100, False)
    assert rules("SHA:688981")["lot"] == (200, 1, False)
    assert rules("SHE:300750")["limit_2024"] == 0.20 and rules("SHE:300750")["limit_2019"] == 0.10
    assert str(rules("SHA:510300")["tick"]) == "0.001" and rules("SHA:510300")["kind"] == "fund"
    assert rules("HKG:00700")["market"] == "hk" and rules("HKG:00700")["lot"] == (100, 100, True)
    assert rules("US:AAPL")["market"] == "us" and rules("US:AAPL")["limit_2024"] is None


@pytest.mark.parametrize("symbol", ["IDX:HSI", "IDX:SPX"])
def test_foreign_indices_are_not_a_share_instruments(symbol):
    assert market_rules.market_for(symbol) in ("hk", "us")
    assert market_rules.instrument_kind(symbol) == "index"
    assert market_rules.price_limit_pct(symbol, "2024-06-03") is None


@pytest.mark.parametrize("symbol", ["BJ:430047", "830799", "920118", "LOF:501018", "REIT:508000", "161725.SZ"])
def test_a_share_forms_the_data_layer_does_not_route_keep_their_board_rules(symbol):
    # BSE, LOF and REIT codes are not routed by symbols.resolve() yet, but the
    # text names an A-share code unambiguously.
    assert market_rules.market_for(symbol) == "a_share"


@pytest.mark.parametrize("symbol", ["FOO_BAR", "XYZ:123", "VOD.LSE", "12345678", ""])
def test_an_unresolvable_symbol_is_an_error_not_us_rules(symbol):
    with pytest.raises(SymbolError):
        market_rules.market_for(symbol)
    with pytest.raises(SymbolError):
        compute_fees(symbol, "buy", 100, 10.0, "2024-06-03")


def test_a_configured_market_still_wins_over_the_symbol():
    assert market_rules.market_for("FOO_BAR", "us") == "us"
    assert market_rules.lot_rule("FOO_BAR", "us").minimum == 1


# ------------------------------------------------------------------ #
#  In the broker
# ------------------------------------------------------------------ #

def bar(date, price, prev_close=None):
    return SimpleNamespace(date=date, open=price, high=price, low=price, close=price, volume=10_000_000.0,
                           prev_close=prev_close if prev_close is not None else price)


def order(symbol, direction, quantity, oid):
    return Order(order_id=oid, symbol=symbol, direction=direction, order_type="market",
                 quantity=quantity, price=0.0, timestamp="2024-06-03")


@pytest.mark.parametrize("symbol", ["SHA:600519"] + ALIASES["SHA:600519"])
def test_broker_applies_a_share_lots_and_t_plus_one_to_every_alias(symbol):
    # Reviewer's reproduction: buy(quantity=1) was rejected for SHA:600519 and
    # filled for SH600519, which was treated as a US share.
    b = SimulatedBroker(initial_cash=1_000_000, config=BrokerConfig(slippage_pct=0.0))
    b.submit_order(order(symbol, "buy", 1, "one"))
    trades, _ = b.process_bar(symbol, bar("2024-06-04", 1700.0))
    assert trades == []

    b.submit_order(order(symbol, "buy", 100, "lot"))
    b.submit_order(order(symbol, "sell", 100, "same-day"))
    trades, _ = b.process_bar(symbol, bar("2024-06-05", 1700.0))
    assert [(t.direction, t.quantity) for t in trades] == [("buy", 100)]
    assert [u["reason"] for u in b.unfilled if u["order_id"] == "same-day"] == ["t_plus_one"]


@pytest.mark.parametrize("symbol", ["SHA:600519", "SH600519", "SSE:600519"])
def test_broker_applies_the_daily_limit_to_every_alias(symbol):
    b = SimulatedBroker(initial_cash=1_000_000, config=BrokerConfig(slippage_pct=0.0))
    b.submit_order(order(symbol, "buy", 100, "board"))
    trades, _ = b.process_bar(symbol, bar("2024-06-04", 11.0, prev_close=10.0))   # one-price +10% board
    assert trades == [] and b.unfilled[0]["reason"] == "limit_up"


@pytest.mark.parametrize("symbol", ["0700", "HKEX:00700"])
def test_broker_charges_hk_fees_to_hk_aliases(symbol):
    b = SimulatedBroker(initial_cash=1_000_000, config=BrokerConfig(slippage_pct=0.0))
    b.submit_order(order(symbol, "buy", 100, "hk"))
    trades, _ = b.process_bar(symbol, bar("2024-06-04", 300.0))
    assert trades[0].fees.get("stamp_duty") == 30.0


def test_broker_rejects_an_unresolvable_symbol_at_order_time_and_when_its_data_is_added():
    b = SimulatedBroker(initial_cash=1_000_000, config=BrokerConfig(slippage_pct=0.0))
    with pytest.raises(SymbolError):
        b.submit_order(order("FOO_BAR", "buy", 100, "x"))
    with pytest.raises(SymbolError):
        b.set_price_basis("FOO_BAR", "raw")


# ------------------------------------------------------------------ #
#  Through the engine
# ------------------------------------------------------------------ #

def run_buy_one(symbol):
    import pandas as pd
    from backtesting.engine import BacktestEngine
    from backtesting.strategies.base import Strategy

    class BuyOne(Strategy):
        def on_bar(self):
            if self.data.current_index == self.data.warmup:
                self.buy(quantity=1)

    df = pd.DataFrame({"open": 1700.0, "high": 1710.0, "low": 1690.0, "close": 1700.0,
                       "volume": 1_000_000.0, "amount": 1.7e9},
                      index=pd.DatetimeIndex(pd.bdate_range("2024-06-03", periods=5), name="date"))
    engine = BacktestEngine(initial_cash=1_000_000, broker_config=BrokerConfig(slippage_pct=0.0), use_cache=False)
    engine.add_data(symbol, "", "", df=df, adjust="qfq", warmup=0)
    engine.add_strategy(BuyOne)
    return engine.run()


@pytest.mark.parametrize("symbol", ["SHA:600519", "SH600519", "SH:600519", "SSE:600519"])
def test_engine_runs_reject_a_one_share_a_share_buy_for_every_alias(symbol):
    # Reviewer's reproduction at the engine level: identical bars plus
    # buy(quantity=1); SHA:600519 rejected the lot, SH600519 bought one share.
    result = run_buy_one(symbol)
    assert [t for t in result.trades if t.direction == "buy"] == []


def test_engine_refuses_an_unresolvable_symbol_when_its_data_is_added():
    with pytest.raises(SymbolError):
        run_buy_one("FOO_BAR")
