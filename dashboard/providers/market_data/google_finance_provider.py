"""
Google Finance 行情数据 Provider
支持全球市场：A股、港股、美股等
"""
from typing import List, Optional
from datetime import datetime
import asyncio
from concurrent.futures import ThreadPoolExecutor

from core.data.base import MarketDataProvider
from core.models import Stock, Quote, Market


class GoogleFinanceProvider(MarketDataProvider):
    """
    Google Finance 行情 Provider

    优势：
    - 支持全球市场（A股、港股、美股、日股等）
    - 数据稳定，无需认证
    - 通过 yahooquery 库访问

    使用说明：
    pip install yahooquery
    """

    def __init__(self):
        self._yq = None
        self._executor = ThreadPoolExecutor(max_workers=4)

    def _ensure_yahooquery(self):
        """确保 yahooquery 已导入"""
        if self._yq is None:
            try:
                from yahooquery import Ticker
                self._yq = Ticker
            except ImportError:
                raise ImportError(
                    "请先安装 yahooquery: pip install yahooquery"
                )

    @property
    def name(self) -> str:
        return "Google Finance (Yahoo Query)"

    @property
    def supported_markets(self) -> List[Market]:
        return [Market.A_SHARE, Market.HK_STOCK, Market.US_STOCK]

    def _convert_to_yahoo_symbol(self, symbol: str, market: Market) -> str:
        """
        转换为 Yahoo Finance 的股票代码格式

        A股: 600519 -> 600519.SS (上交所) 或 000001.SZ (深交所)
        港股: 9988 -> 9988.HK
        美股: GOOGL -> GOOGL (无需转换)
        """
        if market == Market.A_SHARE:
            # 特殊处理：中国指数
            index_map = {
                '000001': '000001.SS',   # 上证指数
                '000300': '000300.SS',   # 沪深300
                '000016': '000016.SS',   # 上证50
                '000905': '000905.SS',   # 中证500
                '000852': '000852.SS',   # 中证1000
                '399001': '399001.SZ',   # 深证成指
                '399006': '399006.SZ',   # 创业板指
                '399005': '399005.SZ',   # 中小板指
            }

            if symbol in index_map:
                return index_map[symbol]

            # A股股票需要添加交易所后缀
            if symbol.startswith('6'):
                return f"{symbol}.SS"  # 上海证券交易所
            elif symbol.startswith(('0', '3')):
                return f"{symbol}.SZ"  # 深圳证券交易所
            elif symbol.startswith('4') or symbol.startswith('8'):
                return f"{symbol}.BJ"  # 北京证券交易所
            else:
                return f"{symbol}.SS"  # 默认上交所

        elif market == Market.HK_STOCK:
            # 港股需要补齐4位并添加.HK
            return f"{int(symbol):04d}.HK"

        elif market == Market.US_STOCK:
            # 美股无需转换
            return symbol.upper()

        return symbol

    def _convert_from_yahoo_symbol(self, yahoo_symbol: str) -> tuple[str, Market]:
        """
        从 Yahoo Finance 格式转换回标准格式

        Returns:
            (symbol, market)
        """
        if '.SS' in yahoo_symbol or '.SZ' in yahoo_symbol or '.BJ' in yahoo_symbol:
            symbol = yahoo_symbol.split('.')[0]
            return symbol, Market.A_SHARE
        elif '.HK' in yahoo_symbol:
            symbol = yahoo_symbol.split('.')[0]
            return symbol, Market.HK_STOCK
        else:
            return yahoo_symbol, Market.US_STOCK

    def _build_quote(self, symbol: str, market: Market, data: dict) -> Optional[Quote]:
        if not isinstance(data, dict) or 'error' in str(data).lower():
            return None

        name = data.get('shortName') or data.get('longName') or symbol
        stock = Stock(symbol=symbol, name=name, market=market)

        return Quote(
            stock=stock,
            price=float(data.get('regularMarketPrice', 0) or 0),
            open=float(data.get('regularMarketOpen', 0) or 0),
            high=float(data.get('regularMarketDayHigh', 0) or 0),
            low=float(data.get('regularMarketDayLow', 0) or 0),
            prev_close=float(data.get('regularMarketPreviousClose', 0) or 0),
            volume=int(data.get('regularMarketVolume', 0) or 0),
            amount=0.0,
            timestamp=datetime.now()
        )

    def _get_quote_sync(self, symbol: str, market: Market) -> Optional[Quote]:
        self._ensure_yahooquery()

        try:
            yahoo_symbol = self._convert_to_yahoo_symbol(symbol, market)
            ticker = self._yq(yahoo_symbol)
            price_data = ticker.price

            if isinstance(price_data, dict) and yahoo_symbol in price_data:
                return self._build_quote(symbol, market, price_data[yahoo_symbol])

            return None

        except Exception as e:
            print(f"获取 {symbol} 行情失败: {e}")
            return None

    async def get_quote(self, symbol: str, market: Market) -> Optional[Quote]:
        """获取单个股票实时行情"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._get_quote_sync,
            symbol,
            market
        )

    def _get_quotes_sync(self, symbols: List[str], market: Market) -> List[Quote]:
        self._ensure_yahooquery()

        try:
            yahoo_symbols = [self._convert_to_yahoo_symbol(s, market) for s in symbols]
            ticker = self._yq(yahoo_symbols)
            price_data = ticker.price

            quotes = []

            if isinstance(price_data, dict):
                for yahoo_symbol, data in price_data.items():
                    symbol, detected_market = self._convert_from_yahoo_symbol(yahoo_symbol)
                    quote = self._build_quote(symbol, detected_market, data)
                    if quote:
                        quotes.append(quote)

            return quotes

        except Exception as e:
            print(f"批量获取行情失败: {e}")
            return []

    async def get_quotes(self, symbols: List[str], market: Market) -> List[Quote]:
        """批量获取实时行情"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._get_quotes_sync,
            symbols,
            market
        )

    async def search_stock(self, keyword: str) -> List[Stock]:
        """
        搜索股票

        注意：Yahoo Finance API 不直接支持搜索，
        这里只是简单的验证功能
        """
        self._ensure_yahooquery()

        # 尝试将关键词作为股票代码查询
        results = []

        # 尝试不同市场
        for market in [Market.A_SHARE, Market.HK_STOCK, Market.US_STOCK]:
            try:
                quote = await self.get_quote(keyword, market)
                if quote:
                    results.append(quote.stock)
            except:
                continue

        return results

    async def get_stock_info(self, symbol: str, market: Market) -> Optional[Stock]:
        """获取股票基本信息"""
        quote = await self.get_quote(symbol, market)
        return quote.stock if quote else None
