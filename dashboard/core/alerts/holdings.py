"""The confirmed holdings a weight rule looks at: the ledger as of today, read without writing.

The monitor and the rule view both value the portfolio the way the Today page
does: the confirmed ledger replayed as of today, the Shanghai date of the
session clock (``core.ledger.replay_as_of``), so an event dated tomorrow has
not happened yet for a rule either.

The ledger is opened read-only (``core.ledger.confirmed_state``): reading it
never creates ``ledger.sqlite3``, never initialises or migrates it and never
grows it, also in the read-only demo. A writer busy for longer than
``LEDGER_TIMEOUT`` makes the weight unavailable for that read, never stale.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional, Tuple

from core.ledger import LedgerBusy, LedgerError, LedgerUnavailable, confirmed_state

logger = logging.getLogger(__name__)

LEDGER_TIMEOUT = 2.0
NO_HOLDINGS = '账本没有已确认的持仓'
LEDGER_BUSY = '账本正忙（正在确认记录），暂时无法读取'
LEDGER_UNREADABLE = '无法读取账本'


def as_of(clock) -> str:
    """Today, the Shanghai date of the session clock, as the Today page counts it."""
    return clock.now().date().isoformat()


def confirmed_holdings(clock, ledger=None, *, timeout: float = LEDGER_TIMEOUT) -> Tuple[Optional[dict], Optional[str]]:
    """``(state, None)`` with the ledger's state as of today, or ``(None, reason)`` (Chinese, fixed texts).

    ``ledger`` is ``None`` for the portfolio's own ledger, read-only; a
    ``core.ledger.Ledger`` (tests, callers that hold one) is read through its
    ``state(as_of)``, the same replay.
    """
    day = as_of(clock)
    try:
        state = confirmed_state(timeout=timeout, as_of=day) if ledger is None else ledger.state(day)
    except LedgerBusy:
        return None, LEDGER_BUSY
    except LedgerUnavailable as exc:      # damaged, or an interrupted write to recover: never "no holdings"
        logger.warning('alert holdings: the ledger could not be read: %s', exc.reason)
        return None, LEDGER_UNREADABLE
    except sqlite3.OperationalError as exc:
        logger.warning('alert holdings: the ledger could not be read: %s', type(exc).__name__)
        text = str(exc)
        return None, LEDGER_BUSY if ('locked' in text or 'busy' in text) else LEDGER_UNREADABLE
    except (LedgerError, sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning('alert holdings: the ledger could not be read: %s', type(exc).__name__)
        return None, LEDGER_UNREADABLE
    if not state or not state.get('accounts'):
        return None, NO_HOLDINGS
    return state, None
