"""
声明式规则策略 (RuleStrategy)

一个小的、可 JSON 序列化的 spec 决定全部行为, 所以一次回测的策略可以原样存进
运行记录里重放. 不写 Python 也能用: 预设 (backtesting/presets.py) 只是替用户把
几个数字填进 spec.

spec 格式 (所有百分数都是"百分之几", 8 表示 8%):

    {
      "version": 1,
      "entry": {"rule": "always"}                       # 第一根可交易 bar 买入, 只买一次
             | {"rule": "compare", "left": X, "op": ">", "right": X 或 数字},
      "exits": [ {"rule": "compare", ...}, ... ],        # 任一条成立即卖出 (可为空)
      "position_pct": 100,        # 用权益的百分之几买入, 受手数和可用现金约束
      "stop_pct": 8,              # 从买入价下跌 8% 止损 (盘中触发, 可选)
      "target_pct": 20,           # 从买入价上涨 20% 止盈 (盘中触发, 可选)
      "trailing_pct": 10,         # 从买入后最高价回落 10% 离场 (盘中触发, 可选)
      "max_hold_days": 20,        # 买入成交日算第 0 天, 第 N 天收盘后决定卖出 (可选)
      "entry_date": "2024-03-01"  # 可选: 在该日期当天或之后第一根可交易 bar 买入, 之后只管理离场
    }

    X = {"indicator": "close"}
      | {"indicator": "sma" | "ema" | "rsi" | "highest_close" | "lowest_close", "period": N}

highest_close / lowest_close 是"之前 N 根"收盘价的最高/最低, 不含当前这根 bar,
所以 `收盘 > highest_close(20)` 就是收盘创 20 日新高.

时点约定 (与引擎一致):
  * 信号在某根 bar 收盘后判断, 委托在下一根 bar 开盘成交; 买单只活 1 根 bar,
    没成交 (一字涨停, 停牌...) 就作废, 之后每根 bar 重新判断规则, 不追单.
  * 止损/止盈/回落用券商的盘中止损单和止盈单 (一个成交后另一个自动撤销),
    回落线只用已收盘 bar 的最高价上移, 不会下移.
  * 规则离场和到期离场发一张下一根 bar 开盘的卖单; 已有卖单在途时不重复发.
  * 买入当天收盘后才挂止损, 所以买入当天盘中的触及不会成交 (A 股 T+1 本来也卖不出;
    港美股同日触及的情形没有模拟).
  * 指标列在第一根可交易 bar 前用 TechnicalFactors 一次算好; 全部是滚动/递推指标,
    第 t 根的值只依赖 t 及更早的 bar.

每笔离场的原因记在成交的 `reason` 上, 并随 round_trips 的 `exit_reason` 输出:
signal 信号 / stop 止损 / target 止盈 / trailing 回落 / max_hold 到期 / end_mark 期末估值.
"""
import copy
import math
import numbers
import re
from datetime import date
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backtesting.factors.technical import TechnicalFactors
from backtesting.strategies.base import Strategy, Trade

SPEC_VERSION = 1

EXIT_REASON_LABELS = {
    "signal": "信号",
    "stop": "止损",
    "target": "止盈",
    "trailing": "回落",
    "max_hold": "到期",
    "end_mark": "期末估值",
}

# indicator name -> (needs a period, label)
INDICATORS = {
    "close": (False, "收盘价"),
    "sma": (True, "简单均线"),
    "ema": (True, "指数均线"),
    "rsi": (True, "RSI"),
    "highest_close": (True, "之前 N 根的最高收盘价"),
    "lowest_close": (True, "之前 N 根的最低收盘价"),
}
OPERATORS = (">", "<", ">=", "<=")
RULE_NAMES = ("always", "compare")
MAX_PERIOD = 1000

_TOP_KEYS = {"version", "entry", "exits", "position_pct", "stop_pct", "target_pct", "trailing_pct",
             "max_hold_days", "entry_date",
             "preset", "name", "params"}          # the last three are informational (the run manifest)


