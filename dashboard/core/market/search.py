"""Instrument search: find the ``EXCH:CODE`` ticker for what a person types.

``search(query, limit)`` merges, in this order and without repeats:

1. tickers held in the confirmed ledger,
2. the watchlist,
3. ``instrument_aliases`` (choices remembered from imports and searches),
4. the name index (``SymbolIndex``): the A-share list from akshare
   ``stock_info_a_code_name`` and the ETF list from ``fund_etf_spot_em``
   (code and name only), cached in ``desk.sqlite3``.

A typed code is also resolved on its own (``core.importers.codes``, the shared
rule mirrored by the frontend), so exact input works without the index:

* a bare 6-digit code goes by its prefix: ``000001`` is ``SHE:000001``
  (平安银行), never the 上证指数, which exists only as ``IDX:000001``. The rule
  holds for every source of the merged result: when the query is only digits
  (a whole code or the start of one), rows of ``kind: index`` from any source
  (watchlist, alias, name index, input) are moved behind all other rows, keeping
  their relative order. So ``q=000001&limit=1`` is always ``SHE:000001`` and an
  index follows only when there is room, labelled ``kind: index``. Indices stay
  first for names (``上证指数``) and for an explicit ``IDX:`` prefix;
* codes the ledger cannot hold are returned with ``supported: false`` and a
  reason: 北交所 (4/8/92), B 股 (900/200/201), 申购代码 (7), 逆回购 (204/1318),
  and codes in an exchange's index range (``39xxxx`` on Shenzhen, ``SHA:000xxx``);
* up to 5 digits is Hong Kong; the form already used in the ledger (or the
  watchlist) is reused (``HKG:0700`` vs ``HKG:00700``), otherwise 5 digits;
* letters are a US symbol. NASDAQ or NYSE comes from the ledger, the watchlist
  or a remembered alias; otherwise both are returned with
  ``needs_confirmation: true`` and the choice is remembered through
  ``POST /api/instruments/aliases``. ``BRK.B`` and ``BRK-B`` are one symbol
  (``identity_key``): stored dotted unless the ledger spells it with a dash;
* every inferred candidate passes ``qualify`` like typed ``EXCH:CODE`` input
  (``0``, ``00000``, ``100000.HK`` come back ``supported: false`` with a reason).

Result rows (``GET /api/instruments/search``)::

    {ticker, name, exchange, currency, kind, supported, reason,
     needs_confirmation, in_holdings, in_watchlist, matched_by}

``ticker`` is ``None`` only for an unsupported code that has no ledger prefix
(北交所, B 股, 申购, 逆回购). ``kind`` is stock, etf, fund, bond or index.
``matched_by`` is holdings, watchlist, alias, index or input.

The name index never blocks a search and never raises. akshare is imported
lazily, each list is fetched in its own thread with a timeout (measured in
November 2026 on a direct connection: about 90 s for the A-share list, which
reads the three exchanges' sites, and 30 s for the paged ETF list), and a
failure is remembered for ``RETRY_AFTER``. When no list could be loaded the search
still works on holdings, watchlist, aliases and exact codes, and says the
name index is unavailable. The index never contacts the network in demo mode
or with the fake quote source, and ``TRACKER_SYMBOL_INDEX=0`` turns it off.
Reads use ``desk_store.ready()`` and never create the store; only a refresh
writes, from a background thread. ``ensure_fresh`` and the request path read
under ``desk_store.read_deadline``, so a locked cache gives
``index_available: false`` with ``index_reason`` instead of a long wait.

Fetches run through one ``core.isolated_call.IsolatedCalls`` per list with a
single slot: at most one fetch per list is outstanding, and a timed-out fetch
keeps the slot (no replacement starts) until it returns, or until it has run
for ``WRITE_OFF_AFTER`` (15 minutes, five times the longest list timeout):
then it is written off and the next refresh may fetch again. At most
``MAX_WRITTEN_OFF`` (2) written-off fetches per list may still be running;
beyond that the list cannot be refreshed until the application restarts, and
the search says so (``index_restart_required: true``). A cached list older
than ``MAX_AGE`` is still used, but the search reports it: ``index_stale:
true``, ``as_of`` (when the oldest list was fetched) and ``index_reason``
saying since when and why it has not been refreshed.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core import desk_store
from core.isolated_call import RESTART_REQUIRED, CallBusy, CallRestartRequired, CallTimeout, IsolatedCalls
from core.provider_errors import log_failure

from .calendar import SHANGHAI
from .instruments import INDICES, InstrumentError, parse

logger = logging.getLogger(__name__)

SCHEMA = 'symbol_index'
MIGRATIONS = [[
    """CREATE TABLE symbol_index (
        list TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        PRIMARY KEY (list, code))""",
    "CREATE INDEX symbol_index_by_code ON symbol_index(code)",
    """CREATE TABLE symbol_index_lists (
        list TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        rows INTEGER NOT NULL)""",
]]
desk_store.register_schema(SCHEMA, MIGRATIONS)

MAX_AGE = timedelta(days=1)
RETRY_AFTER = timedelta(minutes=10)
WRITE_OFF_AFTER = 900.0   # seconds a fetch may run before it is written off (the longest timeout is 180 s)
MAX_WRITTEN_OFF = 2       # written-off fetches per list still running; past it, only a restart helps
RESTART_REASON = '下载多次没有返回，已无法自动恢复，需要重启应用后才能更新'
STUCK_REASON = '上一次下载超时后仍未返回，暂不重复下载；它运行满 {minutes} 分钟后会被放弃，之后的下一次更新会重新下载'
MIN_BUDGET = 0.25     # seconds ``ensure_fresh`` always allows itself for reading the cache
MAX_QUERY = 40
MAX_LIMIT = 50
DEFAULT_LIMIT = 10
MAX_NAME = 40
ENV_SWITCH = 'TRACKER_SYMBOL_INDEX'

STOCK, ETF, FUND, BOND, INDEX = 'stock', 'etf', 'fund', 'bond', 'index'

# The names of the indices ``core.market.instruments`` knows, for name search.
INDEX_NAMES = {
    '000001': '上证指数', '000016': '上证50', '000300': '沪深300', '000688': '科创50', '000852': '中证1000',
    '000905': '中证500', '399001': '深证成指', '399006': '创业板指', 'HSI': '恒生指数', 'HSTECH': '恒生科技指数',
    'IXIC': '纳斯达克综合指数', 'DJIA': '道琼斯工业平均指数', 'SPX': '标普500指数',
}

NOT_IN_INDEX = '名称索引中没有这个代码，请核对后再使用。'
INDEX_UNREADABLE = '名称索引无法读取'
BAD_TICKER = ('代码格式不正确。请使用“交易所:代码”，例如 SHA:600519、SHE:000001、HKG:00700、NASDAQ:AAPL；'
              '指数用 IDX:000300。')


# -- one ticker ---------------------------------------------------------------

@dataclass(frozen=True)
class Qualified:
    """What the search knows about one code without any list."""
    ticker: Optional[str]
    exchange: Optional[str]
    currency: Optional[str]
    kind: str
    supported: bool
    reason: Optional[str] = None
    needs_confirmation: bool = False
    code: Optional[str] = None      # the bare code of a row without a ticker


def _codes():
    # core.importers pulls in the ledger; import it only when a code is resolved.
    from core.importers import codes
    return codes


def _index_range_reason(exchange_name: str, code: str, span: str) -> str:
    hint = f'；如要查看指数请写作 IDX:{code}' if code in INDICES else ''
    return f'代码 {code} 在{exchange_name}属于指数编号段（{span}），不是可交易的证券{hint}'


REPO_REASON = '逆回购代码不是上市证券，导入交割单时会自动处理。'


def _unsupported(code: str, kind: Optional[str], message: Optional[str]) -> Qualified:
    codes = _codes()
    if re.fullmatch(r'\d{6}', code or '') and code.startswith(codes.BSE_PREFIXES):
        return Qualified(None, 'BSE', 'CNY', STOCK, False, message or '北交所证券：账本暂不支持北交所。', code=code)
    if re.fullmatch(r'\d{6}', code or '') and code.startswith(codes.B_SHARE_PREFIXES):
        exchange, currency = ('SHA', 'USD') if code.startswith('900') else ('SHE', 'HKD')
        return Qualified(None, exchange, currency, STOCK, False, message or 'B 股：账本暂不支持 B 股（外币计价）。',
                         code=code)
    kind = kind if kind in (STOCK, ETF, FUND, BOND, INDEX) else STOCK
    return Qualified(None, None, None, kind, False, message or f'无法使用代码 {code}。', code=code)


def classify_a_share(code: str, exchange: Optional[str] = None) -> Qualified:
    """A 6-digit mainland code (optionally with the exchange it was typed with)."""
    codes = _codes()
    if exchange is None and re.fullmatch(r'39\d{4}', code):
        return Qualified(f'SHE:{code}', 'SHE', 'CNY', INDEX, False, _index_range_reason('深交所', code, '39xxxx'))
    resolution = codes.a_share(code, exchange)
    if resolution.status == 'ok':
        return _known(resolution.ticker, {'etf': ETF, 'fund': FUND, 'bond': BOND}.get(resolution.kind, STOCK))
    if resolution.status == 'repo':
        return Qualified(None, None, 'CNY', STOCK, False, REPO_REASON, code=code)
    if resolution.choices and exchange:
        # Typed with the other mainland exchange's prefix.
        right = resolution.choices[0].split(':')[0]
        return Qualified(f'{exchange}:{code}', exchange, 'CNY', STOCK, False,
                         f'代码 {code} 属于{codes.EXCHANGE_NAMES[right]}，应写作 {resolution.choices[0]}。', code=code)
    return _unsupported(code, resolution.kind, resolution.message)


def _known(ticker: str, kind: str) -> Qualified:
    instrument = parse(ticker)
    problem = instrument.symbol_problem
    if problem:
        return Qualified(instrument.ticker, instrument.exchange, instrument.currency, INDEX, False, problem)
    return Qualified(instrument.ticker, instrument.exchange, instrument.currency, kind, True)


def qualify(text, known: Iterable[str] = ()) -> Qualified:
    """An exchange-qualified ticker as the watchlist and the search accept it.

    ``IDX:`` indices are supported (they can be watched and quoted). A Hong
    Kong code takes the form already in ``known``, else 5 digits. Raises
    ``InstrumentError`` (Chinese message) when the text is not ``EXCH:CODE``.
    """
    try:
        instrument = parse(text)
    except InstrumentError:
        raise InstrumentError(BAD_TICKER) from None
    if instrument.kind == 'index':
        return Qualified(instrument.ticker, 'IDX', instrument.currency, INDEX, True)
    if instrument.exchange in ('SHA', 'SHE'):
        if not re.fullmatch(r'\d{6}', instrument.code):
            raise InstrumentError('A 股代码必须是 6 位数字。')
        if instrument.symbol_problem:
            return Qualified(instrument.ticker, instrument.exchange, 'CNY', INDEX, False, instrument.symbol_problem)
        return classify_a_share(instrument.code, instrument.exchange)
    if instrument.exchange == 'HKG':
        if not re.fullmatch(r'\d{1,5}', instrument.code) or int(instrument.code) == 0:
            raise InstrumentError('港股代码必须是 1 到 5 位数字。')
        return _known(_codes().hk_ticker(instrument.code, known), STOCK)
    if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', instrument.code):
        raise InstrumentError('美股代码应以字母开头，例如 NASDAQ:AAPL。')
    return _known(us_ticker(instrument.exchange, instrument.code, known), STOCK)


def watchable(text, known: Iterable[str] = ()) -> Qualified:
    """What the watchlist accepts and the search marks ``supported``: ``qualify``, plus a known ticker.

    The ledger accepts any ``EXCH:CODE`` (the demo holds ``SHA:DEMO``), so a
    ticker in ``known`` (held in the ledger, or already watched) that the code
    rules cannot read is usable as it is stored; the same text that nothing
    holds is refused with ``qualify``'s reason. ``watchlist.check_ticker`` and
    every search row go through this one function, so a row the search offers
    as usable is never refused when it is added. Raises ``InstrumentError``
    like ``qualify``.
    """
    known = [ticker for ticker in known if isinstance(ticker, str)]
    try:
        return qualify(text, known)
    except InstrumentError:
        try:
            instrument = parse(text)
        except InstrumentError:
            raise InstrumentError(BAD_TICKER) from None
        if identity_key(instrument.ticker) in {identity_key(ticker) for ticker in known}:
            return Qualified(instrument.ticker, instrument.exchange, instrument.currency,
                             INDEX if instrument.kind == 'index' else STOCK, True)
        raise


def us_ticker(exchange: str, code: str, known: Iterable[str] = ()) -> str:
    """One spelling for a US symbol: the form in ``known`` (the ledger), else the dotted ``BRK.B``.

    ``BRK.B`` and ``BRK-B`` are one security: both give the same Yahoo and
    Eastmoney symbol, and ``backtesting`` files them under ``US:BRK.B``.
    """
    canonical = f'{exchange}:{code.replace("-", ".")}'
    for ticker in known:
        if isinstance(ticker, str) and ticker.upper().replace('-', '.') == canonical:
            return ticker.upper()
    return canonical


def identity_key(ticker) -> Optional[str]:
    """What makes two tickers the same listing: ``quote_key`` (Hong Kong padding) plus US dot/dash."""
    if not ticker:
        return None
    try:
        instrument = parse(ticker)
    except InstrumentError:
        return ticker
    if instrument.exchange in ('NASDAQ', 'NYSE'):
        return f'{instrument.exchange}:{instrument.code.replace("-", ".")}'
    return instrument.quote_key


def infer(text: str, known: Sequence[str] = (), aliases: Optional[Mapping[str, str]] = None) -> List[Qualified]:
    """Candidates for exactly what was typed; ``[]`` when it does not look like a code."""
    raw = normalize(text).upper()
    if not raw:
        return []
    if raw.startswith('IDX:'):
        try:
            return [qualify(raw)]
        except InstrumentError:
            return [Qualified(None, 'IDX', None, INDEX, False, '未知的指数代码。可用：' + '、'.join(
                f'IDX:{code}' for code in INDICES), code=raw[4:])]
    if re.fullmatch(r'[A-Z]+:.+', raw):
        try:
            return [watchable(raw, known)]
        except InstrumentError as exc:
            return [Qualified(None, None, None, STOCK, False, str(exc), code=raw)]
    if re.fullmatch(r'\d{6}', raw):
        return [classify_a_share(raw)]
    codes = _codes()
    looks_like_code = (re.fullmatch(r'\d{1,5}', raw) or re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', raw)
                       or re.fullmatch(r'(\d{1,6})\.(SH|SS|SZ|HK|BJ)|(SH|SZ|HK|BJ)(\d{1,6})', raw))
    if not looks_like_code:
        return []
    resolution = codes.resolve(raw, known=known, aliases=dict(aliases or {}))
    if resolution.status == 'ok':
        return [_validated(resolution.ticker, known, resolution.code or raw)]
    if resolution.status == 'repo':
        return [Qualified(None, None, 'CNY', STOCK, False, REPO_REASON, code=resolution.code)]
    if resolution.status == 'review' and resolution.choices:
        return [_validated(choice, known, resolution.code or raw, reason=resolution.message, confirm=True)
                for choice in resolution.choices]
    return [_unsupported(resolution.code or raw, resolution.kind, resolution.message)]


def _validated(ticker: str, known: Sequence[str], code: str, reason: Optional[str] = None,
               confirm: bool = False) -> Qualified:
    """A ticker the shared code rule produced, held to the checks typed ``EXCH:CODE`` gets.

    The rule maps a suffix or a short number to a ticker without looking at
    the code itself (``100000.HK``, ``0``); ``qualify`` does, so an inferred
    candidate is never more permissive than the same code typed in full.
    """
    try:
        found = qualify(ticker, known)
    except InstrumentError as exc:
        try:
            instrument = parse(ticker)
            exchange, currency = instrument.exchange, instrument.currency
        except InstrumentError:
            exchange, currency = None, None
        return Qualified(None, exchange, currency, STOCK, False, str(exc), code=code)
    if found.supported and (reason or confirm):
        return replace(found, reason=reason, needs_confirmation=confirm)
    return found


def normalize(text) -> str:
    """NFKC, no spaces, at most ``MAX_QUERY`` characters."""
    if not isinstance(text, str):
        return ''
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))[:MAX_QUERY]


# -- the name index -----------------------------------------------------------

class IndexFetchError(Exception):
    """A list could not be fetched; the message is shown to the user."""


@dataclass(frozen=True)
class ListSpec:
    name: str
    function: str
    label: str
    code_columns: Tuple[str, ...]
    name_columns: Tuple[str, ...]
    kind: str
    min_rows: int
    timeout: float


LISTS = {
    'a_stock': ListSpec('a_stock', 'stock_info_a_code_name', 'akshare.stock_info_a_code_name（沪深京A股列表）',
                        ('code', '证券代码', 'A股代码'), ('name', '证券简称', 'A股简称', '名称'), STOCK, 1000, 180.0),
    'etf': ListSpec('etf', 'fund_etf_spot_em', 'akshare.fund_etf_spot_em（东方财富ETF列表）',
                    ('代码', 'code'), ('名称', 'name'), ETF, 100, 120.0),
}
PRIMARY_LIST = 'a_stock'


def _column(columns, candidates) -> Optional[str]:
    names = {str(column).strip(): column for column in columns}
    return next((names[candidate] for candidate in candidates if candidate in names), None)


def rows_from_frame(frame, spec: ListSpec) -> List[Tuple[str, str]]:
    """``[(code, name)]`` from an akshare frame: 6-digit codes, cleaned names, no repeats."""
    columns = getattr(frame, 'columns', None)
    if columns is None or not hasattr(frame, 'itertuples'):
        raise IndexFetchError('数据源返回了无法识别的数据')
    code_column, name_column = _column(columns, spec.code_columns), _column(columns, spec.name_columns)
    if code_column is None or name_column is None:
        raise IndexFetchError('数据源的列名已变化，无法读取代码和名称')
    seen: Dict[str, str] = {}
    for code, name in zip(frame[code_column].tolist(), frame[name_column].tolist()):
        code_text = str(code).strip()
        if code_text.endswith('.0'):      # a numeric column
            code_text = code_text[:-2]
        if code_text.isdigit() and len(code_text) < 6:
            code_text = code_text.zfill(6)
        name_text = _clean_name(name)
        if re.fullmatch(r'\d{6}', code_text) and name_text:
            seen.setdefault(code_text, name_text)
    return sorted(seen.items())


def _clean_name(value) -> str:
    if value is None:
        return ''
    text = unicodedata.normalize('NFKC', str(value))
    text = ''.join(ch for ch in text if unicodedata.category(ch)[0] != 'C').strip()
    text = re.sub(r'\s+', '', text)
    return '' if text.lower() == 'nan' else text[:MAX_NAME]


def akshare_fetcher(spec: ListSpec) -> Callable[[], List[Tuple[str, str]]]:
    def fetch() -> List[Tuple[str, str]]:
        try:
            import importlib
            ak = importlib.import_module('akshare')
        except Exception:
            raise IndexFetchError('akshare 未安装或无法导入') from None
        function = getattr(ak, spec.function, None)
        if not callable(function):
            raise IndexFetchError(f'当前 akshare 版本没有 {spec.function}')
        # stock_info_a_code_name is lru_cached by akshare: a daily refresh in
        # a long-running process must not get the first day's list back.
        clear = getattr(function, 'cache_clear', None)
        if callable(clear):
            clear()
        try:
            frame = function()
        except Exception as exc:
            raise IndexFetchError(f'数据源请求失败（{log_failure(logger, "akshare " + spec.function, exc)}）') from None
        return rows_from_frame(frame, spec)
    return fetch


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(text) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


class SymbolIndex:
    """The cached code-and-name lists. See the module docstring."""

    def __init__(self, fetchers: Optional[Mapping[str, Callable[[], List[Tuple[str, str]]]]] = None, *,
                 max_age: timedelta = MAX_AGE, retry_after: timedelta = RETRY_AFTER,
                 now: Callable[[], datetime] = _utc_now, timeouts: Optional[Mapping[str, float]] = None,
                 min_rows: Optional[Mapping[str, int]] = None, monotonic: Callable[[], float] = time.monotonic,
                 write_off_after: float = WRITE_OFF_AFTER, max_written_off: int = MAX_WRITTEN_OFF):
        self._fetchers = dict(fetchers) if fetchers is not None else None
        self.max_age = max_age
        self.retry_after = retry_after
        self._now = now
        self._timeouts = dict(timeouts or {})
        self._min_rows = dict(min_rows or {})
        self._monotonic = monotonic
        self.write_off_after = write_off_after
        self.max_written_off = max_written_off
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._fetch_lock = threading.Lock()
        self._pools: Dict[str, IsolatedCalls] = {}
        self._failures: Dict[str, Tuple[datetime, str]] = {}

    # -- network ----------------------------------------------------------

    def offline_reason(self) -> Optional[str]:
        """Why the lists may not be fetched now, or ``None``; the text does not name the index (callers do)."""
        if self._fetchers is not None:
            return None
        if os.environ.get(ENV_SWITCH, '').strip().lower() in ('0', 'false', 'no', 'off'):
            return f'已关闭（{ENV_SWITCH}=0）'
        if (os.environ.get('TRACKER_DEMO_MODE') == '1'
                or os.environ.get('TRACKER_QUOTE_SOURCE', '').strip().lower() == 'fake'):
            return '演示模式和模拟行情不联网'
        return None

    def _fetcher(self, name: str):
        if self._fetchers is not None:
            return self._fetchers.get(name)
        return akshare_fetcher(LISTS[name])

    # -- reading ----------------------------------------------------------

    def lists(self) -> Dict[str, dict]:
        """``{list: {source, fetched_at, rows}}`` for the lists in the cache. Read-only."""
        if not desk_store.ready(SCHEMA, MIGRATIONS):
            return {}
        with desk_store.connect() as db:
            return {row['list']: {'source': row['source'], 'fetched_at': row['fetched_at'], 'rows': row['rows']}
                    for row in db.execute('SELECT list, source, fetched_at, rows FROM symbol_index_lists')}

    def stale(self) -> List[str]:
        """Lists missing from the cache or older than ``max_age``."""
        cached = self.lists()
        now = self._now()
        result = []
        for name in LISTS:
            fetched = _parse_time(cached.get(name, {}).get('fetched_at'))
            if fetched is None or now - fetched > self.max_age:
                result.append(name)
        return result

    def status(self) -> dict:
        """``{available, source, as_of, reason, stale, restart_required}`` for the search response.

        ``stale`` is true while a cached list is older than ``max_age``;
        ``reason`` then says since when and why it has not been refreshed.
        """
        cached = self.lists()
        now = self._now()
        loaded = [name for name in LISTS if cached.get(name, {}).get('rows')]
        fetched = {name: _parse_time(cached[name]['fetched_at']) for name in loaded}
        stale = [name for name in loaded if fetched[name] is None or now - fetched[name] > self.max_age]
        restart = [name for name in LISTS if self._pool_state(name) == RESTART_REQUIRED]
        reasons = []
        offline = self.offline_reason()
        if offline and len(loaded) < len(LISTS):
            reasons.append(f'名称索引暂不可用：{offline}' if not loaded else f'名称索引未更新：{offline}')
        for name in LISTS:
            label = '名称索引' if name == PRIMARY_LIST else 'ETF 列表'
            if name in stale:
                since = fetched[name].astimezone(SHANGHAI).strftime('%Y-%m-%d %H:%M') if fetched[name] else '未知时间'
                reasons.append(f'{label}停留在 {since}，{offline or self._why_not_refreshed(name)}')
                continue
            if offline or name in loaded:
                continue
            failure = self._failures.get(name)
            if name in restart:
                reasons.append(f'{label}暂不可用：{RESTART_REASON}')
            elif failure:
                reasons.append(f'{label}暂不可用：{failure[1]}')
            elif self.refreshing:
                reasons.append(f'{label}正在下载，请稍后再搜索')
            else:
                reasons.append(f'{label}暂不可用：尚未下载')
        times = [moment for moment in fetched.values() if moment is not None]
        return {'available': PRIMARY_LIST in loaded,
                'source': '+'.join(LISTS[name].label for name in loaded) or 'none',
                'as_of': min(times).astimezone(SHANGHAI).isoformat(timespec='seconds') if times else None,
                'reason': '；'.join(reasons) or None, 'stale': bool(stale), 'restart_required': bool(restart)}

    def _why_not_refreshed(self, name: str) -> str:
        state, failure = self._pool_state(name), self._failures.get(name)
        if state == RESTART_REQUIRED:
            return RESTART_REASON
        pool = self._pools.get(name)
        if pool is not None and pool.stuck:
            return STUCK_REASON.format(minutes=int(self.write_off_after // 60))
        if failure:
            return f'更新失败：{failure[1]}'
        if self.refreshing:
            return '正在更新'
        return '尚未更新'

    def _pool_state(self, name: str) -> Optional[str]:
        pool = self._pools.get(name)
        return pool.state if pool is not None else None

    def find(self, query: str, limit: int = 200) -> List[dict]:
        """Rows whose code starts with, or whose name contains, ``query``; best first. Read-only."""
        text = normalize(query)
        if not text or not desk_store.ready(SCHEMA, MIGRATIONS):
            return []
        escaped = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with desk_store.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT list, code, name FROM symbol_index WHERE code LIKE ? ESCAPE '\\' "
                "OR name LIKE ? ESCAPE '\\' LIMIT 2000", (escaped + '%', '%' + escaped + '%'))]
        folded = text.casefold()

        def rank(row):
            code, name = row['code'], row['name'].casefold()
            order = (0 if code == text else 1 if code.startswith(text) else 2 if name == folded
                     else 3 if name.startswith(folded) else 4)
            return order, len(row['name']), code, row['list']
        return sorted(rows, key=rank)[:limit]

    def names(self, codes: Iterable[str]) -> Dict[str, str]:
        """``{code: name}`` for 6-digit codes in the cache. Read-only."""
        wanted = sorted({code for code in codes if re.fullmatch(r'\d{6}', code or '')})
        if not wanted or not desk_store.ready(SCHEMA, MIGRATIONS):
            return {}
        with desk_store.connect() as db:
            rows = db.execute(f"SELECT code, name FROM symbol_index WHERE code IN ({','.join('?' * len(wanted))}) "
                              "ORDER BY list", wanted).fetchall()
        result: Dict[str, str] = {}
        for row in rows:
            result.setdefault(row['code'], row['name'])
        return result

    # -- refreshing -------------------------------------------------------

    def _pool(self, name: str) -> IsolatedCalls:
        with self._fetch_lock:
            pool = self._pools.get(name)
            if pool is None:
                pool = self._pools[name] = IsolatedCalls(
                    f'symbol-index-{name}', max_threads=1, write_off_after=self.write_off_after,
                    max_written_off=self.max_written_off, clock=self._monotonic, thread_name='symbol-index-fetch')
            return pool

    def _run_with_timeout(self, name: str, fetch: Callable[[], List[Tuple[str, str]]],
                          timeout: float) -> List[Tuple[str, str]]:
        """``fetch()`` for list ``name`` in a daemon thread, waiting at most ``timeout`` seconds.

        One ``IsolatedCalls`` slot per list (see the module docstring): while
        an abandoned fetch still runs and has not been written off, the
        attempt fails at once. What an abandoned fetch returns later is dropped.
        """
        try:
            return list(self._pool(name).call(fetch, timeout) or [])
        except CallTimeout:
            raise IndexFetchError(f'数据源超时（{timeout:g} 秒）') from None
        except CallRestartRequired:
            raise IndexFetchError(RESTART_REASON) from None
        except CallBusy:
            raise IndexFetchError('数据源上一次的请求仍未返回，暂不重复请求') from None
        except IndexFetchError:
            raise
        except Exception as exc:
            # Only a fixed text: the class and message are not ours to show.
            raise IndexFetchError(f'数据源请求失败（{log_failure(logger, "symbol index " + name, exc)}）') from None

    @property
    def refreshing(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def refresh(self, lists: Optional[Sequence[str]] = None) -> dict:
        """Fetch the given (default: stale) lists now and store what arrives. Never raises."""
        if self.offline_reason():
            return self.status()
        for name in (lists if lists is not None else self.stale()):
            spec = LISTS.get(name)
            fetch = self._fetcher(name) if spec else None
            if fetch is None:
                continue
            try:
                rows = self._run_with_timeout(name, fetch, self._timeouts.get(name, spec.timeout))
                if len(rows) < self._min_rows.get(name, spec.min_rows):
                    raise IndexFetchError(f'数据源只返回了 {len(rows)} 条，少于正常数量，未更新')
                self._store(spec, rows)
                self._failures.pop(name, None)
            except IndexFetchError as exc:
                self._failures[name] = (self._now(), str(exc))
                logger.warning('symbol index %s not refreshed: %s', name, exc)
            except Exception as exc:   # the store; nothing may escape a search
                self._failures[name] = (self._now(), '代码表无法保存到本地数据库')
                logger.warning('symbol index %s not stored: %s', name, type(exc).__name__)
        return self.status()

    def _store(self, spec: ListSpec, rows: List[Tuple[str, str]]) -> None:
        desk_store.ensure_schema(SCHEMA, MIGRATIONS)
        with desk_store.transaction() as db:
            db.execute('DELETE FROM symbol_index WHERE list=?', (spec.name,))
            db.executemany('INSERT INTO symbol_index(list, code, name) VALUES (?,?,?)',
                           [(spec.name, code, name) for code, name in rows])
            db.execute('INSERT INTO symbol_index_lists(list, source, fetched_at, rows) VALUES (?,?,?,?) '
                       'ON CONFLICT(list) DO UPDATE SET source=excluded.source, fetched_at=excluded.fetched_at, '
                       'rows=excluded.rows',
                       (spec.name, spec.label, self._now().isoformat(timespec='seconds'), len(rows)))

    def _due(self) -> List[str]:
        now = self._now()
        return [name for name in self.stale()
                if name not in self._failures or now - self._failures[name][0] >= self.retry_after]

    def _background(self, due: List[str]) -> None:
        with desk_store.read_deadline(None):     # no request is waiting on this thread
            self.refresh(due)

    def ensure_fresh(self, wait_seconds: float = 0.0) -> None:
        """Start a background refresh when a list is stale; wait at most ``wait_seconds`` for it.

        ``wait_seconds`` is the budget of the whole call: reading the cache, waiting
        for the start lock and joining the refresh together never take longer
        (``MIN_BUDGET`` at least, so a plain "start it" still reads the cache).
        When the budget runs out, whatever is cached is used.
        """
        budget = max(float(wait_seconds), MIN_BUDGET)
        deadline = time.monotonic() + budget
        try:
            if self.offline_reason():
                return
            with desk_store.read_deadline(budget):
                due = [] if self.refreshing else self._due()
            if not self._lock.acquire(timeout=max(deadline - time.monotonic(), 0.0)):
                return
            try:
                if due and not self.refreshing:
                    self._thread = threading.Thread(target=self._background, args=(due,), name='symbol-index',
                                                    daemon=True)
                    self._thread.start()
                thread = self._thread
            finally:
                self._lock.release()
            if thread is not None and wait_seconds > 0:
                thread.join(max(deadline - time.monotonic(), 0.0))
        except Exception as exc:   # a broken or locked store must not break the search
            logger.warning('symbol index refresh not started: %s', type(exc).__name__)


_index: Optional[SymbolIndex] = None
_index_lock = threading.Lock()


def get_symbol_index() -> SymbolIndex:
    global _index
    with _index_lock:
        if _index is None:
            _index = SymbolIndex()
        return _index


def set_symbol_index(index: Optional[SymbolIndex]) -> None:
    """Install an index (tests) or pass ``None`` to build the default on next use."""
    global _index
    with _index_lock:
        _index = index


# -- the search ---------------------------------------------------------------

def _quote_key(ticker: Optional[str]) -> Optional[str]:
    return identity_key(ticker)


def _held(ledger) -> List[Tuple[str, Optional[str]]]:
    """``[(ticker, name)]`` of the confirmed ledger's open positions."""
    if ledger is None:
        return []
    try:
        state = ledger.state()
    except Exception as exc:   # an unreadable ledger must not take the search down
        logger.warning('instrument search could not read the ledger: %s', type(exc).__name__)
        return []
    return held_positions(state)


