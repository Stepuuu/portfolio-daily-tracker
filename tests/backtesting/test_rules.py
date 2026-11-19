"""RuleStrategy: a declarative spec runs through the real engine on hand-built bars.

Dates: frame() starts on Tuesday 2024-01-02 and counts business days, so index
0..13 are 01-02, 01-03, 01-04, 01-05, 01-08, 01-09, 01-10, 01-11, 01-12, 01-15,
01-16, 01-17, 01-18, 01-19. A signal on bar t's close fills at bar t+1's open;
slippage is 0 so prices are exact. AAPL is a US symbol (lot 1, no T+1), 600000 an
A-share (lot 100, T+1, 10% limits).
"""
import json

import numpy as np
import pandas as pd
import pytest

from backtesting.broker.simulated import BrokerConfig
from backtesting.engine import BacktestEngine
from backtesting.factors.technical import TechnicalFactors
from backtesting.strategies.rules import (
    EXIT_REASON_LABELS, RuleStrategy, normalize_spec, rule_signal,
)

ALWAYS = {"rule": "always"}
SMA3 = {"indicator": "sma", "period": 3}
CLOSE = {"indicator": "close"}


def frame(rows, start="2024-01-02"):
    """rows: (open, high, low, close) tuples, or one price for a one-price bar."""
    rows = [(r, r, r, r) if isinstance(r, (int, float)) else r for r in rows]
    dates = pd.date_range(start, periods=len(rows), freq="B")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=dates)
    df["volume"] = 1_000_000.0
    df["amount"] = df["close"] * 1_000_000.0
    df.index.name = "date"
    return df


def day(index, start="2024-01-02"):
    return str(pd.bdate_range(start, periods=index + 1)[-1].date())


def run(df, spec, symbol="AAPL", cash=100_000, warmup=1):
    engine = BacktestEngine(initial_cash=cash, broker_config=BrokerConfig(slippage_pct=0.0), use_cache=False)
    engine.add_data(symbol, "", "", df=df, adjust="", warmup=warmup)
    engine.add_strategy(RuleStrategy, spec=spec)
    return engine.run()


def buys(result):
    return [(str(t.timestamp.date()), t.price) for t in result.trades if t.direction == "buy"]


def sells(result):
    """(date, price, reason) of every real sell; the end-of-data valuation is left out."""
    return [(str(t.timestamp.date()), t.price, t.reason) for t in result.trades
            if t.direction == "sell" and t.reason != "end_mark"]


# ------------------------------------------------------------------ #
#  Signal on the close, fill at the next open
# ------------------------------------------------------------------ #

def test_signal_on_close_fills_at_the_next_open_and_exit_likewise():
    spec = {"entry": {"rule": "compare", "left": CLOSE, "op": ">", "right": SMA3},
            "exits": [{"rule": "compare", "left": CLOSE, "op": "<", "right": SMA3}]}
    df = frame([
        10, 10, 10,                                  # 0-2 warm-up
        10,                                          # 3: close 10, sma3 10 -> not above
        (10.4, 11.1, 10.3, 11.0),                    # 4: close 11 > sma3 10.33 -> buy signal
        (11.5, 12.2, 11.4, 12.0),                    # 5: the buy fills at this open, 11.5
        (12.0, 12.1, 11.9, 12.0),                    # 6: close 12 > sma3 11.67 -> hold
        (11.8, 11.9, 8.9, 9.0),                      # 7: close 9 < sma3 11 -> sell signal
        (8.8, 9.1, 8.7, 9.0),                        # 8: the sell fills at this open, 8.8
        9.0, 9.0,
    ])
    result = run(df, spec, warmup=3)
    assert buys(result) == [(day(5), 11.5)]
    assert sells(result) == [(day(8), 8.8, "signal")]
    quantity = result.trades[0].quantity
    assert quantity * 11.5 <= 100_000 < (quantity + 1) * 11.5       # as many shares as the cash buys at the fill price
    assert result.round_trips[0]["exit_reason"] == "signal"