# ---------------------------------------------------------------------- #
#  spec validation
# ---------------------------------------------------------------------- #

def _is_number(value) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(float(value))


def _num(value, what: str) -> float:
    if not _is_number(value):
        raise ValueError(f"{what}应为数字, 收到 {value!r}")
    return float(value)


def _int(value, what: str, low: int, high: int) -> int:
    number = _num(value, what)
    if number != int(number):
        raise ValueError(f"{what}应为整数, 收到 {value!r}")
    if not low <= number <= high:
        raise ValueError(f"{what}应在 {low} 到 {high} 之间, 收到 {value!r}")
    return int(number)


def _operand(value, what: str):
    if _is_number(value):
        return float(value)
    if not isinstance(value, dict):
        raise ValueError(f"{what}应是指标 (如 {{\"indicator\": \"sma\", \"period\": 20}}) 或数字, 收到 {value!r}")
    extra = set(value) - {"indicator", "period"}
    if extra:
        raise ValueError(f"{what}有未知的字段: {', '.join(sorted(map(str, extra)))}")
    name = value.get("indicator")
    if name not in INDICATORS:
        raise ValueError(f"未知的指标 {name!r}; 可用的指标: {', '.join(INDICATORS)}")
    needs_period, _ = INDICATORS[name]
    if needs_period:
        return {"indicator": name, "period": _int(value.get("period"), f"指标 {name} 的周期", 1, MAX_PERIOD)}
    if "period" in value:
        raise ValueError(f"指标 {name} 没有周期参数")
    return {"indicator": name}


def _rule(value, what: str, allow_always: bool) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{what}应是一个规则, 收到 {value!r}")
    name = value.get("rule")
    if name not in RULE_NAMES:
        raise ValueError(f"未知的规则 {name!r}; 可用的规则: {', '.join(RULE_NAMES)}")
    if name == "always":
        if not allow_always:
            raise ValueError("规则 always (第一根可交易 bar 就买) 只能用作入场规则")
        if set(value) - {"rule"}:
            raise ValueError("规则 always 没有其他参数")
        return {"rule": "always"}
    extra = set(value) - {"rule", "left", "op", "right"}
    if extra:
        raise ValueError(f"{what}有未知的字段: {', '.join(sorted(map(str, extra)))}")
    if value.get("op") not in OPERATORS:
        raise ValueError(f"未知的比较符号 {value.get('op')!r}; 可用: {' '.join(OPERATORS)}")
    left = _operand(value.get("left"), f"{what}的 left")
    right = _operand(value.get("right"), f"{what}的 right")
    if not isinstance(left, dict) and not isinstance(right, dict):
        raise ValueError(f"{what}至少要有一侧是指标")
    return {"rule": "compare", "left": left, "op": value["op"], "right": right}


def _optional_pct(spec: Dict[str, Any], key: str, label: str, high: float) -> Optional[float]:
    value = spec.get(key)
    if value is None:
        return None
    number = _num(value, label)
    if number == 0:
        return None                     # 0 means "off", the same as leaving it out
    if not 0 < number < high:
        raise ValueError(f"{label}应大于 0 且小于 {high:g}, 收到 {value!r}")
    return number


_DATE_TEXT = re.compile(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?)?\s*")
ENTRY_DATE_RANGE = (date(1900, 1, 1), date(2200, 12, 31))