def held_positions(state: Optional[Mapping]) -> List[Tuple[str, Optional[str]]]:
    """``[(ticker, name)]`` of the open positions in a ledger state (``Ledger.state()``)."""
    accounts = (state or {}).get('accounts', {})
    found: Dict[str, Optional[str]] = {}
    for account in accounts.values():
        for ticker, position in account.get('positions', {}).items():
            name = position.get('name')
            found.setdefault(ticker, name if name and name != ticker else None)
    return list(found.items())


def _matches(query: str, ticker: Optional[str], name: Optional[str], extra_keys: Sequence[str] = ()) -> Optional[int]:
    """0 exact, 1 prefix, 2 contains; ``None`` when ``query`` does not match."""
    upper, folded = query.upper(), query.casefold()
    best = None

    def better(rank):
        nonlocal best
        best = rank if best is None else min(best, rank)

    dotted = upper.replace('-', '.')        # BRK-B and BRK.B are one US symbol
    for key in [ticker or ''] + list(extra_keys):
        key = key.upper()
        if key.startswith(('NYSE:', 'NASDAQ:', 'US:')):
            key, upper = key.replace('-', '.'), dotted
        else:
            upper = query.upper()
        code = key.split(':', 1)[1] if ':' in key else key
        if upper in (key, code):
            better(0)
        elif key.startswith(upper) or code.startswith(upper):
            better(1)
        elif upper.isdigit() and code.isdigit() and len(upper) <= 5 and key.startswith('HKG:') \
                and int(upper or 0) == int(code):
            better(0)
    if name:
        lowered = name.casefold()
        if folded == lowered:
            better(0)
        elif lowered.startswith(folded):
            better(1)
        elif folded in lowered:
            better(2)
    return best


