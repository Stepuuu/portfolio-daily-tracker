"""
数据馈送器 - 向回测引擎逐 bar 提供数据
灵感: backtrader 的 DataFeed 机制

在回测期间模拟实时数据流,每次 next() 调用推进一个交易日.
"""
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np


class Bar:
    """单根 K 线数据容器"""
    __slots__ = [
        "symbol", "date", "open", "high", "low", "close",
        "volume", "amount", "pct_change", "turnover",
        "factors",  # 额外的因子值
    ]

    def __init__(self, symbol: str, date, row: pd.Series):
        self.symbol = symbol
        self.date = date
        self.open = float(row.get("open", 0))
        self.high = float(row.get("high", 0))
        self.low = float(row.get("low", 0))
        self.close = float(row.get("close", 0))
        self.volume = float(row.get("volume", 0))
        self.amount = float(row.get("amount", 0))
        self.pct_change = float(row.get("pct_change", 0))
        self.turnover = float(row.get("turnover", 0))
        self.factors: Dict[str, float] = {}

    def __repr__(self):
        return (
            f"Bar({self.symbol} {self.date} "
            f"O={self.open:.2f} H={self.high:.2f} "
            f"L={self.low:.2f} C={self.close:.2f})"
        )


class DataFeed:
    """
    数据馈送器
    
    回测引擎通过此类遍历历史数据,策略通过
    self.data 访问当前 bar 和历史窗口.
    
    使用示例:
        feed = DataFeed("600519", df)
        while not feed.is_done:
            feed.next()
            bar = feed.current_bar
            window = feed.get_window(20)  # 最近20根K线
    """

    def __init__(
        self,
        symbol: str,
        df: pd.DataFrame,
        warmup: int = 0,  # 预热期 (不触发 next() 的 bar 数)
    ):
        self.symbol = symbol
        self._df = df.copy()
        self.warmup = warmup
        self._cursor = -1  # 当前 bar 位置
        self._len = len(df)

    # ------------------------------------------------------------------ #
    #  迭代接口
    # ------------------------------------------------------------------ #

    def reset(self):
        """重置游标到初始位置"""
        self._cursor = -1

    def advance(self) -> bool:
        """
        推进到下一根 bar.
        返回 True 表示还有数据, False 表示已结束.
        """
        self._cursor += 1
        return self._cursor < self._len

    @property
    def is_done(self) -> bool:
        return self._cursor >= self._len - 1

    @property
    def is_warmup(self) -> bool:
        """当前是否处于预热期"""
        return self._cursor < self.warmup

    @property
    def current_bar(self) -> Optional[Bar]:
        """当前 bar"""
        if self._cursor < 0 or self._cursor >= self._len:
            return None
        row = self._df.iloc[self._cursor]
        return Bar(self.symbol, self._df.index[self._cursor], row)

    @property
    def current_index(self) -> int:
        return self._cursor

    @property
    def total_bars(self) -> int:
        return self._len

    # ------------------------------------------------------------------ #
    #  数据访问接口 (供策略使用)
    # ------------------------------------------------------------------ #

    def get_window(self, n: int) -> pd.DataFrame:
        """
        获取最近 n 根 K 线 (含当前 bar).
        返回 DataFrame, 最新的在最后一行.
        """
        if self._cursor < 0:
            return pd.DataFrame()
        start = max(0, self._cursor - n + 1)
        return self._df.iloc[start : self._cursor + 1]

    def close(self, n: int = 0) -> float:
        """获取收盘价, n=0 当前, n=1 上一根, 以此类推"""
        idx = self._cursor - n
        if idx < 0 or idx >= self._len:
            return float("nan")
        return float(self._df["close"].iloc[idx])

    def open(self, n: int = 0) -> float:
        idx = self._cursor - n
        if idx < 0 or idx >= self._len:
            return float("nan")
        return float(self._df["open"].iloc[idx])

    def high(self, n: int = 0) -> float:
        idx = self._cursor - n
        if idx < 0 or idx >= self._len:
            return float("nan")
        return float(self._df["high"].iloc[idx])

    def low(self, n: int = 0) -> float:
        idx = self._cursor - n
        if idx < 0 or idx >= self._len:
            return float("nan")
        return float(self._df["low"].iloc[idx])

    def volume(self, n: int = 0) -> float:
        idx = self._cursor - n
        if idx < 0 or idx >= self._len:
            return float("nan")
        return float(self._df["volume"].iloc[idx])

    def get_series(self, col: str, periods: Optional[int] = None) -> pd.Series:
        """
        获取某列的时间序列 (截止当前 bar).
        col: 列名, 如 "close", "volume", "rsi" (因子)
        """
        if self._cursor < 0:
            return pd.Series(dtype=float)
        end = self._cursor + 1
        if periods is not None:
            start = max(0, end - periods)
        else:
            start = 0
        if col in self._df.columns:
            return self._df[col].iloc[start:end]
        return pd.Series(dtype=float)

    def add_factor(self, name: str, values: pd.Series):
        """向 DataFrame 中添加因子列"""
        self._df[name] = values

    def get_factor(self, name: str, n: int = 0) -> float:
        """获取某因子的当前值 (或历史值)"""
        idx = self._cursor - n
        if name not in self._df.columns or idx < 0 or idx >= self._len:
            return float("nan")
        return float(self._df[name].iloc[idx])
