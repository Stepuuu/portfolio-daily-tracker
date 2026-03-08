"""
AKShare 行情数据 Provider
免费开源的 A 股数据源
"""
from typing import List, Optional
from datetime import datetime
import asyncio
from concurrent.futures import ThreadPoolExecutor

from core.data.base import MarketDataProvider
from core.models import Stock, Quote, Market


class AKShareProvider(MarketDataProvider):
    """
    AKShare 行情 Provider

    使用说明：
    pip install akshare
    """

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._ak = None

    def _ensure_akshare(self):
        """确保 akshare 已导入"""
        if self._ak is None:
            try:
                import akshare as ak
                self._ak = ak
                # 禁用 SSL 验证警告（临时解决网络问题）
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except ImportError:
                raise ImportError(
                    "请先安装 akshare: pip install akshare"
                )

    @property
    def name(self) -> str:
        return "AKShare"

    @property
    def supported_markets(self) -> List[Market]:
        return [Market.A_SHARE, Market.HK_STOCK, Market.US_STOCK]

    def _get_a_share_quote_sync(self, symbol: str) -> Optional[Quote]:
        """同步获取 A 股行情"""
        self._ensure_akshare()

        df = None
        is_index = False

        try:
            # 判断是否是指数（上证000001、深证399开头）
            if symbol in ['000001', '000300', '000016', '000905', '000852']:  # 常见上证指数
                is_index = True
                # 尝试获取指数数据
                try:
                    df = self._ak.stock_zh_index_spot_em()
                    # 上证指数在这个API中的代码是 sh000001 格式
                    index_code_map = {
                        '000001': 'sh000001',  # 上证指数
                        '000300': 'sh000300',  # 沪深300
                        '000016': 'sh000016',  # 上证50
                        '000905': 'sh000905',  # 中证500
                        '000852': 'sh000852',  # 中证1000
                    }
                    search_code = index_code_map.get(symbol, f'sh{symbol}')
                    row = df[df["代码"] == search_code]

                    if row.empty:
                        # 如果没找到，尝试原代码
                        row = df[df["代码"] == symbol]
                except Exception as e:
                    print(f"获取指数 {symbol} 失败（网络/API问题），返回 None 让其他数据源处理: {e}")
                    return None  # 指数获取失败时直接返回None，不要fallback到股票

            elif symbol.startswith('399'):  # 深证指数
                is_index = True
                # 尝试获取指数数据
                try:
                    df = self._ak.stock_zh_index_spot_em()
                    # 深证指数代码格式 sz399001
                    search_code = f'sz{symbol}'
                    row = df[df["代码"] == search_code]

                    if row.empty:
                        row = df[df["代码"] == symbol]
                except Exception as e:
                    print(f"获取指数 {symbol} 失败（网络/API问题），返回 None 让其他数据源处理: {e}")
                    return None  # 指数获取失败时直接返回None

            # 如果指数API失败或不是指数，使用普通股票API
            if df is None:
                df = self._ak.stock_zh_a_spot_em()
                row = df[df["代码"] == symbol]

            if row.empty:
                return None

            row = row.iloc[0]
            stock = Stock(
                symbol=symbol,
                name=row["名称"],
                market=Market.A_SHARE
            )

            return Quote(
                stock=stock,
                price=float(row["最新价"]) if row["最新价"] != "-" else 0.0,
                open=float(row["今开"]) if row["今开"] != "-" else 0.0,
                high=float(row["最高"]) if row["最高"] != "-" else 0.0,
                low=float(row["最低"]) if row["最低"] != "-" else 0.0,
                prev_close=float(row["昨收"]) if row["昨收"] != "-" else 0.0,
                volume=int(row["成交量"]) if row["成交量"] != "-" else 0,
                amount=float(row["成交额"]) if row["成交额"] != "-" else 0.0,
                timestamp=datetime.now()
            )
        except Exception as e:
            print(f"获取 {symbol} 行情失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _search_stock_sync(self, keyword: str) -> List[Stock]:
        """同步搜索股票"""
        self._ensure_akshare()
        try:
            df = self._ak.stock_zh_a_spot_em()
            # 按代码或名称搜索
            matches = df[
                df["代码"].str.contains(keyword, na=False) |
                df["名称"].str.contains(keyword, na=False)
            ].head(20)

            return [
                Stock(
                    symbol=row["代码"],
                    name=row["名称"],
                    market=Market.A_SHARE
                )
                for _, row in matches.iterrows()
            ]
        except Exception as e:
            print(f"搜索股票失败: {e}")
            return []

    async def get_quote(self, symbol: str, market: Market) -> Optional[Quote]:
        """获取单个股票实时行情"""
        if market == Market.A_SHARE:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._executor,
                self._get_a_share_quote_sync,
                symbol
            )
        else:
            # TODO: 支持港股、美股
            raise NotImplementedError(f"暂不支持 {market.value} 市场")

    async def get_quotes(self, symbols: List[str], market: Market) -> List[Quote]:
        """批量获取实时行情"""
        tasks = [self.get_quote(s, market) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, Quote)]

    async def search_stock(self, keyword: str) -> List[Stock]:
        """搜索股票"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._search_stock_sync,
            keyword
        )

    async def get_stock_info(self, symbol: str, market: Market) -> Optional[Stock]:
        """获取股票基本信息"""
        quote = await self.get_quote(symbol, market)
        return quote.stock if quote else None
