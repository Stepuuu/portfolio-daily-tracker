"""Daily bars for the agent's data tools.

``BarSource`` is the one seam between the tools and wherever bars come from.
``LoaderBarSource`` wraps the backtest ``DataLoader`` (read only, run in a
worker thread, a fresh loader per call so the loader's in-memory cache and the
backtest ``DataStore`` are never on this path). ``FakeBarSource`` serves
scripted bars for tests.

Every result states the provider and adjustment that were ACTUALLY used (read
from the loader's result, ``frame.attrs["source"]``, never assumed from the
market) and what range it covers::

    result = await get_bar_source().get_daily('SHE:000001', days=60)
    result.adjust        # 'qfq'
    result.coverage      # {first, last, rows, requested_start, requested_end, complete}

Tickers are the ledger's ``EXCH:CODE``. An A-share code is taken from the
exchange prefix and checked against it, never guessed from a bare code
(``SHE:000001`` is 平安银行, the 上证指数 is ``IDX:000001`` and is not served).
Failures raise ``BarError``; callers turn that into ``{error, source}``.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Union

from core.isolated_call import BUSY_TEXT, RESTART_TEXT, CallBusy, CallRestartRequired, CallTimeout, IsolatedCalls
from core.provider_errors import log_failure

from .instruments import Instrument, InstrumentError, parse

logger = logging.getLogger(__name__)

_SHANGHAI = timezone(timedelta(hours=8))

ADJUST_QFQ = 'qfq'                  # 前复权, A-shares
ADJUST_YAHOO = 'yahoo_auto_adjust'  # Yahoo split/dividend adjusted, US

# What the loader says it called (``frame.attrs["source"]``) -> what to cite,
# the adjustment a qfq request gets from it, and what its volume counts.
PROVIDER_LABELS = {
    'akshare.stock_zh_a_hist': 'akshare.stock_zh_a_hist（东方财富）',
    'akshare.fund_etf_hist_em': 'akshare.fund_etf_hist_em（东方财富场内ETF）',
    'akshare.stock_hk_hist': 'akshare.stock_hk_hist（东方财富港股）',
    'yfinance': 'yfinance（雅虎财经）',
}
PROVIDER_ADJUST = {
    'akshare.stock_zh_a_hist': ADJUST_QFQ, 'akshare.fund_etf_hist_em': ADJUST_QFQ,
    'akshare.stock_hk_hist': ADJUST_QFQ, 'yfinance': ADJUST_YAHOO,
}
VOLUME_LABELS = {'akshare.fund_etf_hist_em': '份'}      # everything else counts shares: 股

# After a long holiday the newest bar can be this many calendar days old and
# still be the latest session.
_MAX_LAST_BAR_AGE_DAYS = 10
_COLUMNS = ('open', 'high', 'low', 'close', 'volume')


class BarError(Exception):
    """Bars could not be produced. The message is safe to show to the model."""

    def __init__(self, message: str, source: str = 'bars'):
        super().__init__(message)
        self.source = source


@dataclass
class BarResult:
    ticker: str
    source: str
    adjust: str
    currency: str
    bars: List[Dict[str, Any]]            # oldest first: date, open, high, low, close, volume
    coverage: Dict[str, Any]
    fetched_at: str                       # ISO time with +08:00 offset
    volume_unit: str = 'shares'
    notes: List[str] = field(default_factory=list)
    provider: str = ''                    # the endpoint the loader reports having called
    market: str = ''                      # CN, HK or US: whose session clock applies
    volume_label: str = '股'              # what one unit of volume is, for the model: 股 or 份


class BarSource(Protocol):
    name: str

    def source_label(self, ticker: str) -> str:
        """What to cite for this ticker, usable even when a fetch fails."""

    async def get_daily(self, ticker: str, days: int, adjust: str = ADJUST_QFQ) -> BarResult:
        """The last ``days`` daily bars. Raise ``BarError`` on any failure."""


def _today(now: Optional[Callable[[], datetime]]) -> date:
    moment = now() if now else datetime.now(_SHANGHAI)
    return moment.date()


def _clean_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 4) if math.isfinite(number) else None


def build_coverage(bars: Sequence[Mapping[str, Any]], days: int, start: date, end: date) -> Dict[str, Any]:
    """``complete`` means: as many bars as asked for, and the newest is recent."""
    rows = len(bars)
    first = bars[0]['date'] if rows else None
    last = bars[-1]['date'] if rows else None
    recent = False
    if last:
        recent = (end - date.fromisoformat(last)).days <= _MAX_LAST_BAR_AGE_DAYS
    return {'first': first, 'last': last, 'rows': rows, 'requested_days': days,
            'requested_start': start.isoformat(), 'requested_end': end.isoformat(),
            'complete': bool(rows >= days and recent)}


def a_share_code(inst: Union[str, Instrument]) -> str:
    """The bare six-digit code of a mainland equity, taken from its exchange prefix.

    Raises ``BarError`` for indices, other markets, malformed codes and codes
    that cannot exist on the stated exchange (Shanghai codes start 5/6/9,
    Shenzhen 0/1/2/3): a mismatch is a typo that would otherwise silently
    return a different security.
    """
    try:
        inst = parse(inst)
    except InstrumentError as exc:
        raise BarError(str(exc), 'bars') from None
    if inst.kind != 'equity' or inst.exchange not in ('SHA', 'SHE'):
        raise BarError('此数据目前只支持A股个股，代码格式如 SHA:600519 或 SHE:000001', 'bars')
    if not re.fullmatch(r'\d{6}', inst.code):
        raise BarError(f'{inst.ticker} 不是有效的A股代码', 'bars')
    if inst.code[0] not in ('569' if inst.exchange == 'SHA' else '0123'):
        raise BarError(f'代码 {inst.code} 与交易所 {inst.exchange} 不匹配', 'bars')
    return inst.code


def _routed_provider(symbol: str, inst: Instrument) -> str:
    """The endpoint the loader's own routing table picks for ``symbol`` (before any fetch)."""
    try:
        from backtesting.data.symbols import resolve   # lazy: pulls in the backtest package
        return resolve(symbol).provider
    except Exception:
        if inst.market == 'HK':
            return 'akshare.stock_hk_hist'
        return 'akshare.stock_zh_a_hist' if inst.market == 'CN' else 'yfinance'


