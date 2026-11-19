"""
数据源抽象基类
"""
from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime

from ..models import Stock, Quote, Position, Portfolio, Market


class MarketDataProvider(ABC):
    """行情数据 Provider 抽象基类"""

    @abstractmethod
    async def get_quote(self, symbol: str, market: Market) -> Optional[Quote]:
        """获取单个股票实时行情"""
        pass

    @abstractmethod
    async def get_quotes(self, symbols: List[str], market: Market) -> List[Quote]:
        """批量获取实时行情"""
        pass

    @abstractmethod
    async def search_stock(self, keyword: str) -> List[Stock]:
        """搜索股票"""
        pass

    @abstractmethod
    async def get_stock_info(self, symbol: str, market: Market) -> Optional[Stock]:
        """获取股票基本信息"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称"""
        pass

    @property
    @abstractmethod
    def supported_markets(self) -> List[Market]:
        """支持的市场"""
        pass


class PortfolioProvider(ABC):
    """持仓数据 Provider 抽象基类"""

    @abstractmethod
    async def get_portfolio(self) -> Portfolio:
        """获取当前持仓"""
        pass

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """获取持仓列表"""
        pass

    @abstractmethod
    async def refresh(self) -> None:
        """刷新持仓数据"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称"""
        pass

    @property
    @abstractmethod
    def last_update(self) -> Optional[datetime]:
        """最后更新时间"""
        pass

