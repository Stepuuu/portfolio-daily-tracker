"""
数据库存储层 - 本地缓存历史行情数据
灵感: vnpy 的 database 模块 (SQLite 适配器)

使用 SQLite 作为轻量级本地存储,避免每次回测都重新下载数据.
"""
import sqlite3
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Dict, Optional, List, Tuple
import pandas as pd

from core.market.calendar import CLOSE_SETTLE

logger = logging.getLogger(__name__)

# Adjusted series carry an anchor: forward-adjusted (qfq) prices are restated at
# every later dividend or 送转, and a source may restate backward-adjusted (hfq)
# history too. Rows from two downloads with different anchors must never be
# mixed in one series.
ADJUSTED = ("qfq", "hfq")
# Two downloads with the same anchor round to the same cent; anything more than
# half a cent apart on a shared date means the anchor moved (or the source
# corrected history), and the new download wins.
ANCHOR_ABS_TOL = 0.005
ANCHOR_REL_TOL = 1e-6

# When a market's daily bar is final. The day's closing price is final at
# core.market.calendar.SessionClock.close_final: the end of continuous trading
# plus the closing auction, in exchange time, with the early closes of the
# shipped holiday table (Hong Kong half days, New York's 13:00 closes). The bar
# is taken as final PUBLICATION_MARGIN later: STAR/ChiNext after-hours
# fixed-price trading (to 15:30) still adds to the day's volume and amount, and
# the provider publishes the bar after the close. Before that moment a bar dated
# that day is provisional: never simulated, stored or counted as coverage. The
# margin is the live quotes' settle delay (core.market.calendar.CLOSE_SETTLE:
# New York's official closes reach the providers minutes after the bell), one
# constant, so a quote and a bar of a day become final at the same moment.
MARKETS = ("CN", "HK", "US")
PUBLICATION_MARGIN = CLOSE_SETTLE
# SQLite's datetime('now','localtime') format of daily_bars.created_at.
_STAMP = "%Y-%m-%d %H:%M:%S"


def _current_time() -> datetime:
    """The store's clock (local, naive): the moment of a save, an import or a coverage
    record that is not given one."""
    return datetime.now()


@lru_cache(maxsize=1)
def _session_clock():
    from core.market.calendar import SessionClock
    try:
        return SessionClock()
    except Exception:  # an unreadable holiday table: weekdays only, no early closes
        logger.warning("[DataStore] 交易日历不可用, 收盘时间按常规交易时段判断", exc_info=True)
        return SessionClock(holidays={})


def bar_final_at(day, market: str) -> datetime:
    """When the daily bar of `day` (an exchange-local date) is final in `market`
    (CN / HK / US), as an aware datetime on the exchange's clock.

    On a day the calendar has the exchange closed (a holiday, a weekend, an
    unscheduled session) a bar dated that day is final no earlier than a regular
    session's bar would be."""
    from core.market.calendar import CLOSING_AUCTION, SESSIONS, ZONES
    day = pd.Timestamp(day).date()
    final = _session_clock().close_final(market, day)
    if final is None:
        final = datetime.combine(day, SESSIONS[market][-1][1], tzinfo=ZONES[market]) + CLOSING_AUCTION[market]
    return (final + PUBLICATION_MARGIN).astimezone(ZONES[market])


def _moment(now) -> datetime:
    """`now` as a datetime: None is the store's clock (_current_time); naive values
    are local time. Plain datetime arithmetic only - no pandas time-zone machinery."""
    if now is None:
        return _current_time()
    if isinstance(now, str):
        try:
            return datetime.fromisoformat(now)
        except ValueError:
            return pd.Timestamp(now).to_pydatetime()
    if isinstance(now, datetime):
        return now.to_pydatetime() if isinstance(now, pd.Timestamp) else now
    if isinstance(now, date):
        return datetime(now.year, now.month, now.day)
    return pd.Timestamp(now).to_pydatetime()


def _aware(now) -> datetime:
    """`now` as an aware datetime (naive local times are this machine's)."""
    moment = _moment(now)
    return moment if moment.tzinfo is not None else moment.astimezone()


def _local_stamp(now) -> str:
    """`now` as this machine's local wall time in daily_bars.created_at's format."""
    return _aware(now).astimezone().strftime(_STAMP)


def _exchange_clock(now, market: str) -> datetime:
    """`now` on the exchange's wall clock (naive local times are this machine's)."""
    from core.market.calendar import ZONES
    return _aware(now).astimezone(ZONES[market])


