"""
量化回测模块
Quantitative Backtesting Module

层次架构:
  Layer 1 - 数据层:   data/loader.py + data/cleaner.py + data/store.py
  Layer 2 - 因子层:   factors/technical.py + factors/fundamental.py
  Layer 3 - 策略层:   strategies/base.py + strategies/examples/
  Layer 4 - 运行层:   engine.py + broker/simulated.py
  Layer 5 - 分析层:   analyzer/stats.py + analyzer/report.py
  Layer 6 - 经验层:   reflection/llm_reflector.py + memory集成
"""

from .engine import BacktestEngine
from .strategies.base import Strategy
from .data.loader import DataLoader
from .data.store import DataStore
from .analyzer.stats import BacktestStats

__all__ = [
    "BacktestEngine",
    "Strategy",
    "DataLoader",
    "DataStore",
    "BacktestStats",
]

__version__ = "1.0.0"
