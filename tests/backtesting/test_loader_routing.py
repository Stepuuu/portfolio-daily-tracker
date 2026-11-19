"""Offline routing checks: each symbol form reaches the right provider call.

The fake providers return frames shaped like the real ones (column names and
order as akshare 1.18 builds them; yfinance's single-level frame).
"""
import inspect
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting.data.loader import DataLoader
from backtesting.data.symbols import SymbolError

EM_COLUMNS = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率"]


def em_frame(close=10.0, volume=1000, amount=None, extra=None, days=("2024-01-30", "2024-01-31")):
    """An Eastmoney-shaped daily frame; `volume` is in the provider's own unit."""
    rows = []
    for day in days:
        rows.append([pd.Timestamp(day).date(), close, close, close * 1.01, close * 0.99, volume,
                     amount if amount is not None else close * volume, 2.0, 0.0, 0.0, 0.5])
    frame = pd.DataFrame(rows, columns=EM_COLUMNS)
    for name, value in (extra or {}).items():
        frame[name] = value
    return frame


class Providers:
    """Fake akshare + yfinance that record every call."""

    def __init__(self, monkeypatch, missing=()):
        self.calls = []
        self.frames = {
            "stock_zh_a_hist": em_frame(volume=100, amount=100_000.0, extra={"股票代码": "000001"}),
            "fund_etf_hist_em": em_frame(close=4.0, volume=10_000, amount=4_000_000.0),
            "index_zh_a_hist": em_frame(close=3500.0, volume=100_000, amount=2.0e9),
            "stock_hk_hist": em_frame(close=300.0, volume=1_000_000, amount=3.0e8),
        }
        functions = {name: self._recorder(name) for name in self.frames if name not in missing}
        monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(**functions))
        monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=self._yahoo))

    def _recorder(self, name):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            return self.frames[name].copy()
        return call

    def _yahoo(self, ticker, **kwargs):
        self.calls.append(("yfinance.download", {"ticker": ticker, **kwargs}))
        index = pd.DatetimeIndex(pd.to_datetime(["2024-01-30", "2024-01-31"]), name="Date")
        return pd.DataFrame({"Open": [190.0, 191.0], "High": [192.0, 193.0], "Low": [189.0, 190.0],
                             "Close": [191.0, 192.0], "Volume": [5.0e7, 6.0e7]}, index=index)

    @property
    def names(self):
        return [name for name, _ in self.calls]


@pytest.fixture
def providers(monkeypatch):
    return Providers(monkeypatch)


@pytest.mark.parametrize("text", ["SHE:000001", "000001", "000001.SZ"])
def test_shenzhen_stock_goes_to_the_stock_api_not_the_index(providers, text):
    DataLoader().get_daily(text, "2024-01-01", "2024-01-31")

    assert providers.names == ["stock_zh_a_hist"]
    kwargs = providers.calls[0][1]
    assert kwargs["symbol"] == "000001"
    assert (kwargs["start_date"], kwargs["end_date"], kwargs["adjust"]) == ("20240101", "20240131", "qfq")


@pytest.mark.parametrize("text", ["IDX:000300", "idx:000300"])
def test_index_goes_to_the_index_api(providers, text):
    frame = DataLoader().get_daily(text, "2024-01-01", "2024-01-31")

    assert providers.names == ["index_zh_a_hist"]
    kwargs = providers.calls[0][1]
    assert kwargs["symbol"] == "000300"
    assert "adjust" not in kwargs  # the index API has no adjustment parameter
    assert frame["close"].tolist() == [3500.0, 3500.0]


@pytest.mark.parametrize("text", ["ETF:510300", "510300", "159915"])
def test_etf_goes_to_the_fund_api(providers, text):
    DataLoader().get_daily(text, "2024-01-01", "2024-01-31", adjust="hfq")

    assert providers.names == ["fund_etf_hist_em"]
    kwargs = providers.calls[0][1]
    assert kwargs["symbol"] == text.split(":")[-1]
    assert kwargs["adjust"] == "hfq"


@pytest.mark.parametrize("text", ["0700.HK", "00700", "HKG:00700", "HKG:700"])
def test_hong_kong_goes_to_the_hk_api_with_five_digits(providers, text):
    DataLoader().get_daily(text, "2024-01-01", "2024-01-31", adjust="")

    assert providers.names == ["stock_hk_hist"]
    kwargs = providers.calls[0][1]
    assert kwargs["symbol"] == "00700"
    assert kwargs["adjust"] == ""


@pytest.mark.parametrize("text", ["AAPL", "AAPL.US", "NASDAQ:AAPL"])
def test_us_goes_to_yfinance_with_an_inclusive_end(providers, text):
    frame = DataLoader().get_daily(text, "2024-01-01", "2024-01-31")

    assert providers.names == ["yfinance.download"]
    kwargs = providers.calls[0][1]
    assert kwargs["ticker"] == "AAPL"
    assert kwargs["start"] == "2024-01-01"
    # yfinance treats end as exclusive: ask for one more day to get 2024-01-31.
    assert kwargs["end"] == "2024-02-01"
    assert kwargs["auto_adjust"] is True
    assert frame.index[-1] == pd.Timestamp("2024-01-31")


