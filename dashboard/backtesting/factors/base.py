"""因子基类"""
from abc import ABC, abstractmethod
import pandas as pd


class FactorBase(ABC):
    """所有因子的抽象基类"""

    name: str = "base_factor"

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.Series:
        """
        计算因子值.
        
        Args:
            df: 标准化的 OHLCV DataFrame
        Returns:
            与 df 等长的 Series, index 对齐
        """
        pass

    def __call__(self, df: pd.DataFrame) -> pd.Series:
        result = self.compute(df)
        result.name = self.name
        return result
