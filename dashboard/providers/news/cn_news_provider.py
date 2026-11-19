"""A-share stock news, announcements and (optionally) the CLS flash, via akshare.

* ``stock_news_em``                   个股新闻 (东方财富)
* ``stock_individual_notice_report``  个股公告 (东方财富)
* ``stock_info_global_cls``           财联社电报; OFF unless ``TRACKER_AGENT_CLS_FLASH=1``

akshare scrapes websites and renames columns without notice, so every call:
imports akshare lazily, looks the function up with ``getattr(ak, name, None)``,
runs in its own daemon thread with an 8 s timeout (a call that outlives it is counted as
stuck and, past a small limit, new calls fail fast as "busy"; see ``core.isolated_call``), checks columns by alias and never
raises. A failure comes back as ``{"error": ..., "source": ...}`` whose text is
one of the FIXED messages below (an exception is mapped to a fixed text by
``core.provider_errors.classify``; its class name and message only go to the
log): nothing the website or the library said (column names, exception class
names and messages, HTML) is ever put into an error. Success comes back as
``{"source", "fetched_at", "data_date", "cached", "items": [...]}``. Items hold
raw third-party strings; the tool layer sanitises and wraps them as untrusted.
Only successes are cached (TTL), so an outage is never remembered.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core.isolated_call import BUSY_TEXT, RESTART_TEXT, CallBusy, CallRestartRequired, CallTimeout, IsolatedCalls
from core.provider_errors import ERR_PROVIDER, ERROR_KINDS, log_failure

logger = logging.getLogger(__name__)

_SHANGHAI = timezone(timedelta(hours=8))
FLASH_FLAG = 'TRACKER_AGENT_CLS_FLASH'

# The complete set of error texts a provider may return. Anything else that a
# provider-shaped dict claims is replaced by ERR_PROVIDER at the tool layer
# (``ERROR_PATTERN`` is what the tools accept).
ERR_IMPORT = 'akshare 未安装或无法导入'
ERR_FORMAT = '数据源返回了无法识别的结果'
ERR_COLUMNS = '数据源返回的列与预期不符（网站可能已改版）'
ERR_BUSY = BUSY_TEXT
ERR_RESTART = RESTART_TEXT
ERR_EMPTY = '数据源没有返回数据'
ERR_NOT_ENABLED = '该数据源未启用'
ERR_BAD_ARGUMENT = '参数不被支持'
ERROR_PATTERN = re.compile('|'.join((
    re.escape(ERR_IMPORT), re.escape(ERR_FORMAT), re.escape(ERR_COLUMNS), re.escape(ERR_BUSY), re.escape(ERR_RESTART),
    *(re.escape(kind) for kind in ERROR_KINDS), re.escape(ERR_EMPTY), re.escape(ERR_NOT_ENABLED), re.escape(ERR_BAD_ARGUMENT),
    r'当前 akshare 版本没有 [a-z_0-9]{1,60}',
    r'数据源超时（\d{1,4}(?:\.\d{1,3})? 秒）',
    r'(?:财联社电报|市场宽度数据)未启用（设置环境变量 [A-Z_]{1,40}=1）',
    r'(?:kind 只能是 industry 或 concept|order 只能是 gainers 或 losers)')))


def error_text(message: str) -> str:
    """``message`` when it is one of the fixed texts, else ``ERR_PROVIDER``."""
    text = message if isinstance(message, str) else ''
    return text if ERROR_PATTERN.fullmatch(text) else ERR_PROVIDER


def _accepts_timeout(func: Callable) -> bool:
    try:
        parameter = inspect.signature(func).parameters.get('timeout')
    except (TypeError, ValueError):
        return False
    return parameter is not None and parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                                        inspect.Parameter.KEYWORD_ONLY)


class _Failure(dict):
    """An ``{error, source}`` made by this module. A plain dict that akshare
    returned is data (and, being no frame, an unrecognised result), never an error."""

SRC_NEWS = 'akshare.stock_news_em（东方财富个股新闻）'
SRC_NOTICE = 'akshare.stock_individual_notice_report（东方财富个股公告）'
SRC_FLASH = 'akshare.stock_info_global_cls（财联社电报）'

# canonical field -> column names seen so far, newest spelling first
NEWS_COLUMNS = {
    'title': ('新闻标题', '标题', 'title'),
    'content': ('新闻内容', '内容', 'content'),
    'published': ('发布时间', '发布日期', 'time', 'date'),
    'origin': ('文章来源', '来源', 'source'),
}
NOTICE_COLUMNS = {
    'title': ('公告标题', '标题', 'title'),
    'kind': ('公告类型', '类型', 'type'),
    'published': ('公告日期', '日期', 'date'),
}
FLASH_COLUMNS = {
    'title': ('标题', 'title'),
    'content': ('内容', 'content'),
    'date': ('发布日期', '日期', 'date'),
    'time': ('发布时间', '时间', 'time'),
}


def _flag(name: str) -> bool:
    import os
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


def _text(value: Any) -> str:
    if value is None:
        return ''
    try:
        if value != value:          # NaN
            return ''
    except Exception:
        pass
    return str(value).strip()


def map_columns(columns: Sequence[Any], wanted: Mapping[str, Sequence[str]]) -> Dict[str, Optional[str]]:
    """canonical field -> the frame's column name (None when absent)."""
    names = [str(c).strip() for c in columns]
    return {field: next((alias for alias in aliases if alias in names), None) for field, aliases in wanted.items()}