def test_always_enters_once_on_the_first_tradable_bar_and_never_again():
    df = frame([10, 10, 10, (10.0, 10.1, 9.9, 10.0), 10.0, 10.0])
    result = run(df, {"entry": ALWAYS, "stop_pct": 8})            # warm-up is bar 0, first tradable bar is 1
    assert buys(result) == [(day(2), 10.0)]                       # decided on bar 1's close, filled at bar 2's open
    assert sells(result) == []
    assert result.round_trips[0]["exit_reason"] == "end_mark"


# ------------------------------------------------------------------ #
#  Stop, target, trailing, maximum hold
# ------------------------------------------------------------------ #

ENTER = [10, 10, (10.0, 10.2, 9.9, 10.0)]        # signal on bar 1, entry at 10.0 on bar 2


def test_stop_triggers_inside_the_bar_at_the_stop_price():
    df = frame(ENTER + [(9.9, 10.0, 9.0, 9.4), 9.4, 9.4])
    result = run(df, {"entry": ALWAYS, "stop_pct": 8})
    assert buys(result) == [(day(2), 10.0)]
    assert sells(result) == [(day(3), 9.2, "stop")]               # 10 less 8%
    assert result.round_trips[0]["exit_reason"] == "stop"
    assert len(buys(result)) == 1                                 # an "always" entry is not repeated after the stop


def test_stop_gapped_through_fills_at_the_open():
    df = frame(ENTER + [(8.5, 9.0, 8.4, 8.8), 8.8])
    assert sells(run(df, {"entry": ALWAYS, "stop_pct": 8})) == [(day(3), 8.5, "stop")]


def test_target_triggers_and_cancels_the_stop():
    df = frame(ENTER + [(10.5, 12.5, 10.4, 12.2), 12.2])
    result = run(df, {"entry": ALWAYS, "stop_pct": 8, "target_pct": 20})
    assert sells(result) == [(day(3), 12.0, "target")]
    assert result._broker.pending_orders() == []                 # one cancels the other
    assert result.round_trips[0]["exit_reason"] == "target"


def test_stop_and_target_in_one_bar_the_stop_goes_first():
    df = frame(ENTER + [(10.0, 12.5, 9.0, 11.0), 11.0])
    assert sells(run(df, {"entry": ALWAYS, "stop_pct": 8, "target_pct": 20})) == [(day(3), 9.2, "stop")]


def test_trailing_stop_follows_the_high_and_exits_after_a_new_high():
    df = frame(ENTER + [
        (10.0, 11.0, 9.9, 10.9),        # 3: high 11 -> line 9.9
        (10.9, 12.0, 10.8, 11.9),       # 4: new high 12 -> line 10.8
        (11.9, 11.95, 11.2, 11.3),      # 5: a lower high does not lower the line; low 11.2 > 10.8
        (11.0, 11.1, 10.0, 10.2),       # 6: low 10.0 touches the line: fills at 10.8 (open 11.0 is above)
        10.2,
    ])
    result = run(df, {"entry": ALWAYS, "trailing_pct": 10})
    assert sells(result) == [(day(6), 10.8, "trailing")]
    assert result.round_trips[0]["exit_reason"] == "trailing"


def test_trailing_line_uses_completed_bars_only():
    # Bar 3 spikes to 13 and falls back inside the same bar. The line for bar 3 was
    # set from the high of bar 2 (10.2 -> 9.18), so bar 3's own spike cannot stop it
    # out; the line moves to 13 * 0.9 = 11.7 only after the bar closes.
    df = frame(ENTER + [(10.0, 13.0, 9.5, 10.0), (10.0, 10.5, 10.0, 10.3), 10.3])
    result = run(df, {"entry": ALWAYS, "trailing_pct": 10})
    assert sells(result) == [(day(4), 10.0, "trailing")]          # bar 4 opens at 10.0, below the new 11.7 line


def test_fixed_stop_wins_label_when_it_is_the_higher_line():
    df = frame(ENTER + [(9.9, 10.0, 9.0, 9.4), 9.4])
    result = run(df, {"entry": ALWAYS, "stop_pct": 8, "trailing_pct": 10})
    assert sells(result) == [(day(3), 9.2, "stop")]               # 9.2 beats 10.2 * 0.9 = 9.18


