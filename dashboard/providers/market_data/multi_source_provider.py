"""
多数据源组合 Provider
自动尝试多个数据源，提高成功率
"""
from typing import List, Optional
from datetime import datetime

from core.data.base import MarketDataProvider
from core.models import Stock, Quote, Market


class MultiSourceProvider(MarketDataProvider):
    """
    多数据源组合 Provider

    特点：
    - 支持多个数据源备选
    - 自动 fallback 到可用的数据源
    - 优先使用稳定性高的数据源

    使用示例：
    provider = MultiSourceProvider([
        GoogleFinanceProvider(),  # 首选，全球市场
        AKShareProvider(),        # 备选，A股专用
    ])
    """

    def __init__(self, providers: List[MarketDataProvider]):
        """
        Args:
            providers: 数据源列表，按优先级排序
        """
        if not providers:
            raise ValueError("至少需要提供一个数据源")

        self.providers = providers
        self._last_successful_provider = None

    @property
    def name(self) -> str:
        provider_names = ", ".join([p.name for p in self.providers])
        return f"多数据源 ({provider_names})"

    @property
    def supported_markets(self) -> List[Market]:
        """返回所有数据源支持的市场"""
        markets = set()
        for provider in self.providers:
            markets.update(provider.supported_markets)
        return list(markets)

    async def get_quote(self, symbol: str, market: Market) -> Optional[Quote]:
        """
        获取单个股票实时行情
        依次尝试各个数据源，直到成功
        """
        # 优先使用上次成功的数据源
        if self._last_successful_provider:
            try:
                if market in self._last_successful_provider.supported_markets:
                    quote = await self._last_successful_provider.get_quote(symbol, market)
                    if quote:
                        return quote
            except Exception as e:
                print(f"[{self._last_successful_provider.name}] 失败: {e}")

        # 依次尝试其他数据源
        for provider in self.providers:
            if provider == self._last_successful_provider:
                continue

            if market not in provider.supported_markets:
                continue

            try:
                quote = await provider.get_quote(symbol, market)
                if quote:
                    self._last_successful_provider = provider
                    return quote
            except Exception as e:
                print(f"[{provider.name}] 获取 {symbol} 失败: {e}")
                continue

        return None

    async def get_quotes(self, symbols: List[str], market: Market) -> List[Quote]:
        """
        批量获取实时行情
        优先使用支持批量查询的数据源
        """
        for provider in self.providers:
            if market not in provider.supported_markets:
                continue

            try:
                quotes = await provider.get_quotes(symbols, market)
                if quotes:
                    return quotes
            except Exception as e:
                print(f"[{provider.name}] 批量获取失败: {e}")
                continue

        # 如果批量失败，尝试逐个获取
        quotes = []
        for symbol in symbols:
            quote = await self.get_quote(symbol, market)
            if quote:
                quotes.append(quote)

        return quotes

    async def search_stock(self, keyword: str) -> List[Stock]:
        """搜索股票"""
        all_results = []

        for provider in self.providers:
            try:
                results = await provider.search_stock(keyword)
                all_results.extend(results)
            except Exception as e:
                print(f"[{provider.name}] 搜索失败: {e}")
                continue

        # 去重（基于 symbol + market）
        unique_results = {}
        for stock in all_results:
            key = f"{stock.symbol}:{stock.market.value}"
            if key not in unique_results:
                unique_results[key] = stock

        return list(unique_results.values())

    async def get_stock_info(self, symbol: str, market: Market) -> Optional[Stock]:
        """获取股票基本信息"""
        quote = await self.get_quote(symbol, market)
        return quote.stock if quote else None
