"""Data quality gates for backtesting inputs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

import pandas as pd
import numpy as np


REQUIRED_DAILY_COLUMNS = ("open", "high", "low", "close", "volume")
SUPPORTED_ADJUSTMENTS = {"", "qfq", "hfq"}

# Always attached to unadjusted (adjust="") series: the gap scan below only finds
# large re-basings, so finding nothing is not evidence that the series is clean.
UNADJUSTED_WARNING = (
    "unadjusted prices: dividends and corporate actions (bonus shares, splits, rights) are not accounted "
    "for - every ex-date drop is booked as a loss and dividend cash is never credited, so returns are "
    "understated or distorted; the gap scan only finds large re-basings and never cash dividends, so no "
    "suspected corporate action does not mean the series is clean; use qfq/hfq for reliable returns"
)
UNADJUSTED_INDEX_WARNING = (
    "price index: constituent dividends are not included, so returns are understated against a "
    "total-return investment that tracks the index"
)


class DataQualityError(ValueError):
    """Bars that fail the quality check; the message is the check's own Chinese text."""


@dataclass
class DataQualityReport:
    symbol: str
    rows: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_failed(self) -> None:
        if self.errors:
            details = "; ".join(self.errors)
            raise DataQualityError(f"数据质量检查失败 {self.symbol}: {details}")


