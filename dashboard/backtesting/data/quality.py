"""Data quality gates for backtesting inputs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import numpy as np


REQUIRED_DAILY_COLUMNS = ("open", "high", "low", "close", "volume")
SUPPORTED_ADJUSTMENTS = {"", "qfq", "hfq"}


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
            raise ValueError(f"数据质量检查失败 {self.symbol}: {details}")


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

    zero_volume_ratio = float((numeric["volume"] <= 0).sum() / len(numeric))
    if (numeric["volume"] < 0).any():
        report.errors.append("negative volume is invalid")
    report.metrics["zero_volume_ratio"] = zero_volume_ratio
    if zero_volume_ratio > max_zero_volume_ratio:
        report.errors.append(f"zero-volume ratio {zero_volume_ratio:.2%} > {max_zero_volume_ratio:.2%}")
    elif zero_volume_ratio > 0:
        report.warnings.append(f"zero-volume ratio {zero_volume_ratio:.2%}")

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
