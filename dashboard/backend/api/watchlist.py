"""The watchlist. Mounted at ``/api/watchlist``.

``GET    ''?quotes=1``     ``{items, count, max_items, as_of, source}``. Each item is
                          ``{ticker, identity, name, note, order, source, added_at, updated_at,
                          in_holdings, quote}`` (``identity``: the listing,
                          ``core.market.search.identity_key``); with ``quotes=1`` (the default)
                          ``quote`` is exactly one element of ``GET /api/quotes``
                          (``{ticker, name, price, prev_close, change_pct, currency,
                          quote{source, quote_time, fetched_at, status, reason,
                          session_date}, estimated, estimated_reason}``), with
                          ``quotes=0`` it is null and so are ``as_of`` / ``source``
``POST   ''``              add: body ``{ticker, note?}`` -> 201 and the item
``PATCH  /{ticker}``       body ``{note?, order?}``: a new note (null clears it) and/or
                          the new 0-based position -> the item
``DELETE /{ticker}``       -> ``{deleted: true, item}``

``{ticker}`` in a path is ``EXCH:CODE``; any spelling of a Hong Kong code
finds the stored item. Errors are ``{"detail": "<中文说明>"}``: 404 (not in the
watchlist), 409 (already there), 422 (invalid or unsupported code, note too
long, list full), 503 (store busy or unusable). The person's own requests are
saved directly; the agent can only propose an addition (``/api/proposals``).
Demo mode rejects writes with 403 (existing middleware); reads work.
"""
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException

from backend.api.desk_errors import desk_call
from core import watchlist
from core.market.search import identity_key

router = APIRouter()


def attempt(operation):
    try:
        return desk_call(operation)
    except watchlist.WatchlistNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except watchlist.WatchlistConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except watchlist.WatchlistError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('')
def listing(quotes: bool = True):
    # A plain ``def``: FastAPI runs it in a worker thread, so the quote
    # request does not hold up the event loop.
    payload = attempt(lambda: watchlist.view(quotes=quotes))
    for item in payload.get('items') or []:
        item['identity'] = identity_key(item.get('ticker'))
    return payload


@router.post('', status_code=201)
def add(payload: Any = Body(None)):
    if not isinstance(payload, dict):
        raise HTTPException(422, '请求内容必须是 JSON 对象：{"ticker": "SHE:000001", "note": "可选备注"}')
    unknown = sorted(set(payload) - {'ticker', 'note'})
    if unknown:
        raise HTTPException(422, f'不支持的字段：{"、".join(map(str, unknown))}。只能提供 ticker 和 note。')
    return attempt(lambda: watchlist.add(payload.get('ticker'), payload.get('note'), source='manual'))


@router.patch('/{ticker}')
def update(ticker: str, changes: Optional[Any] = Body(None)):
    return attempt(lambda: watchlist.update(ticker, changes))


@router.delete('/{ticker}')
def remove(ticker: str):
    return attempt(lambda: {'deleted': True, 'item': watchlist.remove(ticker)})
