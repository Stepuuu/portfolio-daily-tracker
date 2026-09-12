"""Offline provider-boundary checks for documented AKShare A-share volume units."""
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting.data.loader import DataLoader


@pytest.fixture
def akshare_frame(monkeypatch):
    frame = pd.DataFrame({
        "日期": ["2024-01-02"],
        "开盘": [10.0], "最高": [11.0], "最低": [9.0], "收盘": [10.0],
        "成交量": [10], "成交额": [10000.0],
    })
    calls = []

    def stock_zh_a_hist(**kwargs):
        calls.append(kwargs)
        return frame.copy(deep=True)

    def refuse_network(*args, **kwargs):
        raise AssertionError("This test must not call another provider")

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_a_hist=stock_zh_a_hist))
    for method in ("_fetch_baostock_daily", "_fetch_yahoo_daily"):
        if hasattr(DataLoader, method):
            monkeypatch.setattr(DataLoader, method, refuse_network)
    return frame, calls


@pytest.mark.parametrize("volume", [10, "10"])
def test_akshare_lots_become_shares_once_and_cache_copies_are_isolated(akshare_frame, volume):
    raw, calls = akshare_frame
    raw["成交量"] = [volume]
    loader = DataLoader()
    first = loader.get_daily("600000", "2024-01-02", "2024-01-03")

    assert first["volume"].tolist() == [1000.0]
    assert first["amount"].tolist() == [10000.0]
    assert first.attrs["volume_unit"] == "shares"
    assert raw["成交量"].tolist() == [volume]

    first.loc[first.index[0], "volume"] = 7
    first.attrs["volume_unit"] = "changed by caller"
    cached = loader.get_daily("600000", "2024-01-02", "2024-01-03")
    assert cached["volume"].tolist() == [1000.0]
    assert cached.attrs["volume_unit"] == "shares"
    assert len(calls) == 1
    assert calls[0]["period"] == "daily"


@pytest.mark.parametrize("volume", [-1, float("inf"), float("-inf"), float("nan")])
def test_invalid_akshare_volume_still_fails_quality_gate(akshare_frame, volume):
    raw, _ = akshare_frame
    raw["成交量"] = [volume]
    loader = DataLoader()
    with pytest.raises(ValueError, match="数据质量检查失败"):
        loader.get_daily("600000", "2024-01-02", "2024-01-03")
    assert not loader._cache


def test_akshare_non_numeric_volume_is_not_coerced_or_filled(akshare_frame):
    raw, _ = akshare_frame
    raw["成交量"] = ["not-a-number"]
    with pytest.raises(ValueError):
        DataLoader()._fetch_akshare_daily("600000", "2024-01-02", "2024-01-03", "qfq")


def test_unlabelled_existing_cache_is_not_automatically_converted(akshare_frame):
    _, calls = akshare_frame
    loader = DataLoader()
    existing = pd.DataFrame({"volume": [10.0]}, index=pd.to_datetime(["2024-01-02"]))
    loader._cache["600000_2024-01-02_2024-01-03_qfq"] = existing
    result = loader.get_daily("600000", "2024-01-02", "2024-01-03")
    assert result["volume"].tolist() == [10.0]
    assert "volume_unit" not in result.attrs
    assert not calls