def test_max_hold_sells_at_the_next_open_with_reason():
    df = frame(ENTER + [10, 10, 10, (10.5, 10.6, 10.4, 10.5), 10.5])
    result = run(df, {"entry": ALWAYS, "max_hold_days": 3})
    # Bought on bar 2 (day 0); bar 5 is day 3 -> the sell is decided on its close, filled at bar 6's open.
    assert sells(result) == [(day(6), 10.5, "max_hold")]
    assert result.round_trips[0]["exit_reason"] == "max_hold"


def test_end_of_data_is_labelled_as_a_valuation():
    result = run(frame(ENTER + [10, 10]), {"entry": ALWAYS})
    assert result.round_trips[0]["exit_reason"] == "end_mark"
    assert set(EXIT_REASON_LABELS) == {"signal", "stop", "target", "trailing", "max_hold", "end_mark"}


# ------------------------------------------------------------------ #
#  entry_date mode
# ------------------------------------------------------------------ #

DATED = frame([(100 + i,) * 4 for i in range(12)])      # bar i opens and closes at 100 + i


@pytest.mark.parametrize("entry_date, fill_index", [
    ("2024-01-09", 6),     # a trading day (bar 5): decided on its close, filled at bar 6's open
    ("2024-01-06", 5),     # a Saturday: the first bar on/after is Monday 01-08 (bar 4), filled at bar 5
    ("2023-12-01", 3),     # before the data starts: the first tradable bar is bar 2, filled at bar 3
    ("2024-01-03", 3),     # inside the warm-up: same as before the start
])
def test_entry_date_mode(entry_date, fill_index):
    result = run(DATED, {"entry_date": entry_date, "stop_pct": 8}, warmup=2)
    assert buys(result) == [(day(fill_index), 100.0 + fill_index)]


def test_entry_date_after_the_data_never_enters():
    result = run(DATED, {"entry_date": "2030-01-01", "stop_pct": 8}, warmup=2)
    assert result.trades == []


def test_entry_date_manages_exits_and_does_not_enter_again():
    df = frame([10, 10, 10, (10.0, 10.1, 9.9, 10.0), (9.9, 10.0, 8.0, 8.5)] + [10.0] * 5)
    result = run(df, {"entry_date": "2024-01-03", "stop_pct": 8}, warmup=1)
    assert buys(result) == [(day(2), 10.0)]          # 01-03 is bar 1, the first tradable bar
    assert sells(result) == [(day(4), 9.2, "stop")]
    assert len(buys(result)) == 1


def test_entry_date_and_entry_rule_together_are_refused():
    with pytest.raises(ValueError, match="买入日期"):
        normalize_spec({"entry_date": "2024-01-03", "entry": {"rule": "compare", "left": CLOSE, "op": ">", "right": SMA3}})


# ------------------------------------------------------------------ #
#  Broker interaction
# ------------------------------------------------------------------ #

def test_buy_blocked_by_a_one_price_limit_up_expires_and_is_decided_again():
    # Bar 1's close is the signal; bar 2 is a one-price limit-up board (11.00): the buy cannot
    # fill and expires after that bar. The rule is looked at again on bar 2's close and a
    # fresh order fills at bar 3's open; the old order is not carried or chased.
    df = frame([10, 10, 11.0, (11.0, 11.4, 10.9, 11.2), 11.2], start="2024-01-02")
    result = run(df, {"entry": ALWAYS}, symbol="600000")
    assert buys(result) == [(day(3), 11.0)]
    assert [(u["reason"], u["outcome"], u["signal_date"]) for u in result.unfilled] == [
        ("limit_up", "expired", day(1))]


def test_a_stop_cannot_fire_on_the_entry_day():
    # The entry bar's low (9.1) is below the stop (9.2) but it is the entry day:
    # A-share T+1 cannot sell it, and the stop only exists after the entry bar closes.
    df = frame([10, 10, (10.0, 10.2, 9.1, 10.0), (9.9, 10.0, 9.1, 9.5), 9.5])
    result = run(df, {"entry": ALWAYS, "stop_pct": 8}, symbol="600000")
    assert buys(result) == [(day(2), 10.0)]
    assert sells(result) == [(day(3), 9.2, "stop")]


