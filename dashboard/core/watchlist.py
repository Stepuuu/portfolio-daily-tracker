"""The watchlist: instruments the person follows without holding them, in ``desk.sqlite3``.

An item is ``{ticker, name, note, order, added_at, updated_at, source}``:

ticker      exchange-qualified (``SHE:000001``, ``HKG:00700``, ``NASDAQ:AAPL``,
            ``IDX:000300``), checked by ``core.market.search.watchable`` (the
            rule the search's ``supported`` follows): codes the ledger cannot
            hold (北交所, B 股, index ranges such as ``SHA:000001``) are
            refused; a ticker the ledger already holds is accepted as stored. A Hong Kong code takes the form the
            ledger already uses, else 5 digits, so one listing is one item. A US class share is stored dotted (``NYSE:BRK.B``,
            or ``NYSE:BRK-B`` when the ledger already spells it so); the other spelling finds the same item.
name        found when the item is added (ledger, remembered alias or the
            name index), else ``None``; the quote's name is shown instead
note        optional, at most ``MAX_NOTE`` characters
order       0-based position in the list
source      ``manual`` (the person, through the API) or the source of a
            confirmed proposal (``agent:chat``)

Like settings, the person's own changes are saved directly. The agent can
only propose an addition: the ``watchlist_add`` proposal kind registered here
is applied by ``core.proposals.confirm`` inside its transaction, so it
happens exactly once. Readers use ``ready()`` and never create the store.

The ledger is only read, through ``core.ledger.read_confirmed`` (read-only,
never initialised): a list waits at most ``LEDGER_READ_SECONDS`` for it,
and when it cannot be read ``in_holdings`` is ``None`` (unknown, never
"not held") and ``holdings_reason`` says why.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, List, Mapping, Optional

from core import desk_store, proposals
from core.market.instruments import InstrumentError
from core.market.search import identity_key, watchable

SCHEMA = 'watchlist'
MIGRATIONS = [[
    """CREATE TABLE watchlist (
        ticker TEXT PRIMARY KEY,
        name TEXT,
        note TEXT,
        sort_order INTEGER NOT NULL,
        source TEXT NOT NULL,
        added_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
]]
desk_store.register_schema(SCHEMA, MIGRATIONS)

MAX_ITEMS = 200
MAX_NOTE = 200
PROPOSAL_KIND = 'watchlist_add'
_SOURCE = re.compile(r'[a-z][a-z0-9_.:\-]{0,63}')
LEDGER_READ_SECONDS = 1.0      # a list (a GET) waits at most this long for the ledger
LEDGER_CHECK_SECONDS = 5.0     # adding an item (a write) may wait longer
HOLDINGS_UNKNOWN = '持仓暂时读不到（{reason}），“是否持有”不可用'
LEDGER_UNREADABLE = '持仓暂时读不到（{reason}），无法核对代码的写法，请稍后再试。'


class WatchlistError(ValueError):
    """The request cannot be carried out; the message is shown to the user."""


class WatchlistNotFound(WatchlistError):
    pass


class WatchlistConflict(WatchlistError):
    pass


def _quote_key(ticker: str) -> str:
    return identity_key(ticker) or ticker


def _ledger(timeout: float):
    """The confirmed ledger read read-only (``ConfirmedLedger``), or ``None``; raises ``LedgerUnavailable``."""
    from core.ledger import read_confirmed
    return read_confirmed(timeout=timeout)


def _known_tickers(timeout: float = LEDGER_CHECK_SECONDS) -> List[str]:
    """Tickers the ledger holds, so a Hong Kong code is stored in the same form.

    Raises ``core.ledger.LedgerUnavailable`` (or ``LedgerError``) when the ledger cannot be read.
    """
    ledger = _ledger(timeout)
    if ledger is None:
        return []
    return [ticker for account in ledger.state().get('accounts', {}).values() for ticker in account.get('positions', {})]


def _unreadable_reason(exc: Exception) -> str:
    from core.ledger import LedgerUnavailable
    return exc.reason if isinstance(exc, LedgerUnavailable) else '账本无法回放'


def check_ticker(value, known: Optional[List[str]] = None) -> str:
    """The canonical ticker for ``value``; ``WatchlistError`` when it cannot be watched."""
    if not isinstance(value, str) or not value.strip():
        raise WatchlistError('请填写代码，例如 SHA:600519、SHE:000001、HKG:00700、NASDAQ:AAPL 或 IDX:000300。')
    if known is None:
        from core.ledger import LedgerError, LedgerUnavailable
        try:
            known = _known_tickers()
        except (LedgerUnavailable, LedgerError) as exc:
            # Conservative: the stored spelling must follow the ledger's; without it, nothing is stored.
            raise WatchlistError(LEDGER_UNREADABLE.format(reason=_unreadable_reason(exc))) from None
    try:
        qualified = watchable(value, known)
    except InstrumentError as exc:
        raise WatchlistError(str(exc)) from None
    if not qualified.supported or qualified.ticker is None:
        raise WatchlistError(qualified.reason or '这个代码不能加入自选。')
    return qualified.ticker


