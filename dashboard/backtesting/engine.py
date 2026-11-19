"""
回测引擎 - 核心调度器
灵感: backtrader 的 Cerebro + vnpy 的事件驱动引擎

Cerebro 式的调度:
  engine.add_data(symbol, df)
  engine.add_strategy(MyStrategy)
  result = engine.run()
  engine.plot()
"""
import dataclasses
import math
import numbers
import os
import tempfile
import uuid
import logging
from typing import Callable, Optional, Type, Dict, List, Any
from datetime import datetime
import pandas as pd

from .data import store as _store_module
from .data.feed import DataFeed
from .data.loader import DataLoader
from .data.cleaner import DataCleaner
from .data.store import DataStore
from .data.quality import validate_daily_bars, require_valid_daily_bars, scan_corporate_action_gaps
from .data.repository import (
    BarRepository, BarsUnavailableError, Coverage, bars_sha256, normalize_adjust, union_calendar,
    warmup_calendar_days as _repository_warmup_days,
)
from .data.symbols import resolve
from .strategies.base import Strategy, Order, Position
from .broker.simulated import SimulatedBroker, BrokerConfig
from .analyzer.stats import BacktestStats, ANNUALIZATION_NOTE, TRADE_STATS_NOTE
from .analyzer.caveats import INFO, WARNING, build_caveats, caveat
from .broker.fees import default_schedule, fees_assumption_text
from .summary import data_caveats

logger = logging.getLogger(__name__)

# Recorded in every run manifest; bump when a change can alter results on the same bars.
ENGINE_VERSION = "2.2.0"


class MarketDataUnavailableError(BarsUnavailableError):
    """The requested range is not covered by the cache and could not be downloaded."""


class RunCancelled(RuntimeError):
    """The run was cancelled through the engine's should_stop callback."""


def _day(value) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


class _EngineLoader:
    """The loader as the engine's repositories call it.

    While add_data loads bars, the first download reports ("prepare_data",
    "download") - the repository, not the engine, decides whether one is needed -
    and a frame from the provider that fails the quality gate is remembered, so a
    run without the cache (use_cache=False) is refused with that error instead
    of a generic "unavailable". Outside add_data (e.g. the service loading an
    index through ``engine.repository``) it only passes the call on. The
    engine's own loader is looked up on every call, so a replaced ``get_daily``
    is used."""

    def __init__(self, engine: "BacktestEngine"):
        self._engine = engine

    def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
        state = self._engine._loading
        if state is not None and not state["downloaded"]:
            state["downloaded"] = True
            self._engine._report("prepare_data", "download")
        frame = self._engine._loader.get_daily(symbol, start_date, end_date, adjust)
        if state is not None:
            try:
                require_valid_daily_bars(frame, symbol=symbol, adjust=adjust)
            except ValueError as exc:
                state["invalid"] = exc
                raise
        return frame


