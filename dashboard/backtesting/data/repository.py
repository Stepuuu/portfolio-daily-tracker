"""行情仓库: 回测取数的唯一入口, 并说明这次用了哪些数据.

    repo = BarRepository()
    bars, coverage = repo.load("SHA:600519", "2023-11-17", "2026-11-16", adjust="qfq", warmup_bars=60)

返回的 DataFrame 以日期为索引, 列为 open/high/low/close/volume/amount/pct_change/
turnover (volume 单位为股), 下载路径与缓存路径返回完全相同的帧 (下载后从缓存读回).

取数顺序:
  1. ``symbols.resolve`` 得到规范键; 依次查找规范键和旧版键 (别名), 已记录的覆盖
     区间包含 [start - 预热, end] 的第一个键直接命中, 不下载.
  2. 有预热期, 而只有 [start, end] 在某个键的一段覆盖区间里: 用这个键 (依次查找
     的第一个) 的这一段, 预热期只取这一段里 start 之前的 K 线 (同一条下载链, 同一个
     复权锚点); 这一段里的预热 K 线够用时不为预热下载. 例外: 这一段里 start 之前的
     K 线不够预热期 (例如这一段是为更短的预热下载的), 或这个键在这一段之前还有 K 线
     (来自另一次下载, 没有和这一段核对过: 复权锚点可能不同, 中间也可能缺 K 线, 不能
     拼在前面): 允许下载就按 3 下载整个区间, 与没有缓存时得到同样的数据; 不允许或
     下载失败时预热期只用这一段, 有更早的 K 线时 ``warmup_seam`` 记下这一段的起始日.
     这时都是 ``offline=False`` (请求区间本身完整), 原因写入 ``warnings``; 有
     ``warmup_seam`` 或预热期没取够时 ``complete=False`` (预热扩展不完整), 这一段里取够了
     预热期时 ``complete=True`` (用到的数据都核对过).
  3. 未命中且允许下载: 按规范键下载整个区间, 校验后写入缓存 (复权锚点检查见
     DataStore.save_daily) 并记录下载区间, 再从缓存读回. 区间内有本次下载没有、
     来自更早下载的复权 K 线时 (无法核对锚点, 可能是拼接), ``complete=False`` 并
     在 ``warnings`` 中写明日期; 这些日期也不计入覆盖区间 (DataStore.record_coverage).
  4. 下载不允许或失败: 返回本地已有的部分 (规范键优先, 否则行数最多的别名),
     ``offline=True, complete=False``, 原因写入 ``warnings``; 本地也没有就抛出
     ``BarsUnavailableError``. 是否拒绝运行由调用方决定.
  返回的数据、``store_key`` 和各个标志说的都是同一个键的同一段数据.

``Coverage`` 记录本次数据的来源、实际区间、是否完整、是否离线、下载时间和
``sha256`` (同样的行情得到同样的哈希, 用于复现一次回测).

临时 K 线: 交易所时间今天收盘价确定之前 (store.bar_final_at: 交易日历
SessionClock.close_final 加发布余量), 今天的日 K 线还会变, 是临时数据. 它不写入缓存、
不计入覆盖区间, 也不出现在返回的数据里 (缓存里已有的同类行在读取时去掉); 请求区间
包含今天时 ``provisional_last_bar=True`` 并写入 ``warnings``. 收盘后的请求会重新下载,
拿到当天的最终 K 线. 这时 ``complete`` 仍按截至最后一个已收盘的交易日的数据判断.
判断用的是数据真正下载的时间: 加载器给出的帧带 ``attrs["fetched_at"]`` (较早取得、
被保留下来的帧) 时按那个时间, 那时还是临时的行不保存. 早先版本或收盘前导入的 CSV
在当天收盘前写入缓存的行 (DataStore.provisional_dates) 不算覆盖: 允许下载就重新下载
(下载没有返回的这类行被删除), 否则去掉并 ``complete=False``, 日期写入
``dropped_provisional``.

拼接: 下载区间内有本次下载没有、来自更早下载的复权 K 线时 (步骤 3), 这些日期写入
``unverified``, 新旧两段的交界日写入 ``splice_seams``; 调用方据此拒绝运行或提示.

数据截止日: 数据源在收盘后过一段时间才发布当天的日 K 线, 收盘后不久的下载可能还没有
这一天, 而下载区间照样整段记录 (DataStore.record_download). ``last_session`` 是请求区间内
最后一个已收盘 (SessionClock.close_settled) 且日 K 线已确定的交易日. 返回的数据止于它之前,
又没有一次记录在案、在它收盘 ``PROVIDER_LAG`` 之后才做的下载 (那时数据源没有的就是真的没有:
停牌, 或休市表没有收录的假日) 时: 数据来自缓存且允许下载就重新下载整个区间, 但这个序列在
``RECHECK_INTERVAL`` 之内 (收盘之后) 已经下载过一次时不再下载 (重复提交沿用那次检查); 仍然 (或
无法) 取到时 ``ends_early=True``, ``complete=False``, 写入 ``warnings``. 重新下载失败时用原来的
缓存, 不算离线.
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
from datetime import datetime
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Mapping, Optional, Tuple

import pandas as pd

from backtesting.data.quality import (
    SUPPORTED_ADJUSTMENTS, require_valid_daily_bars, scan_corporate_action_gaps,
)
from backtesting.data.store import (
    MARKETS, DataStore, _aware, _exchange_clock, _session_clock, bar_final_at, market_of,
)
from backtesting.data.symbols import INDEX, Symbol, resolve

BAR_COLUMNS = ("open", "high", "low", "close", "volume", "amount", "pct_change", "turnover")
# bars_sha256 hashes these columns first, in this order, then any other column of the frame.
HASH_COLUMNS = BAR_COLUMNS
# 1: open/high/low/close/volume/amount only. 2: every column handed to the strategy and broker.
HASH_VERSION = 2


class BarsUnavailableError(RuntimeError):
    """No bars for the request: not in the cache, and the download was not made or failed."""


class _RefreshFailed(RuntimeError):
    """The download of a cached range that ends early failed (the message is the reason)."""


# A download made this long after a session's close settled holds every bar the provider
# will publish for it: a bar it lacks then is not late (a suspension, or a holiday the
# session table does not list). Before that the day's bar may not be published yet.
PROVIDER_LAG = pd.Timedelta(hours=24)
# A series that ends early is downloaded again at most this often: a download of it made after
# the session settled, less than this long ago, is the check (repeated runs reuse it).
RECHECK_INTERVAL = pd.Timedelta(hours=1)


def last_settled_session(market: Optional[str], end, now) -> Optional[pd.Timestamp]:
    """The last trading day on or before `end` whose close has settled at `now`
    (core.market.calendar.SessionClock.close_settled) and whose daily bar is final
    (DataStore.last_complete_day): where a complete series of a window ending `end`
    stops. None for a market without a session calendar."""
    if market not in MARKETS:
        return None
    clock = _session_clock()
    moment = _exchange_clock(now, market)
    day = min(pd.Timestamp(end).normalize(), DataStore.last_complete_day(now, market))
    for _ in range(40):                  # longer than any exchange closure
        settled = clock.close_settled(market, day.date())
        if settled is not None and settled <= moment:
            return day
        day -= pd.Timedelta(days=1)
    return None


@dataclass
class Coverage:
    """What one ``BarRepository.load`` call returned, for caveats and the run manifest."""
    ticker: str                     # 规范键
    adjust: str                     # 实际使用的复权方式 (指数总是 "")
    source: str                     # 数据来源: 接口名 / "import" (CSV) / "unknown" (旧缓存无记录); 多个用 + 连接
    requested_start: str
    requested_end: str
    first: Optional[str]            # 返回的第一根 K 线 (含预热)
    last: Optional[str]
    rows: int
    complete: bool                  # 请求区间 (含预热扩展) 已完整下载过
    offline: bool                   # 需要下载但没有下载 (不允许或失败), 用的是本地已有部分
    fetched_at: Optional[str]       # 数据的下载时间 (缓存命中时为该覆盖区间的记录时间)
    sha256: str
    store_key: str = ""             # 读取的缓存键 (规范键或旧版别名)
    cached: bool = False            # 数据来自已有缓存, 不是本次下载
    warmup_bars: int = 0            # 请求的预热 K 线数
    warmup_rows: int = 0            # 实际返回的 start 之前的 K 线数
    suspected_corporate_actions: List[str] = field(default_factory=list)  # 不复权序列的疑似除权日
    warnings: List[str] = field(default_factory=list)
    # 请求区间包含今天 (交易所时间), 而今天还没收盘: 今天的 K 线是临时的, 未存入缓存,
    # 也不在返回的数据里 (返回的数据截至上一个已收盘的交易日)
    provisional_last_bar: bool = False
    # 预热期止于这一天 (请求区间所在覆盖区间的起始日): 缓存里更早的 K 线来自另一次
    # 下载, 没有和这一段核对过 (复权锚点可能不同), 没有拼在前面 (没有下载或下载失败). 否则为 None
    warmup_seam: Optional[str] = None
    # 返回的数据里来自更早一次下载、本次下载没有的复权 K 线的日期 (无法核对复权锚点)
    unverified: List[str] = field(default_factory=list)
    # 返回的数据在这些日期从一次下载换到另一次 (新旧两段交界后的第一天)
    splice_seams: List[str] = field(default_factory=list)
    # 去掉的 K 线: 当天收盘价确定之前取得或存入的临时数据, 没能重新下载
    dropped_provisional: List[str] = field(default_factory=list)
    # 请求区间内最后一个已收盘 (SessionClock.close_settled) 的交易日; 没有交易日历的市场为 None
    last_session: Optional[str] = None
    # 数据止于 last_session 之前, 而且不能确定数据源没有这一天的 K 线 (见 load: 数据截止日)
    ends_early: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def bars_sha256(df: pd.DataFrame) -> str:
    """Hash of exactly the bars a run hands to the strategy and the broker (HASH_VERSION 2).

    The sha256 of this UTF-8 text, one line per row:

        bars-v2
        date,<column>,<column>,...
        <date>,<cell>,<cell>,...

    Columns: the eight BAR_COLUMNS in that order (a column the frame does not have is
    "nan" in every row, so a missing column and a column of zeros differ), then every
    other column of the frame in sorted order of its name. The repository's Coverage
    hashes the bars it returns, before the engine adds its derived features; those come
    from code, so the run service also hashes the frame handed to the strategy, features
    included (manifest coverage features_sha256, data_hash_version 3).
    Rows: sorted by date; the date as YYYY-MM-DD, or ISO 8601 with the time when it is
    not midnight. Row and column order of the frame do not matter.
    Cells: a number (int, float or bool) as Python's shortest round-trip repr of its
    float value ("100.0", "0.1", "1e-05"; -0.0 as "0.0"), "nan" for a missing value,
    "inf" / "-inf"; any other value as the JSON string of str(value).
    """
    digest = hashlib.sha256()
    frame = df if df is not None else pd.DataFrame()
    names = list(frame.columns)
    first = {}
    for position, name in enumerate(names):
        first.setdefault(name, position)
    extra = sorted((p for p, name in enumerate(names) if name not in BAR_COLUMNS), key=lambda p: (str(names[p]), p))
    positions = [first.get(name) for name in BAR_COLUMNS] + extra
    header = list(BAR_COLUMNS) + [str(names[p]) for p in extra]
    digest.update(("bars-v2\ndate," + ",".join(header) + "\n").encode("utf-8"))
    if frame.empty:
        return digest.hexdigest()
    order = sorted(range(len(frame)), key=lambda i: pd.Timestamp(frame.index[i]))
    columns = [["nan"] * len(frame) if p is None else _hash_cells(frame.iloc[:, p]) for p in positions]
    for i in order:
        day = pd.Timestamp(frame.index[i])
        text = day.strftime("%Y-%m-%d") if day == day.normalize() else day.isoformat()
        digest.update((text + "," + ",".join(column[i] for column in columns) + "\n").encode("utf-8"))
    return digest.hexdigest()


def _hash_cells(values: pd.Series) -> List[str]:
    if pd.api.types.is_bool_dtype(values) or pd.api.types.is_numeric_dtype(values):
        return [repr(float(v) + 0.0) for v in values.to_numpy(dtype=float)]
    out = []
    for value in values.tolist():
        if isinstance(value, (bool, numbers.Real)):
            out.append(repr(float(value) + 0.0))
        elif value is None or (not isinstance(value, (list, dict, tuple)) and pd.isna(value)):
            out.append("nan")
        else:
            out.append(json.dumps(str(value), ensure_ascii=False))
    return out


def union_calendar(frames: Mapping[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """Trading days of a multi-symbol run: every day any symbol has a bar.

    A day missing from one symbol (suspended) is still a day for the others.
    """
    days = pd.DatetimeIndex([])
    for frame in frames.values():
        days = days.union(pd.DatetimeIndex(frame.index).normalize())
    return days.sort_values().unique()


def normalize_adjust(adjust) -> str:
    value = "" if adjust is None else str(adjust).strip().lower()
    if value == "none":
        value = ""
    if value not in SUPPORTED_ADJUSTMENTS:
        raise ValueError(f"不支持的复权方式 {adjust!r}; 可选 qfq (前复权) / hfq (后复权) / 空 (不复权)")
    return value


def warmup_calendar_days(bars: int) -> int:
    """Calendar days that hold about `bars` trading days (A-shares trade ~242 of 365)."""
    return 0 if bars <= 0 else math.ceil(bars * 1.5) + 15


class BarRepository:
    def __init__(self, store: Optional[DataStore] = None, loader=None,
                 now: Optional[Callable[[], pd.Timestamp]] = None):
        self._now = now or pd.Timestamp.now
        if loader is None:
            from backtesting.data.loader import DataLoader
            loader = DataLoader(source="akshare", now=self._now)
        self.store = store if store is not None else DataStore()
        self.loader = loader

    # ------------------------------------------------------------------ #
    #  公开 API
    # ------------------------------------------------------------------ #

    def load(
        self,
        ticker,
        start,
        end,
        adjust: str = "qfq",
        warmup_bars: int = 0,
        allow_download: bool = True,
    ) -> Tuple[pd.DataFrame, Coverage]:
        """Bars for [start, end] plus up to `warmup_bars` bars before start.

        Raises SymbolError (a ValueError) for an unknown symbol, ValueError for an
        invalid request, and BarsUnavailableError when there is nothing to return.
        """
        sym = resolve(ticker)
        adjust = normalize_adjust(adjust)
        if isinstance(warmup_bars, bool) or not isinstance(warmup_bars, numbers.Integral) or warmup_bars < 0:
            raise ValueError(f"warmup_bars 必须是非负整数, 收到 {warmup_bars!r}")
        warmup_bars = int(warmup_bars)
        try:
            start_ts = pd.Timestamp(start).normalize()
            end_ts = pd.Timestamp(end).normalize()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"日期格式不正确: {start!r} ~ {end!r}") from exc
        if pd.isna(start_ts) or pd.isna(end_ts):
            raise ValueError(f"日期格式不正确: {start!r} ~ {end!r}")
        if end_ts < start_ts:
            raise ValueError(f"结束日期 {_day(end_ts)} 早于开始日期 {_day(start_ts)}")
        if not sym.adjustable:
            adjust = ""
        now = self._now()
        if not isinstance(now, datetime):
            now = pd.Timestamp(now)
        # An aware datetime is kept as is: no pandas time-zone conversion (see DataStore._moment).
        hints = self._other_adjustments(sym, adjust)

        load_start = start_ts - pd.Timedelta(days=warmup_calendar_days(warmup_bars))
        final_day = DataStore.last_complete_day(now, sym.market)
        today = DataStore.exchange_day(now, sym.market)
        provisional = end_ts >= today and final_day < today
        try:
            frame, info = self._load_range(sym, load_start, end_ts, adjust, allow_download, now, start_ts,
                                           need_before=warmup_bars)
        except BarsUnavailableError as exc:
            if not hints:
                raise
            raise BarsUnavailableError(f"{exc} {' '.join(hints)}") from None
        before = frame[frame.index < start_ts]
        if warmup_bars and len(before) < warmup_bars and len(frame) \
                and frame.index[0] <= load_start + pd.Timedelta(days=10):
            # The data reaches the extended start, so there is probably more before it.
            load_start -= pd.Timedelta(days=warmup_calendar_days(warmup_bars - len(before)) + 30)
            frame, info = self._load_range(sym, load_start, end_ts, adjust, allow_download, now, start_ts,
                                           need_before=warmup_bars)
            before = frame[frame.index < start_ts]

        # The data end (module notes): a cached series that stops before the last settled
        # session may only have been fetched before the provider published that session.
        # Up to the last day the bar cache itself takes as final for this key: a ticker the cache
        # cannot place in a market (the demo's) has no final bar of today there.
        session = last_settled_session(
            sym.market, min(end_ts, DataStore.last_complete_day(now, market_of(sym.ticker))), now)
        refresh_failed = None
        checked = self._checked_since(session, info["store_key"], adjust, sym.market, now) \
            if session is not None else None
        if session is not None and allow_download and info["cached"] and not info["offline"] and checked is None \
                and self._ends_early(frame, session, info["store_key"], adjust, sym.market):
            try:
                frame, info = self._load_range(sym, load_start, end_ts, adjust, allow_download, now, start_ts,
                                               need_before=warmup_bars, refresh=True)
            except _RefreshFailed as exc:
                refresh_failed = str(exc)
            before = frame[frame.index < start_ts]
        ends_early = session is not None and self._ends_early(frame, session, info["store_key"], adjust,
                                                              sym.market)

        warnings = hints + info["warnings"]
        if provisional:
            settled = bar_final_at(today, sym.market).strftime("%H:%M")
            warnings.append(
                f"{_day(today)} 的日 K 线要到收盘后 (交易所时间 {settled}) 才是最终数据, 现在是临时的: "
                f"未存入缓存, 也不在返回的数据里, 数据截至 {_day(final_day)}; 收盘后再运行会重新下载"
            )
        elif info.get("dropped"):
            warnings.append(f"去掉了 {_list_days(info['dropped'])} 的 K 线: 晚于最后一个已收盘的交易日 {_day(final_day)}")
        inside = frame[(frame.index >= start_ts) & (frame.index <= end_ts)]
        kept = before.iloc[len(before) - min(warmup_bars, len(before)):] if warmup_bars else before.iloc[:0]
        out = pd.concat([kept, inside]) if len(kept) else inside.copy()
        complete = info["complete"]
        if info.get("window_only") and not info.get("warmup_seam") and len(kept) >= warmup_bars:
            # The window's interval held every warm-up bar asked for: nothing the run
            # uses is missing or from another download.
            complete = True
        returned = {_day(day) for day in out.index}
        # Left out of the bars returned (from their first date on; not an earlier
        # warm-up bar the run would not have used anyway).
        since = min(out.index[0], start_ts) if len(out) else start_ts
        stale = sorted({day for day in info.get("stale", [])
                        if since <= pd.Timestamp(day) <= final_day and day not in returned})
        if stale:
            complete = False
            warnings.append(
                f"去掉了 {_list_days(stale)} 的 K 线: 它们是当天收盘前取得或存入的临时数据, 不是当天最终的"
                f" K 线, 也没能重新下载; 数据不完整"
            )
        if ends_early:
            complete = False
            settled = _session_clock().close_settled(sym.market, session.date())
            why = (f"重新下载失败 ({refresh_failed})" if refresh_failed else
                   "未允许下载" if not allow_download else
                   f"{checked.strftime('%H:%M')} 下载时数据源也没有" if checked is not None and info["cached"]
                   else "重新下载也没有")
            warnings.append(
                f"数据截至 {_day(out.index[-1]) if len(out) else '(无)'}, 而 {_day(session)} 已收盘 "
                f"(交易所时间 {settled.strftime('%H:%M')}): 本地缓存没有这一天的 K 线, {why}; 数据源可能还没"
                f"发布 (也可能停牌), 数据不完整, 稍后再运行会重新下载"
            )
        unverified = sorted(day for day in info.get("unverified", []) if day in returned)
        seams = _seams(out.index, set(unverified)) if unverified else []
        if warmup_bars and len(kept) < warmup_bars:
            warnings.append(
                f"预热期只取到 {len(kept)}/{warmup_bars} 根 K 线"
                + (f" (数据从 {_day(frame.index[0])} 开始)" if len(frame) else "")
            )
        if inside.empty:
            warnings.append(f"{_day(start_ts)}~{_day(end_ts)} 区间内没有 K 线")
        suspected = []
        if adjust == "":
            # Always, whatever the gap scan below finds (it never sees cash dividends).
            warnings.append(UNADJUSTED_INDEX_NOTE if sym.kind == INDEX else UNADJUSTED_NOTE)
        if adjust == "" and sym.kind != INDEX:
            for event in scan_corporate_action_gaps(out, symbol=sym):
                suspected.append(event.date)
                warnings.append(
                    f"疑似除权/拆合股 {event.date}: 不复权价格较前一日 {event.change:+.1%}, 之后未回到原价位; "
                    f"回测会把它当成真实涨跌, 建议改用前复权 (qfq)"
                )

        coverage = Coverage(
            ticker=sym.ticker,
            adjust=adjust,
            source=info["source"],
            requested_start=_day(start_ts),
            requested_end=_day(end_ts),
            first=_day(out.index[0]) if len(out) else None,
            last=_day(out.index[-1]) if len(out) else None,
            rows=len(out),
            complete=complete,
            offline=info["offline"],
            fetched_at=info["fetched_at"],
            sha256=bars_sha256(out),
            store_key=info["store_key"],
            cached=info["cached"],
            warmup_bars=warmup_bars,
            warmup_rows=len(kept),
            suspected_corporate_actions=suspected,
            warnings=warnings,
            provisional_last_bar=provisional,
            warmup_seam=info.get("warmup_seam"),
            unverified=unverified,
            splice_seams=seams,
            dropped_provisional=stale,
            last_session=_day(session) if session is not None else None,
            ends_early=ends_early,
        )
        return out, coverage

    def available(self, ticker) -> dict:
        """Adjustments with local bars for this instrument, canonical key first.

        {"": {"store_key": "600519", "rows": 242, "first": ..., "last": ...,
              "coverage": [[start, end], ...], "sources": ["import"]}, "qfq": {...}}
        Callers use it to offer the adjustment that has data (e.g. a CSV import,
        stored as 不复权) instead of downloading another one over it.
        """
        sym = resolve(ticker)
        keys = self.store.stored_symbols(sym.store_keys)
        by_key = {item["symbol"]: item["adjustments"] for item in self.store.list_available(keys)}
        found = {}
        for key in keys:
            for item in by_key.get(key, []):
                if item["adjust"] not in found:
                    found[item["adjust"]] = {"store_key": key, **{k: v for k, v in item.items() if k != "adjust"}}
        return found

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _other_adjustments(self, sym: Symbol, adjust: str) -> List[str]:
        """Hints about local data stored under other adjustments, when none is under `adjust`."""
        available = self.available(sym)
        if adjust in available:
            return []
        return [
            f"本地另有{_ADJUST_NAMES[other]}数据 ({info['store_key']}, {info['first']}~{info['last']}, "
            f"来源 {'+'.join(info['sources'])}) 未使用; 要用它请选择{_ADJUST_NAMES[other]}"
            for other, info in available.items() if other in _ADJUST_NAMES
        ]

    def _checked_since(self, session: pd.Timestamp, key: str, adjust: str, market: str, now) -> Optional[datetime]:
        """When the latest download recorded for `key` that holds `session` was made, on the
        exchange's clock, if that was after the session settled and less than RECHECK_INTERVAL
        before `now` (a recent check: the series is not downloaded again), else None."""
        settled = _session_clock().close_settled(market, session.date())
        if settled is None:
            return None
        moment, latest = _exchange_clock(now, market), None
        for fetched in self.store.download_times(key, adjust, _day(session)):
            try:
                when = _exchange_clock(fetched, market)
            except (TypeError, ValueError):
                continue
            if settled <= when <= moment and moment - when < RECHECK_INTERVAL and (latest is None or when > latest):
                latest = when
        return latest

    def _ends_early(self, frame: pd.DataFrame, session: pd.Timestamp, key: str, adjust: str, market: str) -> bool:
        """The bars stop before `session` and no download recorded for `key` that holds the
        session was made PROVIDER_LAG after its close settled (module notes: the data end)."""
        if len(frame) and frame.index[-1] >= session:
            return False
        settled = _session_clock().close_settled(market, session.date())
        if settled is None:
            return False
        for fetched in self.store.download_times(key, adjust, _day(session)):
            try:
                if _aware(fetched) >= settled + PROVIDER_LAG:
                    return False
            except (TypeError, ValueError):
                continue
        return True

    def _load_range(self, sym: Symbol, lo: pd.Timestamp, hi: pd.Timestamp, adjust: str,
                    allow_download: bool, now: pd.Timestamp, window_start: Optional[pd.Timestamp] = None,
                    *, need_before: int = 0, refresh: bool = False):
        """Bars for [lo, hi] up to the last final trading day; info["dropped"] lists
        later rows left out (provisional, never cached or returned). window_start:
        where the requested window begins when [lo, window_start) is warm-up, of which
        `need_before` bars are wanted. refresh: download, whatever the cache holds
        (_RefreshFailed when the download fails)."""
        final_day = DataStore.last_complete_day(now, sym.market)
        stale: List[str] = []
        frame, info = self._load_range_raw(sym, lo, hi, adjust, allow_download, now, final_day, window_start, stale,
                                           need_before=need_before, refresh=refresh)
        late = frame.index > final_day
        info["dropped"] = info.get("dropped", []) + [_day(day) for day in frame.index[late]]
        info["stale"] = info.get("stale", []) + stale
        return frame[~late], info

    def _load_range_raw(self, sym: Symbol, lo: pd.Timestamp, hi: pd.Timestamp, adjust: str,
                        allow_download: bool, now: pd.Timestamp, final_day: pd.Timestamp,
                        window_start: Optional[pd.Timestamp] = None, stale: Optional[List[str]] = None,
                        *, need_before: int = 0, refresh: bool = False):
        """`stale` collects the dates of bars left out because they were fetched or
        stored before the day's bar was final (DataStore.provisional_dates)."""
        store = self.store
        stale = stale if stale is not None else []
        keys = store.stored_symbols(sym.store_keys)
        for key in keys if not refresh else []:
            if store.is_data_available(key, _day(lo), _day(hi), adjust, now=now):
                return self._read(key, lo, hi, adjust, stale), {
                    "store_key": key, "cached": True, "complete": True, "offline": False,
                    "fetched_at": self._fetched_at(key, adjust, lo, hi, now=now),
                    "source": self._source(key, adjust, lo, hi), "warnings": [],
                }

        # Only the window is recorded (step 2 of the module notes): that key's interval.
        held = self._window_interval(keys, window_start, lo, hi, adjust, now) \
            if window_start is not None and window_start > lo and not refresh else None
        # Its warm-up bars fall short of what is wanted (it was fetched for a shorter one):
        # download the whole range, as without a cache.
        short = held is not None and allow_download and not held["seam"] and need_before > 0 \
            and len(self._read(held["key"], held["since"], window_start - pd.Timedelta(days=1), adjust)) < need_before
        if held is not None and not short and (not held["seam"] or not allow_download):
            return self._read_window(held, hi, adjust, now, None if allow_download else "未允许下载", stale)

        warnings: List[str] = []
        reason = "未允许下载"
        if allow_download:
            try:
                frame = self.loader.get_daily(sym.ticker, _day(lo), _day(hi), adjust)
                require_valid_daily_bars(frame, symbol=sym.ticker, adjust=adjust)
            except Exception as exc:  # network, provider or validation failure
                reason = f"下载失败 ({type(exc).__name__}: {str(exc)[:200]})"
            else:
                # Judged at the time the frame was fetched: a loader may hand back a
                # frame it fetched earlier (attrs["fetched_at"]). A bar dated after the
                # last session settled then (today before the close) is provisional: it
                # is never written as final. One dated on a day that is final by now
                # is missing from this download (stale).
                fetched = _fetched_time(frame, now)
                fetched_at = fetched.isoformat(timespec="seconds")
                fetched_final = min(final_day, DataStore.last_complete_day(fetched, sym.market))
                late = frame.index > fetched_final
                dropped = [_day(day) for day in frame.index[late & (frame.index > final_day)]]
                stale += [_day(day) for day in frame.index[late & (frame.index <= final_day)]]
                frame = frame[~late]
                store.save_daily(sym.ticker, frame, adjust, fetched_at=fetched)
                report = store.last_save
                # Rows of the range written while provisional that the download did not
                # replace: the provider has no bar for that day (any more).
                store.discard_provisional(sym.ticker, adjust, lo, min(hi, fetched_final))
                if report is not None and report.replaced:
                    warnings.append(
                        f"复权锚点已变化 ({report.first_mismatch} 起与旧缓存不一致): "
                        f"已用本次下载整段替换旧缓存, 不与旧数据拼接"
                    )
                store.record_download(sym.ticker, adjust, _day(lo), _day(hi),
                                      fetched_at=fetched_at, source=sym.provider)
                # Cached adjusted rows in the range that this download did not return
                # were never anchor-checked: the frame read back splices two downloads.
                record = store.last_record
                unverified = [day for day in (record.unverified if record is not None else [])
                              if _day(lo) <= day <= _day(hi)]
                if unverified:
                    warnings.append(
                        f"复权锚点无法核对: 缓存中 {_list_days(unverified)} 的 K 线来自更早的下载, 本次下载没有这些日期; "
                        f"返回的数据可能在这里拼接了两个不同锚点的序列, 不算完整"
                    )
                return self._read(sym.ticker, lo, hi, adjust, stale), {
                    "store_key": sym.ticker, "cached": False, "complete": not unverified, "offline": False,
                    "fetched_at": fetched_at, "source": sym.provider, "warnings": warnings, "dropped": dropped,
                    "unverified": unverified,
                }

        if refresh:
            raise _RefreshFailed(reason)
        if held is not None:
            # The download that was to replace the unverified warm-up, or to fetch the
            # warm-up the interval lacks, failed: the window's own interval is complete,
            # the warm-up stops at its start.
            return self._read_window(held, hi, adjust, now, reason, stale, wanted_more=short)

        # Offline: the canonical key if it has rows, otherwise the alias with the most.
        best, best_frame, best_stale = None, None, []
        for key in keys:
            left_out: List[str] = []
            frame = self._read(key, lo, hi, adjust, left_out)
            if len(frame) and (best_frame is None or len(frame) > len(best_frame)):
                best, best_frame, best_stale = key, frame, left_out
            if best == sym.ticker:
                break
        stale += best_stale
        label = adjust or "不复权"
        if best is None:
            raise BarsUnavailableError(
                f"{sym.ticker} ({label}) {_day(lo)}~{_day(hi)} 本地没有缓存数据, 且{reason}. "
                f"请联网重试, 或导入覆盖该区间的 CSV."
            )
        warnings.append(
            f"离线: 只用本地缓存 {_day(best_frame.index[0])}~{_day(best_frame.index[-1])} "
            f"({len(best_frame)} 根), 请求 {_day(lo)}~{_day(hi)} 未完整覆盖; {reason}"
        )
        return best_frame, {
            "store_key": best, "cached": True, "complete": False, "offline": True,
            "fetched_at": self._fetched_at(best, adjust, lo, hi),
            "source": self._source(best, adjust, lo, hi), "warnings": warnings,
        }

    def _window_interval(self, keys: List[str], window_start: pd.Timestamp, lo: pd.Timestamp,
                         hi: pd.Timestamp, adjust: str, now) -> Optional[dict]:
        """The first key whose recorded coverage holds the window [window_start, hi]:
        {"key", "since": the start of that interval (the latest one, if several hold
        the window), "seam": dates of the key's bars in [lo, since) - from another
        download, never compared with that interval}. None if no key holds the window."""
        for key in keys:
            if not self.store.is_data_available(key, _day(window_start), _day(hi), adjust, now=now):
                continue
            top = _day(min(hi, DataStore.last_complete_day(now, market_of(key))))
            since = max(pd.Timestamp(start) for start, end, _ in self.store.get_coverage(key, adjust)
                        if start <= _day(window_start) and end >= top)
            earlier = self._read(key, lo, since - pd.Timedelta(days=1), adjust) if since > lo else None
            return {"key": key, "since": max(since, lo),
                    "seam": [_day(day) for day in earlier.index] if earlier is not None else []}
        return None

    def _read_window(self, held: dict, hi: pd.Timestamp, adjust: str, now, reason: Optional[str],
                     stale: Optional[List[str]] = None, wanted_more: bool = False):
        """Bars of the interval that holds the window, from its start (step 2 of the
        module notes); `reason` says why earlier bars were not replaced by a download
        (wanted_more: a download for the warm-up the interval lacks)."""
        key, since, seam = held["key"], held["since"], held["seam"]
        if seam:
            note = (f"预热期没有用缓存里 {seam[0]}~{seam[-1]} 的 {len(seam)} 根 K 线: 它们来自另一次下载, "
                    f"没有和请求区间所在的覆盖区间 (从 {_day(since)} 开始) 核对过 (复权锚点可能不同, 中间也可能缺 "
                    f"K 线), 拼在前面可能出现假跳空; {reason}, 预热期只用 {_day(since)} 起的数据")
        elif wanted_more:
            note = (f"请求区间已在本地缓存中 (覆盖区间从 {_day(since)} 开始), 但这一段里的预热 K 线不够; "
                    f"更早的 K 线{reason}, 预热期只用这一段里更早的数据")
        else:
            note = f"请求区间已在本地缓存中 (覆盖区间从 {_day(since)} 开始); 预热期只用这一段里更早的数据, 未为预热下载"
        return self._read(key, since, hi, adjust, stale), {
            "store_key": key, "cached": True, "complete": False, "offline": False,
            "fetched_at": self._fetched_at(key, adjust, since, hi, now=now),
            "source": self._source(key, adjust, since, hi), "warnings": [note],
            "warmup_seam": _day(since) if seam else None, "window_only": True,
        }

    def _read(self, key: str, lo: pd.Timestamp, hi: pd.Timestamp, adjust: str,
              stale: Optional[List[str]] = None) -> pd.DataFrame:
        """Stored bars of `key` in [lo, hi], without rows written while provisional
        (their dates are added to `stale`)."""
        frame = self.store.load_daily(key, _day(lo), _day(hi), adjust)
        if frame.empty:
            empty = pd.DataFrame(columns=list(BAR_COLUMNS), dtype=float)
            empty.index = pd.DatetimeIndex([], name="date")
            return empty
        provisional = self.store.provisional_dates(key, adjust, lo, hi)
        if provisional:
            if stale is not None:
                stale.extend(provisional)
            frame = frame[~frame.index.isin(pd.DatetimeIndex(pd.to_datetime(provisional)))]
        return frame

    def _fetched_at(self, key: str, adjust: str, lo, hi, now=None) -> Optional[str]:
        """Latest fetch time of the recorded intervals that contain [lo, hi] (with now),
        or that overlap it (offline fallback, now=None)."""
        lo = _day(lo)
        hi = (_day(min(pd.Timestamp(hi), DataStore.last_complete_day(now, market_of(key))))
              if now is not None else _day(hi))
        stamps = [fetched for start, end, fetched in self.store.get_coverage(key, adjust)
                  if (start <= lo and end >= hi if now is not None else start <= hi and end >= lo)]
        return max(stamps) if stamps else None

    def _source(self, key: str, adjust: str, lo, hi) -> str:
        sources = self.store.get_sources(key, adjust, lo, hi)
        return "+".join(sources) if sources else "unknown"