def parse_entry_date(value) -> Optional[str]:
    """A buy date as YYYY-MM-DD, or None for no date (None or "").

    Accepted: a string YYYY-MM-DD, where month and day may have one digit (2024-1-2),
    optionally followed by a time of day (T or a space, then HH:MM[:SS[.fraction]]),
    which is dropped: a daily backtest buys on the first bar on or after that day.
    Refused with a ValueError: other orders (1/2/2024 is ambiguous), words (today,
    which changes with the day it is read), numbers, impossible dates, and dates
    outside 1900-01-01 .. 2200-12-31.
    """
    if value is None or value == "":
        return None
    match = _DATE_TEXT.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"买入日期格式不对, 应为 YYYY-MM-DD, 收到 {value!r}")
    year, month, day, hour, minute, second = match.groups()
    try:
        parsed = date(int(year), int(month), int(day))
        if hour is not None and not (int(hour) < 24 and int(minute) < 60 and int(second or 0) < 60):
            raise ValueError
    except ValueError:
        raise ValueError(f"买入日期不存在, 应为 YYYY-MM-DD, 收到 {value!r}") from None
    low, high = ENTRY_DATE_RANGE
    if not low <= parsed <= high:
        raise ValueError(f"买入日期应在 {low} 到 {high} 之间, 收到 {value!r}")
    return parsed.isoformat()


def _entry_date(value) -> Optional[str]:
    return parse_entry_date(value)


def normalize_spec(spec: Any) -> Dict[str, Any]:
    """Validate a spec and return it fully resolved (every key present, plain JSON types).

    Raises ValueError with a Chinese message for an unknown rule, indicator or
    field, or a value out of range."""
    if not isinstance(spec, dict):
        raise ValueError("策略规则 spec 应是一个字典")
    unknown = set(spec) - _TOP_KEYS
    if unknown:
        raise ValueError(f"spec 有未知的字段: {', '.join(sorted(map(str, unknown)))}")
    version = spec.get("version", SPEC_VERSION)
    if version != SPEC_VERSION:
        raise ValueError(f"不支持的 spec 版本 {version!r} (当前 {SPEC_VERSION})")

    entry_date = _entry_date(spec.get("entry_date"))
    entry = spec.get("entry")
    if entry_date is not None:
        if entry not in (None, {"rule": "always"}):
            raise ValueError("设了买入日期就不能再设入场规则 (到那天直接买入)")
        entry = {"rule": "always"}
    elif entry is None:
        raise ValueError("缺少入场规则 entry (或买入日期 entry_date)")
    else:
        entry = _rule(entry, "入场规则", allow_always=True)

    exits_in = spec.get("exits") or []
    if not isinstance(exits_in, (list, tuple)):
        raise ValueError("exits 应是规则列表")
    exits = [_rule(item, f"离场规则 {n + 1}", allow_always=False) for n, item in enumerate(exits_in)]

    size = spec.get("position_pct", 100)
    size = _num(size, "仓位比例 position_pct")
    if not 0 < size <= 100:
        raise ValueError(f"仓位比例 position_pct 应在 0 到 100 之间 (不含 0), 收到 {size:g}")

    hold = spec.get("max_hold_days")
    hold = None if hold is None or hold == 0 else _int(hold, "最长持有天数 max_hold_days", 1, 5000)

    out: Dict[str, Any] = {
        "version": SPEC_VERSION,
        "entry": entry,
        "exits": exits,
        "position_pct": size,
        "stop_pct": _optional_pct(spec, "stop_pct", "止损 stop_pct", 100),
        "target_pct": _optional_pct(spec, "target_pct", "止盈 target_pct", 10_000),
        "trailing_pct": _optional_pct(spec, "trailing_pct", "回落 trailing_pct", 100),
        "max_hold_days": hold,
        "entry_date": entry_date,
    }
    for key in ("preset", "name", "params"):
        if key in spec:
            out[key] = copy.deepcopy(spec[key])
    return out


# ---------------------------------------------------------------------- #
#  indicators and rules -> boolean arrays (computed once)
# ---------------------------------------------------------------------- #