class BacktestEngine:
    """
    回测引擎 (Cerebro)
    
    使用示例:
    --------
    engine = BacktestEngine(initial_cash=500_000)
    engine.add_data("600519", "2020-01-01", "2024-01-01")
    engine.add_strategy(SmaCrossStrategy, fast_period=10, slow_period=30)
    result = engine.run()
    print(result.summary())
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        broker_config: Optional[BrokerConfig] = None,
        use_cache: bool = True,
        *,
        loader=None,
        now: Optional[Callable[[], pd.Timestamp]] = None,
        run_id: Optional[str] = None,
        progress: Optional[Callable[[str, Any], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ):
        """
        Args:
            use_cache:   True: bars come from BarRepository (cache, then download).
                         False: downloaded through a BarRepository over a throwaway store;
                         the local cache is neither read nor written.
            loader:      object with get_daily(ticker, start, end, adjust) (default DataLoader)
            now:         clock that decides which daily bars are final: a bar dated today
                         before the exchange's close is final (data.store.bar_final_at:
                         the session calendar's close plus the publication margin) is
                         left out of the run and the cache, whether it was downloaded,
                         cached or passed in as a DataFrame (default: the current time)
            run_id:      id of this run (default: random)
            progress:    progress(stage, detail) callback: ("prepare_data", "cache" | "download"),
                         ("simulate", fraction 0..1)
            should_stop: checked before loading and on every bar; True raises RunCancelled
        """
        self.initial_cash = initial_cash
        self.broker_config = broker_config or BrokerConfig()
        self.use_cache = use_cache

        self._strategy_class: Optional[Type[Strategy]] = None
        self._strategy_params: Dict[str, Any] = {}
        self._strategy: Optional[Strategy] = None

        self._data_feeds: Dict[str, DataFeed] = {}  # symbol -> DataFeed
        self._primary_symbol: Optional[str] = None

        self.broker = SimulatedBroker(initial_cash, self.broker_config)
        self._store_instance: Optional[DataStore] = None    # the bar cache, opened on first use
        self._loader = loader if loader is not None else DataLoader(source="akshare", now=now)
        self._bar_loader = _EngineLoader(self)
        self._loading: Optional[Dict[str, Any]] = None   # set while add_data loads bars
        self._now = now
        self._repository: Optional[BarRepository] = None
        self._progress = progress
        self._should_stop = should_stop

        self._run_id = run_id or str(uuid.uuid4())[:8]
        self._run_date: Optional[str] = None
        self._data_quality_reports: Dict[str, Any] = {}
        self._warmup_info: Dict[str, Dict[str, Any]] = {}
        self._end_marks: List[Dict[str, Any]] = []
        self._stale_prices: Dict[str, Dict[str, Any]] = {}   # symbol -> days held without a bar
        self._current_day: Optional[pd.Timestamp] = None    # the day run() is simulating
        self._coverage: Dict[str, Coverage] = {}      # symbol -> what the bars are
        self._adjust: Dict[str, str] = {}
        self._kinds: Dict[str, str] = {}
        self._loaded: Dict[str, pd.DataFrame] = {}    # symbol -> the frame as handed over (copy)

    @property
    def _store(self) -> DataStore:
        """The local bar cache, opened (and created) only when a load needs it: an engine
        that never reads the cache (use_cache=False, a DataFrame) never creates it."""
        if self._store_instance is None:
            self._store_instance = DataStore()
        return self._store_instance

    @property
    def repository(self) -> BarRepository:
        """The bar repository over this engine's store and loader."""
        if self._repository is None:
            self._repository = BarRepository(store=self._store, loader=self._bar_loader, now=self._now)
        return self._repository

    @property
    def coverage(self) -> Dict[str, Coverage]:
        return dict(self._coverage)

    def _check_stop(self) -> None:
        if self._should_stop is not None and self._should_stop():
            raise RunCancelled("回测已取消")

    def _report(self, stage: str, detail: Any = None) -> None:
        if self._progress is not None:
            self._progress(stage, detail)

    # ------------------------------------------------------------------ #
    #  配置接口
    # ------------------------------------------------------------------ #

    def add_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        df: Optional[pd.DataFrame] = None,
        is_primary: bool = True,
        warmup: int = 60,
        *,
        ticker=None,
        allow_partial: bool = False,
        allow_download: bool = True,
    ):
        """
        添加标的数据.

        The requested window [start_date, end_date] is traded and measured in full:
        `warmup` bars before start_date are loaded for the indicators and are
        neither traded nor counted in the statistics. With too little history
        before the start the strategy begins once `warmup` bars exist and the
        result carries a warmup_short caveat. Without a start_date (a DataFrame
        passed as is) the first `warmup` bars of the frame are the warm-up.

        Without `df` the bars come from BarRepository under the canonical ticker
        (``symbols.resolve``; legacy cache keys are read as aliases), and the
        repository alone picks the stored series (one key, one recorded coverage
        interval). When the cache already covers [start_date, end_date] nothing
        is downloaded, not even to extend the warm-up (cached and imported data
        keep working offline): the warm-up is what that interval holds before
        the start. Earlier cached bars of another download (an adjusted-series
        seam) are never prepended: the window plus the warm-up is downloaded
        instead, and when that is not possible the warm-up stops at the start of
        the window's interval (warmup_seam and warmup_short caveats). A window
        that is not covered is downloaded with its warm-up. If that is not
        possible the run is refused, unless `allow_partial` is set: then the
        local fragment is used and the result carries an offline_cache caveat.
        The same holds for a download that leaves older adjusted rows the
        provider no longer returns among the bars (an unverified splice:
        unverified_splice caveat naming the seam) and for rows stored while
        provisional that could not be downloaded again (provisional_dropped).
        Today's bar before the exchange's close is never used (provisional_bar
        caveat), downloaded, cached or passed in as `df`. What the bars are (source, dates, hash, offline, suspected
        corporate actions) is kept as a Coverage per symbol (``coverage``).

        Args:
            symbol:         label of the symbol in this run (trades, feeds, results)
            start_date:     开始日期 "YYYY-MM-DD" ("" = 从数据第一根开始, 仅限传入 df)
            end_date:       结束日期 "YYYY-MM-DD" ("" = 到数据最后一根, 仅限传入 df)
            adjust:         复权方式 "qfq"/"hfq"/"" ("none" = "")
            df:             直接传入 DataFrame (跳过数据下载); 早于 start_date 的行用作预热
            is_primary:     是否为主标的
            warmup:         预热期 bar 数 (非负整数)
            ticker:         what to load (any form symbols.resolve accepts, or a Symbol);
                            default: `symbol`
            allow_partial:  run on the local fragment when the download fails
            allow_download: False: never download (demo, reruns that must not refresh)
        """
        if isinstance(warmup, bool) or not isinstance(warmup, numbers.Integral) or warmup < 0:
            # A negative value would slice the benchmark from the end (iloc[-n:]).
            raise ValueError(f"warmup must be a nonnegative integer, got {warmup!r}")
        warmup = int(warmup)
        adjust = normalize_adjust(adjust)
        self._check_stop()
        provisional: List[str] = []
        if df is None:
            df, coverage = self._load_bars(symbol, ticker if ticker is not None else symbol,
                                           start_date, end_date, adjust, warmup,
                                           allow_partial=allow_partial, allow_download=allow_download)
        else:
            coverage = None
            df, provisional = self._without_provisional_bars(symbol, ticker, df)

        df, warmup_info = self._trim_to_window(df, start_date, end_date, warmup)
        if coverage is None:
            coverage = self._frame_coverage(symbol, df, start_date, end_date, adjust, warmup_info, provisional)

        quality_report = validate_daily_bars(df, symbol=symbol, adjust=adjust)
        self._data_quality_reports[symbol] = quality_report
        quality_report.raise_if_failed()
        for warning in quality_report.warnings:
            logger.warning("[Engine] 数据质量警告 %s: %s", symbol, warning)
        # Derived columns are computed here, on the exact bars the run uses, whether
        # they were downloaded, cached or passed in: same bars -> same features.
        df = self.with_features(df)

        feed = DataFeed(symbol, df, warmup=warmup)
        self._data_feeds[symbol] = feed
        self._loaded[symbol] = df.copy(deep=True)
        self._warmup_info[symbol] = warmup_info
        self._coverage[symbol] = coverage
        self._adjust[symbol] = adjust
        self._kinds[symbol] = self._kind_of(ticker if ticker is not None else symbol)
        # Limits are compared on the same series: as a ratio when prices are adjusted.
        self.broker.set_price_basis(symbol, "adjusted" if adjust in ("qfq", "hfq") else "raw")

        if is_primary or self._primary_symbol is None:
            self._primary_symbol = symbol

        logger.info(f"[Engine] 添加数据: {symbol} ({len(df)} 个交易日, 其中预热 {warmup_info['available']} 根)")

    def loaded_frame(self, symbol: str) -> pd.DataFrame:
        """A copy of the frame add_data handed to `symbol`'s feed: the bars the run
        uses (warm-up included) plus the engine's derived features, as they were
        before any strategy ran. Whatever a strategy does to its feed's frame
        (add_factor, overwriting a column) never reaches it. A new copy each call;
        KeyError for a symbol that was not added."""
        return self._loaded[symbol].copy(deep=True)

    @staticmethod
    def warmup_calendar_days(warmup: int) -> int:
        """Calendar days loaded before the start to hold `warmup` trading days
        (the repository's rule; it reaches further back when they are not enough)."""
        return _repository_warmup_days(warmup)

    def _load_bars(self, symbol: str, ticker, start_date: str, end_date: str, adjust: str, warmup: int,
                   *, allow_partial: bool, allow_download: bool):
        """(bars from the warm-up through end_date, Coverage), always through a
        BarRepository: its validation, warm-up and provisional-bar rules apply to
        every run (today's bar before the close is never simulated or cached)."""
        if not start_date or not end_date:
            raise ValueError("加载行情需要开始和结束日期 (YYYY-MM-DD)")
        sym = resolve(ticker)
        self._loading = {"invalid": None, "downloaded": False}
        try:
            if not self.use_cache:
                bars, coverage = self._load_direct(sym, start_date, end_date, adjust, warmup,
                                                   allow_download=allow_download)
            else:
                bars, coverage = self._load_cached(sym, start_date, end_date, adjust, warmup,
                                                   allow_partial=allow_partial, allow_download=allow_download)
        finally:
            self._loading = None
        if not allow_partial:
            self._refuse_incomplete(sym, adjust, coverage)
        return bars, coverage

    @staticmethod
    def _refuse_incomplete(sym, adjust: str, coverage: Coverage) -> None:
        """Without allow_partial, bars the repository marked incomplete are not run on
        (offline bars are refused in _load_cached): an unverified splice of two
        downloads (their adjustment anchors could not be compared) anywhere in the
        bars, or provisional rows dropped inside the window."""
        label = f"{sym.ticker} ({adjust or '不复权'})"
        if coverage.unverified:
            raise MarketDataUnavailableError(
                f"{label} 本地缓存里 {', '.join(coverage.unverified[:5])} 的 K 线来自更早的一次下载, 本次下载没有这些"
                f"日期, 无法核对复权锚点; 数据在 {', '.join(coverage.splice_seams[:5])} 处拼接了两次下载, 锚点不同就会"
                f"出现假的涨跌. 未用未经核对的拼接数据运行; 请重新下载完整区间 (或允许使用不完整数据)."
            )
        start, end = coverage.requested_start, coverage.requested_end
        missing = [day for day in coverage.dropped_provisional if start <= day <= end]
        if missing:
            raise MarketDataUnavailableError(
                f"{label} {', '.join(missing[:5])} 的 K 线是当天收盘前取得或存入的临时数据, 不是当天最终的 K 线, "
                f"也没能重新下载; 未用不完整的数据运行. 请联网重试.")

    def _load_cached(self, sym, start_date: str, end_date: str, adjust: str, warmup: int,
                     *, allow_partial: bool, allow_download: bool):
        """Through the engine's BarRepository. The repository alone decides which stored
        series serves the window and its warm-up (one key, one coverage interval: a
        cached window is not re-downloaded for the warm-up, and warm-up bars from
        another download are never prepended; see data/repository.py), and the
        Coverage it returns describes exactly those bars. Partial bars (offline) are
        refused unless allow_partial."""
        try:
            bars, coverage = self.repository.load(sym, start_date, end_date, adjust, warmup_bars=warmup,
                                                  allow_download=allow_download)
        except BarsUnavailableError as exc:
            raise MarketDataUnavailableError(str(exc)) from exc
        if not self._loading["downloaded"]:
            self._report("prepare_data", "cache")
        if coverage.offline and not allow_partial:
            raise MarketDataUnavailableError(self._uncovered_message(sym, start_date, end_date, adjust, coverage))
        logger.info("[Engine] %s %s~%s: %s, %d 根 (source %s)", sym.ticker, coverage.first, coverage.last,
                    "缓存" if coverage.cached else "下载", coverage.rows, coverage.source)
        return bars, coverage

    def _load_direct(self, sym, start_date: str, end_date: str, adjust: str, warmup: int, *, allow_download: bool):
        """use_cache=False: the same BarRepository load over a throwaway store, so the
        local cache is neither read nor written."""
        state = self._loading
        with tempfile.TemporaryDirectory(prefix="backtest-bars-") as folder:
            scratch = BarRepository(store=_store_module.DataStore(os.path.join(folder, "bars.sqlite")),
                                    loader=self._bar_loader, now=self._now)
            try:
                bars, coverage = scratch.load(sym, start_date, end_date, adjust, warmup_bars=warmup,
                                              allow_download=allow_download)
            except BarsUnavailableError as exc:
                if state is not None and state["invalid"] is not None:
                    raise state["invalid"]          # the provider's bars failed the quality gate
                raise MarketDataUnavailableError(f"{exc} (未启用本地缓存)") from exc
        logger.info("[Engine] %s %s~%s: 下载 (不写缓存), %d 根 (source %s)", sym.ticker, coverage.first,
                    coverage.last, coverage.rows, coverage.source)
        return bars, dataclasses.replace(coverage, store_key="", cached=False)

    def _without_provisional_bars(self, symbol: str, ticker, df: pd.DataFrame):
        """(df without the bars that are not final at the engine's clock, their dates).

        The rule every other path follows (data.store.bar_final_at): a bar dated
        today before the market's close is final - in the exchange's own time zone,
        whatever time of day the row is stamped with - is provisional and never
        simulated. A frame passed in has no fetch time, so it is judged now."""
        if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
            return df, []
        now = self._now() if self._now is not None else None
        final_day = _store_module.DataStore.last_complete_day(now, self._market_of(symbol, ticker))
        late = df.index.normalize() > final_day
        if not late.any():
            return df, []
        dates = sorted({_day(day) for day in df.index[late]})
        logger.info("[Engine] %s: 去掉了 %s 的临时 K 线 (收盘前, 不是当天最终数据)", symbol, ", ".join(dates))
        return df[~late], dates

    def _market_of(self, symbol: str, ticker) -> Optional[str]:
        """CN / HK / US of the instrument (the configured market for a label that does
        not resolve), None when unknown."""
        try:
            return resolve(ticker if ticker is not None else symbol).market
        except ValueError:
            pass
        try:
            return {"a_share": "CN", "hk": "HK", "us": "US"}.get(self.broker.config.market_for(symbol))
        except ValueError:
            return None

    def _frame_coverage(self, symbol: str, df: pd.DataFrame, start_date: str, end_date: str,
                        adjust: str, warmup_info: Dict[str, Any], provisional: Optional[List[str]] = None) -> Coverage:
        """Coverage of a DataFrame passed in (source "dataframe"); `provisional`: dates
        of bars left out because they were not final yet."""
        dated = isinstance(df.index, pd.DatetimeIndex) and len(df)
        suspected = []
        if adjust == "" and dated:
            try:
                suspected = [e.date for e in scan_corporate_action_gaps(df, symbol=symbol)]
            except Exception:       # an unknown label (e.g. "TEST") is scanned without a board
                suspected = [e.date for e in scan_corporate_action_gaps(df)]
        return Coverage(
            ticker=str(symbol), adjust=adjust, source="dataframe",
            requested_start=start_date or (_day(df.index[0]) if dated else ""),
            requested_end=end_date or (_day(df.index[-1]) if dated else ""),
            first=_day(df.index[0]) if dated else None, last=_day(df.index[-1]) if dated else None,
            rows=len(df), complete=True, offline=False, fetched_at=None,
            sha256=bars_sha256(df) if dated else "", warmup_bars=warmup_info.get("bars", 0),
            warmup_rows=warmup_info.get("available", 0), suspected_corporate_actions=suspected,
            provisional_last_bar=bool(provisional),
            warnings=[f"去掉了 {', '.join(provisional)} 的 K 线: 收盘价确定之前的临时数据, 不是当天最终的 K 线"]
            if provisional else [],
        )

    @staticmethod
    def _kind_of(ticker) -> str:
        try:
            return resolve(ticker).kind
        except ValueError:
            return "stock"

    @staticmethod
    def with_features(df: pd.DataFrame) -> pd.DataFrame:
        """Add the derived feature columns (returns, prev_close, change, ma_*, ema_*,
        macd_*, rsi_14, hist_vol_20, bb_*_20, atr_14, volume/amount means, vwap).

        Columns the frame already has are kept as they are. Every feature is a
        rolling or shifted value, so a bar's features only use that bar and
        earlier ones.
        """
        if df.empty:
            return df
        computed = DataCleaner(df).add_returns().add_derived_features().add_feature_cache().result
        out = df.copy()
        for column in computed.columns:
            if column not in out.columns:
                out[column] = computed[column]
        return out

    @staticmethod
    def _trim_to_window(df: pd.DataFrame, start_date: str, end_date: str, warmup: int):
        """Keep at most `warmup` bars before start_date (all of them when there are
        fewer), then the window up to end_date."""
        if df.empty or not isinstance(df.index, pd.DatetimeIndex):
            # Nothing to trim; validate_daily_bars reports a missing date index.
            return df, {"bars": warmup, "available": 0, "requested_start": start_date or None, "first_date": None}
        if end_date:
            df = df[df.index.normalize() <= pd.Timestamp(end_date).normalize()]
        if start_date:
            start = pd.Timestamp(start_date).normalize()
            before = df[df.index.normalize() < start]
            # max(.., 0): with fewer bars than `warmup`, len - warmup is negative and
            # iloc[-k:] would keep only the last k of them.
            kept = before.iloc[max(len(before) - warmup, 0):] if warmup > 0 else before.iloc[0:0]
            df = pd.concat([kept, df[df.index.normalize() >= start]])
            available = len(kept)
        else:
            available = min(warmup, len(df))
        first = df.index[warmup] if len(df) > warmup else None
        info = {
            "bars": warmup,
            "available": available,
            "requested_start": start_date or None,
            "first_date": first.strftime("%Y-%m-%d") if first is not None else None,
        }
        return df, info

    def _uncovered_message(self, sym, start_date, end_date, adjust, coverage: Coverage) -> str:
        recorded = []
        for key in self._store.stored_symbols(sym.store_keys):
            try:
                recorded += [f"{start}~{end}" for start, end, _ in self._store.get_coverage(key, adjust)]
            except Exception:
                pass
        reasons = [w.split("; ", 1)[1] for w in coverage.warnings if w.startswith("离线") and "; " in w]
        return (
            f"{sym.ticker} ({adjust or '不复权'}) {start_date}~{end_date} 的行情不在本地缓存已记录的完整区间内 "
            f"(已记录: {', '.join(recorded) or '无'}; 本地只有 {coverage.first}~{coverage.last}), "
            f"{reasons[0] if reasons else '未能下载'}. "
            f"未使用不完整的缓存数据运行; 请联网重试, 或导入覆盖该区间的 CSV."
        )

    def add_strategy(
        self,
        strategy_class: Type[Strategy],
        **params,
    ):
        """
        添加策略.
        
        Args:
            strategy_class: 策略类
            **params:       覆盖策略默认参数
        """
        self._strategy_class = strategy_class
        self._strategy_params = params

    # ------------------------------------------------------------------ #
    #  下单接口 (由策略调用)
    # ------------------------------------------------------------------ #

    def submit_order(
        self,
        strategy: Strategy,
        symbol: str,
        direction: str,
        quantity: Optional[float],
        price: float,
        order_type: str,
        pct_cash: Optional[float] = None,
    ) -> Optional[Order]:
        """处理策略下单请求"""

        feed = self._data_feeds.get(symbol)
        if feed is None or feed.current_bar is None:
            return None

        # Sized at the symbol's last close (on a day it has no bar, the close before).
        current_price = feed.current_bar.close
        signal_day = self._signal_day(feed)

        # 计算数量
        if quantity is None and pct_cash is not None and direction == "buy":
            available = self.broker.cash * pct_cash
            # Whole lots whose cost plus commission, taxes and levies fit in the budget.
            quantity = self.broker.affordable_quantity(symbol, current_price, available, signal_day)
            if quantity <= 0:
                lot = self.broker.config.lot_for(symbol)
                lot_cost = self.broker.lot_cost(symbol, current_price, signal_day)
                logger.warning(f"[Engine] 资金不足以买入一手 {symbol}: 一手 {lot} 股约 {lot_cost:.0f}, 可用 {available:.0f}")
                self.broker.log_unfilled(
                    symbol=symbol, direction="buy", order_type=order_type, quantity=lot,
                    reason="lot_unaffordable" if available > 0 else "insufficient_cash",
                    signal_date=signal_day, outcome="not_submitted",
                    lot=lot, price=round(current_price, 4), lot_cost=round(lot_cost, 2), budget=round(available, 2),
                )
                return None
        elif quantity is None:
            # 卖出时, 默认全部持仓
            pos = self.broker.get_position(symbol)
            quantity = pos.quantity if pos else 0

        if quantity <= 0:
            return None

        order = Order(
            order_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            direction=direction,
            order_type=order_type,
            quantity=quantity,
            price=price,
            timestamp=signal_day,
        )

        submitted = self.broker.submit_order(order)
        strategy.on_order(submitted)
        return submitted

    def _note_stale_prices(self, day, moved: List[str], last_bars: Dict[str, Any]) -> None:
        """Count a day on which a held symbol has no bar and is valued at its last close."""
        for sym, pos in self.broker.positions.items():
            if pos.quantity > 0 and sym not in moved and sym in last_bars:
                mark = self._stale_prices.setdefault(sym, {"symbol": sym, "days": 0, "first": None, "last": None})
                mark["days"] += 1
                mark["first"] = mark["first"] or _day(day)
                mark["last"] = _day(day)

    def _signal_day(self, feed: DataFeed):
        """The day an order is sent: the day being simulated (a symbol without a bar
        that day still has yesterday's bar as its current one)."""
        return self._current_day if self._current_day is not None else feed.current_bar.date

    def get_position(self, symbol: str) -> Optional[Position]:
        return self.broker.get_position(symbol)

    def submit_exit(self, strategy: Strategy, symbol: str, kind: str, price: float,
                    quantity: Optional[float] = None) -> Optional[Order]:
        """Place a stop or target sell for `symbol`, replacing the working one of that kind."""
        feed = self._data_feeds.get(symbol)
        if feed is None or feed.current_bar is None:
            return None
        for old in self.broker.pending_orders(symbol):
            if old.order_type == kind:
                old.reason = "replaced"
                if self.broker.cancel_order(old):
                    strategy.on_order(old)
        order = Order(
            order_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            direction="sell",
            order_type=kind,
            quantity=float(quantity) if quantity is not None else 0.0,
            price=float(price),
            timestamp=self._signal_day(feed),
            follow_position=quantity is None,
        )
        submitted = self.broker.submit_order(order)
        strategy.on_order(submitted)
        return submitted

    def get_pending_orders(self, symbol: Optional[str] = None) -> List[Order]:
        return self.broker.pending_orders(symbol)

    def cancel_order(self, order: Order) -> bool:
        return self.broker.cancel_order(order)

    # ------------------------------------------------------------------ #
    #  运行引擎
    # ------------------------------------------------------------------ #

    def run(self) -> "BacktestResult":
        """
        执行回测. 返回 BacktestResult 对象.

        The run walks the trading days of all loaded symbols together (the union
        of their dates). Order of events inside one day T:

          1. Bars: every symbol with a bar dated T moves to it; the others keep
             their last bar.
          2. Matching, for every symbol, before any callback: orders working
             before T are matched against T's bar (SimulatedBroker.process_bars:
             market orders at the open, gaps through stops, then stops, limits
             and targets inside the bar). The opening fills of every symbol come
             before any symbol's fills inside the bar, each in the order the
             orders were sent, whatever the order the data was added in: cash
             from a sale inside the bar never pays for a purchase at that
             morning's open. A symbol without a bar on T while its
             market traded is suspended: its market and limit orders count one
             bar without a fill (stops and targets wait). On a day its market
             was closed (only other markets traded) its orders just wait.
          3. Valuation at T's close; a held symbol without a bar on T is valued
             at its last close (counted in ``stale_valuations``, with a caveat).
          4. Callbacks: on_order / on_trade for everything step 2 changed,
             symbol by symbol in the order the data was added. They run at T's
             close, after all of T's matching: an order or a cancellation sent
             from a callback takes effect from that symbol's next bar and can
             never use any price of bar T, whatever symbol it is for.
          5. Equity is recorded for T once the primary symbol's warm-up is over.
          6. on_bar(), only on a day the primary symbol has a bar (after its
             warm-up). On a day it has none (suspended, or its market closed
             while another symbol's traded) on_bar is not called - the primary
             data has nothing new, and a strategy would read yesterday's bar
             again - but steps 2-5 run for every symbol. Orders sent from
             on_bar are matched from the next bar on.

        After the last day, orders still working are logged as open_at_end, and
        each open position is valued at its own last close (end_mark; blocked
        "suspended" when its market traded after that bar, "limit_down" on a
        one-price limit-down board, "t_plus_one" for A-shares bought that day,
        with blocked_quantity the shares the reason applies to).
        """
        if self._strategy_class is None:
            raise ValueError("请先调用 add_strategy() 添加策略")
        if not self._data_feeds:
            raise ValueError("请先调用 add_data() 添加数据")

        self._run_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[Engine] 开始回测 run_id={self._run_id}")

        # 初始化策略
        self._strategy = self._strategy_class()
        self._strategy.parameters = {
            **self._strategy.parameters,
            **self._strategy_params,
        }
        self._strategy._set_engine(self)
        primary_feed = self._data_feeds[self._primary_symbol]
        self._strategy._set_data(primary_feed, self._data_feeds)
        self._strategy.on_init()
        self._strategy.on_start()

        # 重置所有 feed
        for feed in self._data_feeds.values():
            feed.reset()

        # Snapshot existing feed state; an engine instance represents one run.
        if self.broker.trades or self.broker.equity_curve:
            raise ValueError("Create a new engine for each independent run")

        feeds = self._data_feeds
        dates = {sym: pd.DatetimeIndex(feed._df.index).normalize() for sym, feed in feeds.items()}
        markets = {sym: self.broker.config.market_for(sym) for sym in feeds}
        calendar = union_calendar({sym: feed._df for sym, feed in feeds.items()})
        last_bars: Dict[str, Any] = {}
        market_days: Dict[str, pd.Timestamp] = {}      # market -> the last day it traded
        total_days = max(len(calendar), 1)
        for count, day in enumerate(calendar):
            self._check_stop()
            if self._progress is not None and count % 50 == 0:
                self._report("simulate", round(count / total_days, 3))
            self._current_day = day

            # 1. Bars of day T.
            moved = []
            for sym, feed in feeds.items():
                following = feed.current_index + 1
                if following < feed.total_bars and dates[sym][following] == day:
                    feed.advance()
                    last_bars[sym] = feed.current_bar
                    moved.append(sym)
            open_markets = {markets[sym] for sym in moved}
            market_days.update((market, day) for market in open_markets)

            # 2. Matching for every symbol (yesterday's signals, today's prices): the
            #    opening fills of all symbols, then the fills inside the bar.
            matched = self.broker.process_bars({sym: last_bars[sym] for sym in moved})
            events = []
            for sym in feeds:
                if sym in moved:
                    trades, changed = matched[sym]
                elif markets[sym] in open_markets:
                    trades, changed = [], self.broker.process_missing_bar(sym, day)
                else:
                    continue
                events.append((changed, trades))

            # 3. Valuation at the close (a symbol without a bar today: its last close).
            prices = {sym: bar.close for sym, bar in last_bars.items()}
            for sym, price in prices.items():
                self.broker.update_positions_price(sym, price)

            # 4. Callbacks, after all of T's matching.
            for changed, trades in events:
                for order in changed:
                    self._strategy.on_order(order)
                for trade in trades:
                    self._strategy.on_trade(trade)

            # 预热期只用于指标: 不交易, 也不记录净值 (统计从第一根可交易 bar 起)
            if not primary_feed.is_warmup:
                self._note_stale_prices(day, moved, last_bars)
                self.broker.record_equity(day, prices)            # 5.
                if self._primary_symbol in moved:
                    self._strategy.on_bar()                       # 6.
        self._current_day = None

        # 结束: 按最后一根K线收盘价强制平仓，便于得到完整已实现收益
        final_prices: Dict[str, float] = {}
        final_day = calendar[-1] if len(calendar) else None
        # Orders still working when the data ends are logged (open_at_end), then dropped.
        for order in self.broker.close_pending(final_day):
            self._strategy.on_order(order)
        for sym, feed in feeds.items():
            pos = self.broker.get_position(sym)
            last_bar = last_bars.get(sym)
            if pos and pos.quantity > 0 and last_bar is not None:
                # The valuation is flagged (end_mark): no slippage, and on a one-price
                # limit-down board, or when its market traded after its last bar
                # (suspended), the position could not really be sold; nor could
                # A-shares bought that same day (t_plus_one, blocked_quantity of them).
                # A market that was simply closed on the run's last days is not a
                # suspension.
                own_day = pd.Timestamp(last_bar.date).normalize()
                blocked = ("suspended" if market_days.get(markets[sym], own_day) > own_day
                           else self.broker.sell_block_reason(sym, last_bar))
                blocked_quantity = pos.quantity if blocked else 0.0
                locked = self.broker.t_plus_one_locked(sym, last_bar.date) if blocked is None else 0.0
                if locked > 0:
                    blocked, blocked_quantity = "t_plus_one", locked
                trade = self.broker.force_close(sym, last_bar.close, last_bar.date)
                if trade:
                    self._strategy.on_trade(trade)
                    final_prices[sym] = last_bar.close
                    self._end_marks.append({
                        "symbol": sym,
                        "date": pd.Timestamp(last_bar.date).strftime("%Y-%m-%d"),
                        "quantity": trade.quantity,
                        "price": round(trade.price, 4),
                        "pnl": round(trade.pnl, 2),
                        # suspended / limit_down / t_plus_one / None, and how many of the
                        # shares that reason kept from being sold on that day
                        "blocked": blocked,
                        "blocked_quantity": blocked_quantity,
                    })
        if final_prices and final_day is not None:
            self.broker.record_equity(final_day, {sym: bar.close for sym, bar in last_bars.items()})
        bar_count = len(calendar)

        self._strategy.on_stop()
        logger.info(
            f"[Engine] 回测完成 共 {bar_count} bars, "
            f"成交 {len(self.broker.trades)} 笔"
        )

        return BacktestResult(
            run_id=self._run_id,
            run_date=self._run_date,
            strategy_name=self._strategy.name,
            strategy_params=self._strategy.parameters,
            primary_symbol=self._primary_symbol,
            broker=self.broker,
            strategy=self._strategy,
            data_feeds=self._data_feeds,
            data_quality_reports=self._data_quality_reports,
            warmup_info=self._warmup_info,
            end_marks=self._end_marks,
            coverage=self._coverage,
            adjust=self._adjust,
            kinds=self._kinds,
            stale_valuations=list(self._stale_prices.values()),
        )


