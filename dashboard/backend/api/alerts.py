"""Price alerts. Mounted at ``/api/alerts``.

``GET    /rules``          every rule with its state, current price and distance to fire
                           (``?ticker=`` one listing, any spelling; ``?quotes=0`` no quotes)
``POST   /rules``          create a rule (201); body ``{ticker, kind, threshold, account?, enabled?,
                           cooldown_seconds?, note?}``
``GET    /rules/{id}``     one rule, same shape as an item of the list
``PATCH  /rules/{id}``     change ``threshold``, ``account``, ``enabled``, ``cooldown_seconds`` or ``note``
``DELETE /rules/{id}``     remove a rule; its past events stay
``GET    /events``         fired alerts, newest first: ``?limit=&before=&rule_id=``
``GET    /status``         whether the monitor is running, the session per market, why it is paused

Every rule also carries ``identity`` (read-only): its listing
(``core.market.search.identity_key``, the key the pages match holdings and
the watchlist by). Every event carries ``digest_sent``: for a ``suppressed``
fire, whether a published ``alert.digest`` covers it yet (``true``) or it
waits for the next one (``false``); ``null`` for the other deliveries.

Rules created here have ``source='manual'``; the agent can only propose one
(``/api/proposals``). Errors are ``{"detail": "<中文说明>"}`` with 404 (no such
rule), 409 (an identical rule exists) or 422 (invalid input). In demo mode
the existing middleware rejects every write with 403; reads work.

Plain ``def`` routes: FastAPI runs them in a worker thread, so the quote
requests and SQLite calls do not block the event loop. No read opens the
ledger for writing: a weight rule shown with quotes reads it read-only, as of
today (``core.alerts.view``); ``?quotes=0`` does not read it at all.
"""
import logging
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Path, Query

from core.alerts import events, monitor, rules, view
from core.market.runtime import get_market_runtime
from core.market.search import identity_key
from core.market.today import moment

logger = logging.getLogger(__name__)
router = APIRouter()

def attempt(operation):
    try:
        return operation()
    except rules.RuleNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except rules.RuleConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except rules.AlertRuleError as exc:
        raise HTTPException(422, str(exc)) from None
    except sqlite3.OperationalError as exc:
        if 'locked' not in str(exc) and 'busy' not in str(exc):
            raise
        raise HTTPException(503, '系统正忙，暂时无法处理。请稍后重试。') from None


def _with_market(items, quotes: bool = True):
    return [{**item, 'identity': identity_key(item.get('ticker'))} for item in _market(items, quotes)]


def _market(items, quotes: bool = True):
    runtime = get_market_runtime()
    try:
        # The ledger is read read-only, as of today, and only for a weight rule shown with quotes.
        return view.with_market(items, runtime, quotes=quotes)
    except Exception as exc:
        logger.warning('alert list could not add market data: %s', type(exc).__name__)
        return view.with_market(items, runtime, None, quotes=False)


@router.get('/rules')
def list_rules(quotes: bool = True, ticker: Optional[str] = Query(None, max_length=40)):
    """``{items, as_of}``. ``?quotes=0`` skips the quote request (``market`` fields are null);
    ``?ticker=`` keeps the rules of that listing, in any spelling (422 for an invalid ticker)."""
    def run():
        items = _with_market(rules.list_rules(ticker=ticker), quotes)
        return {'items': items, 'as_of': moment(get_market_runtime().clock.now())}
    return attempt(run)


@router.post('/rules', status_code=201)
def create_rule(payload: Any = Body(None)):
    return attempt(lambda: _with_market([rules.create(payload, source='manual')])[0])


@router.get('/rules/{rule_id}')
def get_rule(rule_id: int = Path(..., ge=1, le=rules.MAX_ID)):
    return attempt(lambda: _with_market([rules.get(rule_id)])[0])


@router.patch('/rules/{rule_id}')
def update_rule(rule_id: int = Path(..., ge=1, le=rules.MAX_ID), changes: Any = Body(None)):
    return attempt(lambda: _with_market([rules.update(rule_id, changes)])[0])


@router.delete('/rules/{rule_id}')
def delete_rule(rule_id: int = Path(..., ge=1, le=rules.MAX_ID)):
    return attempt(lambda: {'deleted': True, 'rule': rules.delete(rule_id)})


@router.get('/events')
def list_events(limit: int = Query(50, ge=1, le=events.MAX_LIMIT),
                before: Optional[int] = Query(None, ge=1, le=rules.MAX_ID),
                rule_id: Optional[int] = Query(None, ge=1, le=rules.MAX_ID)):
    """``{items, next_before}``, newest first. Pass ``next_before`` as ``before`` for the next page."""
    def run():
        page = events.list_events(limit, before, rule_id)
        if not page['items']:
            return page
        # The monitor records how far its published digests reach (``digest_covered_event_id``).
        covered = int(monitor.heartbeat().get('digest_covered_event_id') or 0)
        for item in page['items']:
            item['digest_sent'] = item['id'] <= covered if item.get('delivery') == 'suppressed' else None
        return page
    return attempt(run)


@router.get('/status')
def status():
    return attempt(lambda: monitor.status(get_market_runtime()))