def indicator_series(df: pd.DataFrame, operand: Dict[str, Any]) -> pd.Series:
    """One indicator over the whole frame. Every one is rolling / recursive: the value on
    bar t uses bars <= t only (highest_close / lowest_close use bars < t)."""
    name = operand["indicator"]
    close = df["close"].astype(float)
    period = operand.get("period")
    if name == "close":
        return close
    if name == "sma":
        return TechnicalFactors.sma(close, period)
    if name == "ema":
        return TechnicalFactors.ema(close, period)
    if name == "rsi":
        return TechnicalFactors.rsi(close, period)
    if name == "highest_close":
        return close.shift(1).rolling(period).max()
    if name == "lowest_close":
        return close.shift(1).rolling(period).min()
    raise ValueError(f"未知的指标 {name!r}")          # unreachable after normalize_spec


def rule_signal(df: pd.DataFrame, rule: Dict[str, Any]) -> np.ndarray:
    """Boolean array: the rule is true at the close of each bar. NaN (not enough history) is False."""
    if rule["rule"] == "always":
        return np.ones(len(df), dtype=bool)

    def side(operand):
        if isinstance(operand, dict):
            return indicator_series(df, operand).to_numpy(dtype=float)
        return np.full(len(df), float(operand))

    left, right = side(rule["left"]), side(rule["right"])
    with np.errstate(invalid="ignore"):
        result = {">": left > right, "<": left < right, ">=": left >= right, "<=": left <= right}[rule["op"]]
    return np.asarray(result & ~np.isnan(left) & ~np.isnan(right), dtype=bool)


# ---------------------------------------------------------------------- #
#  the strategy
# ---------------------------------------------------------------------- #

def uses_indicators(spec: Dict[str, Any]) -> bool:
    """True when a rule of the (normalised) spec reads an indicator with a period, i.e.
    needs bars before the first tradable one; entry "always", a buy date, fixed price
    levels and stop / target / trailing / max-hold exits need none."""
    rules = [spec.get("entry") or {}] + list(spec.get("exits") or [])
    return any(isinstance(side, dict) and side.get("period") is not None
               for rule in rules for side in (rule.get("left"), rule.get("right")))


def single_entry(spec: Dict[str, Any]) -> bool:
    """True when the spec buys at most once (entry "always" or a buy date): at most one round trip."""
    return (spec.get("entry") or {}).get("rule") == "always"


def holds_to_end(spec: Dict[str, Any]) -> bool:
    """True for plain buy and hold: buy on the first tradable bar and never sell
    (no exits, stop, target, trailing line, holding limit or buy date) - nothing to tune."""
    return single_entry(spec) and not spec.get("exits") and not spec.get("entry_date") and not any(
        spec.get(key) for key in ("stop_pct", "target_pct", "trailing_pct", "max_hold_days"))


