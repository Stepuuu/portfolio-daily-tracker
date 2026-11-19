"""The five presets: describe/build/spec, parameter validation, and each preset's known
trades through the real engine on hand-built bars (see test_rules.py for the dates:
bar i of frame() is the i-th business day from 2024-01-02; signals fill at the next open)."""
import json
import re

import pytest

from backtesting import presets
from backtesting.broker.simulated import BrokerConfig
from backtesting.engine import BacktestEngine
from backtesting.factors.technical import TechnicalFactors
from backtesting.strategies.rules import EXIT_REASON_LABELS, RuleStrategy
from tests.backtesting.test_rules import buys, day, frame, random_bars, sells

CJK = re.compile(r"[一-鿿]")
IDS = ["buy_and_hold", "ma_trend", "rsi_rebound", "breakout", "stop_target"]


def run_preset(df, preset_id, params=None, warmup=1, symbol="AAPL", cash=100_000):
    strategy_class, kwargs = presets.build(preset_id, params)
    engine = BacktestEngine(initial_cash=cash, broker_config=BrokerConfig(slippage_pct=0.0), use_cache=False)
    engine.add_data(symbol, "", "", df=df, adjust="", warmup=warmup)
    engine.add_strategy(strategy_class, **kwargs)
    return engine.run()


# ------------------------------------------------------------------ #
#  describe / build / spec
# ------------------------------------------------------------------ #

def test_describe_lists_the_five_presets_with_the_agreed_shape():
    described = presets.describe()
    assert [item["id"] for item in described] == IDS
    for item in described:
        assert set(item) == {"id", "name", "summary", "params"}
        assert CJK.search(item["name"]) and CJK.search(item["summary"])
        assert item["summary"].count("。") == 1               # one sentence
        for param in item["params"]:
            assert set(param) == {"key", "label", "unit", "default", "min", "max", "step"}
            assert CJK.search(param["label"])
            if param["unit"] != "date":
                assert param["min"] <= param["default"] <= param["max"] and param["step"] > 0


def test_describe_defaults_are_the_plan_numbers():
    defaults = {item["id"]: {p["key"]: p["default"] for p in item["params"]} for item in presets.describe()}
    assert defaults == {
        "buy_and_hold": {},
        "ma_trend": {"period": 20},
        "rsi_rebound": {"buy_below": 30, "sell_above": 70, "max_hold_days": 20, "stop_pct": 8},
        "breakout": {"high_days": 20, "low_days": 10},
        "stop_target": {"entry_date": None, "stop_pct": 8, "target_pct": 20, "trailing_pct": 10},
    }
    one = next(item for item in presets.describe() if item["id"] == "ma_trend")
    assert one == {"id": "ma_trend", "name": "均线趋势",
                   "summary": "收盘价站上 N 日均线就买入，跌破这条均线就卖出。",
                   "params": [{"key": "period", "label": "均线天数", "unit": "日", "default": 20,
                               "min": 2, "max": 250, "step": 1}]}


def test_describe_returns_copies():
    presets.describe()[1]["params"][0]["default"] = 999
    assert presets.describe()[1]["params"][0]["default"] == 20


@pytest.mark.parametrize("preset_id", IDS)
def test_spec_with_defaults_round_trips_through_json(preset_id):
    spec = presets.spec(preset_id, {})
    assert json.loads(json.dumps(spec)) == spec
    assert spec["preset"] == preset_id and spec["position_pct"] == 100
    strategy_class, kwargs = presets.build(preset_id, {})
    assert strategy_class is RuleStrategy and kwargs == {"spec": spec}


