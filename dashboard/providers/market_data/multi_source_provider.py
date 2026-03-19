"""
多数据源组合 Provider
自动尝试多个数据源，提高成功率
"""
import asyncio
import time
from typing import List, Optional

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

    def __init__(
        self,
        providers: List[MarketDataProvider],
        provider_timeout: float = 6.0,
        cache_ttl: float = 5.0,
        stale_ttl: float = 30.0
    ):
        """
        Args:
            providers: 数据源列表，按优先级排序
        """
        if not providers:
            raise ValueError("至少需要提供一个数据源")

        self.providers = providers
        self._last_successful_provider = None
        self.provider_timeout = provider_timeout
        self.cache_ttl = cache_ttl
        self.stale_ttl = stale_ttl
        self._quote_cache: dict[str, tuple[float, Quote]] = {}

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

    def _cache_key(self, symbol: str, market: Market) -> str:
        return f"{symbol}:{market.value}"

    def _get_cached_quote(self, key: str, max_age: float) -> Optional[Quote]:
        cached = self._quote_cache.get(key)
        if not cached:
            return None

        cached_at, quote = cached
        if time.monotonic() - cached_at <= max_age:
            return quote
        return None

    def _store_quote(self, symbol: str, market: Market, quote: Quote) -> None:
        self._quote_cache[self._cache_key(symbol, market)] = (time.monotonic(), quote)

    async def _get_quote_from_provider(
        self,
        provider: MarketDataProvider,
        symbol: str,
        market: Market
    ) -> Optional[Quote]:
        try:
            return await asyncio.wait_for(
                provider.get_quote(symbol, market),
                timeout=self.provider_timeout
            )
        except asyncio.TimeoutError:
            print(f"[{provider.name}] 获取 {symbol} 超时")
            return None
        except Exception as e:
            print(f"[{provider.name}] 获取 {symbol} 失败: {e}")
            return None

    async def get_quote(self, symbol: str, market: Market) -> Optional[Quote]:
        """
        获取单个股票实时行情
        依次尝试各个数据源，直到成功
        """
        cache_key = self._cache_key(symbol, market)
        cached_quote = self._get_cached_quote(cache_key, self.cache_ttl)
        if cached_quote:
            return cached_quote

        # 优先使用上次成功的数据源
        if self._last_successful_provider:
            if market in self._last_successful_provider.supported_markets:
                quote = await self._get_quote_from_provider(
                    self._last_successful_provider,
                    symbol,
                    market
                )
                if quote:
                    self._store_quote(symbol, market, quote)
                    return quote

        # 依次尝试其他数据源
        for provider in self.providers:
            if provider == self._last_successful_provider:
                continue

            if market not in provider.supported_markets:
                continue

            quote = await self._get_quote_from_provider(provider, symbol, market)
            if quote:
                self._last_successful_provider = provider
                self._store_quote(symbol, market, quote)
                return quote

        return self._get_cached_quote(cache_key, self.stale_ttl)

    async def get_quotes(self, symbols: List[str], market: Market) -> List[Quote]:
        """
        批量获取实时行情
        优先使用支持批量查询的数据源
        """
        cached_quotes = []
        missing_symbols = []

        for symbol in symbols:
            cached_quote = self._get_cached_quote(self._cache_key(symbol, market), self.cache_ttl)
            if cached_quote:
                cached_quotes.append(cached_quote)
            else:
                missing_symbols.append(symbol)

        if not missing_symbols:
            return cached_quotes

        for provider in self.providers:
            if market not in provider.supported_markets:
                continue

            try:
                quotes = await asyncio.wait_for(
                    provider.get_quotes(missing_symbols, market),
                    timeout=self.provider_timeout
                )
                if quotes:
                    for quote in quotes:
                        self._store_quote(quote.stock.symbol, quote.stock.market, quote)
                    return cached_quotes + quotes
            except asyncio.TimeoutError:
                print(f"[{provider.name}] 批量获取超时")
                continue
            except Exception as e:
                print(f"[{provider.name}] 批量获取失败: {e}")
                continue

        # 如果批量失败，尝试逐个获取
        quotes = list(cached_quotes)
        for symbol in missing_symbols:
            quote = await self.get_quote(symbol, market)
            if quote:
                quotes.append(quote)

        return quotes

    async def search_stock(self, keyword: str) -> List[Stock]:
        """搜索股票"""
        all_results = []

        for provider in self.providers:
            try:
                results = await asyncio.wait_for(
                    provider.search_stock(keyword),
                    timeout=self.provider_timeout
                )
                all_results.extend(results)
            except asyncio.TimeoutError:
                print(f"[{provider.name}] 搜索超时")
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
