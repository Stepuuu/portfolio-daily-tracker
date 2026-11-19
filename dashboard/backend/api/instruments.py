"""Instrument search and remembered choices. Mounted at ``/api/instruments``.

``GET  /search?q=&limit=``   ``{query, results, source, as_of, index_available, index_reason,
                             omitted}`` (rules and result fields in ``core/market/search.py``).
                             ``limit`` 1-50, default 10; ``q`` at most 40 characters.
                             An empty ``q`` lists holdings, then the watchlist.
                             ``omitted`` is ``[{source, reason}]``: each of the person's
                             own sources (``holdings``, ``watchlist``, ``aliases``) that
                             could not be read in time and is missing from ``results``;
                             ``[]`` when every one was read. The name index reports
                             through ``index_available`` / ``index_reason``.
``POST /aliases``            remember a choice: body ``{ticker, symbol?, name?}``.
                             ``symbol`` (letters) stores ``US:<SYMBOL>`` -> ticker and needs a
                             NASDAQ/NYSE ticker with that symbol; ``name`` stores
                             ``NAME:<名称>`` -> ticker. Returns ``{aliases: [{alias, ticker, name}]}``.

Search never creates the store and never waits long for the name index: a
stale index is refreshed in the background (the request waits up to
``SEARCH_WAIT_SECONDS``) and meanwhile the results say ``index_available``
false with the reason. The whole request, lock waits on the store and on the
ledger included, stays within ``SEARCH_BUDGET_SECONDS``: the ledger is read
through a read-only connection (``core.ledger.read_confirmed``) that never
creates a file, and reading and replaying it may take at most
``LEDGER_WAIT_SECONDS`` (a commit in progress included; a writer's open
transaction does not block it). A slower read goes on in the background and
is cached for the next search. Past the budget, cached or no index data is
used and the answer says ``index_available: false`` with ``index_reason``,
and a source that could not be read is named in ``omitted``, with the reason
(a damaged ledger is said to be damaged, never shown as having no holdings).
In fake-quote mode the scripted quotes name the rows the index would.
Aliases are written to the importer's existing ``instrument_aliases`` table.
Demo mode rejects the POST with 403.
"""
import logging
import re
import sqlite3
import time
import unicodedata
from typing import Any, Callable, List

from fastapi import APIRouter, Body, HTTPException, Query

from backend.api.desk_errors import desk_call
from core import desk_store, watchlist
from core.importers import aliases
from core.market import search as instrument_search
from core.market.instruments import InstrumentError

logger = logging.getLogger(__name__)
router = APIRouter()

SEARCH_BUDGET_SECONDS = 2.0      # the whole request, every desk-store and ledger wait included
SEARCH_WAIT_SECONDS = 1.5        # of which a stale index may be waited for; the rest is for reading
LEDGER_WAIT_SECONDS = 0.5        # of which reading the ledger may take (a commit in progress, replaying it)
ALIAS_LEDGER_SECONDS = 5.0       # the ledger read behind remembering a choice (a write: it may wait longer)
MAX_ALIAS_NAME = 40

# ``omitted[].source`` and what the person is told about it.
OMITTED_LABELS = {'holdings': '持仓', 'watchlist': '自选', 'aliases': '记住的代码选择'}
BUSY_REASON = '{label}暂时读取不到（正在写入或读取较慢，超过了本次搜索的等待时间），本次结果中没有{label}'
BROKEN_REASON = '{label}读取出错，本次结果中没有{label}'
LEDGER_REASON = '{label}读取出错：{reason}。本次结果中没有{label}'


def _busy(exc: BaseException) -> bool:
    """A wait that ran out (the ledger's or the desk store's lock, or a slow ledger read), not a broken file."""
    from core.ledger import LedgerBusy
    if isinstance(exc, (desk_store.DeskStoreError, LedgerBusy)):
        return True
    return isinstance(exc, sqlite3.OperationalError) and ('locked' in str(exc) or 'busy' in str(exc))


def _read(source: str, read: Callable[[], Any], default, omitted: List[dict]):
    """``read()``, or ``default`` with ``source`` added to ``omitted`` when it fails."""
    from core.ledger import LedgerUnavailable
    try:
        return read()
    except Exception as exc:
        logger.warning('instrument search left out %s: %s', source, type(exc).__name__)
        label = OMITTED_LABELS[source]
        if _busy(exc):
            reason = BUSY_REASON.format(label=label)
        elif isinstance(exc, LedgerUnavailable):       # its reason is a fixed text: damaged, needs recovery ...
            reason = LEDGER_REASON.format(label=label, reason=exc.reason)
        else:
            reason = BROKEN_REASON.format(label=label)
        omitted.append({'source': source, 'reason': reason})
        return default


def _held_within(deadline: float) -> List[tuple]:
    """The ledger's held ``(ticker, name)`` pairs, read (and replayed) at most until ``deadline``."""
    from core.ledger import LedgerBusy, confirmed_state
    wait = min(LEDGER_WAIT_SECONDS, deadline - time.monotonic())
    if wait <= 0:
        raise LedgerBusy()
    return instrument_search.held_positions(confirmed_state(timeout=wait))


