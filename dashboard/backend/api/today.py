"""Today: confirmed ledger holdings at live prices. Read-only.

``GET /api/today`` values the confirmed ledger with the live quote layer.
``GET /api/quotes?tickers=A,B`` returns the same quote objects a Today row
carries, for up to 50 tickers that need not be held.
``?refresh=1`` skips the short quote cache. Money and quantities are decimal
strings; ``null`` means the value is unavailable, never zero; times are
ISO-8601 with an offset. A quote source that is down yields ``unavailable``
quotes and ``null`` totals with reasons, not an error. The full response
contract is in ``core/market/today.py`` (every row carries ``identity``, its
listing, which the pages match alert rules, plans and the watchlist by).

The ledger is read read-only (``core.ledger.read_confirmed``: never
initialised or created) and waited for at most ``LEDGER_WAIT_SECONDS``; a
ledger that is being written past that, or cannot be read (damaged, an
interrupted write to recover), gives the usual payload without accounts and
a ``notice`` saying why, never an error.
"""
from fastapi import APIRouter, HTTPException

from core.ledger import LedgerBusy, LedgerUnavailable, read_confirmed
from core.market.runtime import get_market_runtime
from core.market.today import (LEDGER_BUSY, LEDGER_UNREADABLE, QuoteRequestError, build_quotes, build_today,
                               requested_tickers, today_unavailable)

router = APIRouter()

LEDGER_WAIT_SECONDS = 1.0


@router.get('/today')
def today(refresh: bool = False):
    # A plain ``def``: FastAPI runs it in a worker thread, so the blocking
    # quote requests do not hold up the event loop.
    runtime = get_market_runtime()
    try:
        ledger = read_confirmed(timeout=LEDGER_WAIT_SECONDS)
    except LedgerBusy:
        return today_unavailable(runtime, LEDGER_BUSY)
    except LedgerUnavailable as exc:
        return today_unavailable(runtime, f'{LEDGER_UNREADABLE}（{exc.reason}）')
    return build_today(ledger, runtime, refresh=refresh)


@router.get('/quotes')
def quotes(tickers: str = '', refresh: bool = False):
    try:
        wanted = requested_tickers(tickers)
    except QuoteRequestError as exc:
        raise HTTPException(422, str(exc)) from None
    return build_quotes(get_market_runtime(), wanted, refresh=refresh)
