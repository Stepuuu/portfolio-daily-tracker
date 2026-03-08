"""
数据清洗器
灵感: qlib 的 DataHandler 预处理流水线

功能:
  - 去除停牌/涨跌停日过滤
  - 填充缺失值
  - 异常值检测与处理
  - 数据对齐 (多标的)
"""
import logging
from typing import Optional, List, Dict
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class DataCleaner:
    """
    数据清洗流水线
    
    支持链式调用:
        cleaner = DataCleaner(df).fill_missing().remove_outliers().clip_price()
        clean_df = cleaner.result
    """

    def __init__(self, df: pd.DataFrame):
        self._df = df.copy()
        self._log: List[str] = []

    @property
    def result(self) -> pd.DataFrame:
        return self._df.copy()

    @property
    def cleaning_log(self) -> List[str]:
        return self._log.copy()

    # ------------------------------------------------------------------ #
    #  清洗步骤 (链式调用)
    # ------------------------------------------------------------------ #

    def fill_missing(self, method: str = "ffill", limit: int = 5) -> "DataCleaner":
        """
        填充缺失值.
        method: "ffill" (前向填充) / "bfill" (后向填充) / "zero"
        """
        before = self._df.isnull().sum().sum()

        if method == "ffill":
            self._df = self._df.ffill(limit=limit)
        elif method == "bfill":
            self._df = self._df.bfill(limit=limit)
        elif method == "zero":
            self._df = self._df.fillna(0)

        # 仍有缺失则删除行
        remaining_na = self._df.isnull().sum().sum()
        if remaining_na > 0:
            self._df = self._df.dropna()
            self._log.append(
                f"[fill_missing] 填充 {before - remaining_na} 个缺失值, "
                f"删除含缺失行后剩余 {len(self._df)} 条记录"
            )
        else:
            self._log.append(f"[fill_missing] 填充了 {before} 个缺失值")
        return self

    def remove_outliers(
        self,
        cols: Optional[List[str]] = None,
        method: str = "winsorize",
        lower: float = 0.01,
        upper: float = 0.99,
    ) -> "DataCleaner":
        """
        去除/压缩异常值.
        method: "winsorize" (分位数截断) / "zscore" (3σ替换)
        """
        if cols is None:
            cols = ["open", "high", "low", "close", "volume"]
        cols = [c for c in cols if c in self._df.columns]

        outlier_count = 0
        for col in cols:
            if method == "winsorize":
                q_low = self._df[col].quantile(lower)
                q_high = self._df[col].quantile(upper)
                mask = (self._df[col] < q_low) | (self._df[col] > q_high)
                outlier_count += mask.sum()
                self._df[col] = self._df[col].clip(q_low, q_high)
            elif method == "zscore":
                z = (self._df[col] - self._df[col].mean()) / self._df[col].std()
                mask = z.abs() > 3
                outlier_count += mask.sum()
                self._df.loc[mask, col] = self._df[col].median()

        self._log.append(
            f"[remove_outliers] 方法={method}, 处理了 {outlier_count} 个异常值点"
        )
        return self

    def clip_price(self) -> "DataCleaner":
        """
        确保 OHLC 逻辑一致性:
          high >= close >= low, high >= open >= low
        """
        if not all(c in self._df.columns for c in ["open", "high", "low", "close"]):
            return self

        violations = (
            (self._df["high"] < self._df["low"])
            | (self._df["close"] > self._df["high"])
            | (self._df["close"] < self._df["low"])
        ).sum()

        self._df["high"] = self._df[["open", "high", "low", "close"]].max(axis=1)
        self._df["low"] = self._df[["open", "high", "low", "close"]].min(axis=1)

        self._log.append(f"[clip_price] 修正了 {violations} 条 OHLC 逻辑错误")
        return self

    def add_returns(self) -> "DataCleaner":
        """计算日收益率并添加到 DataFrame"""
        if "close" in self._df.columns:
            self._df["returns"] = self._df["close"].pct_change()
            self._df["log_returns"] = np.log(self._df["close"] / self._df["close"].shift(1))
            self._log.append("[add_returns] 添加了 returns 和 log_returns 列")
        return self

    def normalize_volume(self) -> "DataCleaner":
        """对成交量做对数变换 (处理量纲差异大问题)"""
        if "volume" in self._df.columns:
            self._df["volume_raw"] = self._df["volume"]
            self._df["volume"] = np.log1p(self._df["volume"])
            self._log.append("[normalize_volume] 成交量做了 log1p 变换")
        return self

    def filter_by_turnover(
        self, min_turnover: float = 0.001, turnover_col: str = "turnover"
    ) -> "DataCleaner":
        """
        过滤低流动性日期 (换手率低于阈值).
        通常用于剔除停牌/极低成交量异常日.
        """
        if turnover_col not in self._df.columns:
            return self

        before = len(self._df)
        self._df = self._df[self._df[turnover_col] >= min_turnover]
        after = len(self._df)
        self._log.append(
            f"[filter_by_turnover] 过滤低流动性日期: 删除 {before - after} 行"
        )
        return self


class MultiAssetAligner:
    """
    多标的数据对齐器
    
    确保多支股票的回溯测试数据具有相同的时间轴,
    参考 qlib 的多资产数据处理方式.
    """

    @staticmethod
    def align(
        data_dict: Dict[str, pd.DataFrame],
        method: str = "inner",
    ) -> Dict[str, pd.DataFrame]:
        """
        对齐多个 DataFrame 的时间索引.
        
        method: 
          "inner" - 只保留所有资产都有数据的日期
          "outer" - 保留任意资产有数据的日期 (缺失则 NaN)
        """
        if not data_dict:
            return {}

        # 获取所有 index 的交集/并集
        indices = [df.index for df in data_dict.values()]
        if method == "inner":
            common_index = indices[0]
            for idx in indices[1:]:
                common_index = common_index.intersection(idx)
        else:  # outer
            common_index = indices[0]
            for idx in indices[1:]:
                common_index = common_index.union(idx)

        common_index = common_index.sort_values()

        aligned = {}
        for symbol, df in data_dict.items():
            aligned[symbol] = df.reindex(common_index)

        logger.info(
            f"[MultiAssetAligner] {len(data_dict)} 个标的对齐至 {len(common_index)} 个交易日"
        )
        return aligned