def test_us_index_goes_to_yfinance_with_the_yahoo_symbol(providers):
    DataLoader().get_daily("IDX:SPX", "2024-01-01", "2024-01-31")

    assert providers.names == ["yfinance.download"]
    assert providers.calls[0][1]["ticker"] == "^GSPC"


def test_yfinance_rows_outside_the_request_are_dropped(monkeypatch, providers):
    # Some yfinance versions return the bar dated `end` itself; never keep it.
    def wide(ticker, **kwargs):
        index = pd.DatetimeIndex(pd.to_datetime(["2023-12-29", "2024-01-30", "2024-02-01"]), name="Date")
        return pd.DataFrame({"Open": [1.0] * 3, "High": [1.0] * 3, "Low": [1.0] * 3,
                             "Close": [1.0] * 3, "Volume": [1.0] * 3}, index=index)

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=wide))
    frame = DataLoader().get_daily("AAPL", "2024-01-01", "2024-01-31")

    assert frame.index.tolist() == [pd.Timestamp("2024-01-30")]


def test_us_backward_adjustment_is_refused_not_relabelled(providers):
    # yfinance only offers forward (auto) adjustment; hfq must not silently become it.
    with pytest.raises(ValueError, match="后复权"):
        DataLoader().get_daily("AAPL", "2024-01-01", "2024-01-31", adjust="hfq")
    assert providers.calls == []


def test_unresolvable_symbols_fail_before_any_provider_call(providers):
    with pytest.raises(SymbolError):
        DataLoader().get_daily("SHA:000001", "2024-01-01", "2024-01-31")
    assert providers.calls == []


def test_missing_akshare_function_raises_a_clear_error(monkeypatch):
    Providers(monkeypatch, missing=("fund_etf_hist_em",))

    with pytest.raises(RuntimeError, match="fund_etf_hist_em"):
        DataLoader().get_daily("ETF:510300", "2024-01-01", "2024-01-31")


def test_get_daily_signature_is_unchanged():
    params = inspect.signature(DataLoader.get_daily).parameters
    assert list(params)[:5] == ["self", "symbol", "start_date", "end_date", "adjust"]
    assert params["adjust"].default == "qfq"


# Volume is stored in shares for every route (the broker's participation cap reads
# it as shares), so amount / (volume * close) is about 1 after normalisation.

def test_hk_volume_is_already_in_shares(providers):
    frame = DataLoader().get_daily("HKG:00700", "2024-01-01", "2024-01-31")

    assert frame["volume"].tolist() == [1_000_000.0, 1_000_000.0]
    assert frame.attrs["volume_unit"] == "shares"
    assert (frame["amount"] / (frame["volume"] * frame["close"])).round(6).tolist() == [1.0, 1.0]


def test_etf_volume_lots_become_shares(providers):
    frame = DataLoader().get_daily("ETF:510300", "2024-01-01", "2024-01-31")

    assert frame["volume"].tolist() == [1_000_000.0, 1_000_000.0]
    assert frame.attrs["volume_unit"] == "shares"
    assert (frame["amount"] / (frame["volume"] * frame["close"])).round(6).tolist() == [1.0, 1.0]


def test_a_share_volume_lots_become_shares(providers):
    frame = DataLoader().get_daily("SHE:000001", "2024-01-01", "2024-01-31")

    assert frame["volume"].tolist() == [10_000.0, 10_000.0]
    assert (frame["amount"] / (frame["volume"] * frame["close"])).round(6).tolist() == [1.0, 1.0]


def test_index_volume_lots_become_shares(providers):
    frame = DataLoader().get_daily("IDX:000300", "2024-01-01", "2024-01-31")

    assert frame["volume"].tolist() == [10_000_000.0, 10_000_000.0]
    assert frame.attrs["volume_unit"] == "shares"


def test_us_volume_is_in_shares(providers):
    frame = DataLoader().get_daily("AAPL", "2024-01-01", "2024-01-31")

    assert frame["volume"].tolist() == [5.0e7, 6.0e7]
    assert frame.attrs["volume_unit"] == "shares"


@pytest.mark.parametrize("ledger_ticker", ["HKG:700", "HKG:09988", "SHE:000001", "SHA:510300", "NASDAQ:AAPL"])
def test_research_lab_provider_symbols_reach_the_right_api(providers, ledger_ticker):
    # The Lab translates ledger tickers with core.lab_fetch.provider_symbol and
    # passes the result to get_daily. It keeps five HK digits ("09988.HK"), which
    # Yahoo does not list; routed by instrument, both spellings reach stock_hk_hist.
    from core.lab_fetch import provider_symbol

    DataLoader().get_daily(provider_symbol(ledger_ticker), "2024-01-01", "2024-01-31")

    expected = {
        "HKG:700": ("stock_hk_hist", "00700"), "HKG:09988": ("stock_hk_hist", "09988"),
        "SHE:000001": ("stock_zh_a_hist", "000001"), "SHA:510300": ("fund_etf_hist_em", "510300"),
        "NASDAQ:AAPL": ("yfinance.download", "AAPL"),
    }[ledger_ticker]
    name, kwargs = providers.calls[0]
    assert (name, kwargs.get("symbol", kwargs.get("ticker"))) == expected