def test_a_sell_blocked_by_limit_down_is_not_sent_twice():
    spec = {"entry": ALWAYS, "exits": [{"rule": "compare", "left": CLOSE, "op": "<", "right": 9.5}]}
    df = frame([
        10, 10, (10.0, 10.1, 9.9, 10.0), 10.0,
        (10.0, 10.0, 9.2, 9.3),          # 4: close 9.3 < 9.5 -> sell signal
        8.37, 7.53,                      # 5, 6: one-price limit-down boards, the sell cannot fill
        (7.0, 7.4, 7.0, 7.2),            # 7: fills at the open, 7.0
        7.2,
    ])
    result = run(df, spec, symbol="600000")
    assert sells(result) == [(day(7), 7.0, "signal")]
    assert [(u["direction"], u["outcome"], u["bars_waited"]) for u in result.unfilled] == [("sell", "filled_late", 2)]


def test_position_size_is_a_share_of_equity_in_whole_lots():
    df = frame([10, 10, 10, 10, 10])
    half = run(df, {"entry": ALWAYS, "position_pct": 50}, symbol="600000")
    # half of 100,000 buys 5,000 shares at 10, but fees must fit in the same budget: 4,900
    assert half.trades[0].quantity == 4900
    full = run(df, {"entry": ALWAYS}, symbol="600000", cash=100_300)
    assert full.trades[0].quantity == 10_000


def test_cash_that_cannot_buy_a_lot_is_logged_not_forced():
    result = run(frame([10, 10, 10, 10]), {"entry": ALWAYS}, symbol="600000", cash=500)
    assert result.trades == []
    assert result.unfilled and result.unfilled[0]["reason"] == "lot_unaffordable"


# ------------------------------------------------------------------ #
#  No look-ahead
# ------------------------------------------------------------------ #

def random_bars(seed, n=140, after=None, split=None):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    df = frame(list(zip(open_, high, low, close)))
    if after is not None:
        # same past up to `split`, a different future afterwards
        other = random_bars(after, n)
        scale = df["close"].iloc[split] / other["close"].iloc[split]
        future = other.iloc[split + 1:][["open", "high", "low", "close"]] * scale
        df.iloc[split + 1:, df.columns.get_indexer(["open", "high", "low", "close"])] = future.to_numpy()
    return df


SPECS = {
    "ma": {"entry": {"rule": "compare", "left": CLOSE, "op": ">", "right": {"indicator": "sma", "period": 10}},
           "exits": [{"rule": "compare", "left": CLOSE, "op": "<", "right": {"indicator": "sma", "period": 10}}],
           "stop_pct": 6},
    "rsi": {"entry": {"rule": "compare", "left": {"indicator": "rsi", "period": 14}, "op": "<", "right": 40},
            "exits": [{"rule": "compare", "left": {"indicator": "rsi", "period": 14}, "op": ">", "right": 60}],
            "max_hold_days": 6, "stop_pct": 5},
    "breakout": {"entry": {"rule": "compare", "left": CLOSE, "op": ">", "right": {"indicator": "highest_close", "period": 8}},
                 "exits": [{"rule": "compare", "left": CLOSE, "op": "<", "right": {"indicator": "lowest_close", "period": 5}}]},
    "protect": {"entry_date": "2024-03-04", "stop_pct": 4, "target_pct": 8, "trailing_pct": 5},
}


def comparable(result, until):
    return [(str(t.timestamp.date()), t.direction, round(t.price, 9), t.quantity, t.reason)
            for t in result.trades if t.reason != "end_mark" and t.timestamp <= until]


@pytest.mark.parametrize("name", sorted(SPECS))
def test_changing_a_future_bar_never_changes_an_earlier_trade(name):
    compared = 0
    for seed in (1, 2, 3, 4):
        original = random_bars(seed)
        for split in (60, 90, 115):
            changed = random_bars(seed, after=seed + 100, split=split)
            assert not changed["close"].iloc[split + 1:].equals(original["close"].iloc[split + 1:])
            until = original.index[split]
            first = comparable(run(original, SPECS[name], warmup=30), until)
            second = comparable(run(changed, SPECS[name], warmup=30), until)
            assert first == second
            compared += len(first)
    assert compared > 0                                    # the comparison was not vacuous