class BacktestResult:
    """
    回测结果容器
    
    提供:
      - summary()   文字摘要
      - stats       BacktestStats 详细统计
      - trades      成交记录列表
      - equity_df   净值曲线 DataFrame
    """

    def __init__(
        self,
        run_id: str,
        run_date: str,
        strategy_name: str,
        strategy_params: Dict,
        primary_symbol: str,
        broker: SimulatedBroker,
        strategy: Strategy,
        data_feeds: Dict[str, DataFeed],
        data_quality_reports: Optional[Dict[str, Any]] = None,
        warmup_info: Optional[Dict[str, Dict[str, Any]]] = None,
        end_marks: Optional[List[Dict[str, Any]]] = None,
        coverage: Optional[Dict[str, Coverage]] = None,
        adjust: Optional[Dict[str, str]] = None,
        kinds: Optional[Dict[str, str]] = None,
        stale_valuations: Optional[List[Dict[str, Any]]] = None,
    ):
        self.run_id = run_id
        self.run_date = run_date
        self.strategy_name = strategy_name
        self.strategy_params = strategy_params
        self.primary_symbol = primary_symbol
        self._broker = broker
        self._strategy = strategy
        self._data_feeds = data_feeds
        self.data_quality_reports = data_quality_reports or {}
        self.warmup_info = warmup_info or {}
        self.end_marks = end_marks or []
        self.coverage = dict(coverage or {})
        self.adjust = dict(adjust or {})
        self.kinds = dict(kinds or {})
        # Held symbols valued at an older close on days they had no bar:
        # [{"symbol", "days", "first", "last"}]
        self.stale_valuations = [dict(item) for item in stale_valuations or []]

        # 延迟计算统计
        self._stats: Optional[BacktestStats] = None
        self._caveats: Optional[List[Dict[str, str]]] = None

    @property
    def stats(self) -> BacktestStats:
        if self._stats is None:
            # 提取基准价格（主标的收盘价序列 = buy-and-hold）
            benchmark_prices = None
            if self.primary_symbol in self._data_feeds:
                feed = self._data_feeds[self.primary_symbol]
                if hasattr(feed, '_df') and 'close' in feed._df.columns:
                    # 预热期内策略不会被调用, 基准从第一根可交易 bar 起算,
                    # 否则预热期的涨跌会被算进超额收益
                    benchmark_prices = feed._df['close'].iloc[feed.warmup:].tolist()
            self._stats = BacktestStats(
                equity_curve=self._broker.equity_curve,
                trades=self._broker.trades,
                initial_cash=self._broker.initial_cash,
                benchmark_prices=benchmark_prices,
            )
        return self._stats

    @property
    def equity_df(self) -> pd.DataFrame:
        """净值曲线 DataFrame"""
        if not self._broker.equity_curve:
            return pd.DataFrame()
        df = pd.DataFrame(
            self._broker.equity_curve,
            columns=["date", "total_value", "cash", "market_value"],
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df["net_value"] = df["total_value"] / self._broker.initial_cash
        df["drawdown"] = df["net_value"] / df["net_value"].cummax() - 1
        return df

    @property
    def trades(self):
        return self._broker.trades

    @property
    def round_trips(self) -> List[Dict[str, Any]]:
        """Completed and end-marked round trips (flat -> position -> flat)."""
        return self.stats.round_trips

    @property
    def end_mark(self) -> Optional[Dict[str, Any]]:
        """Positions still open at the end, valued at the last close (not a real sale)."""
        if not self.end_marks:
            return None
        return {
            "date": max(mark["date"] for mark in self.end_marks),
            "pnl": round(sum(mark["pnl"] for mark in self.end_marks), 2),
            "positions": [dict(mark) for mark in self.end_marks],
        }

    @property
    def period(self) -> Dict[str, Any]:
        """The dates the equity curve and every statistic cover."""
        curve = self._broker.equity_curve
        if not curve:
            return {"first_date": None, "last_date": None, "calendar_days": 0, "bars": 0}
        first, last = pd.Timestamp(curve[0][0]), pd.Timestamp(curve[-1][0])
        return {"first_date": first.strftime("%Y-%m-%d"), "last_date": last.strftime("%Y-%m-%d"),
                "calendar_days": int((last - first).days), "bars": len(curve)}

    @property
    def unfilled(self) -> List[Dict[str, Any]]:
        """Signals that did not fill in full, with reason and outcome."""
        return self._broker.unfilled

    @property
    def caveats(self) -> List[Dict[str, str]]:
        """Rule-generated warnings about how far the numbers can be trusted."""
        if self._caveats is None:
            extra = []
            # What each symbol's bars are (an index is always unadjusted, and named by
            # its IDX: ticker so that it gets the index wording).
            adjustments = {}
            for symbol, coverage in self.coverage.items():
                index = self.kinds.get(symbol) == "index"
                adjustments[coverage.ticker if index else symbol] = coverage.adjust
                # build_caveats states unadjusted prices itself, as a warning (adjustments=).
                extra += [item for item in data_caveats(coverage.to_dict(), adjust=coverage.adjust,
                                                        kind=self.kinds.get(symbol, "stock"))
                          if item["code"] != "unadjusted_prices"]
                extra += self._loading_caveats(symbol, coverage)
            extra += self._stale_caveats()
            self._caveats = build_caveats(
                unfilled=self.unfilled,
                symbols=list(self._data_feeds),
                config=self._broker.config,
                warmup_info=self.warmup_info,
                bar_counts={symbol: feed.total_bars for symbol, feed in self._data_feeds.items()},
                end_mark=self.end_mark,
                closed_trades=self.stats.closed_trades,
                fee_assumptions=self._broker.fee_assumptions,
                extra=extra,
                adjustments=adjustments,
                single_entry=bool(getattr(self._strategy, "single_entry", False)),
            )
        return self._caveats

    @staticmethod
    def _loading_caveats(symbol: str, coverage: Coverage) -> List[Dict[str, str]]:
        """Caveats about how one symbol's bars were loaded (codes: provisional_bar,
        provisional_dropped, unverified_splice, warmup_seam, data_ends_early)."""
        out = []
        if getattr(coverage, "ends_early", False):
            out.append(caveat(
                "data_ends_early", WARNING,
                f"{symbol}：行情只到 {coverage.last or '（无）'}，而 {coverage.last_session} 已经收盘，取到的数据里"
                f"没有这一天的日 K 线（数据源可能还没发布，也可能停牌）；结果截至 {coverage.last or '（无）'}，"
                f"不含之后的交易日，稍后再运行会重新下载。",
            ))
        if coverage.unverified:
            out.append(caveat(
                "unverified_splice", WARNING,
                f"{symbol}：{'、'.join(coverage.unverified[:5])} 的 K 线来自本地缓存里更早的一次下载，本次下载没有这些"
                f"日期，无法核对复权锚点；数据在 {'、'.join(coverage.splice_seams[:5])} 处由两次下载拼接而成，锚点不同"
                f"就会在拼接处出现假的涨跌，结果可能失真。",
            ))
        if coverage.dropped_provisional:
            out.append(caveat(
                "provisional_dropped", WARNING,
                f"{symbol}：{'、'.join(coverage.dropped_provisional[:5])} 的 K 线是当天收盘前取得或存入的临时数据，"
                f"不是当天最终的 K 线，没有用于回测，也没能重新下载；这些交易日没有行情，结果按缺少这些天计算。",
            ))
        if coverage.warmup_seam:
            out.append(caveat(
                "warmup_seam", WARNING,
                f"{symbol}：本地缓存里 {coverage.warmup_seam} 之前的 K 线来自另一次下载，没有和回测区间所在的那段数据"
                f"核对过（复权锚点可能不同，中间也可能缺 K 线），拼在一起可能出现假的涨跌，所以没有用作预热，"
                f"也没能重新下载连贯的历史；预热期只用了 {coverage.warmup_seam} 起的数据。",
            ))
        if coverage.provisional_last_bar:
            out.append(caveat(
                "provisional_bar", INFO,
                f"{symbol}：请求区间包含今天，今天的日 K 线要等收盘后才是最终数据，没有用于回测，也没有存入缓存；"
                f"结果截至最后一个已收盘的交易日 {coverage.last}。",
            ))
        return out

    def _stale_caveats(self) -> List[Dict[str, str]]:
        """stale_price: a held symbol valued at an older close on days it had no bar."""
        return [caveat(
            "stale_price", INFO,
            f"{item['symbol']} 有 {item['days']} 个交易日（{item['first']}~{item['last']}）仍持有但没有当日行情"
            f"（停牌、休市或数据缺失）：这些天按它此前最后一个收盘价估值，净值没有反映它在这些天的涨跌。",
        ) for item in self.stale_valuations]

    @property
    def fees_total(self) -> float:
        """Every commission, tax and levy paid, end mark included (trade currency)."""
        return round(sum(float(trade.commission) for trade in self.trades), 2)

    def summary(self) -> str:
        """生成文字摘要 (a statistic without a value is shown as 不可用, never 0)"""
        s = self.stats

        def shown(name, spec, unit=""):
            value = getattr(s, name)
            if name in s.undefined or value is None or not math.isfinite(value):
                return f"{'不可用':>11}"
            return format(value, spec) + unit

        lines = [
            f"{'='*60}",
            f"  回测结果摘要  [{self.run_date}]",
            f"{'='*60}",
            f"策略:     {self.strategy_name}",
            f"标的:     {self.primary_symbol}",
            f"参数:     {self.strategy_params}",
            f"区间:     {self.period['first_date']} ~ {self.period['last_date']} ({self.period['bars']} 个交易日)",
            f"{'─'*60}",
            f"初始资金: {self._broker.initial_cash:>15,.0f} 元",
            f"最终净值: {s.final_value:>15,.0f} 元",
            f"总收益率: {s.total_return:>14.2%}",
            f"年化收益: {shown('annualized_return', '>14.2%')}  (按日历天数)",
            f"基准收益: {shown('benchmark_return', '>14.2%')}  (持有不动)",
            f"超额收益: {shown('alpha', '>14.2%')}",
            f"{'─'*60}",
            f"夏普比率: {shown('sharpe_ratio', '>14.4f')}  (少于 60 个交易日不计算)",
            f"索提诺率: {shown('sortino_ratio', '>14.4f')}",
            f"卡玛比率: {shown('calmar_ratio', '>14.4f')}",
            f"最大回撤: {shown('max_drawdown', '>14.2%')}",
            f"{'─'*60}",
            f"交易次数: {s.total_trades:>14d}  (回合; 完整 {s.closed_trades} 笔)",
            f"胜率:     {shown('win_rate', '>14.2%')}  (完整交易, 持平不计)",
            f"盈亏比:   {shown('payoff_ratio', '>14.4f')}  (平均盈利/平均亏损)",
            f"利润因子: {shown('profit_factor', '>14.4f')}  (总盈利/总亏损)",
            f"平均盈利: {shown('avg_profit', '>14.2f', ' 元')}",
            f"平均亏损: {shown('avg_loss', '>14.2f', ' 元')}",
            f"最大单笔盈利: {s.max_single_profit:>10.2f} 元",
            f"最大单笔亏损: {s.max_single_loss:>10.2f} 元",
        ]
        if self.end_mark:
            lines.append(f"期末估值: {self.end_mark['pnl']:>14.2f} 元  (未平仓按收盘价估值, 不计入胜率)")
        for item in self.caveats:
            lines.append(f"[{'注意' if item['level'] == 'warning' else '说明'}] {item['text']}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        """转为字典 (用于 API 返回).

        Every statistic is BacktestStats.to_dict()'s value: one without a value
        (zero denominator or empty sample, e.g. Calmar without a drawdown, win
        rate without a decided trade) is null, and `undefined` maps its name to
        the reason. It is never reported as 0.
        """
        metrics = self.stats.to_dict()
        schedule = default_schedule()
        return {
            "run_id": self.run_id,
            "run_date": self.run_date,
            "strategy_name": self.strategy_name,
            "strategy_params": self.strategy_params,
            "primary_symbol": self.primary_symbol,
            "initial_cash": self._broker.initial_cash,
            **metrics,
            "period": self.period,
            "round_trips": self.round_trips,
            "end_mark": self.end_mark,
            "stale_valuations": [dict(item) for item in self.stale_valuations],
            "data_quality": {symbol: {"errors": report.errors, "warnings": report.warnings,
                                        "metrics": report.metrics} for symbol, report in self.data_quality_reports.items()},
            "caveats": self.caveats,
            "unfilled": self.unfilled,
            "warmup": self.warmup_info.get(self.primary_symbol),
            "coverage": {symbol: coverage.to_dict() for symbol, coverage in self.coverage.items()},
            "fees_total": self.fees_total,
            "engine_version": ENGINE_VERSION,
            "assumptions": {
                "frequency": "daily",
                "market_rules": self._broker.config.market,
                "end_of_run": ("positions still open at the end are valued at the last observed close, "
                               "net of sell fees and without slippage (end_mark); this is not a real sale"),
                "calendar": ("the run steps through every day any loaded symbol has a bar; on_bar runs only on "
                             "days the primary symbol has one; a held symbol without a bar on a day is valued at "
                             "its last close (stale_valuations)"),
                "same_bar": ("every symbol's orders are matched against the day's bar before any callback; "
                             "the opening fills of all symbols come before any fill inside the bar (in the order "
                             "the orders were sent), so a sale inside the bar never pays for a buy at that open; "
                             "orders and cancellations sent from on_order/on_trade or on_bar take effect from the "
                             "symbol's next bar, never at a price of the bar on which they were sent"),
                "annualization": ANNUALIZATION_NOTE,
                "trade_statistics": TRADE_STATS_NOTE,
                "fees": fees_assumption_text(schedule),
                "fee_schedule_version": schedule.version,
                "order_lifecycle": (f"market orders fill at the next open +/- slippage; an unfilled buy expires after "
                                    f"{self._broker.config.buy_expiry_bars} bar(s), a sell retries for up to "
                                    f"{self._broker.config.sell_retry_bars} bars; stops fill at min(open, stop), "
                                    "targets at max(open, target), the stop first when both are touched in one bar; "
                                    "A-share stocks and ETFs are T+1"),
                "lots": ("A-share 100 shares (STAR at least 200 then 1, BSE at least 100 then 1), HK 100 unless "
                         "set (an assumption), US 1"),
                "price_limits": ("A-share dated board table: main board and funds 10%, ChiNext 10% "
                                 "before 2020-08-24 and 20% from then, STAR 20%, BSE 30%; adjusted "
                                 "series are compared as a ratio to the previous close; ST status is "
                                 "unknown, so only one-price bars moving >= 4.8% on 10% boards are "
                                 "treated as ST limit boards; limit-free new-listing days and "
                                 "STAR/ChiNext ETF 20% limits are not modelled"),
                "cost_currency": "single quote currency; mixed currency portfolios need external conversion",
            },
        }
