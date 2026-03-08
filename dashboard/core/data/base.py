"""
数据源抽象基类
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Callable
from datetime import datetime
import asyncio

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


class PriceMonitor:
    """价格监控器"""

    def __init__(self, market_provider: MarketDataProvider):
        self.market_provider = market_provider
        self._callbacks: List[Callable[[Quote, Dict[str, Any]], None]] = []
        self._watch_list: Dict[str, Dict[str, Any]] = {}  # symbol -> conditions
        self._running = False
        self._interval = 5  # 秒

    def add_callback(self, callback: Callable[[Quote, Dict[str, Any]], None]):
        """添加价格变动回调"""
        self._callbacks.append(callback)

    def watch(
        self,
        symbol: str,
        market: Market,
        conditions: Optional[Dict[str, Any]] = None
    ):
        """
        添加监控

        conditions 示例:
        {
            "price_above": 10.5,    # 价格突破
            "price_below": 9.0,     # 价格跌破
            "change_pct_above": 5,  # 涨幅超过
            "change_pct_below": -5  # 跌幅超过
        }
        """
        self._watch_list[symbol] = {
            "market": market,
            "conditions": conditions or {},
            "last_quote": None
        }

    def unwatch(self, symbol: str):
        """移除监控"""
        self._watch_list.pop(symbol, None)

    async def start(self):
        """启动监控"""
        self._running = True
        while self._running:
            await self._check_prices()
            await asyncio.sleep(self._interval)

    def stop(self):
        """停止监控"""
        self._running = False

    async def _check_prices(self):
        """检查价格变动"""
        for symbol, watch_info in self._watch_list.items():
            try:
                quote = await self.market_provider.get_quote(
                    symbol, watch_info["market"]
                )
                if quote:
                    triggered = self._check_conditions(quote, watch_info["conditions"])
                    if triggered:
                        for callback in self._callbacks:
                            callback(quote, triggered)
                    watch_info["last_quote"] = quote
            except Exception as e:
                print(f"监控 {symbol} 出错: {e}")

    def _check_conditions(
        self,
        quote: Quote,
        conditions: Dict[str, Any]
    ) -> Dict[str, Any]:
        """检查触发条件"""
        triggered = {}

        if "price_above" in conditions and quote.price > conditions["price_above"]:
            triggered["price_above"] = {
                "threshold": conditions["price_above"],
                "current": quote.price
            }

        if "price_below" in conditions and quote.price < conditions["price_below"]:
            triggered["price_below"] = {
                "threshold": conditions["price_below"],
                "current": quote.price
            }

        if "change_pct_above" in conditions and quote.change_pct > conditions["change_pct_above"]:
            triggered["change_pct_above"] = {
                "threshold": conditions["change_pct_above"],
                "current": quote.change_pct
            }

        if "change_pct_below" in conditions and quote.change_pct < conditions["change_pct_below"]:
            triggered["change_pct_below"] = {
                "threshold": conditions["change_pct_below"],
                "current": quote.change_pct
            }

        return triggered