def _scripted_names() -> dict:
    """``{ticker: name}`` of the scripted quotes in fake-quote mode (the demo); ``{}`` otherwise."""
    from core.market.runtime import fake_requested, get_market_runtime
    if not fake_requested():
        return {}
    try:
        for source in get_market_runtime().quotes.sources:
            names = getattr(source, 'names', None)
            if callable(names):
                return names()
    except Exception as exc:   # the names are a convenience: never take the search down
        logger.warning('instrument search could not read the scripted names: %s', type(exc).__name__)
    return {}


@router.get('/search')
def search(q: str = Query('', max_length=instrument_search.MAX_QUERY),
           limit: int = Query(instrument_search.DEFAULT_LIMIT, ge=1, le=instrument_search.MAX_LIMIT)):
    index = instrument_search.get_symbol_index()
    deadline = time.monotonic() + SEARCH_BUDGET_SECONDS
    omitted: List[dict] = []
    # One deadline for every wait in this request: the ledger (read-only, at
    # most LEDGER_WAIT_SECONDS of it), the desk store (lock waits, the cache
    # reads, the join on a refresh). A source that cannot be read in time is
    # left out and named in ``omitted``; the name index says it is unavailable.
    with desk_store.read_deadline(SEARCH_BUDGET_SECONDS):
        held = _read('holdings', lambda: _held_within(deadline), [], omitted)
        watched = _read('watchlist', watchlist.list_items, [], omitted)
        remembered = _read('aliases', aliases.entries, [], omitted)
        if q.strip():
            index.ensure_fresh(min(SEARCH_WAIT_SECONDS, max(deadline - time.monotonic(), 0.0)))
        payload = instrument_search.search(q, limit, holdings=held, watchlist_items=watched, alias_rows=remembered,
                                           index=index, names=_scripted_names())
    return {**payload, 'omitted': omitted}


@router.post('/aliases')
def remember(payload: Any = Body(None)):
    if not isinstance(payload, dict):
        raise HTTPException(422, '请求内容必须是 JSON 对象：{"ticker": "NASDAQ:EXAMPLE", "symbol": "EXAMPLE"}')
    unknown = sorted(set(payload) - {'ticker', 'symbol', 'name'})
    if unknown:
        raise HTTPException(422, f'不支持的字段：{"、".join(map(str, unknown))}。')
    ticker_text, symbol, name = payload.get('ticker'), payload.get('symbol'), payload.get('name')
    if not isinstance(ticker_text, str):
        raise HTTPException(422, '请提供 ticker，例如 NASDAQ:EXAMPLE。')
    try:
        qualified = instrument_search.watchable(ticker_text, [*_held(), *_read('watchlist', watchlist.tickers, [], [])])
    except InstrumentError as exc:
        raise HTTPException(422, str(exc)) from None
    if not qualified.supported or qualified.ticker is None:
        raise HTTPException(422, qualified.reason or '这个代码不能使用。')
    ticker = qualified.ticker
    stored = []
    if symbol is not None:
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9.\-]{0,14}', symbol.strip()):
            raise HTTPException(422, 'symbol 必须是美股代码，例如 AAPL。')
        symbol = symbol.strip().upper()
        exchange, code = ticker.split(':', 1)
        if exchange not in ('NASDAQ', 'NYSE') or code.replace('-', '.') != symbol.replace('-', '.'):
            raise HTTPException(422, f'symbol {symbol} 只能对应 NASDAQ:{symbol} 或 NYSE:{symbol}。')
        stored.append((f'US:{code}', name if isinstance(name, str) else None))
    if name is not None:
        if not isinstance(name, str):
            raise HTTPException(422, 'name 必须是文字。')
        clean = re.sub(r'\s+', '', unicodedata.normalize('NFKC', name))
        clean = ''.join(ch for ch in clean if unicodedata.category(ch)[0] != 'C')
        if not clean or len(clean) > MAX_ALIAS_NAME:
            raise HTTPException(422, f'name 不能为空，最多 {MAX_ALIAS_NAME} 个字。')
        stored = [(alias, clean) for alias, _ in stored] + [(f'NAME:{clean}', clean)]
    if not stored:
        raise HTTPException(422, '请提供 symbol（美股代码）或 name（名称），说明要记住哪个选择。')

    def write():
        for alias, label in stored:
            aliases.remember(alias, ticker, label, source='search')
        return {'aliases': [{'alias': alias, 'ticker': ticker, 'name': label} for alias, label in stored]}
    return desk_call(write)


def _held():
    """Held tickers for remembering a choice; ``[]`` when the ledger cannot be read (the choice is then
    stored in the spelling typed, which ``identity_key`` matches to the held one anyway)."""
    from core.ledger import confirmed_state
    try:
        state = confirmed_state(timeout=ALIAS_LEDGER_SECONDS)
    except Exception as exc:   # a broken ledger must not take the request down
        logger.warning('remembering an instrument choice could not read the ledger: %s', type(exc).__name__)
        return []
    return [ticker for ticker, _ in instrument_search.held_positions(state)]
