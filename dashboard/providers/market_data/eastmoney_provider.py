"""
东方财富直连行情 Provider
为 A 股/主要指数提供更轻量的单票实时行情获取。
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
import asyncio

import httpx

from core.data.base import MarketDataProvider
from core.models import Stock, Quote, Market


class EastmoneyDirectProvider(MarketDataProvider):
    """直连东方财富接口，绕开代理与整表抓取。"""

    _QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
    _DEFAULT_FIELDS = ",".join([
        "f43",   # 最新价
        "f44",   # 最高价
        "f45",   # 最低价
        "f46",   # 今开
        "f47",   # 成交手/量
        "f48",   # 成交额
        "f57",   # 代码
        "f58",   # 名称
        "f60",   # 昨收
        "f168",  # 换手率
        "f162",  # 市盈率
        "f169",  # 涨跌额
        "f170",  # 涨跌幅
    ])
    _INDEX_SECID_MAP = {
        "000001": "1.000001",  # 上证指数
        "000300": "1.000300",  # 沪深300
        "000016": "1.000016",  # 上证50
        "000905": "1.000905",  # 中证500
        "000852": "1.000852",  # 中证1000
        "399001": "0.399001",  # 深证成指
        "399006": "0.399006",  # 创业板指
        "399005": "0.399005",  # 中小100
    }

    def __init__(self):
        self._headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json,text/plain,*/*",
        }

    @property
    def name(self) -> str:
        return "Eastmoney Direct"

    @property
    def supported_markets(self) -> List[Market]:
        return [Market.A_SHARE]

    def _secid_for_symbol(self, symbol: str) -> Optional[str]:
        if symbol in self._INDEX_SECID_MAP:
            return self._INDEX_SECID_MAP[symbol]
        if symbol.startswith(("6", "5", "9")):
            return f"1.{symbol}"
        if symbol.startswith(("0", "3", "2", "4", "8")):
            return f"0.{symbol}"
        return None

    def _scale_price(self, value) -> float:
        if value in (None, "-", ""):
            return 0.0
        return float(value)

    def _scale_turnover(self, value) -> Optional[float]:
        if value in (None, "-", ""):
            return None
        return float(value)

    async def _fetch_quote_data(self, symbol: str) -> Optional[dict]:
        secid = self._secid_for_symbol(symbol)
        if not secid:
            return None

        params = {
            "fltt": "2",
            "invt": "2",
            "fields": self._DEFAULT_FIELDS,
            "secid": secid,
        }

        async with httpx.AsyncClient(
            timeout=8.0,
            trust_env=False,
            follow_redirects=True,
            headers=self._headers,
        ) as client:
            response = await client.get(self._QUOTE_URL, params=params)
            response.raise_for_status()
            payload = response.json()

        if payload.get("rc") != 0:
            return None

        return payload.get("data")

    def _quote_from_payload(self, symbol: str, payload: dict) -> Optional[Quote]:
        if not payload:
            return None

        stock = Stock(
            symbol=str(payload.get("f57") or symbol),
            name=str(payload.get("f58") or symbol),
            market=Market.A_SHARE,
        )

        price = self._scale_price(payload.get("f43"))
        prev_close = self._scale_price(payload.get("f60"))

        return Quote(
            stock=stock,
            price=price,
            open=self._scale_price(payload.get("f46")),
            high=self._scale_price(payload.get("f44")),
            low=self._scale_price(payload.get("f45")),
            prev_close=prev_close,
            volume=int(float(payload.get("f47") or 0)),
            amount=float(payload.get("f48") or 0),
            timestamp=datetime.now(),
            turnover=self._scale_turnover(payload.get("f168")),
            pe=float(payload.get("f162")) if payload.get("f162") not in (None, "-", "") else None,
        )

    async def get_quote(self, symbol: str, market: Market) -> Optional[Quote]:
        """获取单个 A 股或指数行情。"""
        if market != Market.A_SHARE:
            return None

        try:
            payload = await self._fetch_quote_data(symbol)
            return self._quote_from_payload(symbol, payload or {})
        except Exception as e:
            print(f"[Eastmoney Direct] 获取 {symbol} 失败: {e}")
            return None

    async def get_quotes(self, symbols: List[str], market: Market) -> List[Quote]:
        """批量获取，内部并发执行。"""
        if market != Market.A_SHARE:
            return []

        tasks = [self.get_quote(symbol, market) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [result for result in results if isinstance(result, Quote)]

    async def search_stock(self, keyword: str) -> List[Stock]:
        """当前仅支持代码直查。"""
        if keyword.isdigit():
            quote = await self.get_quote(keyword, Market.A_SHARE)
            return [quote.stock] if quote else []
        return []

    async def get_stock_info(self, symbol: str, market: Market) -> Optional[Stock]:
        quote = await self.get_quote(symbol, market)
        return quote.stock if quote else None
