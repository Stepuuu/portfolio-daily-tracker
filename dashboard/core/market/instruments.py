"""Exchange-qualified instruments and their per-source symbols.

The ledger identifies a holding as ``EXCH:CODE`` (``SHE:000001``). Every quote
source must be addressed from that prefix. A bare code is ambiguous:
``000001`` is 平安银行 on Shenzhen and the 上证指数 on Shanghai, and guessing
has priced the former as the latter. Indices therefore need an explicit
``IDX:`` prefix and a bare code is rejected.

The providers file an exchange's own indices under that exchange's market id
(Eastmoney ``1.000001``, Yahoo ``000001.SS`` is the Shanghai Composite), so an
*equity* ticker whose code lies in an index range would be priced as the
index. Such a ticker gets no provider symbol and ``symbol_problem`` says why:

    SHA 000xxx   上交所指数（上证、中证系列在上交所发布的代码）
    SHE 39xxxx   深交所指数（399xxx）与成交统计指标（395xxx）

No listed share, fund or bond uses these ranges. Only ``IDX:`` reaches an index.

Hong Kong codes are numbers: ``HKG:700`` and ``HKG:00700`` are one listing.
Provider symbols are derived from the numeric value and ``quote_key`` gives
both the same identity for quoting, while ``ticker`` keeps the spelling the
ledger stores.

B shares trade on the mainland exchanges (and their calendar) in foreign
currency: ``SHA:900xxx`` in US dollars, ``SHE:200xxx``/``SHE:201xxx`` in
Hong Kong dollars. ``currency`` says so and the providers quote them in it
(Eastmoney ``1.900901``, Yahoo ``900901.SS`` in USD). The ledger, however,
books every SHA/SHE holding in CNY, so a B-share holding cannot be valued:
``ledger_currency_problem`` says why.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple, Union

CN, HK, US = 'CN', 'HK', 'US'
MARKETS = (CN, HK, US)

# Must list the same exchanges and currencies as ``core.ledger.MARKETS``
# (a test compares them).
EXCHANGES = {
    'SHA': ('CNY', CN),
    'SHE': ('CNY', CN),
    'HKG': ('HKD', HK),
    'NASDAQ': ('USD', US),
    'NYSE': ('USD', US),
}

# code -> (market, currency, Eastmoney secid, Yahoo symbol).
# The Eastmoney ids were checked against the live endpoint; an index that is
# not listed here is rejected instead of being guessed.
INDICES = {
    '000001': (CN, 'CNY', '1.000001', '000001.SS'),   # 上证指数
    '000016': (CN, 'CNY', '1.000016', '000016.SS'),   # 上证50
    '000300': (CN, 'CNY', '1.000300', '000300.SS'),   # 沪深300
    '000688': (CN, 'CNY', '1.000688', '000688.SS'),   # 科创50
    '000852': (CN, 'CNY', '1.000852', '000852.SS'),   # 中证1000
    '000905': (CN, 'CNY', '1.000905', '000905.SS'),   # 中证500
    '399001': (CN, 'CNY', '0.399001', '399001.SZ'),   # 深证成指
    '399006': (CN, 'CNY', '0.399006', '399006.SZ'),   # 创业板指
    'HSI': (HK, 'HKD', '100.HSI', '^HSI'),            # 恒生指数
    'HSTECH': (HK, 'HKD', '124.HSTECH', '^HSTECH'),   # 恒生科技指数
    # Eastmoney files the Nasdaq Composite under the code NDX.
    'IXIC': (US, 'USD', '100.NDX', '^IXIC'),          # 纳斯达克综合指数
    'DJIA': (US, 'USD', '100.DJIA', '^DJI'),          # 道琼斯工业平均
    'SPX': (US, 'USD', '100.SPX', '^GSPC'),           # 标普500
}

# code -> Tencent (qt.gtimg.cn) symbol of each index above, checked against the
# live endpoint on 2026-11-19 (the answer names the index: 上证指数, 恒生指数,
# 纳斯达克, 道琼斯, 标普500). A test keeps the keys equal to ``INDICES``.
TENCENT_INDICES = {
    '000001': 'sh000001', '000016': 'sh000016', '000300': 'sh000300', '000688': 'sh000688',
    '000852': 'sh000852', '000905': 'sh000905', '399001': 'sz399001', '399006': 'sz399006',
    'HSI': 'hkHSI', 'HSTECH': 'hkHSTECH', 'IXIC': 'us.IXIC', 'DJIA': 'us.DJI', 'SPX': 'us.INX',
}

# Code ranges an exchange reserves for its indices: (pattern, exchange name, range).
INDEX_RANGES = {
    'SHA': (re.compile(r'000\d{3}'), '上交所', '000xxx'),
    'SHE': (re.compile(r'39\d{4}'), '深交所', '39xxxx'),
}

# B shares: code prefixes and trading currency per mainland exchange. The
# prefixes are the importer's (``core.importers.codes.B_SHARE_PREFIXES``; a
# test keeps them equal).
B_SHARE_PREFIXES = {'SHA': ('900',), 'SHE': ('200', '201')}
B_SHARE_CURRENCY = {'SHA': 'USD', 'SHE': 'HKD'}

_TICKER = re.compile(r'([A-Z]+):([A-Z0-9.\-]{1,20})')


class InstrumentError(ValueError):
    """The ticker is not an exchange-qualified instrument this layer knows."""


@dataclass(frozen=True)
class Instrument:
    ticker: str      # canonical ``EXCH:CODE``
    exchange: str    # SHA, SHE, HKG, NASDAQ, NYSE or IDX
    code: str
    currency: str    # CNY, HKD or USD
    market: str      # CN, HK or US: selects the trading calendar
    kind: str        # 'equity' (anything listed, ETFs included) or 'index'

    @property
    def quote_key(self) -> str:
        """The identity used for quoting and caching.

        Hong Kong codes are numbers, so ``HKG:700``, ``HKG:0700`` and
        ``HKG:00700`` are one listing and share the key ``HKG:00700``. Every
        other ticker is its own key. ``ticker`` keeps the spelling given, so
        what the ledger stores is never rewritten.
        """
        if self.exchange == 'HKG' and _hk_number(self.code) is not None:
            return f'HKG:{_hk_number(self.code):05d}'
        return self.ticker

    @property
    def symbol_problem(self) -> Optional[str]:
        """Why this ticker must not be sent to a provider at all, or ``None``.

        Set for an equity ticker whose code lies in an index range of its
        exchange: every provider would answer with the index.
        """
        if self.kind != 'equity' or self.exchange not in INDEX_RANGES:
            return None
        pattern, exchange, span = INDEX_RANGES[self.exchange]
        if not pattern.fullmatch(self.code):
            return None
        hint = f'；如要查看指数请写作 IDX:{self.code}' if self.code in INDICES else ''
        return (f'代码 {self.code} 在{exchange}属于指数编号段（{span}），不能按股票报价，'
                f'请核对代码和交易所前缀{hint}')

    @property
    def ledger_currency_problem(self) -> Optional[str]:
        """Why a ledger holding of this ticker cannot be valued, or ``None``.

        Set for B shares: they trade in USD or HKD while the ledger books every
        SHA/SHE holding, its cost and its trades in CNY.
        """
        booked = EXCHANGES.get(self.exchange, (None, None))[0]
        if self.kind != 'equity' or booked is None or self.currency == booked:
            return None
        return (f'B 股以 {self.currency} 交易，账本却按 {booked} 记录沪深持仓，'
                f'不能把 {self.currency} 价格当 {booked} 计入合计（账本暂不支持 B 股）')

    @property
    def eastmoney_secids(self) -> Tuple[str, ...]:
        """Candidate Eastmoney ``secid`` values, most likely first.

        Empty when the code cannot exist on that exchange, or would reach an
        index (``symbol_problem``), in which case the source reports the
        ticker as unsupported instead of guessing.
        """
        if self.kind == 'index':
            return (INDICES[self.code][2],)
        if self.symbol_problem:
            return ()
        if self.exchange in ('SHA', 'SHE'):
            if not re.fullmatch(r'\d{6}', self.code):
                return ()
            return (('1.' if self.exchange == 'SHA' else '0.') + self.code,)
        if self.exchange == 'HKG':
            number = _hk_number(self.code)
            return () if number is None else (f'116.{number:05d}',)
        symbol = self.code.replace('.', '_').replace('-', '_')
        if self.exchange == 'NASDAQ':
            return ('105.' + symbol,)
        # The ledger has no prefix for NYSE Arca / NYSE American, where most
        # US ETFs trade, so those are entered as NYSE. US symbols are unique
        # across exchanges, so trying 107 cannot return a different security.
        return ('106.' + symbol, '107.' + symbol)

    @property
    def eastmoney_secid(self) -> Optional[str]:
        candidates = self.eastmoney_secids
        return candidates[0] if candidates else None

    @property
    def yahoo_symbol(self) -> Optional[str]:
        if self.kind == 'index':
            return INDICES[self.code][3]
        if self.symbol_problem:
            return None
        if self.exchange in ('SHA', 'SHE'):
            if not re.fullmatch(r'\d{6}', self.code):
                return None
            return self.code + ('.SS' if self.exchange == 'SHA' else '.SZ')
        if self.exchange == 'HKG':
            number = _hk_number(self.code)
            return None if number is None else f'{number:04d}.HK'
        return self.code.replace('.', '-')

    @property
    def tencent_symbol(self) -> Optional[str]:
        """The Tencent (qt.gtimg.cn) symbol: ``sh600519``, ``sz000001``, ``hk00700``, ``usBRK.B``.

        ``None`` when the code cannot exist on that exchange or would reach an
        index (``symbol_problem``). Tencent spells US class shares with a dot
        and knows no lower-case symbol.
        """
        if self.kind == 'index':
            return TENCENT_INDICES[self.code]
        if self.symbol_problem:
            return None
        if self.exchange in ('SHA', 'SHE'):
            if not re.fullmatch(r'\d{6}', self.code):
                return None
            return ('sh' if self.exchange == 'SHA' else 'sz') + self.code
        if self.exchange == 'HKG':
            number = _hk_number(self.code)
            return None if number is None else f'hk{number:05d}'
        symbol = self.code.replace('-', '.')
        return 'us' + symbol if re.fullmatch(r'[A-Z0-9]+(?:\.[A-Z0-9]+)*', symbol) else None


def _hk_number(code: str) -> Optional[int]:
    """The numeric value of a Hong Kong code (1 to 5 digits, not zero)."""
    if not re.fullmatch(r'\d{1,5}', code) or int(code) == 0:
        return None
    return int(code)


def parse(ticker: Union[str, Instrument]) -> Instrument:
    """Parse ``EXCH:CODE``; raise ``InstrumentError`` for anything else."""
    if isinstance(ticker, Instrument):
        return ticker
    if not isinstance(ticker, str):
        raise InstrumentError('ticker must be a string such as SHE:000001')
    text = ticker.strip().upper()
    match = _TICKER.fullmatch(text)
    if not match:
        raise InstrumentError('use an exchange-qualified ticker such as SHE:000001 or IDX:000001')
    exchange, code = match.groups()
    if exchange == 'IDX':
        if code not in INDICES:
            raise InstrumentError('unknown index code')
        market, currency = INDICES[code][0], INDICES[code][1]
        return Instrument(text, exchange, code, currency, market, 'index')
    if exchange not in EXCHANGES:
        raise InstrumentError('unknown exchange prefix')
    currency, market = EXCHANGES[exchange]
    if re.fullmatch(r'\d{6}', code) and code.startswith(B_SHARE_PREFIXES.get(exchange, ())):
        currency = B_SHARE_CURRENCY[exchange]
    return Instrument(text, exchange, code, currency, market, 'equity')


def quote_key(ticker) -> str:
    """``parse(ticker).quote_key``, or the ticker itself when it does not parse."""
    try:
        return parse(ticker).quote_key
    except InstrumentError:
        return ticker if isinstance(ticker, str) else str(ticker)


def eastmoney_secid(ticker: Union[str, Instrument]) -> Optional[str]:
    return parse(ticker).eastmoney_secid


def yahoo_symbol(ticker: Union[str, Instrument]) -> Optional[str]:
    return parse(ticker).yahoo_symbol


def tencent_symbol(ticker: Union[str, Instrument]) -> Optional[str]:
    return parse(ticker).tencent_symbol
