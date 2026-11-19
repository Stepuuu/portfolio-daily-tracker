"""
五个现成的回测方法 (预设)

给不写代码的用户: 选一个方法, 最多改一两个数字. 每个预设只是把这些数字填进
`RuleStrategy` 的声明式 spec (见 strategies/rules.py), 所以保存运行记录时存下
`spec()` 的结果就能原样重放.

接口 (API 层按这个写, 不要改形状):
    describe()                  -> [{id, name, summary, params: [{key, label, unit, default, min, max, step}]}]
    build(preset_id, params)    -> (策略类, 策略参数 kwargs), 传给 engine.add_strategy(cls, **kwargs)
    spec(preset_id, params)     -> 填好全部默认值的声明式 spec (dict, 可 JSON 序列化)

params 里没给 (或为 null) 的数字用默认值; 未知的方法、未知的参数名、数值超出范围或类型不对都抛 ValueError
(中文消息). 带 "不用" 含义的百分比参数, 0 表示不启用. 买入日期 (unit == "date") 是
YYYY-MM-DD 字符串 (月、日可以是一位数, 可带时刻, 时刻被忽略; 见 rules.parse_entry_date), 缺省 (null)
表示从第一根可交易 bar 起买入; 它的 min/max/step 是 None. spec() 里是规范化后的 YYYY-MM-DD,
调用方 (如 service.plan) 必须用它, 不要再解析原始输入.

仓位: 单只股票的预设一律用 100% 权益买入 (整手, 受可用现金约束); 本金就是这只股票的
全部资金, 想看"满仓"的结果, 不需要用户再选. 这个数字不对用户开放.
"""
import copy
import math
import numbers
from typing import Any, Callable, Dict, List, Optional, Tuple

from backtesting.strategies.rules import RuleStrategy, normalize_spec, parse_entry_date

POSITION_PCT = 100


def _p(key, label, unit, default, low, high, step, kind="float"):
    return {"key": key, "label": label, "unit": unit, "default": default,
            "min": low, "max": high, "step": step, "kind": kind}


def _close_vs_ma(op: str, period: int) -> Dict[str, Any]:
    return {"rule": "compare", "left": {"indicator": "close"}, "op": op,
            "right": {"indicator": "sma", "period": period}}


def _spec_buy_and_hold(p):
    return {"entry": {"rule": "always"}, "exits": []}


def _spec_ma_trend(p):
    return {"entry": _close_vs_ma(">", p["period"]), "exits": [_close_vs_ma("<", p["period"])]}


def _spec_rsi_rebound(p):
    return {
        "entry": {"rule": "compare", "left": {"indicator": "rsi", "period": 14}, "op": "<", "right": p["buy_below"]},
        "exits": [{"rule": "compare", "left": {"indicator": "rsi", "period": 14}, "op": ">", "right": p["sell_above"]}],
        "max_hold_days": p["max_hold_days"],
        "stop_pct": p["stop_pct"],
    }


def _spec_breakout(p):
    return {
        "entry": {"rule": "compare", "left": {"indicator": "close"}, "op": ">",
                  "right": {"indicator": "highest_close", "period": p["high_days"]}},
        "exits": [{"rule": "compare", "left": {"indicator": "close"}, "op": "<",
                   "right": {"indicator": "lowest_close", "period": p["low_days"]}}],
    }


def _spec_stop_target(p):
    # No date means "from the first tradable bar"; both are one-time entries.
    return {"entry": {"rule": "always"}, "entry_date": p["entry_date"], "exits": [],
            "stop_pct": p["stop_pct"], "target_pct": p["target_pct"], "trailing_pct": p["trailing_pct"]}


def _check_rsi_levels(p):
    if p["buy_below"] >= p["sell_above"]:
        raise ValueError(f"买入的 RSI 线 ({p['buy_below']:g}) 应低于卖出的 RSI 线 ({p['sell_above']:g})")