def test_spec_carries_the_resolved_numbers():
    spec = presets.spec("rsi_rebound", {"stop_pct": 5, "max_hold_days": 10})
    assert spec["stop_pct"] == 5 and spec["max_hold_days"] == 10
    assert spec["params"] == {"buy_below": 30, "sell_above": 70, "max_hold_days": 10, "stop_pct": 5}
    assert spec["entry"]["right"] == 30 and spec["exits"][0]["right"] == 70
    assert presets.spec("stop_target", {"stop_pct": 0})["stop_pct"] is None        # 0 switches it off
    assert presets.spec("stop_target", {"entry_date": "2024-3-5"})["entry_date"] == "2024-03-05"
    assert presets.spec("ma_trend", {"period": None}) == presets.spec("ma_trend", {})   # an empty form field


def test_a_saved_spec_replays_to_the_same_trades():
    df = random_bars(11, n=200)
    first = run_preset(df, "breakout", {"high_days": 10, "low_days": 5}, warmup=30)
    replay_spec = json.loads(json.dumps(presets.spec("breakout", {"high_days": 10, "low_days": 5})))
    engine = BacktestEngine(initial_cash=100_000, broker_config=BrokerConfig(slippage_pct=0.0), use_cache=False)
    engine.add_data("AAPL", "", "", df=df, adjust="", warmup=30)
    engine.add_strategy(RuleStrategy, spec=replay_spec)
    second = engine.run()
    assert first.trades and [(t.timestamp, t.price, t.quantity, t.reason) for t in first.trades] == [
        (t.timestamp, t.price, t.quantity, t.reason) for t in second.trades]


@pytest.mark.parametrize("preset_id, params, message", [
    ("nope", {}, "没有这个方法"),
    (None, {}, "没有这个方法"),
    ("ma_trend", {"periods": 20}, "没有参数"),
    ("buy_and_hold", {"period": 20}, "没有参数"),
    ("ma_trend", {"period": 1}, "应在 2 到 250 之间"),
    ("ma_trend", {"period": 251}, "应在 2 到 250 之间"),
    ("ma_trend", {"period": 12.5}, "应为整数"),
    ("ma_trend", {"period": "20"}, "应为数字"),
    ("ma_trend", {"period": True}, "应为数字"),
    ("ma_trend", {"period": float("nan")}, "应为数字"),
    ("rsi_rebound", {"stop_pct": -1}, "应在 0 到 50 之间"),
    ("rsi_rebound", {"stop_pct": 51}, "应在 0 到 50 之间"),
    ("rsi_rebound", {"buy_below": 50, "sell_above": 50}, "应低于"),
    ("breakout", {"low_days": 1}, "应在 2 到 250 之间"),
    ("stop_target", {"entry_date": "yesterday"}, "YYYY-MM-DD"),
    ("stop_target", {"entry_date": 20240301}, "YYYY-MM-DD"),
    ("stop_target", {"target_pct": 501}, "应在 0 到 500 之间"),
])
def test_bad_input_is_refused_in_chinese(preset_id, params, message):
    with pytest.raises(ValueError, match=message) as caught:
        presets.build(preset_id, params)
    assert CJK.search(str(caught.value))
    with pytest.raises(ValueError):
        presets.spec(preset_id, params)


def test_params_must_be_a_dict():
    with pytest.raises(ValueError, match="字典"):
        presets.build("ma_trend", [20])


# ------------------------------------------------------------------ #
#  Each preset's known trades
# ------------------------------------------------------------------ #

def test_buy_and_hold_buys_on_the_first_tradable_bar_and_holds_to_the_end():
    df = frame([10, 10, (10.0, 10.1, 9.9, 10.0), 11, 12, 13])
    result = run_preset(df, "buy_and_hold")
    assert buys(result) == [(day(2), 10.0)]          # first tradable bar 1 decides, bar 2's open fills
    assert sells(result) == []
    assert result.round_trips[0]["exit_reason"] == "end_mark"
    assert result.round_trips[0]["exit_price"] == 13.0


