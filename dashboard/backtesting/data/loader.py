"""
数据加载器 - 从多种数据源获取历史 K 线数据
灵感: qlib 的数据获取模块 + RQAlpha 的 bundle 思路

支持数据源:
  - AKShare (已集成在项目中, A股免费)
  - TuShare (需要 token)
  - CSV 文件 (本地历史数据)
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import pandas as pd

logger = logging.getLogger(__name__)


class DataLoader:
    """
    数据加载器
    
    类似 qlib 的 DataHandler 思路,统一数据获取接口.
    """

    def __init__(self, source: str = "akshare"):
        """
        Args:
            source: 数据源, 支持 "akshare" / "csv"
        """
        self.source = source
        self._cache: Dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------ #
    #  公开 API
    # ------------------------------------------------------------------ #

    def get_daily(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",  # 前复权
    ) -> pd.DataFrame:
        """
        获取日 K 线数据.

        返回 DataFrame, 列: date/open/high/low/close/volume/amount
        index 为 pd.DatetimeIndex
        """
        cache_key = f"{symbol}_{start_date}_{end_date}_{adjust}"
        if cache_key in self._cache:
            return self._cache[cache_key].copy()

        if self.source == "akshare":
            if symbol.endswith(".HK") or symbol.endswith(".US") or not symbol[0].isdigit():
                # 对于非A股代码，自动回退到 yfinance 下载
                df = self._fetch_yahoo_daily(symbol, start_date, end_date, adjust)
            else:
                df = self._fetch_akshare_daily(symbol, start_date, end_date, adjust)
        elif self.source == "csv":
            raise ValueError("CSV 模式请直接使用 DataLoader.from_csv()")
        else:
            raise ValueError(f"不支持的数据源: {self.source}")

        df = self._standardize(df)
        self._cache[cache_key] = df
        return df.copy()

    @classmethod
    def from_csv(cls, filepath: str, symbol: str = "custom") -> pd.DataFrame:
        """
        从 CSV 文件加载数据.
        
        CSV 需包含列: date, open, high, low, close, volume
        """
        df = pd.read_csv(filepath, parse_dates=["date"])
        df["symbol"] = symbol
        loader = cls.__new__(cls)
        loader.source = "csv"
        loader._cache = {}
        return loader._standardize(df)

    def get_index_components(self, index_code: str = "000300") -> List[str]:
        """
        获取指数成分股列表 (沪深300 / 中证500 等).
        返回: ["600519", "000858", ...]
        """
        try:
            import akshare as ak
            if index_code == "000300":
                df = ak.index_stock_cons_weight_csindex(symbol="000300")
                return df["成分券代码"].tolist()
            elif index_code == "000905":
                df = ak.index_stock_cons_weight_csindex(symbol="000905")
                return df["成分券代码"].tolist()
            else:
                logger.warning(f"不支持的指数 {index_code}, 返回空列表")
                return []
        except Exception as e:
            logger.error(f"获取成分股失败: {e}")
            return []

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _fetch_yahoo_daily(
        self, symbol: str, start_date: str, end_date: str, adjust: str
    ) -> pd.DataFrame:
        """通过 yfinance 获取港美股日 K 线"""
        try:
            import yfinance as yf
            
            # yfinance 需要日期格式 YYYY-MM-DD
            # adjust: qfq -> auto_adjust=True
            auto_adj = (adjust != "")
            
            df = yf.download(symbol, start=start_date, end=end_date, auto_adjust=auto_adj, multi_level_index=False, progress=False)
            if df.empty:
                raise ValueError(f"yfinance 没拉取到数据: {symbol}")
                
            df = df.reset_index()
            # 重命名列
            col_map = {
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
            df = df.rename(columns=col_map)
            # yfinance 可能没有 amount 
            if "amount" not in df.columns and "volume" in df.columns and "close" in df.columns:
                df["amount"] = df["volume"] * df["close"]
                
            df["symbol"] = symbol
            return df
            
        except Exception as e:
            logger.error(f"Yahoo 获取 {symbol} 数据失败: {e}")
            raise

    def _fetch_akshare_daily(
        self, symbol: str, start_date: str, end_date: str, adjust: str
    ) -> pd.DataFrame:
        """通过 AKShare 获取 A 股日 K 线"""
        try:
            import akshare as ak

            # 去掉市场前缀 (600519.SH -> 600519)
            clean_symbol = symbol.split(".")[0]

            # AKShare 日期格式: YYYYMMDD
            start = start_date.replace("-", "")
            end = end_date.replace("-", "")

            df = ak.stock_zh_a_hist(
                symbol=clean_symbol,
                period="daily",
                start_date=start,
                end_date=end,
                adjust=adjust,
            )

            # 重命名列
            col_map = {
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
                "涨跌幅": "pct_change",
                "涨跌额": "change",
                "换手率": "turnover",
            }
            df = df.rename(columns=col_map)
            df["symbol"] = clean_symbol
            return df

        except Exception as e:
            logger.error(f"AKShare 获取 {symbol} 数据失败: {e}")
            raise

    def _standardize(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        标准化 DataFrame:
          - date 列转为 DatetimeIndex
          - 数值列转 float
          - 按日期升序排列
          - 去重
        """
        required_cols = ["date", "open", "high", "low", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"数据缺少必要列: {missing}")

        df = df.copy()

        # 日期处理
        if not pd.api.types.is_datetime64_any_dtype(df["date"]):
            df["date"] = pd.to_datetime(df["date"])

        # 数值列类型
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        if "amount" in df.columns:
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

        # 排序 + 去重
        df = df.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)

        # 设置日期为 index
        df = df.set_index("date")

        return df
