"""One resolver from every accepted user form to the canonical store key."""
import re

import pytest

from backtesting.data.symbols import (
    ETF, INDEX, STOCK,
    PROVIDER_A_INDEX, PROVIDER_A_STOCK, PROVIDER_ETF, PROVIDER_HK, PROVIDER_YAHOO,
    Symbol, SymbolError, resolve,
)


@pytest.mark.parametrize("text,ticker,market,kind,provider,provider_symbol", [
    # A-share stocks: exchange prefix, bare code, suffixes, lower case
    ("SHA:600519", "SHA:600519", "CN", STOCK, PROVIDER_A_STOCK, "600519"),
    ("600519", "SHA:600519", "CN", STOCK, PROVIDER_A_STOCK, "600519"),
    ("600519.SH", "SHA:600519", "CN", STOCK, PROVIDER_A_STOCK, "600519"),
    ("600519.ss", "SHA:600519", "CN", STOCK, PROVIDER_A_STOCK, "600519"),
    ("sh600519", "SHA:600519", "CN", STOCK, PROVIDER_A_STOCK, "600519"),
    (" 688981 ", "SHA:688981", "CN", STOCK, PROVIDER_A_STOCK, "688981"),
    ("SHE:000001", "SHE:000001", "CN", STOCK, PROVIDER_A_STOCK, "000001"),
    ("000001", "SHE:000001", "CN", STOCK, PROVIDER_A_STOCK, "000001"),
    ("000001.SZ", "SHE:000001", "CN", STOCK, PROVIDER_A_STOCK, "000001"),
    ("SZ000001", "SHE:000001", "CN", STOCK, PROVIDER_A_STOCK, "000001"),
    ("300750", "SHE:300750", "CN", STOCK, PROVIDER_A_STOCK, "300750"),
    # Exchange-traded funds
    ("ETF:510300", "SHA:510300", "CN", ETF, PROVIDER_ETF, "510300"),
    ("510300", "SHA:510300", "CN", ETF, PROVIDER_ETF, "510300"),
    ("SHA:510300", "SHA:510300", "CN", ETF, PROVIDER_ETF, "510300"),
    ("159915.SZ", "SHE:159915", "CN", ETF, PROVIDER_ETF, "159915"),
    ("ETF:588000", "SHA:588000", "CN", ETF, PROVIDER_ETF, "588000"),
    # Indices need IDX:
    ("IDX:000300", "IDX:000300", "CN", INDEX, PROVIDER_A_INDEX, "000300"),
    ("IDX:000001", "IDX:000001", "CN", INDEX, PROVIDER_A_INDEX, "000001"),
    ("idx:399001", "IDX:399001", "CN", INDEX, PROVIDER_A_INDEX, "399001"),
    ("IDX:HSI", "IDX:HSI", "HK", INDEX, PROVIDER_YAHOO, "^HSI"),
    ("IDX:SPX", "IDX:SPX", "US", INDEX, PROVIDER_YAHOO, "^GSPC"),
    ("^GSPC", "IDX:SPX", "US", INDEX, PROVIDER_YAHOO, "^GSPC"),
    # Hong Kong: always the 5-digit exchange code
    ("0700.HK", "HKG:00700", "HK", STOCK, PROVIDER_HK, "00700"),
    ("700.HK", "HKG:00700", "HK", STOCK, PROVIDER_HK, "00700"),
    ("HKG:00700", "HKG:00700", "HK", STOCK, PROVIDER_HK, "00700"),
    ("HKG:0700", "HKG:00700", "HK", STOCK, PROVIDER_HK, "00700"),
    ("00700", "HKG:00700", "HK", STOCK, PROVIDER_HK, "00700"),
    ("9988.HK", "HKG:09988", "HK", STOCK, PROVIDER_HK, "09988"),
    # United States: one key whatever exchange prefix was typed
    ("AAPL", "US:AAPL", "US", STOCK, PROVIDER_YAHOO, "AAPL"),
    ("aapl", "US:AAPL", "US", STOCK, PROVIDER_YAHOO, "AAPL"),
    ("NASDAQ:AAPL", "US:AAPL", "US", STOCK, PROVIDER_YAHOO, "AAPL"),
    ("AAPL.US", "US:AAPL", "US", STOCK, PROVIDER_YAHOO, "AAPL"),
    ("NYSE:BRK.B", "US:BRK.B", "US", STOCK, PROVIDER_YAHOO, "BRK-B"),
    ("BRK-B", "US:BRK.B", "US", STOCK, PROVIDER_YAHOO, "BRK-B"),
])
def test_accepted_forms_resolve_to_one_canonical_key(text, ticker, market, kind, provider, provider_symbol):
    symbol = resolve(text)

    assert (symbol.ticker, symbol.market, symbol.kind) == (ticker, market, kind)
    assert (symbol.provider, symbol.provider_symbol) == (provider, provider_symbol)
    # The canonical key resolves to itself, and resolving a Symbol is a no-op.
    assert resolve(symbol.ticker) == symbol
    assert resolve(symbol) is symbol