def test_ma_trend_buys_above_the_average_and_sells_below_it():
    df = frame([
        10, 10, 10, 10,
        (10.4, 11.1, 10.3, 11.0),     # 4: 11 > sma3 10.33 -> buy signal
        (11.5, 12.2, 11.4, 12.0),     # 5: fills at 11.5
        (12.0, 12.1, 11.9, 12.0),
        (11.8, 11.9, 8.9, 9.0),       # 7: 9 < sma3 11 -> sell signal
        (8.8, 9.1, 8.7, 9.0),         # 8: fills at 8.8
        9.0, 9.0,
    ])
    result = run_preset(df, "ma_trend", {"period": 3}, warmup=3)
    assert buys(result) == [(day(5), 11.5)]
    assert sells(result) == [(day(8), 8.8, "signal")]
    assert [trip["exit_reason"] for trip in result.round_trips] == ["signal"]


FALLING = [(100 - i,) * 4 for i in range(20)]        # closes 100 .. 81: RSI(14) is 0 from bar 14 on


def test_rsi_rebound_buys_oversold_and_leaves_after_twenty_days():
    df = frame(FALLING + [(81,) * 4] * 25)
    result = run_preset(df, "rsi_rebound", warmup=14)
    # Bar 14 is the first tradable bar and its RSI is 0 -> buy at bar 15's open (85).
    # The position is bar 15 = day 0; day 20 is bar 35; the sell fills at bar 36's open (81).
    first = [t for t in result.trades][:2]
    assert (str(first[0].timestamp.date()), first[0].price) == (day(15), 85.0)
    assert (str(first[1].timestamp.date()), first[1].price, first[1].reason) == (day(36), 81.0, "max_hold")
    assert result.round_trips[0]["exit_reason"] == "max_hold"


def test_rsi_rebound_waits_for_fourteen_real_price_changes():
    # Closes 100, 99, ..., 82 on the business days from 2024-01-02, no warm-up. Bar 0 has
    # no change; the 14th change is bar 14 (2024-01-22, close 86), the first bar with an
    # RSI(14), which is 0. The buy fills at the next open: bar 15, 2024-01-23, at 85.
    df = frame([(100 - i,) * 4 for i in range(19)])
    result = run_preset(df, "rsi_rebound", {"stop_pct": 0}, warmup=0)
    assert buys(result) == [("2024-01-23", 85.0)]


def test_rsi_rebound_stops_out_at_minus_eight_percent():
    df = frame(FALLING + [(84.0, 84.0, 70.0, 72.0)] + [(72,) * 4] * 5)
    result = run_preset(df, "rsi_rebound", warmup=14)
    assert buys(result)[0] == (day(15), 85.0)
    assert sells(result)[0] == (day(20), 78.2, "stop")           # 85 less 8%; the open (84) is above it
    assert result.round_trips[0]["exit_reason"] == "stop"


def test_rsi_rebound_sells_when_rsi_passes_seventy():
    closes = [100.0 - i for i in range(20)] + [81.0 + 6 * k for k in range(1, 11)]
    df = frame([(c,) * 4 for c in closes])
    rsi = TechnicalFactors.rsi(df["close"], 14)
    over = int(next(i for i in range(21, len(df)) if rsi.iloc[i] > 70))      # first bar after the entry above 70
    assert over < len(df) - 1
    result = run_preset(df, "rsi_rebound", warmup=14)
    assert sells(result) == [(day(over + 1), float(df["open"].iloc[over + 1]), "signal")]


def test_breakout_buys_a_new_high_of_the_previous_bars_and_sells_below_the_recent_low():
    df = frame([
        10, 10, 10, 10,
        (10.4, 11.1, 10.3, 11.0),     # 4: 11 > the highest close of bars 1-3 (10) -> buy signal
        (11.5, 12.2, 11.4, 12.0),     # 5: fills at 11.5
        (12.5, 13.2, 12.4, 13.0),
        (12.9, 13.0, 12.4, 12.5),     # 7: 12.5 is above the lower of the previous 2 closes (12) -> hold
        (12.0, 12.1, 11.4, 11.5),     # 8: 11.5 < min(13, 12.5) -> sell signal
        (11.3, 11.4, 11.0, 11.2),     # 9: fills at 11.3
        11.2, 11.2,
    ])
    result = run_preset(df, "breakout", {"high_days": 3, "low_days": 2}, warmup=3)
    assert buys(result) == [(day(5), 11.5)]
    assert sells(result) == [(day(9), 11.3, "signal")]