class RuleStrategy(Strategy):
    """Runs a declarative spec (see the module docstring). Pass it as `spec=...`."""

    name = "规则策略"
    parameters: Dict[str, Any] = {"spec": None}

    @staticmethod
    def needs_warmup(kwargs: Dict[str, Any]) -> bool:
        """Whether a run with these kwargs needs warm-up bars (service.auto_warmup)."""
        spec = kwargs.get("spec")
        return not isinstance(spec, dict) or uses_indicators(normalize_spec(spec))

    def on_init(self):
        spec = self.parameters.get("spec")
        if spec is None:
            raise ValueError("RuleStrategy 需要参数 spec")
        self.spec = normalize_spec(spec)
        self.parameters["spec"] = self.spec          # the run records the resolved spec
        self.single_entry = single_entry(self.spec)  # at most one round trip (caveats.build_caveats)
        if self.spec.get("name"):
            self.name = str(self.spec["name"])
        self._ready = False
        self._entered_once = False
        self._clear_position_state()

    # -- state ----------------------------------------------------------

    def _clear_position_state(self):
        self._entry_index: Optional[int] = None
        self._entry_cost = 0.0          # sum of price * qty of the buy fills
        self._entry_qty = 0.0
        self._high_water: Optional[float] = None
        self._stop_level: Optional[float] = None
        self._stop_kind = "stop"
        self._target_set = False
        self._exit_reason = "signal"    # why the sell now working was sent

    @property
    def _entry_price(self) -> Optional[float]:
        return self._entry_cost / self._entry_qty if self._entry_qty else None

    def _prepare(self):
        """Indicator columns and rule signals, once, from the whole frame. All of them are
        causal, so the decision on bar t never depends on a later bar."""
        df = self.data._df
        spec = self.spec
        self._entry_signal = rule_signal(df, spec["entry"])
        if spec["entry_date"]:
            start = pd.Timestamp(spec["entry_date"]).normalize()
            self._entry_signal = self._entry_signal & (df.index.normalize() >= start)
        exit_signal = np.zeros(len(df), dtype=bool)
        for rule in spec["exits"]:
            exit_signal |= rule_signal(df, rule)
        self._exit_signal = exit_signal
        self._once = spec["entry"]["rule"] == "always"      # "always" / entry_date: one entry only
        self._ready = True

    # -- callbacks -------------------------------------------------------

    def on_trade(self, trade: Trade):
        if trade.direction == "buy":
            if self._entry_index is None:
                self._entry_index = self.data.current_index
            self._entry_cost += trade.price * trade.quantity
            self._entry_qty += trade.quantity
            self._entered_once = True
            return
        if trade.reason == "stop":
            trade.reason = self._stop_kind          # stop / trailing
        elif trade.reason == "signal" and self._exit_reason == "max_hold":
            trade.reason = "max_hold"
        position = self.get_position(trade.symbol)
        if position is None or position.quantity <= 0:
            self._clear_position_state()

    # -- the bar loop ----------------------------------------------------

    def on_bar(self):
        if not self._ready:
            self._prepare()
        position = self.get_position()
        if position and position.quantity > 0:
            if self._entry_index is not None:
                self._manage(position)
        else:
            self._enter()

    def _enter(self):
        if self.has_pending_order(direction="buy"):
            return
        if self._once and self._entered_once:
            return
        if not self._entry_signal[self.data.current_index]:
            return
        cash = self.cash
        if cash <= 0:
            return
        fraction = min(1.0, self.spec["position_pct"] / 100.0 * self.portfolio_value / cash)
        self.buy(pct_cash=fraction)

    def _working_sell(self) -> bool:
        return any(order.order_type in ("market", "limit")
                   for order in self.get_pending_orders(direction="sell"))

    def _manage(self, position):
        spec = self.spec
        index = self.data.current_index
        bar = self.data.current_bar
        if self._working_sell():
            return                                       # never resubmit while a sell is pending
        held = index - self._entry_index
        if self._exit_signal[index]:
            self._exit_reason = "signal"
            self.close_position()
            return
        if spec["max_hold_days"] and held >= spec["max_hold_days"]:
            self._exit_reason = "max_hold"
            self.close_position()
            return

        entry = self._entry_price
        self._high_water = bar.high if self._high_water is None else max(self._high_water, bar.high)
        candidates = []
        if spec["stop_pct"]:
            candidates.append((entry * (1 - spec["stop_pct"] / 100.0), "stop"))
        if spec["trailing_pct"]:
            candidates.append((self._high_water * (1 - spec["trailing_pct"] / 100.0), "trailing"))
        if candidates:
            level, kind = max(candidates, key=lambda item: item[0])
            level = round(level, 4)
            working = self.get_pending_orders(direction="sell", order_type="stop")
            hit_and_waiting = any(order.status == "queued" for order in working)
            if not hit_and_waiting:
                if self._stop_level is None or level > self._stop_level:
                    self._stop_level, self._stop_kind = level, kind     # the line only moves up
                    self.set_stop(level)
                elif not working:
                    self.set_stop(self._stop_level)                     # it was cancelled: put it back
        if spec["target_pct"] and not self._target_set:
            self.set_target(round(entry * (1 + spec["target_pct"] / 100.0), 4))
            self._target_set = True
