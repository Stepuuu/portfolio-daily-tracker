"""How the backend was started, for the web app's shell. Mounted at ``/api/app``. Read-only.

``GET /status``::

    mode     'full' | 'lab' | 'demo'
             lab:  TRACKER_LAB_ONLY=1, research only (the shell shows 研究 and 设置)
             demo: TRACKER_DEMO_MODE=1, fictional data, every write is refused (403)
    ledger   {active, revision}: whether a confirmed ledger exists and its revision
             (0 and False when there is none; both null when it cannot be read)
    version  the application version

It never writes: the ledger is opened read-only (``core.ledger.read_only_connection``: no
``-wal`` / ``-shm`` file is left beside a WAL ledger), and a missing file (or one without a
ledger in it yet) is reported as inactive.
"""
import logging
import os
import sqlite3

from fastapi import APIRouter, Request

from core.ledger import LedgerBusy, portfolio_directory, read_only_connection

logger = logging.getLogger(__name__)
router = APIRouter()


def mode() -> str:
    if os.environ.get('TRACKER_LAB_ONLY') == '1':
        return 'lab'
    return 'demo' if os.environ.get('TRACKER_DEMO_MODE') == '1' else 'full'


def ledger_status() -> dict:
    path = portfolio_directory() / 'ledger.sqlite3'
    if not path.is_file():
        return {'active': False, 'revision': 0}
    try:
        with read_only_connection(path.resolve(), timeout=5) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_meta'").fetchone() is None:
                row = None
            else:
                row = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()
    except (sqlite3.Error, OSError, LedgerBusy) as exc:
        logger.warning('app status could not read the ledger: %s', type(exc).__name__)
        return {'active': None, 'revision': None}
    revision = int(row[0]) if row else 0
    return {'active': revision > 0, 'revision': revision}


@router.get('/status')
def status(request: Request):
    # A plain ``def``: the SQLite read runs in a worker thread.
    return {'mode': mode(), 'ledger': ledger_status(), 'version': request.app.version}