def stored_while_provisional(day: str, created_at, market: Optional[str]) -> bool:
    """True when a row dated `day` was written (daily_bars.created_at, local time)
    before that day's bar was final: it holds a provisional value. A row whose write
    time cannot be read counts as provisional."""
    try:
        written = datetime.strptime(str(created_at), _STAMP).astimezone()
    except (TypeError, ValueError):
        return True
    if market not in MARKETS:
        # Without a market a day's bar is final from the next local midnight
        # (DataStore.last_complete_day).
        return written < (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).astimezone()
    return written < bar_final_at(day, market)


def market_of(symbol) -> Optional[str]:
    """CN / HK / US of a stored symbol (canonical ticker or legacy key), None if it does not resolve."""
    try:
        from backtesting.data.symbols import resolve
        return resolve(symbol).market
    except (ValueError, ImportError):
        return None


def _day(value) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _split_around(start: pd.Timestamp, end: pd.Timestamp, days) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """[start, end] without the given dates, as the intervals left between them."""
    pieces, lo = [], start
    for day in sorted(pd.Timestamp(d) for d in days):
        if day > lo:
            pieces.append((lo, day - pd.Timedelta(days=1)))
        lo = max(lo, day + pd.Timedelta(days=1))
    if lo <= end:
        pieces.append((lo, end))
    return pieces


@dataclass
class SaveReport:
    """What the last save_daily call did (DataStore.last_save)."""
    symbol: str
    adjust: str
    rows: int
    compared: int = 0                     # stored bars on the new frame's dates that were compared
    replaced: bool = False                # the stored adjusted series was dropped for the new one
    max_diff: float = 0.0                 # largest price difference found on a compared date
    first_mismatch: Optional[str] = None  # earliest date beyond the tolerance
    dates: Tuple[str, ...] = ()           # dates of the frame written
    compared_dates: Tuple[str, ...] = ()  # dates in both the stored series and the frame, compared
    provisional: Tuple[str, ...] = ()     # dates left out: not final when the frame was fetched


@dataclass
class CoverageReport:
    """What the last record_coverage call did (DataStore.last_record)."""
    symbol: str
    adjust: str
    requested: Tuple[str, str]
    recorded: List[Tuple[str, str]] = field(default_factory=list)    # intervals written (after merging)
    # Adjusted bars cached inside the requested range that the download just saved
    # does not contain: from an older download, never anchor-checked. The range is
    # recorded around them, so no coverage interval vouches for them.
    unverified: List[str] = field(default_factory=list)
    kept_apart: List[Tuple[str, str]] = field(default_factory=list)  # touching intervals not merged (no comparison)