def test_breakout_does_not_trigger_on_a_close_equal_to_the_previous_high():
    df = frame([10, 10, 10, 10, 10, 10.0, 10.0, 10.0])
    assert run_preset(df, "breakout", {"high_days": 3, "low_days": 2}, warmup=3).trades == []


ENTER = [10, 10, (10.0, 10.2, 9.9, 10.0)]


def test_stop_target_stop():
    df = frame(ENTER + [(9.5, 9.6, 9.0, 9.1), 9.1])
    result = run_preset(df, "stop_target", {"entry_date": "2024-01-03"})
    assert buys(result) == [(day(2), 10.0)]
    assert sells(result) == [(day(3), 9.2, "stop")]


def test_stop_target_target():
    df = frame(ENTER + [(10.5, 12.5, 10.4, 12.2), 12.2])
    result = run_preset(df, "stop_target", {"entry_date": "2024-01-03"})
    assert sells(result) == [(day(3), 12.0, "target")]
    assert result.round_trips[0]["exit_reason"] == "target"


def test_stop_target_pulls_back_from_the_high():
    df = frame(ENTER + [(10.0, 12.0, 9.9, 11.8), (11.5, 11.6, 10.5, 10.6), 10.6])
    result = run_preset(df, "stop_target", {"entry_date": "2024-01-03", "target_pct": 0})
    assert sells(result) == [(day(4), 10.8, "trailing")]          # 12 less 10%
    assert result.round_trips[0]["exit_reason"] == "trailing"


def test_stop_target_with_everything_off_just_holds():
    df = frame(ENTER + [(9.0, 9.0, 5.0, 6.0), 6.0])
    result = run_preset(df, "stop_target", {"stop_pct": 0, "target_pct": 0, "trailing_pct": 0})
    assert sells(result) == []
    assert result.round_trips[0]["exit_reason"] == "end_mark"


def test_stop_target_enters_on_the_first_bar_on_or_after_the_date():
    df = frame([(100 + i,) * 4 for i in range(12)])
    result = run_preset(df, "stop_target", {"entry_date": "2024-01-06"}, warmup=2)      # a Saturday
    assert buys(result) == [(day(5), 105.0)]


# ------------------------------------------------------------------ #
#  Every preset through the real engine
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("preset_id", IDS)
@pytest.mark.parametrize("seed", [21, 22])
def test_every_preset_runs_and_every_exit_carries_a_reason(preset_id, seed):
    df = random_bars(seed, n=260)
    result = run_preset(df, preset_id, warmup=60)
    assert result.trades                                            # something happened
    reasons = {trip["exit_reason"] for trip in result.round_trips}
    assert reasons and reasons <= set(EXIT_REASON_LABELS)
    assert all(t.reason in EXIT_REASON_LABELS for t in result.trades if t.direction == "sell")
    assert result.strategy_name == presets.spec(preset_id)["name"]
    json.dumps(result.to_dict(), default=str)


def test_each_exit_kind_shows_up_across_the_presets():
    seen = set()
    for seed in range(30, 40):
        df = random_bars(seed, n=260)
        for preset_id, params in [("rsi_rebound", {"stop_pct": 4}), ("stop_target", {"stop_pct": 3, "target_pct": 5, "trailing_pct": 4}),
                                  ("ma_trend", {"period": 10}), ("breakout", {"high_days": 10, "low_days": 5})]:
            seen |= {trip["exit_reason"] for trip in run_preset(df, preset_id, params, warmup=60).round_trips}
    assert {"signal", "stop", "target", "trailing", "max_hold", "end_mark"} <= seen