def _row(qualified: Qualified, name: Optional[str], matched_by: str, held: set, watched: set,
         reason: Optional[str] = None, needs_confirmation: Optional[bool] = None, kind: Optional[str] = None) -> dict:
    key = _quote_key(qualified.ticker)
    return {'ticker': qualified.ticker, 'name': name, 'exchange': qualified.exchange,
            'currency': qualified.currency, 'kind': kind or qualified.kind, 'supported': qualified.supported,
            'reason': reason if reason is not None else qualified.reason,
            'needs_confirmation': qualified.needs_confirmation if needs_confirmation is None else needs_confirmation,
            'in_holdings': key in held, 'in_watchlist': key in watched, 'matched_by': matched_by}


def _safe_qualify(ticker: str, known: Sequence[str]) -> Optional[Qualified]:
    """``watchable`` for a ticker already stored somewhere (the ledger, the watchlist, an alias).

    A held or watched ticker that ``qualify`` cannot read (the demo's
    ``SHA:DEMO``) is kept and usable, as the watchlist accepts it; another one
    (an alias to such a code) is listed unsupported with the reason. ``None``
    when the text is not ``EXCH:CODE`` at all.
    """
    try:
        return watchable(ticker, known)
    except InstrumentError as exc:
        try:
            instrument = parse(ticker)
        except InstrumentError:
            return None
        return Qualified(instrument.ticker, instrument.exchange, instrument.currency,
                         INDEX if instrument.kind == 'index' else STOCK, False, str(exc))


