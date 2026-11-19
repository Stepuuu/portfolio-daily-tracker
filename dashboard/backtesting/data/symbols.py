"""标的代码解析: 把用户输入的各种写法统一成一个规范键 ``EXCH:CODE``.

回测的缓存、下载路由和运行记录都按规范键识别标的. 规范键与项目账本的
``EXCH:CODE`` 写法一致 (解析交易所前缀用 ``core.market.instruments``), 只有
两处不同:

* 美股统一写成 ``US:CODE``. 美股代码在各交易所之间唯一, 账本里的
  ``NASDAQ:AAPL`` / ``NYSE:KO`` 与裸代码 ``AAPL`` 指向同一份缓存.
* 场内 ETF 归在所属交易所下 (``ETF:510300`` -> ``SHA:510300``), ``kind`` 为 ``etf``.

接受的写法::

    A 股     SHA:600519  600519  600519.SH  600519.SS  SH600519  (深市同理 SHE/SZ)
    ETF      ETF:510300  510300  SHA:510300  159915.SZ
    指数     IDX:000300  IDX:399001  IDX:HSI  IDX:SPX  ^GSPC (仅限已收录指数)
    港股     HKG:00700  HKG:700  0700.HK  00700  (规范为 5 位代码)
    美股     AAPL  AAPL.US  NASDAQ:AAPL  NYSE:BRK.B  BRK-B

裸 6 位代码按代码段判断为股票或 ETF, 绝不当作指数 (``000001`` 是深市平安银行,
上证指数要写 ``IDX:000001``). 上交所 000 段、深交所 399 段只有指数, 写成
``SHA:000001`` / ``000001.SH`` / ``399001`` 会报错并提示 ``IDX:`` 写法, 不猜.

已知限制: 北交所、B 股、LOF 与封闭式基金暂不支持; 港股、美股的 ETF 不单独识别
(``kind`` 为 ``stock``, 行情接口相同).

``legacy_keys`` 是旧版本可能写入缓存的键 (旧版按用户原样输入存储), 读取时作为
规范键的别名, 旧缓存因此继续可用.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple

from core.market.instruments import INDICES, InstrumentError, parse as parse_instrument

STOCK, ETF, INDEX = "stock", "etf", "index"

PROVIDER_A_STOCK = "akshare.stock_zh_a_hist"
PROVIDER_ETF = "akshare.fund_etf_hist_em"
PROVIDER_A_INDEX = "akshare.index_zh_a_hist"
PROVIDER_HK = "akshare.stock_hk_hist"
PROVIDER_YAHOO = "yfinance"

_EXAMPLES = "SHA:600519、600519、0700.HK、HKG:00700、AAPL、NASDAQ:AAPL、ETF:510300 或 IDX:000300"

# Prefix spellings accepted for each canonical exchange.
_PREFIXES = {
    "SHA": "SHA", "SH": "SHA", "SSE": "SHA",
    "SHE": "SHE", "SZ": "SHE", "SZSE": "SHE",
    "HKG": "HKG", "HK": "HKG", "HKEX": "HKG",
    "US": "US", "NASDAQ": "US", "NYSE": "US", "AMEX": "US",
    "ETF": "ETF", "IDX": "IDX",
}

# A-share code ranges: (prefixes, exchange, kind). Checked in order.
_CN_RANGES = (
    (("600", "601", "603", "605", "688", "689"), "SHA", STOCK),
    (("000", "001", "002", "003", "300", "301"), "SHE", STOCK),
    (("51", "56", "58"), "SHA", ETF),
    (("159",), "SHE", ETF),
)

_YAHOO_INDICES = {row[3]: code for code, row in INDICES.items() if row[3].startswith("^")}

_US_CODE = re.compile(r"[A-Z][A-Z0-9]{0,9}(?:[.\-][A-Z])?")


class SymbolError(ValueError):
    """The text is not a symbol the backtest data layer can route."""


@dataclass(frozen=True)
class Symbol:
    ticker: str            # 规范键, 也是新写入缓存的 symbol: SHA:600519 / HKG:00700 / US:AAPL / IDX:000300
    exchange: str          # SHA, SHE, HKG, US or IDX
    code: str
    market: str            # CN, HK or US
    kind: str              # stock, etf or index
    currency: str
    provider: str          # PROVIDER_* : 下载时调用的接口
    provider_symbol: str   # 传给该接口的代码: 600519 / 510300 / 000300 / 00700 / AAPL / ^GSPC
    legacy_keys: Tuple[str, ...] = ()

    @property
    def store_keys(self) -> Tuple[str, ...]:
        """Keys to look up in the cache, canonical first."""
        return (self.ticker,) + self.legacy_keys

    @property
    def adjustable(self) -> bool:
        """Indices have no corporate actions: qfq/hfq do not apply."""
        return self.kind != INDEX


def resolve(text) -> Symbol:
    """Resolve any accepted form to a ``Symbol``; raise ``SymbolError`` otherwise."""
    if isinstance(text, Symbol):
        return text
    if not isinstance(text, str):
        raise SymbolError(f"代码必须是文本, 例如 {_EXAMPLES}")
    raw = text.strip().upper().replace("：", ":")
    if not raw:
        raise SymbolError(f"代码不能为空; 请写成 {_EXAMPLES}")

    if ":" in raw:
        prefix, code = (part.strip() for part in raw.split(":", 1))
        exchange = _PREFIXES.get(prefix)
        if exchange is None:
            raise SymbolError(f"不支持的交易所前缀 {prefix}: (输入 {text!r}); 请写成 {_EXAMPLES}")
        if exchange == "IDX":
            return _index(code, text)
        if exchange == "ETF":
            return _cn_etf(code, text)
        if exchange in ("SHA", "SHE"):
            return _cn(code, exchange, text)
        if exchange == "HKG":
            return _hk(code, text)
        return _us(code, text)

    if raw.startswith("^"):
        if raw in _YAHOO_INDICES:
            return _index(_YAHOO_INDICES[raw], text)
        raise SymbolError(f"未收录的指数 {text!r}; 支持: " + "、".join(f"IDX:{c}" for c in INDICES))
    match = re.fullmatch(r"(\d{6})\.(SH|SS|SZ)", raw)
    if match:
        return _cn(match.group(1), "SHE" if match.group(2) == "SZ" else "SHA", text)
    match = re.fullmatch(r"(SH|SZ)(\d{6})", raw)
    if match:
        return _cn(match.group(2), "SHE" if match.group(1) == "SZ" else "SHA", text)
    match = re.fullmatch(r"(\d{1,5})\.HK", raw)
    if match:
        return _hk(match.group(1), text)
    if raw.endswith(".US"):
        return _us(raw[:-3], text)
    if re.fullmatch(r"\d{6}", raw):
        return _cn(raw, None, text)
    if re.fullmatch(r"\d{4,5}", raw):
        return _hk(raw, text)
    if re.fullmatch(r"[A-Z][A-Z0-9]*\.[A-Z]{2,}", raw):
        raise SymbolError(f"暂不支持该市场的代码 {text!r}; 目前支持 A 股、港股、美股: {_EXAMPLES}")
    if _US_CODE.fullmatch(raw):
        return _us(raw, text)
    raise SymbolError(f"无法识别的代码格式 {text!r}; 请写成 {_EXAMPLES}")


def _cn_range(code: str):
    for prefixes, exchange, kind in _CN_RANGES:
        if code.startswith(prefixes):
            return exchange, kind
    return None


def _cn_reject(code: str, exchange, text) -> None:
    """Raise the most helpful error for a 6-digit code outside the supported ranges."""
    if (exchange in (None, "SHE") and code.startswith("399")) or (exchange == "SHA" and code.startswith("000")):
        raise SymbolError(f"{text!r} 是指数代码 (该交易所此代码段只有指数); 指数请写 IDX:{code}"
                          + ("" if exchange is None or code.startswith("399") else f", 深市股票请写 SHE:{code}"))
    if code.startswith(("4", "8", "92")):
        raise SymbolError(f"暂不支持北交所代码 {text!r}")
    if code.startswith(("900", "200")):
        raise SymbolError(f"暂不支持 B 股代码 {text!r}")
    if code.startswith(("16", "18", "50", "52")):
        raise SymbolError(f"暂不支持 LOF 或封闭式基金 {text!r}; 场内 ETF 请写 ETF:代码")
    raise SymbolError(f"无法识别的 A 股代码 {text!r}; 请写成 {_EXAMPLES}")


def _cn(code: str, exchange, text) -> Symbol:
    if not re.fullmatch(r"\d{6}", code):
        raise SymbolError(f"A 股代码应为 6 位数字: {text!r}")
    found = _cn_range(code)
    if found is None or (exchange == "SHA" and code.startswith("000")):
        _cn_reject(code, exchange, text)
    actual, kind = found
    if exchange is not None and exchange != actual:
        names = {"SHA": "上交所", "SHE": "深交所"}
        raise SymbolError(f"{code} 是{names[actual]}代码, 不能写在 {exchange}: 下; 请写 {actual}:{code}")
    return _cn_symbol(actual, code, kind)


def _cn_etf(code: str, text) -> Symbol:
    found = _cn_range(code) if re.fullmatch(r"\d{6}", code) else None
    if found is None or found[1] != ETF:
        raise SymbolError(f"{text!r} 不是支持的场内 ETF 代码 (沪市 51/56/58 开头, 深市 159 开头)")
    return _cn_symbol(found[0], code, ETF)


def _cn_symbol(exchange: str, code: str, kind: str) -> Symbol:
    ticker = f"{exchange}:{code}"
    parse_instrument(ticker)  # the project's canonical parser must accept it
    suffixes = (".SH", ".SS") if exchange == "SHA" else (".SZ",)
    short = "SH" if exchange == "SHA" else "SZ"
    legacy = [code] + [code + s for s in suffixes] + [short + code, f"{short}:{code}"]
    if kind == ETF:
        legacy.append(f"ETF:{code}")
    provider = PROVIDER_ETF if kind == ETF else PROVIDER_A_STOCK
    return Symbol(ticker, exchange, code, "CN", kind, "CNY", provider, code, _unique(ticker, legacy))


def _index(code: str, text) -> Symbol:
    try:
        instrument = parse_instrument(f"IDX:{code}")
    except InstrumentError:
        raise SymbolError(f"未收录的指数代码 {text!r}; 支持: " + "、".join(f"IDX:{c}" for c in INDICES)) from None
    if instrument.market == "CN":
        provider, provider_symbol, legacy = PROVIDER_A_INDEX, instrument.code, []
    else:
        provider, provider_symbol = PROVIDER_YAHOO, instrument.yahoo_symbol
        legacy = [provider_symbol]
    return Symbol(instrument.ticker, "IDX", instrument.code, instrument.market, INDEX,
                  instrument.currency, provider, provider_symbol, _unique(instrument.ticker, legacy))


def _hk(code: str, text) -> Symbol:
    if not re.fullmatch(r"\d{1,5}", code) or int(code) == 0:
        raise SymbolError(f"港股代码应为 1-5 位数字 (如 HKG:00700): {text!r}")
    number = int(code)
    five, four, plain = f"{number:05d}", f"{number:04d}", str(number)
    ticker = f"HKG:{five}"
    parse_instrument(ticker)
    legacy = [f"{four}.HK", f"{five}.HK", f"{plain}.HK", five, four,
              f"HKG:{four}", f"HKG:{plain}", f"HK:{five}"]
    return Symbol(ticker, "HKG", five, "HK", STOCK, "HKD", PROVIDER_HK, five, _unique(ticker, legacy))


def _us(code: str, text) -> Symbol:
    code = code.strip().replace("-", ".")
    if not _US_CODE.fullmatch(code):
        raise SymbolError(f"无法识别的美股代码 {text!r}; 例如 AAPL、NASDAQ:AAPL、BRK.B")
    parse_instrument(f"NASDAQ:{code}")
    ticker = f"US:{code}"
    yahoo = code.replace(".", "-")
    legacy = [code, yahoo, f"{code}.US", f"NASDAQ:{code}", f"NYSE:{code}", f"AMEX:{code}"]
    return Symbol(ticker, "US", code, "US", STOCK, "USD", PROVIDER_YAHOO, yahoo, _unique(ticker, legacy))


def _unique(ticker: str, keys) -> Tuple[str, ...]:
    seen, out = {ticker}, []
    for key in keys:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out)
