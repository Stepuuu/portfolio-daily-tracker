from types import SimpleNamespace
import json

import numpy as np
import pandas as pd
import pytest

from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.data.quality import require_valid_daily_bars
from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Order, Strategy
from tests.backtesting.test_engine import make_df, BuyOnceStrategy
from tests.backtesting.test_broker import make_bar, make_order
from backtesting.data.loader import DataLoader
from backtesting.data.feed import DataFeed


@pytest.mark.parametrize("problem", ["missing", "infinite", "negative_volume", "ohlc"])
def test_raw_download_rejected_before_cache_write(monkeypatch, problem):
    frame = make_df().astype({"volume": float})
    if problem == "missing":
        frame.iloc[3, frame.columns.get_loc("open")] = np.nan
    elif problem == "infinite":
        frame.iloc[3, frame.columns.get_loc("volume")] = float("inf")
    elif problem == "negative_volume":
        frame.iloc[3, frame.columns.get_loc("volume")] = -1
    else:
        frame.iloc[3, frame.columns.get_loc("high")] = 1.0
    engine = BacktestEngine(use_cache=False)
    monkeypatch.setattr(engine._loader, "get_daily", lambda *args: frame)
    writes = []
    monkeypatch.setattr(engine._store, "save_daily", lambda *args: writes.append(args))
    with pytest.raises(ValueError, match="数据质量"):
        engine.add_data("600000", "2024-01-01", "2024-01-31")
    assert writes == []


def test_quality_rejects_minute_observations_and_non_datetime_index():
    frame = make_df()
    frame.index = pd.date_range("2024-01-01", periods=len(frame), freq="min")
    with pytest.raises(ValueError, match="daily bars"):
        require_valid_daily_bars(frame)
    frame.index = range(len(frame))
    with pytest.raises(ValueError, match="DatetimeIndex"):
        require_valid_daily_bars(frame)


@pytest.mark.parametrize("symbol", ["NASDAQ:EXAMPLE", "HKG:00001", "EXAMPLE.US", "00001.HK"])
def test_other_markets_do_not_inherit_a_share_limits_or_t_plus_one(symbol):
    broker = SimulatedBroker(100000, BrokerConfig(slippage_pct=0))
    buy = make_order("buy", 100)
    buy.symbol = symbol
    broker.submit_order(buy)
    limit_bar = make_bar(open_=11, high=11, low=11, close=11)
    trades, _ = broker.process_bar(symbol, limit_bar)
    assert len(trades) == 1
    sell = make_order("sell", 100)
    sell.symbol = symbol
    broker.submit_order(sell)
    trades, _ = broker.process_bar(symbol, limit_bar)
    assert len(trades) == 1


def test_zero_volume_cannot_fill():
    broker = SimulatedBroker()
    broker.submit_order(make_order("buy"))
    trades, _ = broker.process_bar("600000", make_bar(volume=0))
    assert not trades and not broker.positions


def test_old_a_share_holdings_remain_sellable_after_same_day_buy():
    broker = SimulatedBroker(100000, BrokerConfig(slippage_pct=0))
    broker.submit_order(make_order("buy", 100))
    broker.process_bar("600000", make_bar(date="2024-01-01"))
    broker.submit_order(make_order("buy", 100))
    broker.process_bar("600000", make_bar(date="2024-01-02"))
    sell = broker.submit_order(make_order("sell", 200))
    trades, _ = broker.process_bar("600000", make_bar(date="2024-01-02"))
    assert trades[0].quantity == sell.filled_qty == 100
    assert broker.get_position("600000").quantity == 100


def test_recorded_fill_quantity_matches_affordable_trade():
    broker = SimulatedBroker(1100, BrokerConfig(slippage_pct=0))
    order = broker.submit_order(make_order("buy", 1000))
    trades, _ = broker.process_bar("600000", make_bar())
    assert trades[0].quantity == order.filled_qty == 100
    assert order.status == "partial_cancelled"


def test_market_lot_defaults_and_explicit_override_are_distinct():
    assert BrokerConfig().lot_for("EXAMPLE") == 1
    assert BrokerConfig().lot_for("600000") == 100
    assert BrokerConfig(lot_size=100).lot_for("EXAMPLE") == 100


def test_final_liquidation_does_not_add_an_extra_trading_day():
    engine = BacktestEngine(initial_cash=100000)
    frame = make_df()
    engine.add_data("EXAMPLE", "", "", df=frame, warmup=1)
    engine.add_strategy(BuyOnceStrategy)
    result = engine.run()
    assert len(result.equity_df) == len(frame)
    assert result.equity_df.index.is_unique
    assert result.trades[-1].order_id == "FORCE_CLOSE"


def test_auxiliary_symbol_is_matched_and_valued():
    class Auxiliary(Strategy):
        def on_init(self):
            self.sent = False

        def on_bar(self):
            if not self.sent:
                self.buy(symbol="BBB", quantity=100)
                self.sent = True

    engine = BacktestEngine(initial_cash=100000)
    engine.add_data("AAA", "", "", df=make_df(), warmup=1)
    engine.add_data("BBB", "", "", df=make_df(), warmup=1, is_primary=False)
    engine.add_strategy(Auxiliary)
    result = engine.run()
    assert any(trade.symbol == "BBB" and trade.direction == "buy" for trade in result.trades)
    assert result.stats.final_value > 100000


def test_unimplemented_short_mode_and_invalid_orders_rejected():
    with pytest.raises(ValueError, match="Short selling"):
        BrokerConfig(allow_short=True)
    broker = SimulatedBroker()
    order = broker.submit_order(make_order("buy", float("nan")))
    assert order.status == "rejected"


@pytest.mark.parametrize("problem", ["duplicate", "unordered", "missing", "bad_price"])
def test_loader_does_not_hide_source_errors(problem):
    frame = make_df().reset_index()
    if problem == "duplicate":
        frame.loc[3, "date"] = frame.loc[2, "date"]
    elif problem == "unordered":
        frame = frame.iloc[::-1]
    elif problem == "missing":
        frame.loc[3, "date"] = pd.NaT
    else:
        frame.loc[3, "high"] = 0
    with pytest.raises(ValueError):
        DataLoader()._standardize(frame)


def test_feed_accessors_refuse_negative_history_offsets():
    feed = DataFeed("EXAMPLE", make_df())
    feed.advance()
    for accessor in (feed.close, feed.open, feed.high, feed.low, feed.volume):
        with pytest.raises(ValueError, match="History offset"):
            accessor(-1)


def test_no_losses_produce_null_profit_factor_and_safe_reflection():
    from backtesting.reflection.llm_reflector import BacktestReflector
    engine = BacktestEngine(initial_cash=100000)
    engine.add_data("EXAMPLE", "", "", df=make_df(), warmup=1)
    engine.add_strategy(BuyOnceStrategy)
    result = engine.run()
    assert result.to_dict()["profit_factor"] is None
    assert result.stats.to_dict()["profit_factor"] is None
    json.dumps(result.to_dict(), allow_nan=False)
    assert isinstance(BacktestReflector()._extract_lessons("", result.to_dict()), list)
