"""
数据加载器 - 从多种数据源获取历史 K 线数据
灵感: qlib 的数据获取模块 + RQAlpha 的 bundle 思路

支持数据源:
  - AKShare (东方财富接口: A 股、ETF、A 股指数、港股)
  - yfinance (美股、港美指数)
  - CSV 文件 (本地历史数据)

路由: 先用 backtesting.data.symbols.resolve() 把代码统一成规范键, 再按标的种类
选择接口. 所有路由输出同一套列 (date/open/high/low/close/volume/amount, 另有
pct_change/turnover 等), 成交量一律换算成"股/份", 成交额为当地货币元
(券商的成交量占比上限按股计算).

| 标的                 | 接口                       | 复权参数             | 成交量原始单位 | 换算 | 成交额      |
|----------------------|----------------------------|----------------------|----------------|------|-------------|
| 沪深股票 SHA/SHE      | akshare.stock_zh_a_hist    | "" / qfq / hfq       | 手 (100 股)    | ×100 | 元          |
| 场内 ETF             | akshare.fund_etf_hist_em   | "" / qfq / hfq       | 手 (100 份)    | ×100 | 元          |
| A 股指数 IDX:000300  | akshare.index_zh_a_hist    | 无 (指数不复权)      | 手 (100 股)    | ×100 | 元          |
| 港股 HKG             | akshare.stock_hk_hist      | "" / qfq / hfq       | 股             | ×1   | 港元        |
| 美股 US, 港美指数    | yfinance.download          | auto_adjust; 无 hfq  | 股             | ×1   | 无, 按 volume×close 估算 |

单位核对 (2024-11-08 实时数据, 成交额 / (成交量 × 收盘价)): stock_zh_a_hist 112
(前复权价低于原始价, 约 100 即"手"), fund_etf_hist_em 106, stock_hk_hist 1.04;
index_zh_a_hist 每单位成交量约 2071 元, 即成分股均价约 20.7 元/股, 说明单位是手.

yfinance 的 end 是开区间: 请求 end + 1 天, 再丢弃区间外的行.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
import pandas as pd
from backtesting.data.quality import require_valid_daily_bars
from backtesting.data.symbols import (
    INDEX, PROVIDER_A_INDEX, PROVIDER_A_STOCK, PROVIDER_ETF, PROVIDER_HK, resolve,
)

logger = logging.getLogger(__name__)

# Eastmoney (akshare *_hist) column names -> ours. Unmapped columns are kept as is.
EM_COLUMNS = {
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


class DataLoader:
    """
    数据加载器
    
    类似 qlib 的 DataHandler 思路,统一数据获取接口.
    """

    def __init__(self, source: str = "akshare", now: Optional[Callable[[], Any]] = None):
        """
        Args:
            source: 数据源, 支持 "akshare" / "csv"
            now:    clock that stamps each fetch (attrs["fetched_at"]) and decides
                    which bars were final when fetched (default: the current time)
        """
        self.source = source
        self._now = now
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

        symbol 可以是 backtesting.data.symbols 接受的任意写法 (600519、SHE:000001、
        ETF:510300、IDX:000300、0700.HK、AAPL ...); 无法识别时抛出 SymbolError.
        返回 DataFrame, 列: date/open/high/low/close/volume/amount
        index 为 pd.DatetimeIndex; volume 单位为股 (attrs["volume_unit"]),
        attrs["source"] 为实际调用的接口, attrs["fetched_at"] 为下载时间 (ISO 8601).

        A downloaded frame is kept for the same request only when every bar in it
        was final when it was fetched: a frame with today's bar before the market's
        close is final (DataStore.last_complete_day) is fetched again next time, so a
        loader kept across the close never hands back the morning's values.
        """
        cache_key = f"{symbol}_{start_date}_{end_date}_{adjust}"
        if cache_key in self._cache:
            return self._cache[cache_key].copy()

        fetched = self._now() if self._now is not None else datetime.now().astimezone()
        if self.source == "akshare":
            df = self._fetch_routed(symbol, start_date, end_date, adjust)
        elif self.source == "csv":
            raise ValueError("CSV 模式请直接使用 DataLoader.from_csv()")
        else:
            raise ValueError(f"不支持的数据源: {self.source}")

        df = self._standardize(df)
        stamp = fetched if isinstance(fetched, datetime) else pd.Timestamp(fetched).to_pydatetime()
        df.attrs["fetched_at"] = stamp.isoformat(timespec="seconds")
        if not self._has_provisional_bars(symbol, df, fetched):
            self._cache[cache_key] = df
        return df.copy()

    @staticmethod
    def _has_provisional_bars(symbol, df: pd.DataFrame, fetched) -> bool:
        """True when `df` has a bar dated after the last session that was final at `fetched`."""
        from backtesting.data.store import DataStore
        try:
            market = resolve(symbol).market
        except ValueError:
            market = None
        if df.empty or not isinstance(df.index, pd.DatetimeIndex):
            return False
        return bool((df.index.normalize() > DataStore.last_complete_day(fetched, market)).any())

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
        loader._now = None
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

    def _fetch_routed(self, symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
        """Resolve the symbol and call the provider its instrument kind needs."""
        sym = resolve(symbol)
        if sym.kind == INDEX:
            adjust = ""  # indices have no corporate actions; the index API takes no adjust
        if sym.provider == PROVIDER_A_STOCK:
            df = self._fetch_akshare_daily(sym.provider_symbol, start_date, end_date, adjust)
        elif sym.provider == PROVIDER_ETF:
            df = self._fetch_akshare_em("fund_etf_hist_em", sym.provider_symbol, start_date, end_date,
                                        adjust=adjust, lots=True)
        elif sym.provider == PROVIDER_A_INDEX:
            df = self._fetch_akshare_em("index_zh_a_hist", sym.provider_symbol, start_date, end_date,
                                        lots=True)
        elif sym.provider == PROVIDER_HK:
            df = self._fetch_akshare_em("stock_hk_hist", sym.provider_symbol, start_date, end_date,
                                        adjust=adjust, lots=False)
        else:
            df = self._fetch_yahoo_daily(sym.provider_symbol, start_date, end_date, adjust)
        df.attrs["source"] = sym.provider
        df.attrs["ticker"] = sym.ticker
        return df

    def _fetch_yahoo_daily(
        self, symbol: str, start_date: str, end_date: str, adjust: str
    ) -> pd.DataFrame:
        """通过 yfinance 获取美股 / 港美指数日 K 线 (symbol 为 Yahoo 代码)"""
        if adjust == "hfq":
            raise ValueError("yfinance 不提供后复权 (hfq) 数据; 美股请用前复权 (qfq) 或不复权")
        try:
            import yfinance as yf

            # adjust: qfq -> auto_adjust=True (splits and dividends, anchored at the download day)
            auto_adj = (adjust != "")
            start = pd.Timestamp(start_date).normalize()
            end = pd.Timestamp(end_date).normalize()
            # yfinance's end is exclusive: ask for one more day so end_date is included.
            end_exclusive = (end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

            df = yf.download(symbol, start=start.strftime("%Y-%m-%d"), end=end_exclusive,
                             auto_adjust=auto_adj, multi_level_index=False, progress=False)
            if df is None or df.empty:
                raise ValueError(f"yfinance 没拉取到数据: {symbol}")

            df = df.reset_index()
            # 重命名列
            col_map = {
                "Date": "date",
                "Datetime": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
            df = df.rename(columns=col_map)
            dates = pd.to_datetime(df["date"])
            if getattr(dates.dt, "tz", None) is not None:
                dates = dates.dt.tz_localize(None)
            df["date"] = dates.dt.normalize()
            df = df[(df["date"] >= start) & (df["date"] <= end)].copy()
            if df.empty:
                raise ValueError(f"yfinance 在 {start_date}~{end_date} 没有 {symbol} 的数据")
            df["volume"] = pd.to_numeric(df["volume"], errors="raise").astype(float)  # shares
            # yfinance 可能没有 amount
            if "amount" not in df.columns and "volume" in df.columns and "close" in df.columns:
                df["amount"] = df["volume"] * df["close"]
            df.attrs["volume_unit"] = "shares"
            df["symbol"] = symbol
            return df

        except Exception as e:
            logger.error(f"Yahoo 获取 {symbol} 数据失败: {e}")
            raise

    def _fetch_akshare_daily(
        self, symbol: str, start_date: str, end_date: str, adjust: str
    ) -> pd.DataFrame:
        """通过 AKShare 获取 A 股股票日 K 线 (stock_zh_a_hist)"""
        # 去掉市场后缀 (600519.SH -> 600519)
        clean_symbol = symbol.split(".")[0]
        df = self._fetch_akshare_em("stock_zh_a_hist", clean_symbol, start_date, end_date,
                                    adjust=adjust, lots=True)
        df["symbol"] = clean_symbol
        return df

    def _fetch_akshare_em(
        self, function: str, symbol: str, start_date: str, end_date: str,
        adjust: Optional[str] = None, lots: bool = True,
    ) -> pd.DataFrame:
        """Call one of akshare's Eastmoney *_hist functions and normalise its frame.

        lots=True: the provider reports volume in lots of 100 shares (see the
        module table). Convert only here, at the provider boundary, never on
        cached frames.
        """
        try:
            import akshare as ak

            fetch = getattr(ak, function, None)
            if fetch is None:
                raise RuntimeError(f"当前安装的 akshare 没有 {function} 接口, 无法下载 {symbol}; 请升级 akshare")

            kwargs = dict(
                symbol=symbol,
                period="daily",
                # AKShare 日期格式: YYYYMMDD
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            if adjust is not None:
                kwargs["adjust"] = adjust
            df = fetch(**kwargs)
            if df is None or df.empty:
                raise ValueError(f"{function} 没有返回 {symbol} 在 {start_date}~{end_date} 的数据")

            df = df.rename(columns=EM_COLUMNS)
            volume = pd.to_numeric(df["volume"], errors="raise").astype(float)
            df["volume"] = volume * 100 if lots else volume
            df.attrs["volume_unit"] = "shares"
            df["symbol"] = symbol
            return df

        except Exception as e:
            logger.error(f"AKShare {function} 获取 {symbol} 数据失败: {e}")
            raise

    def _standardize(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        标准化 DataFrame:
          - date 列转为 DatetimeIndex
          - 数值列转 float
          - 拒绝乱序、重复日期及无效 OHLCV；不静默修复行情
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

        # 设置日期为 index
        df = df.set_index("date")
        require_valid_daily_bars(df)
        return df
