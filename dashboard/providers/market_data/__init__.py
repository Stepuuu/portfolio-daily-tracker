"""
市场数据提供商
"""
from .akshare_provider import AKShareProvider
from .eastmoney_provider import EastmoneyDirectProvider
from .google_finance_provider import GoogleFinanceProvider
from .multi_source_provider import MultiSourceProvider

__all__ = [
    'AKShareProvider',
    'EastmoneyDirectProvider',
    'GoogleFinanceProvider',
    'MultiSourceProvider',
]