def validate_daily_bars(
    df: pd.DataFrame,
    *,
    symbol: str = "",
    adjust: str = "qfq",
    max_missing_ratio: float = 0.0,
    max_zero_volume_ratio: float = 0.25,
) -> DataQualityReport:
    """Validate daily OHLCV bars before they enter a backtest."""
    report = DataQualityReport(symbol=symbol or "<unknown>", rows=len(df))

    if adjust not in SUPPORTED_ADJUSTMENTS:
        report.errors.append(f"unsupported adjust={adjust!r}")

    if df.empty:
        report.errors.append("empty dataframe")
        return report

    if df.columns.duplicated().any():
        report.errors.append("duplicate column names")
        return report

    missing_cols = [col for col in REQUIRED_DAILY_COLUMNS if col not in df.columns]
    if missing_cols:
        report.errors.append(f"missing columns: {', '.join(missing_cols)}")
        return report

    if not isinstance(df.index, pd.DatetimeIndex):
        report.errors.append("index must be a DatetimeIndex")
    elif df.index.hasnans:
        report.errors.append("index contains missing timestamps")
    elif df.index.normalize().duplicated().any():
        report.errors.append("daily bars must contain at most one observation per calendar date")

    duplicate_count = int(df.index.duplicated().sum())
    report.metrics["duplicate_index_count"] = duplicate_count
    if duplicate_count:
        report.errors.append(f"duplicate index rows: {duplicate_count}")

    if not df.index.is_monotonic_increasing:
        report.errors.append("index must be sorted ascending")

    numeric = df.loc[:, list(REQUIRED_DAILY_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    infinite_count = int(np.isinf(numeric.to_numpy(dtype=float)).sum())
    if infinite_count:
        report.errors.append(f"infinite OHLCV values: {infinite_count}")
    missing_ratio = float(numeric.isna().sum().sum() / numeric.size)
    report.metrics["missing_ratio"] = missing_ratio
    if missing_ratio > max_missing_ratio:
        report.errors.append(f"missing OHLCV ratio {missing_ratio:.2%} > {max_missing_ratio:.2%}")
    elif missing_ratio > 0:
        report.warnings.append(f"missing OHLCV ratio {missing_ratio:.2%}")

    non_positive_prices = int((numeric[["open", "high", "low", "close"]] <= 0).sum().sum())
    report.metrics["non_positive_price_count"] = non_positive_prices
    if non_positive_prices:
        report.errors.append(f"non-positive OHLC values: {non_positive_prices}")

    ohlc_bad = (
        (numeric["high"] < numeric["low"])
        | (numeric["open"] > numeric["high"])
        | (numeric["open"] < numeric["low"])
        | (numeric["close"] > numeric["high"])
        | (numeric["close"] < numeric["low"])
    )
    ohlc_bad_count = int(ohlc_bad.sum())
    report.metrics["ohlc_violation_count"] = ohlc_bad_count
    if ohlc_bad_count:
        report.errors.append(f"OHLC consistency violations: {ohlc_bad_count}")

    # A suspension day is a zero-volume bar whose open, high, low and close are one
    # positive price (sources fill it with the last traded close). It is valid data
    # however many there are; the broker never fills on it. Zero-volume bars with a
    # price range cannot come from a day without trades and count against the limit.
    zero_volume = numeric["volume"] <= 0
    prices = numeric[["open", "high", "low", "close"]]
    flat = np.isclose(prices.max(axis=1), prices.min(axis=1), rtol=1e-9, atol=0.0)
    suspended = (numeric["volume"] == 0) & flat & (prices > 0).all(axis=1)
    suspended_count = int(suspended.sum())
    zero_volume_ratio = float(zero_volume.sum() / len(numeric))
    unexplained_ratio = float((zero_volume & ~suspended).sum() / len(numeric))
    if (numeric["volume"] < 0).any():
        report.errors.append("negative volume is invalid")
    report.metrics["zero_volume_ratio"] = zero_volume_ratio
    report.metrics["suspended_bar_count"] = suspended_count
    if unexplained_ratio > max_zero_volume_ratio:
        report.errors.append(
            f"zero-volume ratio {unexplained_ratio:.2%} > {max_zero_volume_ratio:.2%} "
            f"(not counting {suspended_count} flat suspension bars)"
        )
    elif zero_volume_ratio > 0:
        report.warnings.append(
            f"zero-volume ratio {zero_volume_ratio:.2%} ({suspended_count} flat suspension bars)"
        )

    if adjust == "":
        sym = _resolve_quietly(symbol or None)
        report.metrics["corporate_actions_accounted"] = False
        report.warnings.append(UNADJUSTED_INDEX_WARNING if getattr(sym, "kind", "") == "index"
                               else UNADJUSTED_WARNING)
    if adjust == "" and not report.errors:
        events = scan_corporate_action_gaps(df, symbol=symbol or None)
        report.metrics["suspected_corporate_actions"] = [event.date for event in events]
        for event in events:
            report.warnings.append(
                f"suspected corporate action on {event.date}: close {event.change:+.1%} vs previous close "
                f"in unadjusted prices; use qfq/hfq or treat results as unreliable"
            )

    max_gap_days = None
    if isinstance(df.index, pd.DatetimeIndex) and len(df.index) >= 2:
        gaps = df.index.to_series().diff().dt.days.dropna()
        if not gaps.empty:
            max_gap_days = int(gaps.max())
            report.metrics["max_calendar_gap_days"] = max_gap_days
            if max_gap_days > 14:
                report.warnings.append(f"large calendar gap: {max_gap_days} days")

    return report


def require_valid_daily_bars(df: pd.DataFrame, *, symbol: str = "", adjust: str = "qfq") -> DataQualityReport:
    """Check provider output before cleaning or writing it to a cache.

    This function does not mutate, fill, sort, deduplicate or repair the input.
    Callers must preserve provenance for any explicit data corrections.
    """
    report = validate_daily_bars(df, symbol=symbol, adjust=adjust)
    report.raise_if_failed()
    return report


# --------------------------------------------------------------------------- #
#  Corporate-action gaps in unadjusted (不复权) prices
# --------------------------------------------------------------------------- #
#
# Raw prices drop on every ex-date: a 10送10 or a 2:1 split halves the price
# overnight, and a backtest on raw bars books that as a -50% loss. The scan
# flags day t when
#
#   1. the whole bar sits beyond the threshold T from the previous close
#      (high <= prev * (1 - T) for a drop, low >= prev * (1 + T) for a rise):
#      an ex-date re-bases the entire bar, a market crash that recovers
#      intraday does not;
#   2. the close is also beyond T from the median close of the 5 bars before,
#      so the bar after a one-day bad print (which returns to the old level)
#      is not flagged; and
#   3. none of the next 3 closes comes back within T/2 of that level (a bad
#      print reverts, a corporate action does not). A gap on the last bar is
#      flagged without this confirmation.
#
# T is the smallest move that cannot be an ordinary trading day: A-share main
# board and ETFs 15% (10% limit plus margin; ST stocks' 5% is covered too),
# ChiNext 25% from 2020-08-24 (20% limit) and 15% before, STAR 25%, HK and US
# 30% (no daily limit). An unresolvable symbol gets 15%.
#
# False positives: the first days after an IPO (no limit on STAR/ChiNext, 44%
# on the main board) and 退市整理期 or relisting days; resumption after a long
# halt in HK/US; genuine HK/US crashes or rallies of 30%+ that hold.
# False negatives: cash dividends (2-5%, always missed); 送转 or splits smaller
# than T (10送2 = -16.7% on ChiNext/STAR after 2020-08-24, 5:4 splits in HK/US);
# an ex-date that coincides with an opposite market move large enough to keep
# part of the bar inside T. Absence of a flag therefore never proves the raw
# series is clean; it only catches the large re-basings that wreck a result.

CHINEXT_20PCT_FROM = pd.Timestamp("2020-08-24")
GAP_CONFIRM_BARS = 3
GAP_LEVEL_WINDOW = 5


@dataclass
class GapEvent:
    date: str
    prev_close: float
    open: float
    close: float
    change: float              # close / previous close - 1
    ratio: float               # close / previous close (0.5 for a 2:1 split)
    threshold: float
    days_since_prev_bar: int   # calendar days; large after a suspension


def _resolve_quietly(symbol):
    if symbol is None:
        return None
    try:
        from backtesting.data.symbols import resolve
        return resolve(symbol)
    except (ValueError, ImportError):
        return None


def gap_threshold(symbol, day) -> float:
    """Smallest overnight move that cannot be an ordinary trading day (see above)."""
    sym = _resolve_quietly(symbol) if not hasattr(symbol, "market") else symbol
    if sym is None:
        return 0.15
    if sym.market in ("HK", "US"):
        return 0.30
    if sym.code.startswith(("688", "689")):
        return 0.25
    if sym.code.startswith(("300", "301")):
        return 0.25 if pd.Timestamp(day) >= CHINEXT_20PCT_FROM else 0.15
    return 0.15


def scan_corporate_action_gaps(df: pd.DataFrame, *, symbol=None) -> List[GapEvent]:
    """Suspected corporate-action dates in an unadjusted daily series (see above)."""
    if df is None or len(df) < 2 or not isinstance(df.index, pd.DatetimeIndex):
        return []
    if any(col not in df.columns for col in ("open", "high", "low", "close")):
        return []
    sym = _resolve_quietly(symbol) if not hasattr(symbol, "market") else symbol
    opens, highs, lows, closes = (
        pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float) for col in ("open", "high", "low", "close")
    )
    events: List[GapEvent] = []
    for t in range(1, len(df)):
        prev = closes[t - 1]
        if not prev > 0 or np.isnan([highs[t], lows[t], closes[t]]).any():
            continue
        threshold = gap_threshold(sym, df.index[t]) if sym is not None else 0.15
        level = float(np.nanmedian(closes[max(0, t - GAP_LEVEL_WINDOW):t]))
        down = highs[t] <= prev * (1 - threshold) and closes[t] <= level * (1 - threshold)
        up = lows[t] >= prev * (1 + threshold) and closes[t] >= level * (1 + threshold)
        if not (down or up):
            continue
        after = closes[t + 1:t + 1 + GAP_CONFIRM_BARS]
        after = after[~np.isnan(after)]
        if len(after):
            if down and after.max() > level * (1 - threshold / 2):
                continue
            if up and after.min() < level * (1 + threshold / 2):
                continue
        events.append(GapEvent(
            date=df.index[t].strftime("%Y-%m-%d"),
            prev_close=float(prev),
            open=float(opens[t]),
            close=float(closes[t]),
            change=float(closes[t] / prev - 1),
            ratio=float(closes[t] / prev),
            threshold=threshold,
            days_since_prev_bar=int((df.index[t] - df.index[t - 1]).days),
        ))
    return events
