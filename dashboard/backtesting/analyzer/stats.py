"""
回测统计分析
灵感: backtrader 的 Analyzers + qlib 的 AnalysePosition

指标计算:
  收益类:   总收益率、年化收益、基准收益、Alpha
  风险类:   最大回撤、波动率、VaR、下行偏差
  效率类:   夏普比率、索提诺比率、卡玛比率、信息比率
  交易类:   成交次数、胜率、盈亏比、持仓天数分布
"""
import math
import numpy as np
import pandas as pd
from typing import List, Tuple, Optional
from dataclasses import dataclass, field


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

    # 效率
    sharpe_ratio: float = field(init=False)
    sortino_ratio: float = field(init=False)
    calmar_ratio: float = field(init=False)

    # 交易统计
    total_trades: int = field(init=False)
    win_rate: float = field(init=False)
    profit_factor: float = field(init=False)
    avg_profit: float = field(init=False)
    avg_loss: float = field(init=False)
    max_single_profit: float = field(init=False)
    max_single_loss: float = field(init=False)
    avg_holding_days: float = field(init=False)

    def __post_init__(self):
        self._compute()

    # ------------------------------------------------------------------ #
    #  计算入口
    # ------------------------------------------------------------------ #

    def _compute(self):
        """计算所有统计指标"""
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
            return

        self.final_value = float(df["total"].iloc[-1])
        self.total_return = (self.final_value / self.initial_cash) - 1

        # 年化 (实际交易日数 / 252)
        days = len(df)
        years = days / 252.0
        if years > 0:
            self.annualized_return = (1 + self.total_return) ** (1 / years) - 1
        else:
            self.annualized_return = 0.0

        # 基准收益: buy-and-hold (如果提供了基准价格序列)
        if self.benchmark_prices and len(self.benchmark_prices) >= 2:
            self.benchmark_return = (self.benchmark_prices[-1] / self.benchmark_prices[0]) - 1
        else:
            # 用净值首尾近似
            self.benchmark_return = 0.0
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
        self.volatility = float(daily_ret.std() * math.sqrt(252))

        # VaR 95% (历史模拟法)
        self.var_95 = float(np.percentile(daily_ret, 5))

        # 下行偏差 (仅负收益)
        neg_ret = daily_ret[daily_ret < 0]
        self.downside_deviation = (
            float(neg_ret.std() * math.sqrt(252)) if len(neg_ret) > 1 else 0.0
        )

    # ------------------------------------------------------------------ #
    #  效率指标
    # ------------------------------------------------------------------ #

    def _compute_ratios(self, df: pd.DataFrame):
        rf = 0.03 / 252  # 无风险利率 (年化3%, 日化)

        if df.empty or len(df) < 2:
            self.sharpe_ratio = 0.0
            self.sortino_ratio = 0.0
            self.calmar_ratio = 0.0
            return

        net = df["total"] / self.initial_cash
        daily_ret = net.pct_change().dropna()

        # 夏普比率
        excess = daily_ret - rf
        if excess.std() > 0:
            self.sharpe_ratio = float(excess.mean() / excess.std() * math.sqrt(252))
        else:
            self.sharpe_ratio = 0.0

        # 索提诺比率 (用下行偏差)
        ann_ret = self.annualized_return
        if self.downside_deviation > 0:
            self.sortino_ratio = (ann_ret - 0.03) / self.downside_deviation
        else:
            self.sortino_ratio = 0.0

        # 卡玛比率
        if self.max_drawdown < 0:
            self.calmar_ratio = ann_ret / abs(self.max_drawdown)
        else:
            self.calmar_ratio = 0.0

    # ------------------------------------------------------------------ #
    #  交易统计
    # ------------------------------------------------------------------ #

    def _compute_trade_stats(self):
        sell_trades = [t for t in self.trades if t.direction == "sell"]
        self.total_trades = len(sell_trades)

        if not sell_trades:
            self.win_rate = 0.0
            self.profit_factor = 0.0
            self.avg_profit = 0.0
            self.avg_loss = 0.0
            self.max_single_profit = 0.0
            self.max_single_loss = 0.0
            self.avg_holding_days = 0.0
            return

        pnls = [t.pnl for t in sell_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        self.win_rate = len(wins) / len(pnls)

        total_profit = sum(wins) if wins else 0.0
        total_loss = abs(sum(losses)) if losses else 0.0
        self.profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

        self.avg_profit = float(np.mean(wins)) if wins else 0.0
        self.avg_loss = float(np.mean(losses)) if losses else 0.0
        self.max_single_profit = float(max(pnls)) if pnls else 0.0
        self.max_single_loss = float(min(pnls)) if pnls else 0.0

        # 平均持仓天数 (买卖配对)
        self.avg_holding_days = 0.0  # TODO: 买卖配对计算

    # ------------------------------------------------------------------ #
    #  报告格式化
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        return {
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
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "calmar_ratio": round(self.calmar_ratio, 4),
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4) if not math.isinf(self.profit_factor) else 9999,
            "avg_profit": round(self.avg_profit, 2),
            "avg_loss": round(self.avg_loss, 2),
            "max_single_profit": round(self.max_single_profit, 2),
            "max_single_loss": round(self.max_single_loss, 2),
        }