_ADJUST_NAMES = {"qfq": "前复权", "hfq": "后复权", "": "不复权"}

UNADJUSTED_NOTE = (
    "不复权价格: 分红、送转、拆合股和配股都没有计入, 除权日的价格下跳会被当成亏损, 分红现金也不会到账, "
    "收益被低估或扭曲; 疑似除权日扫描只能发现大幅跳空, 发现不了现金分红, 没有发现不代表数据可靠. "
    "要得到可靠的收益请用前复权 (qfq)"
)
UNADJUSTED_INDEX_NOTE = "价格指数不含成分股分红: 按它计算的收益低于跟踪该指数的全收益投资"


def _day(value) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _fetched_time(frame: pd.DataFrame, now):
    """When `frame` was fetched: its attrs["fetched_at"] when the loader states an
    earlier time than `now` (a frame it kept), otherwise `now`."""
    stated = getattr(frame, "attrs", {}).get("fetched_at")
    if not stated:
        return now
    try:
        when = datetime.fromisoformat(str(stated))
        mine = now.to_pydatetime() if isinstance(now, pd.Timestamp) else now
        earlier = (when if when.tzinfo else when.astimezone()) < (mine if mine.tzinfo else mine.astimezone())
    except (AttributeError, TypeError, ValueError):
        return now
    return when if earlier else now


def _seams(index: pd.DatetimeIndex, old: set) -> List[str]:
    """Dates where consecutive bars switch between `old` dates (another download)
    and the rest: the first bar after each switch."""
    days = [_day(day) for day in index]
    return [day for previous, day in zip(days, days[1:]) if (previous in old) != (day in old)]


def _list_days(days, limit: int = 5) -> str:
    shown = "、".join(days[:limit])
    return shown + (f" 等 {len(days)} 天" if len(days) > limit else "")