def check_note(value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WatchlistError('备注必须是文字。')
    text = unicodedata.normalize('NFC', value)
    text = ''.join(' ' if ch in '\r\n\t' else ch for ch in text if unicodedata.category(ch)[0] != 'C' or ch in '\r\n\t')
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > MAX_NOTE:
        raise WatchlistError(f'备注最多 {MAX_NOTE} 个字。')
    return text or None


def _item(row, order: int) -> dict:
    return {'ticker': row['ticker'], 'name': row['name'], 'note': row['note'], 'order': order,
            'source': row['source'], 'added_at': row['added_at'], 'updated_at': row['updated_at']}


def _rows(db) -> list:
    return db.execute('SELECT * FROM watchlist ORDER BY sort_order, added_at, ticker').fetchall()


def available() -> bool:
    """For readers: whether the watchlist table exists. Never creates or migrates the store."""
    return desk_store.ready(SCHEMA, MIGRATIONS)


def list_items() -> List[dict]:
    """Every item in display order; ``[]`` when nothing was ever stored."""
    if not available():
        return []
    with desk_store.connect() as db:
        return [_item(row, order) for order, row in enumerate(_rows(db))]


def tickers() -> List[str]:
    return [item['ticker'] for item in list_items()]


def view(items: Optional[List[dict]] = None, *, quotes: bool = True, runtime=None) -> dict:
    """``{items, count, max_items, as_of, source, holdings_reason}`` as ``GET /api/watchlist`` returns it.

    Every item gains ``in_holdings`` and ``quote``: with ``quotes`` one element
    of ``GET /api/quotes`` (one batched request through the market runtime),
    otherwise ``None``. ``in_holdings`` is ``None`` when the ledger could not
    be read within ``LEDGER_READ_SECONDS`` (or not at all); ``holdings_reason``
    then says why, and is ``None`` otherwise.
    """
    from core.ledger import LedgerError, LedgerUnavailable
    items = list_items() if items is None else items
    held, holdings_reason = None, None
    try:
        held = {_quote_key(ticker) for ticker in _known_tickers(LEDGER_READ_SECONDS)}
    except (LedgerUnavailable, LedgerError) as exc:
        holdings_reason = HOLDINGS_UNKNOWN.format(reason=_unreadable_reason(exc))
    payload = {'items': [], 'count': len(items), 'max_items': MAX_ITEMS, 'as_of': None, 'source': None,
               'holdings_reason': holdings_reason}
    quoted = {}
    if quotes and items:
        from core.market.runtime import get_market_runtime
        from core.market.today import build_quotes
        result = build_quotes(runtime or get_market_runtime(), [item['ticker'] for item in items])
        payload['as_of'], payload['source'] = result['as_of'], result['source']
        quoted = {entry['ticker']: entry for entry in result['quotes']}
    for item in items:
        payload['items'].append({**item, 'in_holdings': None if held is None else _quote_key(item['ticker']) in held,
                                 'quote': quoted.get(item['ticker'])})
    return payload


def _find(db, ticker: str):
    """The stored row for ``ticker`` (any spelling of a Hong Kong code or US class share), or ``None``."""
    key = _quote_key(ticker.strip().upper()) if isinstance(ticker, str) else None
    if not key:
        return None
    for row in _rows(db):
        if _quote_key(row['ticker']) == key:
            return row
    return None


def _require(db, ticker: str):
    row = _find(db, ticker)
    if row is None:
        raise WatchlistNotFound('自选中没有这个代码。请刷新列表。')
    return row


def _order_of(db, ticker: str) -> int:
    for order, row in enumerate(_rows(db)):
        if row['ticker'] == ticker:
            return order
    raise WatchlistNotFound('自选中没有这个代码。请刷新列表。')


def _lookup_name(ticker: str) -> Optional[str]:
    try:
        from core.importers import aliases
        from core.market.search import known_name
        return known_name(ticker, _ledger(LEDGER_CHECK_SECONDS), aliases.entries())
    except Exception:
        return None


def add(ticker, note=None, *, source: str = 'manual', name: Optional[str] = None) -> dict:
    """Append ``ticker`` and return the item. ``WatchlistConflict`` when it is already there."""
    canonical = check_ticker(ticker)
    note = check_note(note)
    if not isinstance(source, str) or not _SOURCE.fullmatch(source):
        raise WatchlistError('来源格式不正确。')
    name = name or _lookup_name(canonical)
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        same = _find(db, canonical)
        if same is not None:
            raise WatchlistConflict(f'{same["ticker"]} 已在自选中。')
        count, last = db.execute('SELECT COUNT(*), MAX(sort_order) FROM watchlist').fetchone()
        if count >= MAX_ITEMS:
            raise WatchlistError(f'自选最多 {MAX_ITEMS} 个，请先删除不需要的。')
        stamp = desk_store.now()
        db.execute('INSERT INTO watchlist(ticker, name, note, sort_order, source, added_at, updated_at) '
                   'VALUES (?,?,?,?,?,?,?)', (canonical, name, note, (last + 1) if last is not None else 0,
                                              source, stamp, stamp))
        return _item(_require(db, canonical), _order_of(db, canonical))


def update(ticker, changes: Any) -> dict:
    """Change ``note`` and/or ``order`` (the new 0-based position). Returns the item."""
    if not isinstance(changes, Mapping):
        raise WatchlistError('修改内容必须是一个 JSON 对象。')
    unknown = sorted(set(changes) - {'note', 'order'})
    if unknown:
        raise WatchlistError(f'不能修改：{"、".join(map(str, unknown))}。只能修改 note（备注）和 order（顺序）。')
    if not changes:
        raise WatchlistError('没有要修改的内容。')
    note = check_note(changes['note']) if 'note' in changes else None
    order = changes.get('order')
    if 'order' in changes and (isinstance(order, bool) or not isinstance(order, int) or order < 0):
        raise WatchlistError('order 必须是不小于 0 的整数（在列表中的位置，从 0 开始）。')
    if not available():
        raise WatchlistNotFound('自选中没有这个代码。请刷新列表。')
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        row = _require(db, ticker)
        stamp = desk_store.now()
        if 'note' in changes:
            db.execute('UPDATE watchlist SET note=?, updated_at=? WHERE ticker=?', (note, stamp, row['ticker']))
        if 'order' in changes:
            others = [item['ticker'] for item in _rows(db) if item['ticker'] != row['ticker']]
            others.insert(min(order, len(others)), row['ticker'])
            db.executemany('UPDATE watchlist SET sort_order=? WHERE ticker=?',
                           [(position, ticker_) for position, ticker_ in enumerate(others)])
            db.execute('UPDATE watchlist SET updated_at=? WHERE ticker=?', (stamp, row['ticker']))
        return _item(_require(db, row['ticker']), _order_of(db, row['ticker']))


def remove(ticker) -> dict:
    """Delete an item and return what was removed."""
    if not available():
        raise WatchlistNotFound('自选中没有这个代码。请刷新列表。')
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        row = _require(db, ticker)
        item = _item(row, _order_of(db, row['ticker']))
        db.execute('DELETE FROM watchlist WHERE ticker=?', (row['ticker'],))
        remaining = [entry['ticker'] for entry in _rows(db)]
        db.executemany('UPDATE watchlist SET sort_order=? WHERE ticker=?',
                       [(position, ticker_) for position, ticker_ in enumerate(remaining)])
        return item


# -- the agent's proposal ------------------------------------------------------

def _validate_proposal(payload: dict) -> dict:
    unknown = sorted(set(payload) - {'ticker', 'note', 'name'})
    if unknown:
        raise WatchlistError(f'方案包含不支持的字段：{"、".join(map(str, unknown))}')
    ticker = check_ticker(payload.get('ticker'))
    if available():
        with desk_store.connect() as db:
            same = _find(db, ticker)
        if same is not None:
            raise WatchlistConflict(f'{same["ticker"]} 已在自选中，无需再添加。')
    result = {'ticker': ticker, 'note': check_note(payload.get('note'))}
    name = payload.get('name') if isinstance(payload.get('name'), str) else None
    name = name or _lookup_name(ticker)
    if name:
        result['name'] = re.sub(r'\s+', '', name)[:40]
    return result


def _describe_proposal(payload: dict) -> str:
    label = f"{payload['name']}（{payload['ticker']}）" if payload.get('name') else payload['ticker']
    text = f'把 {label} 加入自选'
    if payload.get('note'):
        text += f'，备注：{payload["note"]}'
    return text + '。只是关注，不会下单。'


def _apply_proposal(payload: dict, proposal: dict) -> dict:
    item = add(payload['ticker'], payload.get('note'), source=proposal['source'], name=payload.get('name'))
    return {'ticker': item['ticker']}


proposals.register_kind(PROPOSAL_KIND, _validate_proposal, _apply_proposal, _describe_proposal, version='1')
