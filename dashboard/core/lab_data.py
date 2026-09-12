"""Explicit, immutable research datasets. No portfolio or account access."""
from __future__ import annotations

import io
import re
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 25000
MAX_SYMBOLS = 10
SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:_-]{0,31}$")
COLUMNS = ["open", "high", "low", "close", "volume"]


def validate_records(records):
    if not records or len(records) > MAX_ROWS:
        raise ValueError(f"数据需要包含 1–{MAX_ROWS} 行")
    df = pd.DataFrame(records)
    required = {"date", "symbol", *COLUMNS}
    if not required.issubset(df.columns):
        raise ValueError("需要 date,symbol,open,high,low,close,volume 列")
    if set(df.columns) != required:
        raise ValueError("请仅导入标准行情列，避免附带无关个人信息")
    if df["symbol"].nunique() > MAX_SYMBOLS:
        raise ValueError(f"每份数据最多包含 {MAX_SYMBOLS} 只股票")
    if not df["symbol"].map(lambda x: isinstance(x, str) and bool(SYMBOL.fullmatch(x))).all():
        raise ValueError("股票代码格式无效")
    if not df["date"].map(lambda x: isinstance(x, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", x))).all():
        raise ValueError("日期需要使用 YYYY-MM-DD 格式")
    dates = pd.to_datetime(df["date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("存在无效日期")
    if df.duplicated(["symbol", "date"]).any():
        raise ValueError("同一股票存在重复日期")
    for _, group in df.groupby("symbol", sort=False):
        if not group["date"].is_monotonic_increasing:
            raise ValueError("每只股票的日期必须递增；请先检查原始数据")
    for col in COLUMNS:
        if df[col].map(lambda v: isinstance(v, bool)).any():
            raise ValueError("行情不能包含布尔值")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    values = df[COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(values).all() or np.abs(values).max() > 1e15:
        raise ValueError("行情存在缺失值、非有限数或超出范围的数值")
    if (df[COLUMNS[:4]] <= 0).any().any() or (df.volume < 0).any():
        raise ValueError("价格必须为正，成交量不能为负")
    if (df.high < df[["open", "close", "low"]].max(axis=1)).any() or (df.low > df[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("最高价/最低价与开收盘价格不一致")
    return df.sort_values(["symbol", "date"])[["date", "symbol", *COLUMNS]].to_dict("records")


def import_csv(content: bytes):
    if len(content) > MAX_BYTES:
        raise ValueError("CSV 最大为 8 MB")
    try:
        df = pd.read_csv(io.BytesIO(content), dtype={"date": str, "symbol": str}, encoding="utf-8-sig")
    except Exception as exc:
        raise ValueError("无法读取 UTF-8 CSV，请使用标准行情模板") from exc
    return validate_records(df.to_dict("records"))


def metadata(records, name, source, synthetic=False):
    dates = [row["date"] for row in records]
    return {"name": name, "source": source, "synthetic": synthetic,
            "symbols": sorted({r["symbol"] for r in records}), "rows": len(records),
            "start": min(dates), "end": max(dates), "frequency": "daily"}


def frames_from_records(records):
    df = pd.DataFrame(records)
    return {str(symbol): group.assign(date=pd.to_datetime(group.date)).set_index("date")[COLUMNS]
            for symbol, group in df.groupby("symbol", sort=True)}


def demo_records():
    rng = np.random.default_rng(20260912)
    dates = pd.bdate_range("2021-01-04", periods=900)
    records = []
    for symbol, phase in (("DEMO-A", 0), ("DEMO-B", 1)):
        volatility = .008 + .007 * (1 + np.sin(np.arange(len(dates)) / 35 + phase)) / 2
        close = 100 * np.exp(np.cumsum(rng.normal(.0002, volatility)))
        opening = close * np.exp(rng.normal(0, .003, len(dates)))
        for i, day in enumerate(dates):
            records.append({"date": day.strftime("%Y-%m-%d"), "symbol": symbol,
                            "open": float(opening[i]), "close": float(close[i]),
                            "high": float(max(opening[i], close[i]) * 1.006),
                            "low": float(min(opening[i], close[i]) * .994),
                            "volume": float(rng.integers(10000, 1000000))})
    return records


def cached_records(symbols, start, end, adjustment, database):
    """Open the existing bar cache read-only; never migrate or mutate it."""
    path = Path(database)
    if not path.is_file():
        raise ValueError("本地尚无行情缓存，请导入 CSV 或下载所选股票行情")
    records = []
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        for symbol in symbols:
            rows = db.execute("""SELECT date,open,high,low,close,volume FROM daily_bars
                WHERE symbol=? AND adjust=? AND date>=? AND date<=? ORDER BY date LIMIT ?""",
                (symbol, adjustment, start, end, MAX_ROWS + 1)).fetchall()
            if not rows:
                raise ValueError(f"所选区间缺少 {symbol} 的本地数据")
            records.extend({"symbol": symbol, **dict(zip(["date", *COLUMNS], row))} for row in rows)
    return validate_records(records)