def _symbol_for_loader(inst: Instrument) -> tuple:
    """(symbol handed to DataLoader, the endpoint it will probably route to)."""
    if inst.kind == 'index':
        raise BarError('指数日线暂不支持，请使用个股代码', 'bars')
    if inst.exchange in ('SHA', 'SHE'):
        try:
            symbol = a_share_code(inst)
        except BarError as exc:
            raise BarError(str(exc), PROVIDER_LABELS['akshare.stock_zh_a_hist']) from None
    else:
        symbol = inst.yahoo_symbol
        if not symbol:
            raise BarError(f'{inst.ticker} 无法映射到数据源代码', PROVIDER_LABELS['yfinance'])
    return symbol, _routed_provider(symbol, inst)


class LoaderBarSource:
    """Bars from ``backtesting.data.loader.DataLoader``, off the event loop."""

    name = 'backtest-loader'

    def __init__(self, loader_factory: Optional[Callable[[], Any]] = None,
                 now: Optional[Callable[[], datetime]] = None, timeout: float = 30.0):
        self._loader_factory = loader_factory
        self._now = now
        self.timeout = timeout
        self._pools: Dict[str, IsolatedCalls] = {}

    def _pool_for(self, function: str) -> IsolatedCalls:
        """The call budget of one upstream function: a hung endpoint cannot starve the others."""
        if function not in self._pools:
            self._pools[function] = IsolatedCalls(function)
        return self._pools[function]

    def source_label(self, ticker: str) -> str:
        try:
            return PROVIDER_LABELS.get(_symbol_for_loader(parse(ticker))[1], 'akshare（东方财富）')
        except (BarError, InstrumentError):
            return 'akshare（东方财富）'

    def _make_loader(self):
        if self._loader_factory:
            return self._loader_factory()
        from backtesting.data.loader import DataLoader   # lazy: pandas and friends
        return DataLoader()

    def _load(self, symbol: str, start: date, end: date, adjust: str):
        return self._make_loader().get_daily(symbol, start.isoformat(), end.isoformat(), adjust=adjust)

    async def get_daily(self, ticker: str, days: int, adjust: str = ADJUST_QFQ) -> BarResult:
        try:
            inst = parse(ticker)
        except InstrumentError as exc:
            raise BarError(str(exc), 'bars') from None
        symbol, routed = _symbol_for_loader(inst)
        label = PROVIDER_LABELS.get(routed, 'akshare（东方财富）')
        if adjust != ADJUST_QFQ:
            raise BarError('目前只提供前复权(qfq)日线', label)
        end = _today(self._now)
        start = end - timedelta(days=int(days * 1.6) + 14)
        try:
            frame = await self._pool_for(routed).run(lambda: self._load(symbol, start, end, 'qfq'), self.timeout)
        except CallTimeout:
            raise BarError(f'数据源超时（{self.timeout:g} 秒）', label) from None
        except CallRestartRequired:
            raise BarError(RESTART_TEXT, label) from None
        except CallBusy:
            raise BarError(BUSY_TEXT, label) from None
        except Exception as exc:
            reason = log_failure(logger, 'bars ' + routed, exc)
            raise BarError(f'日线获取失败：{reason}', label) from None
        provider, adjust_used, unit, notes = self._provenance(frame, routed, label)
        label = PROVIDER_LABELS[provider]
        bars = self._to_bars(frame, days, label)
        if not bars:
            raise BarError('数据源没有返回该代码的日线（代码错误、停牌或尚未上市）', label)
        return BarResult(
            ticker=inst.ticker, source=label, adjust=adjust_used, currency=inst.currency, bars=bars,
            coverage=build_coverage(bars, days, start, end),
            fetched_at=(datetime.now(_SHANGHAI) if not self._now else self._now()).isoformat(timespec='seconds'),
            volume_unit=unit, notes=notes, provider=provider, market=inst.market,
            volume_label=VOLUME_LABELS.get(provider, '股'))

    @staticmethod
    def _provenance(frame: Any, routed: str, label: str) -> tuple:
        """(provider, adjust, volume unit, notes) as the loader's result declares them.

        The loader sets ``attrs["source"]`` (the endpoint it called) and
        ``attrs["volume_unit"]``; it does not report the adjustment, so that is
        derived from the endpoint for the ``qfq`` request made here (an
        ``attrs["adjust"]``, if a later loader sets one, wins). A result that
        declares nothing falls back to the loader's routing table, and says so.
        """
        attrs = getattr(frame, 'attrs', None) or {}
        notes: List[str] = []
        declared, unit = attrs.get('source'), attrs.get('volume_unit')
        if declared is None or unit is None:
            notes.append('加载器未声明数据接口或成交量单位：接口按其代码段路由表推断，成交量按“股/份”处理。')
        provider = declared if declared is not None else routed
        if provider not in PROVIDER_LABELS:
            raise BarError('日线数据来源未识别', label)
        if unit not in (None, 'shares'):
            raise BarError('日线成交量单位未识别', PROVIDER_LABELS[provider])
        adjust = attrs.get('adjust')
        adjust = PROVIDER_ADJUST[provider] if adjust is None else (ADJUST_QFQ if adjust == 'qfq' else None)
        if adjust is None:
            raise BarError('日线复权方式未识别', PROVIDER_LABELS[provider])
        return provider, adjust, unit or 'shares', notes

    @staticmethod
    def _to_bars(frame: Any, days: int, label: str) -> List[Dict[str, Any]]:
        try:
            tail = frame.tail(days)
            bars = []
            for stamp, row in tail.iterrows():
                item: Dict[str, Any] = {'date': stamp.strftime('%Y-%m-%d')}
                for column in _COLUMNS:
                    item[column] = _clean_number(row[column])
                if any(item[c] is None for c in ('open', 'high', 'low', 'close')):
                    raise ValueError('non-finite price')
                bars.append(item)
            return bars
        except Exception as exc:
            log_failure(logger, 'bars frame', exc)
            raise BarError('日线数据格式异常', label) from None