class DataStore:
    """
    SQLite 本地数据存储

    表结构:
        daily_bars (symbol, date, open, high, low, close, volume, amount,
                    pct_change, turnover, adjust)
        bar_coverage (symbol, adjust, start_date, end_date, fetched_at)
            已完整下载/导入的日期区间, 缓存命中的依据
        bar_downloads (symbol, adjust, start_date, end_date, source, fetched_at)
            每次下载/导入的记录 (来源: 接口名, CSV 导入为 "import"), 只增不改;
            复权数据整段替换时一并清除该标的该复权方式的记录

    复权序列 (qfq/hfq) 的锚点保护: 新下载与已存的同标的同复权数据在重叠日期上
    价格不一致 (超过取整误差) 时, 整段替换为新下载, 不逐行合并 (逐行合并会在
    新旧数据交界处留下一个假跳空); 覆盖区间同时重置为本次下载的区间.
    """

    DB_FILE = "data/backtesting.db"

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # dashboard/data, TRACKER_BACKTEST_DIR, or the demo's own directory (see storage.py)
            from backtesting.storage import bars_db_path
            db_path = bars_db_path()

        self.db_path = db_path
        self.last_save: Optional[SaveReport] = None
        self.last_record: Optional[CoverageReport] = None
        # The latest save per (symbol, adjust), consumed by the record_coverage call
        # that follows it: which bars that download brought and which it compared.
        self._saved: Dict[Tuple[str, str], SaveReport] = {}
        # Constructed for reading (backtesting.storage.for_reading) before the first download: the caller
        # answers empty and the database is not created.
        from backtesting.storage import absent_for_reading
        self.absent = absent_for_reading(db_path)
        if self.absent:
            return
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ #
    #  公开 API
    # ------------------------------------------------------------------ #

    def save_daily(
        self,
        symbol: str,
        df: pd.DataFrame,
        adjust: str = "qfq",
        fetched_at=None,
    ) -> int:
        """
        保存日 K 线到数据库.
        返回实际写入行数. 本次保存的详情 (是否因锚点变化整段替换) 见 self.last_save.

        fetched_at: when the frame was fetched (default: now; an upload has no other
        time). A row dated after last_complete_day(fetched_at) - today before the
        market's close is final - is provisional and never written
        (SaveReport.provisional). Each row records fetched_at as its write time
        (created_at), so a row an earlier version stored while provisional can be
        recognised (stored_while_provisional).

        qfq/hfq: 先与已存数据在重叠日期上比较开高低收; 有差异超过取整误差
        (ANCHOR_ABS_TOL) 时, 删除该标的该复权方式的全部行和覆盖记录, 再写入
        新数据. 调用方随后记录的下载区间就是新的唯一覆盖区间. A stored row that was
        written while provisional is not an anchor to compare with.
        不复权 (""): 价格没有锚点, 逐行覆盖 (数据源更正).
        """
        moment = _moment(fetched_at)
        provisional: Tuple[str, ...] = ()
        if not df.empty:
            days = pd.DatetimeIndex(df.index if "date" not in df.columns else pd.to_datetime(df["date"])).normalize()
            late = days > self.last_complete_day(moment, market_of(symbol))
            provisional = tuple(sorted({_day(day) for day in days[late]}))
            df = df[~late]
        if df.empty:
            self.last_save = SaveReport(symbol, adjust, 0, provisional=provisional)
            self._saved[(symbol, adjust)] = self.last_save
            return 0

        df = df.copy().reset_index()  # 将 date index 还原为列
        df["symbol"] = symbol
        df["adjust"] = adjust
        written = _local_stamp(moment)

        rows = []
        for _, row in df.iterrows():
            date_str = row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"])
            rows.append((
                symbol,
                date_str,
                float(row.get("open", 0)),
                float(row.get("high", 0)),
                float(row.get("low", 0)),
                float(row.get("close", 0)),
                float(row.get("volume", 0)),
                float(row.get("amount", 0) if "amount" in row else 0),
                float(row.get("pct_change", 0) if "pct_change" in row else 0),
                float(row.get("turnover", 0) if "turnover" in row else 0),
                adjust,
                written,
            ))

        report = SaveReport(symbol, adjust, len(rows), dates=tuple(sorted({row[1] for row in rows})),
                            provisional=provisional)
        with sqlite3.connect(self.db_path) as conn:
            # Compare and write under one write lock so a concurrent save cannot interleave.
            conn.execute("BEGIN IMMEDIATE")
            if adjust in ADJUSTED:
                self._compare_anchor(conn, symbol, adjust, rows, report)
                if report.replaced:
                    for table in ("daily_bars", "bar_coverage", "bar_downloads"):
                        conn.execute(f"DELETE FROM {table} WHERE symbol=? AND adjust=?", (symbol, adjust))
            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_bars
                (symbol, date, open, high, low, close, volume, amount,
                 pct_change, turnover, adjust, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

        self.last_save = report
        self._saved[(symbol, adjust)] = report
        if report.replaced:
            logger.warning(
                "[DataStore] %s (%s) 复权锚点已变化 (%s 起差 %.4f): 已用新下载整段替换旧数据",
                symbol, adjust, report.first_mismatch, report.max_diff,
            )
        logger.info(f"[DataStore] 保存 {symbol} {len(rows)} 条日 K 线")
        return len(rows)

    @staticmethod
    def _compare_anchor(conn, symbol: str, adjust: str, rows: list, report: SaveReport) -> None:
        """Compare the new rows' OHLC with stored bars on the same dates (not with a
        row stored while provisional: its value was never the day's bar)."""
        new = {row[1]: row[2:6] for row in rows}
        stored = conn.execute(
            "SELECT date, open, high, low, close, created_at FROM daily_bars "
            "WHERE symbol=? AND adjust=? AND date>=? AND date<=? ORDER BY date",
            (symbol, adjust, min(new), max(new)),
        ).fetchall()
        market = market_of(symbol)
        compared = []
        for date, *old, created_at in stored:
            if date not in new or stored_while_provisional(date, created_at, market):
                continue
            report.compared += 1
            compared.append(date)
            for before, after in zip(old, new[date]):
                if before is None or after is None:
                    continue
                diff = abs(after - before)
                if diff > ANCHOR_ABS_TOL + ANCHOR_REL_TOL * abs(before):
                    report.replaced = True
                    report.first_mismatch = report.first_mismatch or date
                    report.max_diff = max(report.max_diff, diff)
        report.compared_dates = tuple(compared)

    def load_daily(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """
        从数据库加载日 K 线.
        返回 DataFrame (date 为 DatetimeIndex).
        """
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                """
                SELECT date, open, high, low, close, volume, amount,
                       pct_change, turnover
                FROM daily_bars
                WHERE symbol = ?
                  AND adjust = ?
                  AND date >= ?
                  AND date <= ?
                ORDER BY date ASC
                """,
                conn,
                params=(symbol, adjust, start_date, end_date),
                parse_dates=["date"],
            )

        if not df.empty:
            df = df.set_index("date")

        return df

    def stored_symbols(self, candidates) -> List[str]:
        """Stored symbol strings equal to any candidate, ignoring case.

        Ordered by the candidates (then exact case first); a symbol counts as
        stored when it has bars or recorded coverage.
        """
        wanted = [c for c in dict.fromkeys(candidates) if c]
        if not wanted:
            return []
        upper = [c.upper() for c in wanted]
        marks = ",".join("?" * len(upper))
        with sqlite3.connect(self.db_path) as conn:
            found = {row[0] for row in conn.execute(
                f"SELECT DISTINCT symbol FROM daily_bars WHERE UPPER(symbol) IN ({marks}) "
                f"UNION SELECT DISTINCT symbol FROM bar_coverage WHERE UPPER(symbol) IN ({marks})",
                upper + upper,
            ).fetchall()}
        rank = {u: i for i, u in reversed(list(enumerate(upper)))}
        exact = set(wanted)
        return sorted(found, key=lambda s: (rank[s.upper()], s not in exact, s))

    def get_sources(self, symbol: str, adjust: str, start_date=None, end_date=None) -> List[str]:
        """Distinct sources of the recorded downloads/imports overlapping the range."""
        lo = _day(start_date) if start_date is not None else "0000-00-00"
        hi = _day(end_date) if end_date is not None else "9999-99-99"
        with sqlite3.connect(self.db_path) as conn:
            return [row[0] for row in conn.execute(
                "SELECT source FROM bar_downloads WHERE symbol=? AND adjust=? AND start_date<=? AND end_date>=? "
                "GROUP BY source ORDER BY MAX(fetched_at) DESC, source",
                (symbol, adjust, hi, lo),
            ).fetchall()]

    ADJUST_ORDER = ("qfq", "", "hfq")

    def list_available(self, symbols=None) -> List[dict]:
        """Per stored symbol, the adjustments that have bars.

        [{"symbol": "600519", "adjustments": [{"adjust": "", "rows": 242,
          "first": "2024-01-02", "last": "2024-12-31",
          "coverage": [["2024-01-01", "2024-12-31"]], "sources": ["import"]}, ...]}, ...]

        Adjustments are ordered qfq, "" (不复权), hfq; sources newest first
        ("import" is a CSV import, "unknown" a cache written before sources
        were logged). `symbols` limits the listing to those exact keys.
        """
        where, params = "", []
        if symbols is not None:
            symbols = list(dict.fromkeys(symbols))
            if not symbols:
                return []
            where = f" WHERE symbol IN ({','.join('?' * len(symbols))})"
            params = symbols
        with sqlite3.connect(self.db_path) as conn:
            counts = conn.execute(
                "SELECT symbol, adjust, COUNT(*), MIN(date), MAX(date) FROM daily_bars"
                + where + " GROUP BY symbol, adjust", params).fetchall()
            coverage = conn.execute(
                "SELECT symbol, adjust, start_date, end_date FROM bar_coverage" + where
                + " ORDER BY start_date", params).fetchall()
            sources = conn.execute(
                "SELECT symbol, adjust, source FROM bar_downloads" + where
                + " GROUP BY symbol, adjust, source ORDER BY MAX(fetched_at) DESC, source", params).fetchall()
        spans, origins = {}, {}
        for symbol, adjust, start, end in coverage:
            spans.setdefault((symbol, adjust), []).append([start, end])
        for symbol, adjust, source in sources:
            origins.setdefault((symbol, adjust), []).append(source)
        rank = {value: i for i, value in enumerate(self.ADJUST_ORDER)}
        listing = {}
        for symbol, adjust, rows, first, last in counts:
            listing.setdefault(symbol, []).append({
                "adjust": adjust, "rows": rows, "first": first, "last": last,
                "coverage": spans.get((symbol, adjust), []),
                "sources": origins.get((symbol, adjust), []) or ["unknown"],
            })
        return [
            {"symbol": symbol, "adjustments": sorted(items, key=lambda i: rank.get(i["adjust"], 9))}
            for symbol, items in sorted(listing.items())
        ]

    def describe(self, symbols=None) -> List[dict]:
        """``list_available`` with what the 数据 tab shows per instrument: where and when it came from and
        whether its bars are complete.

        Each adjustment gains ``fetched_at`` (the latest recorded download or import, None when none was
        recorded) and ``complete`` (True when ``first``..``last`` lies inside one coverage interval, False
        when intervals were recorded but none holds them, None when nothing was recorded: unknown, not
        "complete"). Each instrument gains ``source`` (that of its latest download, None for a cache written
        before sources were logged), ``fetched_at`` (its latest) and ``complete`` (False when one adjustment
        is incomplete, None when one is unknown, else True).
        """
        listing = self.list_available(symbols)
        if not listing:
            return []
        wanted = [entry["symbol"] for entry in listing]
        marks = ",".join("?" * len(wanted))
        with sqlite3.connect(self.db_path) as conn:
            latest = conn.execute(
                f"SELECT symbol, adjust, source, fetched_at FROM bar_downloads WHERE symbol IN ({marks}) "
                "ORDER BY fetched_at", wanted).fetchall()
        newest: Dict[Tuple[str, str], Tuple[str, str]] = {}
        for symbol, adjust, source, fetched_at in latest:       # ascending: the last one wins
            newest[(symbol, adjust)] = (source, fetched_at)
        described = []
        for entry in listing:
            adjustments, sources = [], []
            for item in entry["adjustments"]:
                source, fetched_at = newest.get((entry["symbol"], item["adjust"]), (None, None))
                spans = item["coverage"]
                complete = (None if not spans else
                            any(start <= item["first"] and item["last"] <= end for start, end in spans))
                adjustments.append({**item, "fetched_at": fetched_at, "complete": complete})
                if fetched_at is not None:
                    sources.append((fetched_at, source if source and source != "unknown" else None))
            states = [item["complete"] for item in adjustments]
            last = max(sources, key=lambda pair: pair[0]) if sources else (None, None)
            described.append({
                "symbol": entry["symbol"], "adjustments": adjustments,
                "source": last[1], "fetched_at": last[0],
                "complete": False if False in states else (None if None in states else True),
            })
        return described

    def get_available_symbols(self) -> List[str]:
        """查询数据库中已有数据的所有标的"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol")
            return [row[0] for row in cursor.fetchall()]

    def get_date_range(self, symbol: str, adjust: str = "qfq"):
        """查询某标的在数据库中的日期范围"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT MIN(date), MAX(date) FROM daily_bars WHERE symbol=? AND adjust=?",
                (symbol, adjust),
            )
            row = cursor.fetchone()
            return row[0], row[1]

    # ------------------------------------------------------------------ #
    #  覆盖区间 (cache coverage)
    #
    #  A cache hit needs proof that the range was fetched, not just bars near its
    #  ends: bar dates cannot tell an interior hole or a missing tail from
    #  non-trading days, a late listing or a suspension. Every successful download
    #  (and CSV import) records the date interval it covers in bar_coverage, and a
    #  request is a hit only if its range lies inside one recorded interval.
    #  Overlapping and adjacent intervals are merged, so containment in the union
    #  is containment in one row - for adjusted series only after the anchors were
    #  compared on a bar both downloads hold (record_coverage); otherwise they stay
    #  separate and no single hit spans the seam. Bars cached without a record
    #  (files written before this table existed) are a miss until the next
    #  successful download.
    # ------------------------------------------------------------------ #

    @staticmethod
    def last_complete_day(now=None, market: Optional[str] = None) -> pd.Timestamp:
        """Last date (naive, exchange calendar) whose daily bar is final at `now`.

        market CN / HK / US: today's date in the exchange's time zone once today's
        bar is final (bar_final_at: the session calendar's close plus the
        publication margin), otherwise the day before - a bar dated today fetched
        before then is provisional. A naive `now` is this machine's local time.
        Without a market (a symbol that does not resolve) the day before `now` in
        local time, which is safe only for markets in the local time zone.
        """
        if market not in MARKETS:
            return pd.Timestamp(_moment(now).date()) - pd.Timedelta(days=1)
        there = _exchange_clock(now, market)
        today = pd.Timestamp(there.date())
        return today if there >= bar_final_at(today, market) else today - pd.Timedelta(days=1)

    @staticmethod
    def exchange_day(now=None, market: Optional[str] = None) -> pd.Timestamp:
        """Today's date (naive) in the exchange's time zone; local time without a market."""
        if market not in MARKETS:
            return pd.Timestamp(_moment(now).date())
        return pd.Timestamp(_exchange_clock(now, market).date())

    def provisional_dates(self, symbol: str, adjust: str, start_date, end_date) -> List[str]:
        """Dates in [start_date, end_date] of stored rows that were written while
        provisional (stored_while_provisional): an earlier version, or an import made
        before the day's close was final, kept the day's morning value."""
        market = market_of(symbol)
        with sqlite3.connect(self.db_path) as conn:
            # A row written two local days after its date is final in every market.
            rows = conn.execute(
                "SELECT date, created_at FROM daily_bars WHERE symbol=? AND adjust=? AND date>=? AND date<=? "
                "AND (created_at IS NULL OR created_at < date(date, '+2 day')) ORDER BY date",
                (symbol, adjust, _day(start_date), _day(end_date)),
            ).fetchall()
        return [day for day, created_at in rows if stored_while_provisional(day, created_at, market)]

    def discard_provisional(self, symbol: str, adjust: str, start_date, end_date) -> List[str]:
        """Delete the rows provisional_dates finds in the range (after a download of
        that range: the provider's answer replaces them, or the day has no bar)."""
        dates = self.provisional_dates(symbol, adjust, start_date, end_date)
        if dates:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany("DELETE FROM daily_bars WHERE symbol=? AND adjust=? AND date=?",
                                 [(symbol, adjust, day) for day in dates])
                conn.commit()
            logger.warning("[DataStore] %s (%s) 删除了收盘前存入的临时 K 线 %s", symbol, adjust, ", ".join(dates))
        return dates

    def record_coverage(
        self,
        symbol: str,
        adjust: str,
        start_date: str,
        end_date: str,
        fetched_at: Optional[str] = None,
        source: str = "import",
    ) -> Optional[tuple]:
        """Record that [start_date, end_date] is complete in the cache, merging
        it with overlapping or adjacent intervals. Returns the merged interval
        holding end_date (None when nothing was recorded). Details in last_record.

        Adjusted series (qfq/hfq), when save_daily just wrote this symbol and
        adjustment (the normal download/import flow):
          - cached bars inside the range that the saved frame does not contain
            came from an older download and were never compared: the range is
            recorded around them (CoverageReport.unverified), so no interval
            vouches for a row of another anchor;
          - an interval is merged with a recorded one only when the two share a
            bar that was in both the stored series and the new frame, and was
            compared by save_daily (see _joins_without_seam).
        Without such a save (coverage recorded on its own), two intervals that
        both hold bars are never merged: nothing was compared.

        The end is clamped to last_complete_day(fetched_at) (default: now): a day
        whose bar was not final then - today before the market's close, for a CSV
        uploaded in the morning - is never coverage."""
        fetched_at = fetched_at or _current_time().isoformat(timespec="seconds")
        start = pd.Timestamp(start_date).normalize()
        end = min(pd.Timestamp(end_date).normalize(), self.last_complete_day(fetched_at, market_of(symbol)))
        if end < start:
            # Nothing final to record (last_record describes this call, not an earlier one).
            self.last_record = CoverageReport(symbol, adjust, (_day(start), _day(end)))
            return None
        # The save this call records: the latest one for the symbol and adjustment
        # whose bars fall in the range (a save of another range is left for its own call).
        saved = self._saved.get((symbol, adjust))
        if saved is not None and any(_day(start) <= day <= _day(end) for day in saved.dates):
            del self._saved[(symbol, adjust)]
        else:
            saved = None
        report = CoverageReport(symbol, adjust, (_day(start), _day(end)))
        result = None
        with sqlite3.connect(self.db_path) as conn:
            # Read-merge-write under the write lock so concurrent runs cannot interleave.
            conn.execute("BEGIN IMMEDIATE")
            pieces = [(start, end)]
            if adjust in ADJUSTED and saved is not None:
                new_dates = set(saved.dates)
                report.unverified = [row[0] for row in conn.execute(
                    "SELECT date FROM daily_bars WHERE symbol=? AND adjust=? AND date>=? AND date<=? ORDER BY date",
                    (symbol, adjust, _day(start), _day(end)),
                ).fetchall() if row[0] not in new_dates]
                pieces = _split_around(start, end, report.unverified)
            for lo, hi in pieces:
                result = self._merge_interval(conn, symbol, adjust, _day(lo), _day(hi), fetched_at, saved, report)
            conn.execute(
                "INSERT INTO bar_downloads (symbol, adjust, start_date, end_date, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, adjust, _day(start), _day(end), source or "unknown", fetched_at),
            )
            conn.commit()
        self.last_record = report
        if report.unverified:
            logger.warning(
                "[DataStore] %s (%s) %s~%s: 缓存中 %s 的 K 线来自更早的下载, 本次下载没有这些日期, "
                "无法核对复权锚点, 不计入覆盖区间", symbol, adjust, _day(start), _day(end), ", ".join(report.unverified))
        return result

    def _merge_interval(self, conn, symbol: str, adjust: str, lo: str, hi: str, fetched_at: str,
                        saved: Optional[SaveReport], report: CoverageReport) -> tuple:
        rows = conn.execute(
            """
            SELECT start_date, end_date, fetched_at FROM bar_coverage
            WHERE symbol=? AND adjust=? AND start_date<=? AND end_date>=?
            """,
            (symbol, adjust, _day(pd.Timestamp(hi) + pd.Timedelta(days=1)),
             _day(pd.Timestamp(lo) - pd.Timedelta(days=1))),
        ).fetchall()
        if adjust in ADJUSTED:
            joined = [row for row in rows if self._joins_without_seam(conn, symbol, adjust, lo, hi, row, saved)]
            report.kept_apart += [(row[0], row[1]) for row in rows if row not in joined]
            rows = joined
        merged_start = min([lo] + [row[0] for row in rows])
        merged_end = max([hi] + [row[1] for row in rows])
        merged_fetched = max([fetched_at] + [row[2] for row in rows])
        conn.executemany(
            "DELETE FROM bar_coverage WHERE symbol=? AND adjust=? AND start_date=?",
            [(symbol, adjust, row[0]) for row in rows],
        )
        conn.execute(
            "INSERT OR REPLACE INTO bar_coverage (symbol, adjust, start_date, end_date, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (symbol, adjust, merged_start, merged_end, merged_fetched),
        )
        report.recorded.append((merged_start, merged_end))
        return merged_start, merged_end

    def record_download(
        self,
        symbol: str,
        adjust: str,
        start_date: str,
        end_date: str,
        fetched_at: Optional[str] = None,
        source: str = "download",
    ) -> Optional[tuple]:
        """Record a successful download of the requested range.

        The whole requested interval is recorded even when the source returned
        fewer bars (listed later, suspended, delisted): it had nothing more, and
        re-downloading every run would not change that. The end is clamped to
        last_complete_day(fetched_at, market of the symbol): a bar dated today
        fetched before the market's close is provisional and never coverage.
        """
        return self.record_coverage(symbol, adjust, start_date, end_date, fetched_at, source=source)

    @staticmethod
    def _joins_without_seam(conn, symbol: str, adjust: str, start: str, end: str, interval,
                            saved: Optional[SaveReport] = None) -> bool:
        """Can the new interval [start, end] be merged with a recorded one?

        Only when the anchors were actually compared on a bar the two share: a
        date inside both intervals that was in the stored series and in the frame
        save_daily just wrote (SaveReport.compared_dates). A bar present only in
        the old series proves nothing. Without a comparison the join is still
        safe when one side has no bars of its own (a download that returned
        nothing, or coverage recorded before any bar), since nothing is spliced.
        """
        def bar_dates(lo, hi):
            if lo > hi:
                return []
            return [row[0] for row in conn.execute(
                "SELECT date FROM daily_bars WHERE symbol=? AND adjust=? AND date>=? AND date<=?",
                (symbol, adjust, lo, hi),
            ).fetchall()]

        old_start, old_end = interval[0], interval[1]
        if saved is None:
            # Nothing was compared: only a side without bars can join.
            return not bar_dates(old_start, old_end) or not bar_dates(start, end)
        shared_lo, shared_hi = max(start, old_start), min(end, old_end)
        if any(shared_lo <= day <= shared_hi for day in saved.compared_dates):
            return True
        new = set(saved.dates)
        new_has_bars = any(start <= day <= end for day in new)
        old_has_own_bars = any(day not in new for day in bar_dates(old_start, old_end))
        return not new_has_bars or not old_has_own_bars

    def get_coverage(self, symbol: str, adjust: str = "qfq") -> List[tuple]:
        """Recorded (start_date, end_date, fetched_at) intervals, oldest first."""
        with sqlite3.connect(self.db_path) as conn:
            return [tuple(row) for row in conn.execute(
                "SELECT start_date, end_date, fetched_at FROM bar_coverage "
                "WHERE symbol=? AND adjust=? ORDER BY start_date",
                (symbol, adjust),
            ).fetchall()]

    def download_times(self, symbol: str, adjust: str, day: str) -> List[str]:
        """fetched_at of every recorded download or import (bar_downloads) whose interval holds `day`."""
        with sqlite3.connect(self.db_path) as conn:
            return [row[0] for row in conn.execute(
                "SELECT fetched_at FROM bar_downloads WHERE symbol=? AND adjust=? AND start_date<=? AND end_date>=? "
                "AND fetched_at IS NOT NULL", (symbol, adjust, _day(day), _day(day)),
            ).fetchall()]

    def is_data_available(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        now=None,
    ) -> bool:
        """
        检查本地缓存是否已记录覆盖整个请求区间 (见上方覆盖区间说明).

        结束日期按 last_complete_day(now) 截断, 与记录下载时相同. 截断后为空的
        区间 (从今天开始的请求) 不算命中. A range holding a row that was stored while
        provisional (provisional_dates) is not covered either, whatever interval was
        recorded: that row is the morning value, not the day's bar.
        """
        try:
            start = pd.Timestamp(start_date).normalize()
            end = min(pd.Timestamp(end_date).normalize(), self.last_complete_day(now, market_of(symbol)))
        except (TypeError, ValueError):
            return False
        if end < start:
            return False
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM bar_coverage
                WHERE symbol=? AND adjust=? AND start_date<=? AND end_date>=?
                LIMIT 1
                """,
                (symbol, adjust, _day(start), _day(end)),
            ).fetchone()
        return row is not None and not self.provisional_dates(symbol, adjust, start, end)

    def delete_symbol(self, symbol: str) -> int:
        """删除某标的的所有数据 (及其覆盖记录)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM daily_bars WHERE symbol=?", (symbol,)
            )
            conn.execute("DELETE FROM bar_coverage WHERE symbol=?", (symbol,))
            conn.execute("DELETE FROM bar_downloads WHERE symbol=?", (symbol,))
            conn.commit()
        logger.info(f"[DataStore] 删除 {symbol} 的 {cursor.rowcount} 条数据")
        return cursor.rowcount

    def get_stats(self) -> dict:
        """获取数据库统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            symbols = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM daily_bars"
            ).fetchone()[0]
            total_rows = conn.execute(
                "SELECT COUNT(*) FROM daily_bars"
            ).fetchone()[0]
            date_range = conn.execute(
                "SELECT MIN(date), MAX(date) FROM daily_bars"
            ).fetchone()

        return {
            "symbols": symbols,
            "total_rows": total_rows,
            "earliest_date": date_range[0],
            "latest_date": date_range[1],
            "db_path": self.db_path,
        }

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _init_db(self):
        """初始化数据表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_bars (
                    symbol      TEXT NOT NULL,
                    date        TEXT NOT NULL,
                    open        REAL,
                    high        REAL,
                    low         REAL,
                    close       REAL,
                    volume      REAL,
                    amount      REAL,
                    pct_change  REAL,
                    turnover    REAL,
                    adjust      TEXT DEFAULT 'qfq',
                    created_at  TEXT DEFAULT (datetime('now','localtime')),
                    PRIMARY KEY (symbol, date, adjust)
                )
                """
            )
            # 创建索引
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbol_date ON daily_bars(symbol, date)"
            )
            # 覆盖区间表; IF NOT EXISTS 让旧缓存文件在打开时原地升级, 可重复执行
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bar_coverage (
                    symbol      TEXT NOT NULL,
                    adjust      TEXT NOT NULL,
                    start_date  TEXT NOT NULL,
                    end_date    TEXT NOT NULL,
                    fetched_at  TEXT NOT NULL,
                    PRIMARY KEY (symbol, adjust, start_date)
                )
                """
            )
            # 下载/导入记录 (来源), 同样原地升级
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bar_downloads (
                    symbol      TEXT NOT NULL,
                    adjust      TEXT NOT NULL,
                    start_date  TEXT NOT NULL,
                    end_date    TEXT NOT NULL,
                    source      TEXT NOT NULL,
                    fetched_at  TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bar_downloads ON bar_downloads(symbol, adjust)"
            )
            conn.commit()

        logger.debug(f"[DataStore] 数据库初始化完成: {self.db_path}")
