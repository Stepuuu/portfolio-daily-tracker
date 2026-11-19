"""
回测统计分析
灵感: backtrader 的 Analyzers + qlib 的 AnalysePosition

指标计算:
  收益类:   总收益率、年化收益 (按日历天数)、基准收益、Alpha
  风险类:   最大回撤、波动率 (日收益标准差 x sqrt(252))、VaR、下行偏差
  效率类:   夏普比率、索提诺比率 (至少 60 个日收益才计算)、卡玛比率
  交易类:   按"回合"统计 (空仓 -> 持仓 -> 空仓, 分批成交合并): 交易次数、胜率、
            盈亏比 (平均盈利/平均亏损)、利润因子 (总盈利/总亏损)、持仓天数

分母为 0 或样本为空的指标没有定义 (例如净值每天涨 1% 时夏普比率的分母为 0):
记入 ``undefined`` (指标名 -> 原因), ``to_dict()`` 输出 None, 绝不输出 0.
"""
import math
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict

# 无风险利率 (年化3%, 日化); 夏普与索提诺共用
RISK_FREE_DAILY = 0.03 / 252
# 年化收益按日历天数: (期末/期初)^(365.25/天数) - 1
DAYS_PER_YEAR = 365.25
# 夏普/索提诺至少需要这么多个日收益, 否则不报告 (None)
MIN_RETURNS_FOR_RATIOS = 60
# |盈亏| 小于半分钱的回合算持平, 既不算盈利也不算亏损
BREAKEVEN_EPSILON = 0.005

ANNUALIZATION_NOTE = (
    "CAGR = (final value / initial cash)^(365.25 / calendar days) - 1 over the measured dates; "
    "volatility = standard deviation of daily returns x sqrt(252); Sharpe and Sortino use a 3% "
    "risk-free rate and are omitted (null) below 60 daily returns or when their denominator is zero"
)
UNDEFINED_NOTE = (
    "a statistic whose denominator is zero or whose sample is empty (no variation, no downside, no "
    "drawdown, no losing or no completed trades, no benchmark, no daily returns) is null, with the "
    "reason under `undefined`; it is never reported as 0"
)
TRADE_STATS_NOTE = (
    "round trips: flat -> position -> flat per symbol, partial fills merged; win rate, payoff ratio "
    "(average win / average loss) and profit factor (gross profit / gross loss) over completed round "
    "trips only, excluding the end-of-data valuation (end_mark); zero-P&L round trips count as "
    "neither win nor loss"
)


def _day(value) -> Optional[str]:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(value)[:10]


def _is_end_mark(trade) -> bool:
    return getattr(trade, "reason", "") == "end_mark" or getattr(trade, "order_id", "") == "FORCE_CLOSE"


def round_trips(trades) -> List[Dict[str, Any]]:
    """Group fills into round trips: per symbol, from flat to a position and back
    to flat. A position still open after the last fill is not a round trip."""
    trips: List[Dict[str, Any]] = []
    open_trips: Dict[str, Dict[str, Any]] = {}
    held: Dict[str, float] = defaultdict(float)
    for trade in trades:
        symbol = trade.symbol
        qty = float(trade.quantity)
        if trade.direction == "buy":
            trip = open_trips.get(symbol)
            if trip is None:
                trip = open_trips[symbol] = {
                    "symbol": symbol, "entry_date": _day(trade.timestamp), "fills": 0,
                    "bought": 0.0, "buy_value": 0.0, "buy_cost": 0.0,
                    "sold": 0.0, "sell_value": 0.0, "pnl": 0.0, "max_quantity": 0.0, "end_mark": False,
                }
            trip["fills"] += 1
            trip["bought"] += qty
            trip["buy_value"] += qty * float(trade.price)
            trip["buy_cost"] += qty * float(trade.price) + float(trade.commission)
            held[symbol] += qty
            trip["max_quantity"] = max(trip["max_quantity"], held[symbol])
        elif trade.direction == "sell":
            trip = open_trips.get(symbol)
            if trip is None:
                continue                      # a sell without a recorded entry
            trip["fills"] += 1
            trip["sold"] += qty
            trip["sell_value"] += qty * float(trade.price)
            trip["pnl"] += float(trade.pnl)
            trip["end_mark"] = trip["end_mark"] or _is_end_mark(trade)
            trip["exit_reason"] = "end_mark" if _is_end_mark(trade) else getattr(trade, "reason", "signal") or "signal"
            trip["exit_date"] = _day(trade.timestamp)
            held[symbol] -= qty
            if held[symbol] <= 1e-9:
                held[symbol] = 0.0
                del open_trips[symbol]
                trips.append(_finish_trip(trip))
    return trips