def test_rule_signals_equal_on_a_truncated_history():
    df = random_bars(7)
    rules = [SPECS["ma"]["entry"], SPECS["rsi"]["entry"], SPECS["breakout"]["entry"], SPECS["breakout"]["exits"][0]]
    for rule in rules:
        rule = normalize_spec({"entry": rule})["entry"]
        full = rule_signal(df, rule)
        for cut in (40, 77, 120):
            assert (rule_signal(df.iloc[:cut], rule) == full[:cut]).all()


def test_highest_close_excludes_the_current_bar():
    df = frame([10, 11, 12, 11.5, 13])
    rule = normalize_spec({"entry": {"rule": "compare", "left": CLOSE, "op": ">",
                                     "right": {"indicator": "highest_close", "period": 3}}})["entry"]
    # bar 3 (11.5) is not above the previous three closes' high (12); bar 4 (13) is
    assert rule_signal(df, rule).tolist() == [False, False, False, False, True]


def test_indicators_come_from_technical_factors():
    df = random_bars(5)
    rule = normalize_spec({"entry": {"rule": "compare", "left": {"indicator": "rsi", "period": 14}, "op": "<", "right": 50}})["entry"]
    expected = (TechnicalFactors.rsi(df["close"], 14) < 50).to_numpy()
    assert (rule_signal(df, rule) == expected).all()


# ------------------------------------------------------------------ #
#  Validation
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("spec, message", [
    ({"entry": {"rule": "magic"}}, "未知的规则"),
    ({"entry": {"rule": "compare", "left": {"indicator": "macd_x", "period": 5}, "op": ">", "right": 1}}, "未知的指标"),
    ({"entry": {"rule": "compare", "left": CLOSE, "op": "=>", "right": 1}}, "未知的比较符号"),
    ({"entry": {"rule": "compare", "left": CLOSE, "op": ">", "right": 1}, "bogus": 1}, "未知的字段"),
    ({"entry": {"rule": "compare", "left": {"indicator": "sma"}, "op": ">", "right": 1}}, "周期"),
    ({"entry": {"rule": "compare", "left": {"indicator": "sma", "period": 0}, "op": ">", "right": 1}}, "周期"),
    ({"entry": {"rule": "compare", "left": {"indicator": "sma", "period": 2.5}, "op": ">", "right": 1}}, "整数"),
    ({"entry": {"rule": "compare", "left": {"indicator": "sma", "period": True}, "op": ">", "right": 1}}, "数字"),
    ({"entry": {"rule": "compare", "left": 3, "op": ">", "right": 1}}, "至少"),
    ({"entry": ALWAYS, "exits": [ALWAYS]}, "入场规则"),
    ({"exits": []}, "缺少入场规则"),
    ({"entry": ALWAYS, "stop_pct": -1}, "止损"),
    ({"entry": ALWAYS, "stop_pct": 100}, "止损"),
    ({"entry": ALWAYS, "position_pct": 0}, "仓位"),
    ({"entry": ALWAYS, "position_pct": 101}, "仓位"),
    ({"entry": ALWAYS, "max_hold_days": 2.5}, "整数"),
    ({"entry_date": "last tuesday"}, "买入日期"),
])
def test_a_bad_spec_is_refused_with_a_chinese_message(spec, message):
    with pytest.raises(ValueError, match=message):
        normalize_spec(spec)


def test_the_engine_refuses_a_bad_spec_before_any_trade():
    with pytest.raises(ValueError, match="未知的规则"):
        run(frame([10, 10, 10]), {"entry": {"rule": "magic"}})


def test_normalized_spec_is_complete_json_and_stable():
    spec = normalize_spec({"entry": {"rule": "compare", "left": CLOSE, "op": ">", "right": SMA3}, "stop_pct": 0})
    assert spec["stop_pct"] is None and spec["position_pct"] == 100 and spec["exits"] == []
    assert json.loads(json.dumps(spec)) == spec
    assert normalize_spec(spec) == spec


def test_result_records_the_resolved_spec_and_is_json_serialisable():
    result = run(frame(ENTER + [10, 10]), {"entry": ALWAYS, "name": "测试", "stop_pct": 8})
    assert result.strategy_name == "测试"
    assert result.strategy_params["spec"]["stop_pct"] == 8
    json.dumps(result.to_dict(), default=str)