def test_bare_six_digit_codes_are_stocks_never_indices():
    # 000001 is 平安银行 on Shenzhen and the 上证指数 on Shanghai; 000300 is the
    # 沪深300 index code. A bare code always names the listed security.
    for code in ("000001", "000300", "000016"):
        symbol = resolve(code)
        assert symbol.kind == STOCK and symbol.ticker == f"SHE:{code}"
        assert symbol.provider == PROVIDER_A_STOCK
    assert resolve("IDX:000001").ticker != resolve("000001").ticker


def test_currency_follows_the_market():
    assert [resolve(t).currency for t in ("600519", "0700.HK", "AAPL", "IDX:000300")] == [
        "CNY", "HKD", "USD", "CNY"]


@pytest.mark.parametrize("text,hint", [
    ("", "代码"),
    ("399001", "IDX:399001"),          # bare Shenzhen index code
    ("SHA:000001", "IDX:000001"),      # Shanghai has no 000 stocks; that is the index
    ("000001.SH", "IDX:000001"),
    ("SHE:399006", "IDX:399006"),
    ("IDX:999999", "指数"),             # not a known index
    ("XYZ:123", "前缀"),
    ("830799", "北交所"),
    ("920001", "北交所"),
    ("900901", "B 股"),
    ("ETF:600519", "ETF"),
    ("SHE:600519", "上交所"),           # a Shanghai code under the Shenzhen prefix
    ("12345678", "无法识别"),
    ("600519.HK", "无法识别"),
    ("7", "无法识别"),
    ("VOD.LSE", "暂不支持"),
    ("HKG:0", "港股"),
])
def test_unsupported_or_ambiguous_forms_raise_a_chinese_error(text, hint):
    with pytest.raises(SymbolError) as excinfo:
        resolve(text)

    message = str(excinfo.value)
    assert re.search(r"[一-鿿]", message), message
    assert hint in message
    assert isinstance(excinfo.value, ValueError)


def test_non_string_input_is_rejected():
    with pytest.raises(SymbolError):
        resolve(600519)


def test_legacy_store_keys_are_aliases_of_the_canonical_key():
    # Older versions stored bars under whatever the user typed.
    assert {"600519", "600519.SH", "600519.SS", "SH600519"} <= set(resolve("SHA:600519").legacy_keys)
    assert {"000001", "000001.SZ"} <= set(resolve("SHE:000001").legacy_keys)
    assert {"0700.HK", "00700.HK", "700.HK"} <= set(resolve("HKG:00700").legacy_keys)
    assert {"AAPL", "AAPL.US", "NASDAQ:AAPL", "NYSE:AAPL"} <= set(resolve("AAPL").legacy_keys)
    assert "^GSPC" in resolve("IDX:SPX").legacy_keys
    for text in ("600519", "0700.HK", "AAPL", "IDX:000300", "ETF:510300"):
        symbol = resolve(text)
        assert symbol.store_keys[0] == symbol.ticker
        assert symbol.ticker not in symbol.legacy_keys
        assert len(set(symbol.store_keys)) == len(symbol.store_keys)
        # Every legacy key a user could type resolves back to the same instrument.
        for key in symbol.legacy_keys:
            assert resolve(key).ticker == symbol.ticker, key


def test_index_and_stock_with_the_same_digits_never_share_store_keys():
    stock, index = resolve("SHE:000001"), resolve("IDX:000001")
    assert not set(stock.store_keys) & set(index.store_keys)
    assert "000001" not in index.store_keys


def test_symbol_is_hashable_and_immutable():
    symbol = resolve("600519")
    assert {symbol: 1}[resolve("SHA:600519")] == 1
    with pytest.raises(Exception):
        symbol.ticker = "x"
    assert isinstance(symbol, Symbol)