def _finish_trip(trip: Dict[str, Any]) -> Dict[str, Any]:
    entry, exit_ = pd.Timestamp(trip["entry_date"]), pd.Timestamp(trip["exit_date"])
    quantity = trip["max_quantity"]
    return {
        "symbol": trip["symbol"],
        "entry_date": trip["entry_date"],
        "exit_date": trip["exit_date"],
        "quantity": int(quantity) if float(quantity).is_integer() else quantity,
        "entry_price": round(trip["buy_value"] / trip["bought"], 6) if trip["bought"] else None,
        "exit_price": round(trip["sell_value"] / trip["sold"], 6) if trip["sold"] else None,
        "pnl": round(trip["pnl"], 2),
        "return_pct": round(trip["pnl"] / trip["buy_cost"], 6) if trip["buy_cost"] else None,
        "holding_days": int((exit_ - entry).days),
        "fills": trip["fills"],
        "exit_reason": "end_mark" if trip["end_mark"] else trip.get("exit_reason", "signal"),
        "end_mark": trip["end_mark"],
    }


@dataclass
class BacktestStats:
    """回测统计结果"""

    # 输入数据
    equity_curve: List[Tuple]       # [(date, total, cash, mkt_value)]
    trades: list                    # Trade 列表
    initial_cash: float
    benchmark_prices: Optional[List[float]] = None  # 基准标的收盘价序列

    # ──────────────── 延迟计算字段 ────────────────
    # 收益
    final_value: float = field(init=False)
    total_return: float = field(init=False)
    annualized_return: float = field(init=False)
    benchmark_return: float = field(init=False)
    alpha: float = field(init=False)

    # 风险
    max_drawdown: float = field(init=False)
    max_drawdown_duration: int = field(init=False)
    volatility: float = field(init=False)
    var_95: float = field(init=False)
    downside_deviation: float = field(init=False)

    # 效率 (None: 日收益少于 MIN_RETURNS_FOR_RATIOS 个, 或分母为 0)
    sharpe_ratio: Optional[float] = field(init=False)
    sortino_ratio: Optional[float] = field(init=False)
    calmar_ratio: float = field(init=False)

    # 交易统计 (按回合)
    round_trips: List[Dict[str, Any]] = field(init=False)
    total_trades: int = field(init=False)          # 回合数, 含期末估值的一笔
    closed_trades: int = field(init=False)         # 已完成的回合 (不含期末估值)
    winning_trades: int = field(init=False)
    losing_trades: int = field(init=False)
    breakeven_trades: int = field(init=False)
    win_rate: float = field(init=False)            # 盈利回合 / (盈利 + 亏损回合); 无则 0
    payoff_ratio: Optional[float] = field(init=False)   # 平均盈利 / 平均亏损
    profit_factor: float = field(init=False)       # 总盈利 / 总亏损 (无亏损: inf; 无回合: nan)
    end_mark_pnl: float = field(init=False)        # 期末按收盘价估值的回合盈亏
    avg_profit: float = field(init=False)
    avg_loss: float = field(init=False)
    max_single_profit: float = field(init=False)
    max_single_loss: float = field(init=False)
    avg_holding_days: float = field(init=False)

    # Statistics without a value: name -> why (Chinese, for the user). to_dict()
    # reports these as None. The attributes of such statistics keep a numeric
    # placeholder (0.0; inf/nan for profit_factor) only because existing callers
    # format them directly; sharpe_ratio, sortino_ratio and payoff_ratio are None.
    undefined: Dict[str, str] = field(init=False)

    def __post_init__(self):
        self._compute()

    # ------------------------------------------------------------------ #
    #  计算入口
    # ------------------------------------------------------------------ #

    def _compute(self):
        """计算所有统计指标"""
        self.undefined = {}
        equity_df = self._to_equity_df()
        self._compute_returns(equity_df)
        self._compute_risk(equity_df)
        self._compute_ratios(equity_df)
        self._compute_trade_stats()

    def _to_equity_df(self) -> pd.DataFrame:
        if not self.equity_curve:
            return pd.DataFrame(
                columns=["date", "total", "cash", "mkt"],
            )
        df = pd.DataFrame(
            self.equity_curve,
            columns=["date", "total", "cash", "mkt"],
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df

    # ------------------------------------------------------------------ #
    #  收益指标
    # ------------------------------------------------------------------ #

    def _compute_returns(self, df: pd.DataFrame):
        if df.empty:
            self.final_value = self.initial_cash
            self.total_return = 0.0
            self.annualized_return = 0.0
            self.benchmark_return = 0.0
            self.alpha = 0.0
            for name in ("annualized_return", "benchmark_return", "alpha"):
                self.undefined[name] = "没有净值记录"
            return

        self.final_value = float(df["total"].iloc[-1])
        self.total_return = (self.final_value / self.initial_cash) - 1

        # 年化: 按首尾日期之间的日历天数 (A股每年约 242 个交易日, 不是 252)
        days = (df.index[-1] - df.index[0]).days
        years = days / DAYS_PER_YEAR
        if years > 0 and self.total_return > -1:
            try:
                self.annualized_return = float((1 + self.total_return) ** (1 / years) - 1)
            except OverflowError:
                self.annualized_return = float("inf")
            if not math.isfinite(self.annualized_return):
                # A large gain over a few days compounds beyond any float: no meaningful rate.
                self.annualized_return = 0.0
                self.undefined["annualized_return"] = (
                    f"{days} 个日历日的收益 {self.total_return:+.2%} 折算成年化后超出可表示的范围, 年化收益没有意义")
        elif years > 0:
            self.annualized_return = -1.0                # wiped out
        else:
            self.annualized_return = 0.0
            self.undefined["annualized_return"] = "测算期不足一个日历日, 无法年化"

        # 基准收益: buy-and-hold (如果提供了基准价格序列)
        if self.benchmark_prices and len(self.benchmark_prices) >= 2:
            self.benchmark_return = (self.benchmark_prices[-1] / self.benchmark_prices[0]) - 1
        else:
            # Without a benchmark the excess return is unknown, not the total return.
            self.benchmark_return = 0.0
            self.undefined["benchmark_return"] = "没有基准价格序列 (少于 2 个价格), 不计算基准收益"
            self.undefined["alpha"] = "没有基准价格序列, 超额收益没有定义"
        self.alpha = self.total_return - self.benchmark_return

    # ------------------------------------------------------------------ #
    #  风险指标
    # ------------------------------------------------------------------ #

    def _compute_risk(self, df: pd.DataFrame):
        if df.empty or len(df) < 2:
            self.max_drawdown = 0.0
            self.max_drawdown_duration = 0
            self.volatility = 0.0
            self.var_95 = 0.0
            self.downside_deviation = 0.0
            for name in ("volatility", "var_95", "downside_deviation"):
                self.undefined[name] = "少于 2 个净值点, 没有日收益"
            if df.empty:
                self.undefined["max_drawdown"] = "没有净值记录"
            return

        net = df["total"] / self.initial_cash  # 净值序列

        # 日收益率
        daily_ret = net.pct_change().dropna()

        # 最大回撤
        rolling_max = net.cummax()
        drawdown = (net - rolling_max) / rolling_max
        self.max_drawdown = float(drawdown.min())

        # 最大回撤持续时间 (天数)
        in_dd = drawdown < 0
        dd_duration = 0
        max_dd_dur = 0
        for v in in_dd:
            if v:
                dd_duration += 1
                max_dd_dur = max(max_dd_dur, dd_duration)
            else:
                dd_duration = 0
        self.max_drawdown_duration = max_dd_dur

        # 年化波动率
        self.volatility = float(daily_ret.std(ddof=0) * math.sqrt(252))

        # VaR 95% (历史模拟法)
        self.var_95 = float(np.percentile(daily_ret, 5))

        # 下行偏差: sqrt(mean(min(r - 目标, 0)^2)), 对全部交易日求均值 (目标 = 日化无风险利率),
        # 再按 sqrt(252) 年化. 只对负收益求样本标准差会在亏损幅度相同时得到 0.
        shortfall = np.minimum(daily_ret - RISK_FREE_DAILY, 0.0)
        self.downside_deviation = float(math.sqrt((shortfall ** 2).mean()) * math.sqrt(252))

    # ------------------------------------------------------------------ #
    #  效率指标
    # ------------------------------------------------------------------ #

    def _compute_ratios(self, df: pd.DataFrame):
        rf = RISK_FREE_DAILY

        if df.empty or len(df) < 2:
            self.sharpe_ratio = None
            self.sortino_ratio = None
            self.calmar_ratio = 0.0
            for name in ("sharpe_ratio", "sortino_ratio", "calmar_ratio"):
                self.undefined[name] = "少于 2 个净值点, 没有日收益"
            return

        net = df["total"] / self.initial_cash
        daily_ret = net.pct_change().dropna()
        ann_ret = self.annualized_return
        enough = len(daily_ret) >= MIN_RETURNS_FOR_RATIOS

        too_few = f"日收益只有 {len(daily_ret)} 个, 少于 {MIN_RETURNS_FOR_RATIOS} 个, 不计算"

        # 夏普比率
        excess = daily_ret - rf
        excess_std = float(excess.std())
        self.sharpe_ratio = None
        if not enough:
            self.undefined["sharpe_ratio"] = too_few
        elif excess_std > 1e-12:
            self.sharpe_ratio = float(excess.mean() / excess_std * math.sqrt(252))
        else:
            self.undefined["sharpe_ratio"] = "日超额收益的标准差为 0 (每天收益相同), 夏普比率没有定义, 不是 0"

        # 索提诺比率: 与夏普相同的日超额收益和年化方式, 分母换成下行偏差
        self.sortino_ratio = None
        if not enough:
            self.undefined["sortino_ratio"] = too_few
        elif self.downside_deviation > 1e-12:
            self.sortino_ratio = float(excess.mean() * 252 / self.downside_deviation)
        else:
            self.undefined["sortino_ratio"] = "没有一天的收益低于无风险收益 (下行偏差为 0), 索提诺比率没有定义, 不是 0"

        # 卡玛比率
        self.calmar_ratio = 0.0
        if "annualized_return" in self.undefined:
            self.undefined["calmar_ratio"] = self.undefined["annualized_return"]
        elif self.max_drawdown < 0:
            self.calmar_ratio = ann_ret / abs(self.max_drawdown)
        else:
            self.undefined["calmar_ratio"] = "期间没有回撤 (最大回撤为 0), 卡玛比率没有定义, 不是 0"

    # ------------------------------------------------------------------ #
    #  交易统计
    # ------------------------------------------------------------------ #

    def _compute_trade_stats(self):
        self.round_trips = round_trips(self.trades)
        closed = [trip for trip in self.round_trips if not trip["end_mark"]]
        self.total_trades = len(self.round_trips)
        self.closed_trades = len(closed)
        self.end_mark_pnl = float(sum(trip["pnl"] for trip in self.round_trips if trip["end_mark"]))

        pnls = [float(trip["pnl"]) for trip in closed]
        wins = [p for p in pnls if p >= BREAKEVEN_EPSILON]
        losses = [p for p in pnls if p <= -BREAKEVEN_EPSILON]
        self.winning_trades = len(wins)
        self.losing_trades = len(losses)
        self.breakeven_trades = len(pnls) - len(wins) - len(losses)

        decided = len(wins) + len(losses)
        no_trades = "没有盈利或亏损的完整交易"
        self.win_rate = len(wins) / decided if decided else 0.0
        if not decided:
            self.undefined["win_rate"] = f"{no_trades}, 胜率没有定义"
        total_profit = sum(wins)
        total_loss = abs(sum(losses))
        if total_loss > 0:
            self.profit_factor = total_profit / total_loss
        else:
            self.profit_factor = float("inf") if total_profit > 0 else float("nan")
            self.undefined["profit_factor"] = (
                "没有亏损的完整交易 (总亏损为 0), 利润因子没有定义" if total_profit > 0 else f"{no_trades}, 利润因子没有定义")

        self.avg_profit = float(np.mean(wins)) if wins else 0.0
        self.avg_loss = float(np.mean(losses)) if losses else 0.0
        if not wins:
            self.undefined["avg_profit"] = "没有盈利的完整交易"
        if not losses:
            self.undefined["avg_loss"] = "没有亏损的完整交易"
        self.payoff_ratio = self.avg_profit / abs(self.avg_loss) if wins and losses else None
        if self.payoff_ratio is None:
            self.undefined["payoff_ratio"] = "需要至少一笔盈利和一笔亏损的完整交易, 盈亏比没有定义"
        self.max_single_profit = float(max(pnls)) if pnls and max(pnls) > 0 else 0.0
        self.max_single_loss = float(min(pnls)) if pnls and min(pnls) < 0 else 0.0

        if not self.trades:
            self.avg_holding_days = 0.0
            self.undefined["avg_holding_days"] = "没有成交, 平均持仓天数没有定义"
            return

        # 平均持仓天数 (按 symbol 做 FIFO 买卖配对)
        holding_days_total = 0.0
        matched_quantity = 0.0
        buy_queues = defaultdict(list)
        for trade in self.trades:
            ts = pd.to_datetime(trade.timestamp)
            if trade.direction == "buy":
                buy_queues[trade.symbol].append([float(trade.quantity), ts])
                continue
            if trade.direction != "sell":
                continue

            remaining = float(trade.quantity)
            queue = buy_queues[trade.symbol]
            while remaining > 0 and queue:
                buy_qty, buy_ts = queue[0]
                matched = min(remaining, buy_qty)
                days = max((ts - buy_ts).days, 0)
                holding_days_total += days * matched
                matched_quantity += matched
                buy_qty -= matched
                remaining -= matched
                if buy_qty <= 1e-8:
                    queue.pop(0)
                else:
                    queue[0][0] = buy_qty

        self.avg_holding_days = holding_days_total / matched_quantity if matched_quantity else 0.0
        if not matched_quantity:
            self.undefined["avg_holding_days"] = "没有与买入配对的卖出, 平均持仓天数没有定义"

    # ------------------------------------------------------------------ #
    #  报告格式化
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        def ratio(value, digits=4):
            return round(value, digits) if value is not None and math.isfinite(value) else None

        out = {
            "final_value": round(self.final_value, 2),
            "total_return": round(self.total_return, 6),
            "annualized_return": round(self.annualized_return, 6),
            "benchmark_return": round(self.benchmark_return, 6),
            "alpha": round(self.alpha, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "max_drawdown_duration": self.max_drawdown_duration,
            "volatility": round(self.volatility, 6),
            "var_95": round(self.var_95, 6),
            "downside_deviation": round(self.downside_deviation, 6),
            "sharpe_ratio": ratio(self.sharpe_ratio),
            "sortino_ratio": ratio(self.sortino_ratio),
            "calmar_ratio": round(self.calmar_ratio, 4),
            "total_trades": self.total_trades,
            "closed_trades": self.closed_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "breakeven_trades": self.breakeven_trades,
            "win_rate": round(self.win_rate, 4),
            "payoff_ratio": ratio(self.payoff_ratio),
            "profit_factor": ratio(self.profit_factor),
            "end_mark_pnl": round(self.end_mark_pnl, 2),
            "avg_profit": round(self.avg_profit, 2),
            "avg_loss": round(self.avg_loss, 2),
            "max_single_profit": round(self.max_single_profit, 2),
            "max_single_loss": round(self.max_single_loss, 2),
            "avg_holding_days": round(self.avg_holding_days, 2),
        }
        for name in self.undefined:
            if name in out:
                out[name] = None
        out["undefined"] = dict(self.undefined)
        return out