def search(query, limit: int = DEFAULT_LIMIT, *, ledger=None, watchlist_items: Sequence[dict] = (),
           alias_rows: Sequence[dict] = (), index: Optional[SymbolIndex] = None,
           holdings: Optional[Sequence[Tuple[str, Optional[str]]]] = None,
           names: Optional[Mapping[str, str]] = None) -> dict:
    """The ``GET /api/instruments/search`` payload. See the module docstring.

    The caller passes what it has read: the held ``(ticker, name)`` pairs
    (``held_positions``) or the active ledger to read them from (or neither),
    the watchlist items and the alias rows; ``index`` defaults to the process
    index. ``names`` (``{ticker: name}``, the scripted quotes in fake-quote
    mode, where the index never loads) names rows nothing else names. Never
    writes.
    """
    text = normalize(query)
    limit = max(1, min(int(limit), MAX_LIMIT))
    index = index or get_symbol_index()
    holdings = list(holdings) if holdings is not None else _held(ledger)
    watched_items = [(item['ticker'], item.get('name')) for item in watchlist_items]
    known = [ticker for ticker, _ in holdings] + [ticker for ticker, _ in watched_items]
    # The code rule looks a US symbol up by the spelling typed; offer both spellings.
    known_spellings = known + [ticker.replace(old, new) for ticker in known
                               if ticker.upper().startswith(('NYSE:', 'NASDAQ:')) for old, new in (('.', '-'), ('-', '.'))]
    held_keys = {_quote_key(ticker) for ticker, _ in holdings}
    watch_keys = {_quote_key(ticker) for ticker, _ in watched_items}
    alias_map = {row['alias']: row['ticker'] for row in alias_rows}
    for alias, target in list(alias_map.items()):      # BRK.B and BRK-B are one symbol
        if alias.startswith('US:'):
            alias_map.setdefault('US:' + alias[3:].replace('-', '.'), target)
            alias_map.setdefault('US:' + alias[3:].replace('.', '-'), target)
    trouble: List[str] = []

    def guarded(read, default):
        # A damaged or newer cache must not take the search down with it.
        try:
            return read()
        except Exception as exc:
            logger.warning('symbol index read failed: %s', type(exc).__name__)
            if INDEX_UNREADABLE not in trouble:     # the class goes to the log, a fixed text to the person
                trouble.append(INDEX_UNREADABLE)
            return default

    index_names = guarded(lambda: index.names([ticker.split(':', 1)[1] for ticker in known if ':' in ticker]), {})
    scripted = {_quote_key(ticker): name for ticker, name in (names or {}).items()
                if isinstance(ticker, str) and isinstance(name, str) and name.strip()}
    results: List[dict] = []
    seen: set = set()

    def add(row: dict) -> None:
        code = row.pop('_code', None)
        identity = _quote_key(row['ticker']) or (row['exchange'], code or row['reason'])
        if identity in seen:
            return
        seen.add(identity)
        results.append(row)

    def name_for(ticker: str, given: Optional[str]) -> Optional[str]:
        return (given or index_names.get(ticker.split(':', 1)[1] if ':' in ticker else '')
                or scripted.get(_quote_key(ticker)))

    # 1-2: holdings and watchlist, best match first within each group.
    for group, label in ((holdings, 'holdings'), (watched_items, 'watchlist')):
        ranked = []
        for position, (ticker, name) in enumerate(group):
            name = name_for(ticker, name)
            rank = 0 if not text else _matches(text, ticker, name)
            if rank is not None:
                ranked.append((rank, position, ticker, name))
        for _, _, ticker, name in sorted(ranked):
            qualified = _safe_qualify(ticker, known)
            if qualified is not None:
                add(_row(qualified, name, label, held_keys, watch_keys))

    if text:
        # 3: remembered aliases.
        ranked = []
        for position, row in enumerate(alias_rows):
            alias = str(row.get('alias') or '')
            keys = [alias.split(':', 1)[1]] if alias.startswith('US:') else []
            alias_name = alias.split(':', 1)[1] if alias.startswith('NAME:') else None
            rank = _matches(text, row.get('ticker'), row.get('name'), keys)
            if alias_name is not None:
                name_rank = _matches(text, None, alias_name)
                rank = name_rank if rank is None else (rank if name_rank is None else min(rank, name_rank))
            if rank is not None:
                ranked.append((rank, position, row))
        for _, _, row in sorted(ranked, key=lambda item: item[:2]):
            qualified = _safe_qualify(row['ticker'], known)
            if qualified is not None:
                add(_row(qualified, row.get('name') or name_for(row['ticker'], None), 'alias', held_keys,
                         watch_keys))

        # 4: indices by name, then exact input, then the name index.
        for code, index_name in INDEX_NAMES.items():
            if _matches(text, f'IDX:{code}', index_name) is not None and not re.fullmatch(r'\d{1,6}', text):
                add(_row(qualify(f'IDX:{code}'), index_name, 'index', held_keys, watch_keys))
        found = guarded(lambda: index.find(text), [])
        status = guarded(index.status, None)
        exact = [row for row in found if row['code'] == text]
        for row in exact:
            add(_index_row(row, held_keys, watch_keys))
        inferred = infer(text, known_spellings, alias_map)
        index_names.update(guarded(lambda: index.names(
            [item.ticker.split(':', 1)[1] for item in inferred if item.ticker and ':' in item.ticker]), {}))
        for qualified in inferred:
            code = qualified.ticker.split(':', 1)[1] if qualified.ticker else ''
            name = name_for(qualified.ticker, None) if qualified.ticker else None
            reason, confirm = None, None
            # An index that could not be read says nothing about whether it lists the code.
            if (qualified.supported and not qualified.needs_confirmation and status and status['available']
                    and not trouble and qualified.exchange in ('SHA', 'SHE') and name is None
                    and re.fullmatch(r'\d{6}', code)):
                reason, confirm = NOT_IN_INDEX, True
            add({**_row(qualified, name, 'input', held_keys, watch_keys, reason=reason, needs_confirmation=confirm),
                 '_code': qualified.code})
        # Codes the ledger can hold before those it cannot; otherwise the index's own order.
        rest = [_index_row(row, held_keys, watch_keys) for row in found if row['code'] != text]
        for row in sorted(rest, key=lambda row: not row['supported']):
            add(row)
    else:
        status = guarded(index.status, None)

    if text.isdigit():
        # A bare number is a security: indices follow every other row (stable).
        results.sort(key=lambda row: row['kind'] == INDEX)

    if status is None:
        status = {'available': False, 'source': 'none', 'as_of': None, 'reason': None}
    reason = '；'.join([status['reason']] * bool(status['reason']) + trouble) or None
    return {'query': text, 'results': results[:limit], 'source': status['source'], 'as_of': status['as_of'],
            'index_available': bool(status['available']) and not trouble, 'index_reason': reason,
            'index_stale': bool(status.get('stale')), 'index_restart_required': bool(status.get('restart_required'))}


def _index_row(row: dict, held: set, watched: set) -> dict:
    qualified = classify_a_share(row['code'])
    kind = LISTS[row['list']].kind if qualified.supported and row['list'] in LISTS else None
    return {**_row(qualified, row['name'], 'index', held, watched, kind=kind), '_code': row['code']}


def known_name(ticker: str, ledger=None, alias_rows: Sequence[dict] = (),
               index: Optional[SymbolIndex] = None) -> Optional[str]:
    """A display name for ``ticker`` from the ledger, an alias or the name index; ``None`` when unknown."""
    key = _quote_key(ticker)
    for held, name in _held(ledger):
        if _quote_key(held) == key and name:
            return name
    for row in alias_rows:
        if _quote_key(row.get('ticker')) == key and row.get('name'):
            return row['name']
    code = ticker.split(':', 1)[1] if ':' in ticker else ''
    if ticker.startswith('IDX:'):
        return INDEX_NAMES.get(code)
    try:
        return (index or get_symbol_index()).names([code]).get(code)
    except Exception:
        return None
