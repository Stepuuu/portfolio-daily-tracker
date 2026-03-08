"""
Mock市场数据Provider - 用于测试和开发
当AKShare API不可用时使用
"""
from typing import List, Optional
from datetime import datetime
import random

from core.data.base import MarketDataProvider
from core.models import Stock, Quote, Market


class MockMarketDataProvider(MarketDataProvider):
    """模拟市场数据Provider"""

    # 预定义的股票数据
    MOCK_DATA = {
        '000001': ('上证指数', 3100.00, Market.A_SHARE),
        '399001': ('深证成指', 10000.00, Market.A_SHARE),
        '399006': ('创业板指', 2100.00, Market.A_SHARE),
        '600519': ('贵州茅台', 1650.00, Market.A_SHARE),
        '000858': ('五粮液', 150.00, Market.A_SHARE),
        '601318': ('中国平安', 42.00, Market.A_SHARE),
        '600036': ('招商银行', 35.00, Market.A_SHARE),
        '000333': ('美的集团', 55.00, Market.A_SHARE),
        '603259': ('药明康德', 98.00, Market.A_SHARE),
    }

    @property
    def name(self) -> str:
        return "Mock数据(仅供测试)"

    @property
    def supported_markets(self) -> List[Market]:
        return [Market.A_SHARE]

    async def get_quote(self, symbol: str, market: Market) -> Optional[Quote]:
        """获取模拟行情数据"""
        if symbol in self.MOCK_DATA:
            name, base_price, market_type = self.MOCK_DATA[symbol]
        else:
            # 未知股票，生成随机数据
            name = f"股票{symbol}"
            base_price = random.uniform(10, 100)
            market_type = market

        # 生成随机波动
        change_pct = random.uniform(-3, 3)
        change = base_price * change_pct / 100
        price = base_price + change

        stock = Stock(symbol=symbol, name=name, market=market_type)

        return Quote(
            stock=stock,
            price=round(price, 2),
            open=round(base_price * random.uniform(0.98, 1.02), 2),
            high=round(price * random.uniform(1.00, 1.03), 2),
            low=round(price * random.uniform(0.97, 1.00), 2),
            prev_close=base_price,
            volume=random.randint(1000000, 100000000),
            amount=random.uniform(100000000, 10000000000),
            timestamp=datetime.now()
        )

    async def get_quotes(self, symbols: List[str], market: Market) -> List[Quote]:
        """批量获取模拟行情"""
        results = []
        for symbol in symbols:
            quote = await self.get_quote(symbol, market)
            if quote:
                results.append(quote)
        return results

    async def search_stock(self, keyword: str) -> List[Stock]:
        """搜索股票"""
        results = []
        for symbol, (name, _, market) in self.MOCK_DATA.items():
            if keyword in symbol or keyword in name:
                results.append(Stock(symbol=symbol, name=name, market=market))
        return results

    async def get_stock_info(self, symbol: str, market: Market) -> Optional[Stock]:
        """获取股票基本信息"""
        quote = await self.get_quote(symbol, market)
        return quote.stock if quote else None