class AkshareBase:
    """Guarded, cached, timed akshare calls. Subclasses add the endpoints."""

    def __init__(self, timeout: float = 8.0, ttl: float = 300.0,
                 clock: Callable[[], float] = time.monotonic,
                 now: Optional[Callable[[], datetime]] = None, max_cached_items: int = 100):
        self.timeout = timeout
        self.ttl = ttl
        self._clock = clock
        self._now = now or (lambda: datetime.now(_SHANGHAI))
        self._max_items = max_cached_items
        self._cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
        self._pools: Dict[str, IsolatedCalls] = {}

    async def _cached(self, kind: str, key: str, source: str, ttl: float, limit: int, fetch) -> Dict[str, Any]:
        slot = (kind, key)
        hit = self._cache.get(slot)
        if hit and self._clock() - hit[0] < ttl:
            return self._slice(hit[1], limit, cached=True)
        try:
            result = await fetch()
        except Exception as exc:                      # nothing may escape this provider
            return _Failure(error=log_failure(logger, f'cn news {kind}', exc), source=source)
        if 'error' not in result:
            self._cache[slot] = (self._clock(), result)
            if len(self._cache) > 64:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest, None)
        return self._slice(result, limit, cached=False) if 'error' not in result else result

    @staticmethod
    def _slice(result: Dict[str, Any], limit: int, cached: bool) -> Dict[str, Any]:
        items = result.get('items')
        if items is None:
            return {**result, 'cached': cached}
        return {**result, 'items': items[:max(int(limit), 0)], 'total': len(items), 'cached': cached}

    def _pool(self, function: str) -> IsolatedCalls:
        """The call budget of one akshare function: a hung one cannot starve the others."""
        if function not in self._pools:
            self._pools[function] = IsolatedCalls(function)
        return self._pools[function]

    def stuck(self, function: str) -> int:
        """Timed-out calls of ``function`` whose thread has not returned yet."""
        return self._pool(function).stuck

    async def _call(self, function: str, source: str, timeout: Optional[float] = None, **kwargs):
        """The frame, or an ``{error, source}`` dict. Never raises."""
        try:
            ak = importlib.import_module('akshare')
        except Exception:
            return _Failure(error=ERR_IMPORT, source=source)
        func = getattr(ak, function, None)
        if not callable(func):
            return _Failure(error=f'当前 akshare 版本没有 {function}', source=source)
        limit = timeout or self.timeout
        if _accepts_timeout(func) and 'timeout' not in kwargs:
            kwargs = {**kwargs, 'timeout': limit}      # let the library give up its own request too
        try:
            return await self._pool(function).run(lambda: func(**kwargs), limit)
        except CallTimeout:
            return _Failure(error=f'数据源超时（{limit:g} 秒）', source=source)
        except CallRestartRequired:
            return _Failure(error=ERR_RESTART, source=source)
        except CallBusy:
            return _Failure(error=ERR_BUSY, source=source)
        except Exception as exc:
            return _Failure(error=log_failure(logger, f'akshare {function}', exc), source=source)

    def _frame_rows(self, frame: Any, wanted: Mapping[str, Sequence[str]], required: Sequence[str],
                    source: str) -> Any:
        """(rows as dicts of canonical fields) or an error dict."""
        columns = getattr(frame, 'columns', None)
        if isinstance(frame, dict) or columns is None or not hasattr(frame, 'iterrows'):
            return _Failure(error=ERR_FORMAT, source=source)
        if len(frame) == 0:
            return []
        mapping = map_columns(list(columns), wanted)
        missing = [field for field in required if not mapping[field]]
        if missing:
            # what the site calls its columns is third-party text: it goes to the log, never to the model
            logger.warning('%s: missing %s; columns seen: %s', source, '/'.join(missing),
                           [str(c)[:30] for c in list(columns)[:12]])
            return _Failure(error=ERR_COLUMNS, source=source)
        rows = []
        for _, record in frame.iterrows():
            rows.append({field: (_text(record[column]) if column else '') for field, column in mapping.items()})
        return rows

    def _envelope(self, source: str, items: List[Dict[str, Any]], dates: List[str]) -> Dict[str, Any]:
        newest = max(dates) if dates else None
        return {'source': source, 'fetched_at': self._now().isoformat(timespec='seconds'),
                'data_date': newest, 'items': items[:self._max_items]}

    @staticmethod
    def _day(value: str) -> str:
        match = re.match(r'\d{4}-\d{2}-\d{2}', value or '')
        if match:
            return match.group(0)
        compact = re.match(r'(\d{4})(\d{2})(\d{2})', value or '')
        return '-'.join(compact.groups()) if compact else ''

    @staticmethod
    def _newest_first(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
        # stable: rows with an unreadable time keep the source order at the end
        return sorted(items, key=lambda item: item.get(key) or '', reverse=True)


class CnNewsProvider(AkshareBase):
    """Real provider. Construct freely: nothing is imported or fetched until a call."""

    name = 'akshare-cn-news'

    def __init__(self, timeout: float = 8.0, ttl: float = 300.0, flash_ttl: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, flash_enabled: Optional[bool] = None,
                 now: Optional[Callable[[], datetime]] = None, max_cached_items: int = 100):
        super().__init__(timeout, ttl, clock, now, max_cached_items)
        self.flash_ttl = flash_ttl
        self._flash_enabled = flash_enabled

    @property
    def flash_enabled(self) -> bool:
        return _flag(FLASH_FLAG) if self._flash_enabled is None else self._flash_enabled

    async def stock_news(self, code: str, limit: int = 10) -> Dict[str, Any]:
        return await self._cached('news', code, SRC_NEWS, self.ttl, limit, lambda: self._fetch_news(code))

    async def announcements(self, code: str, limit: int = 10, days: int = 90) -> Dict[str, Any]:
        return await self._cached('notice', f'{code}:{days}', SRC_NOTICE, self.ttl, limit,
                                  lambda: self._fetch_notices(code, days))

    async def market_flash(self, limit: int = 10) -> Dict[str, Any]:
        if not self.flash_enabled:
            return _Failure(error=f'财联社电报未启用（设置环境变量 {FLASH_FLAG}=1）', source=SRC_FLASH)
        return await self._cached('flash', '', SRC_FLASH, self.flash_ttl, limit, self._fetch_flash)

    async def _fetch_news(self, code: str) -> Dict[str, Any]:
        frame = await self._call('stock_news_em', SRC_NEWS, symbol=code)
        if isinstance(frame, _Failure):
            return frame
        rows = self._frame_rows(frame, NEWS_COLUMNS, ('title',), SRC_NEWS)
        if isinstance(rows, _Failure):
            return rows
        items = [{'title': r['title'], 'summary': r['content'], 'published': r['published'], 'origin': r['origin']}
                 for r in rows if r['title']]
        items = self._newest_first(items, 'published')
        return self._envelope(SRC_NEWS, items, [self._day(i['published']) for i in items if self._day(i['published'])])

    async def _fetch_notices(self, code: str, days: int) -> Dict[str, Any]:
        end = self._now().date()
        begin = end - timedelta(days=days)
        frame = await self._call('stock_individual_notice_report', SRC_NOTICE, security=code, symbol='全部',
                                 begin_date=begin.strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d'))
        if isinstance(frame, _Failure):
            return frame
        rows = self._frame_rows(frame, NOTICE_COLUMNS, ('title',), SRC_NOTICE)
        if isinstance(rows, _Failure):
            return rows
        items = [{'title': r['title'], 'kind': r['kind'], 'published': r['published']} for r in rows if r['title']]
        items = self._newest_first(items, 'published')
        return self._envelope(SRC_NOTICE, items, [self._day(i['published']) for i in items if self._day(i['published'])])

    async def _fetch_flash(self) -> Dict[str, Any]:
        frame = await self._call('stock_info_global_cls', SRC_FLASH, symbol='全部')
        if isinstance(frame, _Failure):
            return frame
        rows = self._frame_rows(frame, FLASH_COLUMNS, ('title',), SRC_FLASH)
        if isinstance(rows, _Failure):
            return rows
        items = [{'title': r['title'], 'summary': r['content'],
                  'published': (r['date'] + ' ' + r['time']).strip()} for r in rows if r['title'] or r['content']]
        items = self._newest_first(items, 'published')
        return self._envelope(SRC_FLASH, items, [self._day(i['published']) for i in items if self._day(i['published'])])


class FakeCnNewsProvider:
    """Scripted twin for tests: each method returns what the script holds.

    ``script`` keys are ``news``, ``announcements`` and ``flash``; a value is a
    ready result dict, an exception to raise (the real provider never raises,
    so tools must survive both) or a callable ``(*args) -> dict``.
    """

    name = 'fake-cn-news'

    def __init__(self, script: Optional[Mapping[str, Any]] = None):
        self.script = dict(script or {})
        self.calls: List[tuple] = []

    async def _serve(self, kind: str, source: str, *args) -> Dict[str, Any]:
        self.calls.append((kind, *args))
        entry = self.script.get(kind)
        if entry is None:
            return {'error': '没有预置数据', 'source': source}
        if isinstance(entry, BaseException):
            raise entry
        return entry(*args) if callable(entry) else entry

    async def stock_news(self, code: str, limit: int = 10):
        return await self._serve('news', SRC_NEWS, code, limit)

    async def announcements(self, code: str, limit: int = 10, days: int = 90):
        return await self._serve('announcements', SRC_NOTICE, code, limit)

    async def market_flash(self, limit: int = 10):
        return await self._serve('flash', SRC_FLASH, limit)


_provider: Any = None


def get_cn_news_provider():
    global _provider
    if _provider is None:
        _provider = CnNewsProvider()
    return _provider


def set_cn_news_provider(provider: Any) -> None:
    global _provider
    _provider = provider