class FakeBarSource:
    """Scripted bars for tests.

    ``script`` maps ``EXCH:CODE`` to a list of bar dicts (oldest first), to an
    exception to raise, or to a callable ``(days) -> list``. Unknown tickers
    raise ``BarError``: the fake never invents data.
    """

    name = 'fake-bars'

    def __init__(self, script: Optional[Mapping[str, Any]] = None, adjust: str = ADJUST_QFQ,
                 today: Union[str, date] = '2026-11-17', label: str = 'fake-bars'):
        self.script = dict(script or {})
        self.adjust = adjust
        self.today = date.fromisoformat(today) if isinstance(today, str) else today
        self.label = label
        self.calls: List[tuple] = []

    def source_label(self, ticker: str) -> str:
        return self.label

    async def get_daily(self, ticker: str, days: int, adjust: str = ADJUST_QFQ) -> BarResult:
        self.calls.append((ticker, days, adjust))
        try:
            inst = parse(ticker)
        except InstrumentError as exc:
            raise BarError(str(exc), self.label) from None
        entry = self.script.get(inst.ticker)
        if isinstance(entry, BaseException):
            raise entry
        if callable(entry):
            entry = entry(days)
        if entry is None:
            raise BarError('没有该代码的日线', self.label)
        bars = [dict(bar) for bar in list(entry)[-days:]]
        start = self.today - timedelta(days=int(days * 1.6) + 14)
        return BarResult(
            ticker=inst.ticker, source=self.label, adjust=self.adjust, currency=inst.currency, bars=bars,
            market=inst.market,
            coverage=build_coverage(bars, days, start, self.today),
            fetched_at=datetime.combine(self.today, datetime.min.time(), _SHANGHAI).replace(hour=15, minute=30)
            .isoformat(timespec='seconds'))


_bar_source: Optional[BarSource] = None


def get_bar_source() -> BarSource:
    """The process-wide source; a ``LoaderBarSource`` unless a test set another."""
    global _bar_source
    if _bar_source is None:
        _bar_source = LoaderBarSource()
    return _bar_source


def set_bar_source(source: Optional[BarSource]) -> None:
    global _bar_source
    _bar_source = source