PRESETS: Dict[str, Dict[str, Any]] = {
    "buy_and_hold": {
        "name": "一直持有（对照）",
        "summary": "第一个可交易日买入，一直拿到最后，用来和其他方法比较。",
        "params": [],
        "make": _spec_buy_and_hold,
    },
    "ma_trend": {
        "name": "均线趋势",
        "summary": "收盘价站上 N 日均线就买入，跌破这条均线就卖出。",
        "params": [_p("period", "均线天数", "日", 20, 2, 250, 1, "int")],
        "make": _spec_ma_trend,
    },
    "rsi_rebound": {
        "name": "超跌反弹",
        "summary": "RSI(14) 低于 30 视为超跌买入；RSI 高于 70、持有满 20 日或亏 8% 就卖出。",
        "params": [
            _p("buy_below", "RSI 低于多少时买入", "点", 30, 5, 50, 1, "int"),
            _p("sell_above", "RSI 高于多少时卖出", "点", 70, 50, 95, 1, "int"),
            _p("max_hold_days", "最长持有", "日", 20, 1, 250, 1, "int"),
            _p("stop_pct", "亏损多少止损（0 表示不用）", "%", 8, 0, 50, 0.5),
        ],
        "check": _check_rsi_levels,
        "make": _spec_rsi_rebound,
    },
    "breakout": {
        "name": "突破",
        "summary": "收盘价创 N 日新高就买入，跌破 M 日最低价（收盘价）就卖出。",
        "params": [
            _p("high_days", "创几日新高买入", "日", 20, 2, 250, 1, "int"),
            _p("low_days", "跌破几日最低卖出", "日", 10, 2, 250, 1, "int"),
        ],
        "make": _spec_breakout,
    },
    "stop_target": {
        "name": "止损止盈（持仓）",
        "summary": "在指定日期买入，亏 8% 止损、赚 20% 止盈、或从高点回落 10% 就离场，三项都可以关掉。",
        "params": [
            _p("entry_date", "买入日期", "date", None, None, None, None, "date"),
            _p("stop_pct", "亏损多少止损（0 表示不用）", "%", 8, 0, 50, 0.5),
            _p("target_pct", "盈利多少止盈（0 表示不用）", "%", 20, 0, 500, 1),
            _p("trailing_pct", "从高点回落多少离场（0 表示不用）", "%", 10, 0, 50, 0.5),
        ],
        "make": _spec_stop_target,
    },
}

_PUBLIC_PARAM_KEYS = ("key", "label", "unit", "default", "min", "max", "step")


def describe() -> List[Dict[str, Any]]:
    """The presets for the method picker: [{id, name, summary, params: [{key, label, unit, default, min, max, step}]}]."""
    return [
        {"id": preset_id, "name": item["name"], "summary": item["summary"],
         "params": [{key: param[key] for key in _PUBLIC_PARAM_KEYS} for param in item["params"]]}
        for preset_id, item in PRESETS.items()
    ]


def _fmt(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def _resolve_value(param: Dict[str, Any], value) -> Any:
    label = param["label"]
    if param["kind"] == "date":
        # The spec keeps the normalised YYYY-MM-DD; callers must use that, not the input.
        try:
            return parse_entry_date(value)
        except ValueError as exc:
            reason = "" if "格式不对" in str(exc) else f"（{exc}）"
            raise ValueError(f"「{label}」应为 YYYY-MM-DD 格式的日期, 收到 {value!r}{reason}") from None
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(float(value)):
        raise ValueError(f"「{label}」应为数字, 收到 {value!r}")
    number = float(value)
    if param["kind"] == "int":
        if number != int(number):
            raise ValueError(f"「{label}」应为整数, 收到 {value!r}")
        number = int(number)
    if not param["min"] <= number <= param["max"]:
        raise ValueError(f"「{label}」应在 {_fmt(param['min'])} 到 {_fmt(param['max'])} 之间, 收到 {_fmt(number)}")
    return number


def _resolve(preset_id: str, params: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(preset_id, str) or preset_id not in PRESETS:
        raise ValueError(f"没有这个方法: {preset_id!r}; 可选: {', '.join(PRESETS)}")
    item = PRESETS[preset_id]
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("方法的参数应是一个字典")
    known = {param["key"]: param for param in item["params"]}
    unknown = [key for key in params if key not in known]
    if unknown:
        allowed = "、".join(known) if known else "无"
        raise ValueError(f"方法「{item['name']}」没有参数 {', '.join(map(str, unknown))}; 可用的参数: {allowed}")
    # A missing key, or None (a form field left empty), means the default.
    values = {key: param["default"] if params.get(key) is None else _resolve_value(param, params[key])
              for key, param in known.items()}
    if "check" in item:
        item["check"](values)
    return item, values


def spec(preset_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The resolved declarative spec of a preset (for the run manifest). JSON-serialisable."""
    item, values = _resolve(preset_id, params)
    raw = {"position_pct": POSITION_PCT, "preset": preset_id, "name": item["name"], "params": values}
    raw.update(item["make"](values))
    return normalize_spec(raw)


def build(preset_id: str, params: Optional[Dict[str, Any]] = None) -> Tuple[type, Dict[str, Any]]:
    """(strategy class, kwargs) for `engine.add_strategy(cls, **kwargs)`."""
    return RuleStrategy, {"spec": spec(preset_id, params)}
